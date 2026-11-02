"""민원 분류(Gemini)·묶음·통계와 참여자 안내 메시지 인식. DB 와 무관한 순수 로직."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx

MIN_CHARS = 5
BATCH = 20
GROUP_WINDOW = timedelta(minutes=30)
REPLY_MAX_AGE = timedelta(minutes=30)
# 업체 톡방 처리 요청은 그 방에 새 글(알림)이 와야 보낼 수 있어 더 오래 기다린다
VENDOR_MAX_AGE = timedelta(hours=12)
SELF_SENDER = "나"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_REPLY_TEMPLATE = "[{분류}] 민원이 접수되었습니다."
# 담당 업체 카톡방으로 보내는 처리 요청. {업체명} {방} {분류} {요약} {내용} {보낸사람} {시각}
DEFAULT_VENDOR_TEMPLATE = (
    "[민원 처리 요청] {업체명} 담당자님,\n"
    "'{방}' 대화방에서 [{분류}] 민원이 접수되었습니다.\n"
    "- 보낸 사람: {보낸사람}\n"
    "- 요약: {요약}\n"
    "- 내용: {내용}\n"
    "- 접수 시각: {시각}\n"
    "빠른 처리 부탁드립니다."
)
# 이전 기본 문구(보낸 사람이 괄호 안에만 있던 것). 저장값이 이것이면 새 기본 문구로 바꿔 쓴다
OLD_VENDOR_TEMPLATES = (
    "[민원 처리 요청] {업체명} 담당자님,\n'{방}' 대화방에서 [{분류}] 민원이 접수되었습니다.\n"
    "- 요약: {요약}\n- 내용: {내용}\n- 접수: {시각} ({보낸사람})\n빠른 처리 부탁드립니다.",
)
VENDOR_PLACEHOLDERS = {
    "{업체명}": "민원처리업체 이름",
    "{방}": "민원이 발생한 카톡 대화방",
    "{분류}": "민원 카테고리",
    "{요약}": "Gemini 한 줄 요약",
    "{내용}": "민원 메시지 원문(최대 300자)",
    "{보낸사람}": "민원을 올린 사람",
    "{시각}": "민원 시각 (MM/DD HH:mm)",
}
FALLBACK_CATEGORY = "기타"
# 처음 설치 시 등록되는 기본 분류(이름, Gemini 판정 기준 설명). 이후에는 웹 '민원 카테고리'에서 관리한다.
DEFAULT_CATEGORIES: list[tuple[str, str]] = [
    ("소음", "층간소음·생활소음 등 소리 문제"),
    ("누수·하자", "물 새는 문제, 결로, 시공 불량"),
    ("주차", "주차 공간·불법주차·이중주차"),
    ("청소·쓰레기", "청소 상태, 쓰레기·분리수거"),
    ("시설·고장", "엘리베이터·조명·보일러·출입문 등 시설 고장"),
    ("관리비", "관리비 문의·이의"),
    ("보안·출입", "출입 통제, 보안, 택배"),
    ("생활·이웃", "이웃 간 흡연·냄새·반려동물 등 생활 불편"),
    (FALLBACK_CATEGORY, "위 분류에 해당하지 않는 민원"),
]
CATEGORIES = [name for name, _ in DEFAULT_CATEGORIES]  # 하위 호환: 기본 분류 이름


def build_prompt(categories: list[tuple[str, str]] | None = None) -> str:
    """분류 목록(이름, 설명)으로 Gemini 지시문을 만든다. '기타'는 항상 포함된다."""
    cats = list(categories or DEFAULT_CATEGORIES)
    if FALLBACK_CATEGORY not in {n for n, _ in cats}:
        cats.append((FALLBACK_CATEGORY, "위 분류에 해당하지 않는 민원"))
    guide = "\n".join(f'  - "{n}": {d}' if d else f'  - "{n}"' for n, d in cats)
    names = "|".join(n for n, _ in cats)
    return f"""너는 아파트·오피스텔 등 공동주택 관리사무소의 민원 분류 담당자다.
카카오톡 대화방에서 수집한 메시지 각각이 "건물에 사는 입주민이 제기하는 민원"인지 판정하라.

민원으로 본다: 입주민이 건물·시설·관리·생활 불편에 대해 해결·조치·개선을 요청하거나 불만·문제를 알리는 메시지.
민원이 아니다: 인사·잡담·감사, 불편 제기가 아닌 단순 대화, 관리사무소·관리자의 공지나 답변,
  광고·홍보, 개인 간 거래, 건물과 무관한 내용. 애매하면 민원이 아닌 것으로 판정한다.

분류는 아래 중 하나만 고른다 (해당 없는 민원은 "{FALLBACK_CATEGORY}"):
{guide}

반드시 아래 JSON 형식으로만 답하라. 입력의 모든 id 에 대해 결과를 하나씩 포함한다.
{{"results":[{{"id":입력id,"is_complaint":true 또는 false,
  "category":"{names}",
  "summary":"민원이면 핵심 내용을 25자 이내로, 아니면 빈 문자열",
  "confidence":0.0~1.0}}]}}"""


SYSTEM_PROMPT = build_prompt()

_WS = re.compile(r"[ \t\r\n]")


def is_eligible(message_type: str, text: str | None, sender: str) -> bool:
    """분류 대상: 텍스트 메시지 · 공백 제외 5자 이상 · 내가 보낸 메시지(자동 안내 포함) 제외"""
    return message_type == "text" and sender != SELF_SENDER and len(_WS.sub("", text or "")) >= MIN_CHARS


# ---- Gemini -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    id: int
    room: str
    sender: str
    text: str


@dataclass(frozen=True)
class Verdict:
    id: int
    is_complaint: bool
    category: str
    summary: str
    confidence: float


class GeminiError(Exception):
    """retry=True 면 일시 오류(나중에 재시도), False 면 설정 오류(키·모델)"""

    def __init__(self, message: str, retry: bool):
        super().__init__(message)
        self.retry = retry


def request_body(items: list[Item], categories: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    user = json.dumps([{"id": i.id, "room": i.room, "sender": i.sender, "text": i.text[:1000]} for i in items], ensure_ascii=False)
    return {
        "systemInstruction": {"parts": [{"text": build_prompt(categories)}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }


def parse_response(body: dict[str, Any], requested: set[int], allowed: set[str] | None = None) -> list[Verdict]:
    allowed = (allowed or set(CATEGORIES)) | {FALLBACK_CATEGORY}
    try:
        parts = body["candidates"][0]["content"]["parts"]
        results = json.loads("".join(p.get("text", "") for p in parts))["results"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"응답 해석 실패: {exc}") from exc
    seen: dict[int, Verdict] = {}
    for r in results:
        if not isinstance(r, dict):
            continue
        try:
            rid = int(r.get("id", -1))
        except (TypeError, ValueError):
            continue
        if rid not in requested or rid in seen:
            continue
        category = r.get("category") if r.get("category") in allowed else FALLBACK_CATEGORY
        try:
            confidence = min(1.0, max(0.0, float(r.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        seen[rid] = Verdict(rid, bool(r.get("is_complaint", False)), category, str(r.get("summary", ""))[:100], confidence)
    return list(seen.values())


class GeminiClient:
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str, model: str, timeout: float = 90.0, categories: list[tuple[str, str]] | None = None):
        self.api_key, self.model, self.timeout = api_key, model, timeout
        self.categories = categories

    def classify(self, items: list[Item]) -> list[Verdict]:
        try:
            res = httpx.post(
                f"{self.BASE}/{self.model}:generateContent",
                headers={"x-goog-api-key": self.api_key},
                json=request_body(items, self.categories),
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise GeminiError(f"네트워크 오류: {exc}", retry=True) from exc
        if res.status_code == 200:
            allowed = {n for n, _ in self.categories} if self.categories else None
            return parse_response(res.json(), {i.id for i in items}, allowed)
        text = res.text[:200]
        if res.status_code == 400 and "API_KEY_INVALID" in text:
            raise GeminiError("Gemini API 키가 올바르지 않습니다", retry=False)
        if res.status_code in (401, 403):
            raise GeminiError(f"Gemini API 키 권한 오류 (HTTP {res.status_code})", retry=False)
        if res.status_code == 404:
            raise GeminiError(f"모델 '{self.model}' 을 사용할 수 없습니다", retry=False)
        if res.status_code == 429 or res.status_code >= 500:
            raise GeminiError(f"Gemini 일시 오류 (HTTP {res.status_code})", retry=True)
        raise GeminiError(f"HTTP {res.status_code}: {text}", retry=False)


def format_vendor_message(template: str, *, vendor: str, room: str, category: str, summary: str, content: str, sender: str, at: datetime) -> str:
    values = {
        "{업체명}": vendor,
        "{방}": room,
        "{분류}": category,
        "{요약}": summary or "-",
        "{내용}": re.sub(r"\s+", " ", content or "").strip()[:300],
        "{보낸사람}": sender,
        "{시각}": at.strftime("%m/%d %H:%M"),
    }
    text = template
    for key, value in values.items():
        text = text.replace(key, value)
    return text.strip()[:1000]


def format_reply(template: str, sender: str, category: str, summary: str, room: str) -> str:
    return (template.replace("{보낸사람}", sender).replace("{분류}", category)
            .replace("{요약}", summary).replace("{방}", room).strip())


# ---- 묶음·통계 -------------------------------------------------------------------------

@dataclass
class Group:
    """같은 방·사람·분류로 직전 민원과 30분 이내 연쇄된 민원 = 하나의 문제"""
    items: list[dict[str, Any]] = field(default_factory=list)

    @property
    def first(self): return self.items[0]

    @property
    def latest(self): return self.items[-1]

    def to_dict(self) -> dict[str, Any]:
        best = max(self.items, key=lambda c: c["confidence"])
        return {
            "ids": [c["id"] for c in self.items],
            "count": len(self.items),
            "room_name": self.first["room_name"],
            "sender_name": self.first["sender_name"],
            "category": self.first["category"],
            "summary": best["summary"] or (self.first["message_text"] or "")[:30],
            "start_at": self.first["posted_at"],
            "end_at": self.latest["posted_at"],
            "latest_text": self.latest["message_text"],
            "reply_status": self.first["reply_status"],
            "reply_note": self.first["reply_note"],
        }


def group_complaints(rows: list[dict[str, Any]], window: timedelta = GROUP_WINDOW) -> list[Group]:
    """rows: id, room_name, sender_name, category, posted_at(datetime) … → 최근 문제 먼저"""
    open_: dict[tuple, Group] = {}
    groups: list[Group] = []
    for r in sorted(rows, key=lambda r: (r["posted_at"], r["id"])):
        key = (r["room_name"], r["sender_name"], r["category"])
        g = open_.get(key)
        if g and r["posted_at"] - g.latest["posted_at"] <= window:
            g.items.append(r)
        else:
            g = Group([r])
            groups.append(g)
            open_[key] = g
    return sorted(groups, key=lambda g: g.latest["posted_at"], reverse=True)


PERIODS = {"today": 1, "week": 7, "month": 30, "all": None}


def compute_stats(groups: list[Group], period: str, now: datetime, top: int = 10) -> dict[str, Any]:
    days = PERIODS.get(period, 7)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1) if days else None
    in_range = [g for g in groups if start is None or g.first["posted_at"] >= start]

    def ranked(key) -> list[list[Any]]:
        counts: dict[str, int] = {}
        for g in in_range:
            counts[key(g)] = counts.get(key(g), 0) + 1
        return [[k, v] for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]

    n_days = days or 30
    first_day = today - timedelta(days=n_days - 1)
    daily = []
    for d in range(n_days):
        day = first_day + timedelta(days=d)
        daily.append([day.strftime("%m/%d"), sum(1 for g in groups if day <= g.first["posted_at"] < day + timedelta(days=1))])
    hourly = [0] * 24
    weekday_hour = [[0] * 24 for _ in range(7)]  # 월(0)~일(6) × 0~23시
    for g in in_range:
        at = g.first["posted_at"]
        hourly[at.hour] += 1
        weekday_hour[at.weekday()][at.hour] += 1
    firsts = [g.first for g in in_range]

    # 분류별 세부: 일별 · 방별 (쌓은 막대용)
    def by_cat(items) -> dict[str, int]:
        out: dict[str, int] = {}
        for g in items:
            out[g.first["category"]] = out.get(g.first["category"], 0) + 1
        return out

    daily_detail = []
    for d in range(n_days):
        day = first_day + timedelta(days=d)
        items = [g for g in groups if day <= g.first["posted_at"] < day + timedelta(days=1)]
        daily_detail.append([day.strftime("%m/%d"), len(items), by_cat(items), day.weekday()])
    room_detail = [
        [room, n, by_cat([g for g in in_range if g.first["room_name"] == room])]
        for room, n in ranked(lambda g: g.first["room_name"])
    ]
    # 직전 같은 길이 기간 (전체 기간은 비교 없음)
    prev_issues = None
    if start is not None:
        prev_start = start - timedelta(days=days)
        prev_issues = sum(1 for g in groups if prev_start <= g.first["posted_at"] < start)
    return {
        "range_start": start,
        "prev_issues": prev_issues,
        "weekday_hour": weekday_hour,
        "daily_detail": daily_detail,
        "room_detail": room_detail,
        "period": period,
        "issues": len(in_range),
        "messages": sum(len(g.items) for g in in_range),
        "repeated_issues": sum(1 for g in in_range if len(g.items) > 1),
        "reply_sent": sum(1 for f in firsts if f["reply_status"] == 1),
        "reply_failed": sum(1 for f in firsts if f["reply_status"] == -1),
        "by_category": ranked(lambda g: g.first["category"]),
        "by_room": ranked(lambda g: g.first["room_name"]),
        "by_sender": ranked(lambda g: f'{g.first["sender_name"]} · {g.first["room_name"]}'),
        "daily": daily,
        "hourly": hourly,
    }


# ---- 참여자 안내 메시지 ---------------------------------------------------------------

_JOIN = re.compile(r"^(.+?)님이 (?:들어왔습니다|입장하셨습니다|입장했습니다)\.?$")
_LEAVE = re.compile(r"^(.+?)님이 (?:나갔습니다|퇴장하셨습니다|퇴장했습니다)\.?$")
_KICK_BY = re.compile(r"^(.+?)님이 (.+?)님을 내보냈습니다\.?$")
_KICK = re.compile(r"^(.+?)님을 내보냈습니다\.?$")
_INVITE = re.compile(r"^(.+?)님이 (.+?)님을 초대했습니다\.?$")
EVENT_LABELS = {"join": "입장", "leave": "퇴장", "kick": "내보냄", "invite": "초대", "rename": "대화명 변경", "first_seen": "첫 발언"}


def split_names(raw: str) -> list[str]:
    return [n.strip().removesuffix("님") for n in re.split(r"님,\s*|님과\s+|,\s*", raw) if n.strip()]


def detect_member_events(text: str) -> list[tuple[str, str, str | None]]:
    """안내 메시지면 [(type, member, actor)], 일반 대화면 []"""
    t = (text or "").strip()
    if m := _INVITE.match(t):
        return [("invite", n, m.group(1)) for n in split_names(m.group(2))]
    if m := _KICK_BY.match(t):
        return [("kick", m.group(2), m.group(1))]
    if m := _KICK.match(t):
        return [("kick", m.group(1), None)]
    if m := _JOIN.match(t):
        return [("join", m.group(1), None)]
    if m := _LEAVE.match(t):
        return [("leave", m.group(1), None)]
    return []
