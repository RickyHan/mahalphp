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
# 같은 방·사람·분류로 이 시간 안에 이어진 민원 = 같은 문제 (웹 '시스템 설정'에서 변경, 1~240분)
DEFAULT_GROUP_MINUTES = 15
GROUP_MINUTES_RANGE = (1, 240)
GROUP_WINDOW = timedelta(minutes=DEFAULT_GROUP_MINUTES)
REPLY_MAX_AGE = timedelta(minutes=30)
# 업체 톡방 처리 요청은 그 방에 새 글(알림)이 와야 보낼 수 있어 더 오래 기다린다
VENDOR_MAX_AGE = timedelta(hours=12)
# 예약 메시지는 휴대폰이 1시간 안에 못 보내면 만료
SCHEDULE_MAX_AGE = timedelta(hours=1)
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
    ("청소", "공용부 청소 상태, 쓰레기·분리수거, 악취"),
    ("승강기", "엘리베이터 고장·멈춤·갇힘·이상 소음·점검"),
    ("소방", "화재감지기·경보 오작동, 소화기·소화전, 스프링클러, 비상구·피난 설비"),
    ("전기", "정전, 조명·전등 고장, 차단기·콘센트 등 전기 설비"),
    ("기계주차", "기계식 주차설비(주차타워·주차 리프트) 고장·오작동·입출고 문제"),
    ("누수", "천장·벽·바닥으로 물이 새는 문제, 결로"),
    ("배관", "배수 막힘, 하수 역류, 급수·수도 배관 문제"),
    ("자동문", "공동현관·출입구 자동문 고장, 개폐 불량"),
    ("펌프", "급수·배수 펌프 고장, 수압 저하, 단수"),
    ("CCTV", "CCTV 고장·화면 불량, 영상 확인 요청"),
    ("조경", "수목·화단·잔디 관리, 가지치기, 제초, 낙엽"),
    (FALLBACK_CATEGORY, "위 분류에 해당하지 않는 민원"),
]
CATEGORIES = [name for name, _ in DEFAULT_CATEGORIES]  # 하위 호환: 기본 분류 이름
# 기본 분류 묶음의 판. 판이 바뀌면 서버 시작 후 첫 사용 때 기존 분류를 한 번 교체한다.
CATEGORY_SET_VERSION = "2"
# 교체할 때 이전 분류의 민원·업체 지정을 이어받는 새 분류
CATEGORY_RENAMES = {"누수·하자": "누수", "청소·쓰레기": "청소"}


def build_prompt(categories: list[tuple[str, str]] | None = None) -> str:
    """분류 목록(이름, 설명)으로 Gemini 지시문을 만든다. '기타'는 항상 포함된다."""
    cats = list(categories or DEFAULT_CATEGORIES)
    if FALLBACK_CATEGORY not in {n for n, _ in cats}:
        cats.append((FALLBACK_CATEGORY, "위 분류에 해당하지 않는 민원"))
    guide = "\n".join(f'  - "{n}": {d}' if d else f'  - "{n}"' for n, d in cats)
    names = "|".join(n for n, _ in cats)
    return f"""너는 아파트·오피스텔 등 공동주택 관리사무소의 민원 분류 담당자다.
카카오톡 대화방에서 수집한 메시지 각각이 "건물에 사는 입주민이 제기하는 민원"인지 판정하라.

민원으로 본다 — 아래를 모두 만족할 때만:
  1) 입주민이 2) 지금 겪고 있는 구체적인 건물·시설·생활 문제(고장·누수·청소·소음·주차 등)에 대해
  3) 관리 측에 확인·수리·조치·처리를 요청하거나, 그 문제를 새로 알리는 메시지.
  예) "3층 복도 전등이 나갔어요", "주차장 천장에서 물이 새요 확인 부탁드립니다", "엘리베이터가 멈췄어요"
민원이 아니다:
  - 시공사·건물·관리회사·관리비·공사비에 대한 비판·항의·하소연·의견 (예: "날림공사라 돈만 낸다", "소송은 왜 안 하냐")
  - 공사·수리 비용, 투표, 업체 선정, 관리 방식에 대한 논의나 질문
  - 지난 일·과거 수리 사례를 꺼내거나, 여러 문제를 나열하며 불만을 토로하는 글
    (단, 그 안에 "지금 이것을 조치해 달라"는 구체적 요청이 분명히 있으면 민원)
  - 다른 사람 말에 대한 맞장구·반박, 법적 문제 제기, 정보 공유
  - 인사·잡담·감사, 관리사무소·관리자(동대표 등)의 공지나 답변, 광고·홍보, 개인 간 거래, 건물과 무관한 내용
애매하면 민원이 아닌 것으로 판정한다.

대화 맥락: 메시지마다 "context" 에 같은 방의 직전 대화(오래된 → 최근 순, "보낸사람: 내용")가 들어 있을 수 있다.
  대화 흐름을 보고 판단하라. 예) 관리비·공사비 논쟁 중의 발언은 민원이 아니다.
  앞 사람이 알린 고장에 대해 "저희 집도 그래요"처럼 같은 문제를 새로 알리면 민원이다.
  context 의 메시지는 판정 대상이 아니다. 입력 id 의 text 만 판정한다.

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
    # 같은 방의 직전 대화 ("보낸사람: 내용", 오래된 → 최근). 맥락 판단용, 판정 대상 아님
    context: tuple[str, ...] = ()


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
    rows = []
    for i in items:
        row: dict[str, Any] = {"id": i.id, "room": i.room, "sender": i.sender, "text": i.text[:1000]}
        if i.context:
            row["context"] = [c[:300] for c in i.context]
        rows.append(row)
    user = json.dumps(rows, ensure_ascii=False)
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

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            res = httpx.post(
                f"{self.BASE}/{self.model}:generateContent",
                headers={"x-goog-api-key": self.api_key},
                json=body,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise GeminiError(f"네트워크 오류: {exc}", retry=True) from exc
        if res.status_code == 200:
            return res.json()
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

    def classify(self, items: list[Item]) -> list[Verdict]:
        body = self._post(request_body(items, self.categories))
        allowed = {n for n, _ in self.categories} if self.categories else None
        return parse_response(body, {i.id for i in items}, allowed)

    def match_faq(self, items: list[Item], faqs: list[tuple[int, str, str]]) -> list["FaqMatch"]:
        body = self._post(faq_request_body(items, faqs))
        return parse_faq_response(body, {i.id for i in items}, {f[0] for f in faqs})


# ---- FAQ 자동 답변: 메시지가 어느 FAQ 질문과 같은 의도인지 ---------------------------------------

FAQ_MIN_CONFIDENCE = 0.75  # 이보다 낮으면 답변하지 않음


@dataclass(frozen=True)
class FaqMatch:
    id: int
    faq_id: int
    confidence: float


def build_faq_prompt(faqs: list[tuple[int, str, str]]) -> str:
    """faqs: (id, 분야, 질문)"""
    guide = "\n".join(f"  {fid}. [{cat}] {q}" if cat else f"  {fid}. {q}" for fid, cat, q in faqs)
    return f"""너는 빌라·공동주택 관리회사의 상담 담당자다.
입주민 카카오톡 단체방에서 수집한 메시지 각각이 아래 자주 묻는 질문(FAQ) 중 하나와 "같은 의도"인지 판정하라.

같은 의도로 본다: 그 FAQ 질문이 다루는 상황을 입주민이 겪고 있거나(예: "엘리베이터가 멈췄어요" → 승강기 고장),
  그 내용을 묻거나 요청하는 메시지. 표현이 달라도 의도가 같으면 해당한다.
해당 없음(null): 인사·잡담·감사, 관리회사·관리자의 공지나 답변, 이미 처리됐다는 보고, 다른 사람 말에 대한 단순 맞장구,
  FAQ 와 주제만 비슷하고 묻는 내용이 다른 경우, 여러 FAQ 중 어느 것인지 애매한 경우. 애매하면 null 로 한다.

FAQ 목록 (번호. [분야] 질문):
{guide}

반드시 아래 JSON 형식으로만 답하라. 입력의 모든 id 에 대해 결과를 하나씩 포함한다.
{{"results":[{{"id":입력id,"faq_id":FAQ 번호 또는 null,"confidence":0.0~1.0}}]}}"""


def faq_request_body(items: list[Item], faqs: list[tuple[int, str, str]]) -> dict[str, Any]:
    user = json.dumps([{"id": i.id, "sender": i.sender, "text": i.text[:1000]} for i in items], ensure_ascii=False)
    return {
        "systemInstruction": {"parts": [{"text": build_faq_prompt(faqs)}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }


def parse_faq_response(body: dict[str, Any], requested: set[int], faq_ids: set[int]) -> list[FaqMatch]:
    """확실한(FAQ_MIN_CONFIDENCE 이상) 짝만 돌려준다"""
    try:
        parts = body["candidates"][0]["content"]["parts"]
        results = json.loads("".join(p.get("text", "") for p in parts))["results"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"FAQ 응답 해석 실패: {exc}") from exc
    seen: dict[int, FaqMatch] = {}
    for r in results if isinstance(results, list) else []:
        if not isinstance(r, dict):
            continue
        try:
            rid, fid = int(r.get("id", -1)), r.get("faq_id")
            fid = int(fid) if fid is not None else None
            confidence = min(1.0, max(0.0, float(r.get("confidence", 0))))
        except (TypeError, ValueError):
            continue
        if rid in requested and rid not in seen and fid in faq_ids and confidence >= FAQ_MIN_CONFIDENCE:
            seen[rid] = FaqMatch(rid, fid, confidence)
    return list(seen.values())

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
    """같은 방·사람·분류로 직전 민원과 설정한 시간(기본 15분) 안에 이어진 민원 = 하나의 문제"""
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


def compute_stats(groups: list[Group], period: str, now: datetime, top: int = 10,
                  room_label: dict[str, str] | None = None, other_label: str = "건물 외 방") -> dict[str, Any]:
    """room_label 을 주면 방 이름을 그 이름(예: 건물)으로 묶은 group_detail 도 만든다 (없는 방은 other_label)."""
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
    group_detail = []
    if room_label is not None:
        label = lambda g: room_label.get(g.first["room_name"], other_label)  # noqa: E731
        group_detail = [[name, n, by_cat([g for g in in_range if label(g) == name])] for name, n in ranked(label)]
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
        "group_detail": group_detail,
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
