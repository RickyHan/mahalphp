"""건물 관리: 건물(이름·주소)을 한 곳씩 등록하고, 수집된 카톡 채팅방을 이름이 가장 비슷한 건물에 하나씩 연결한다.

    GET    /api/admin/apartments                 목록 (주소 · 연결 채팅방 · 메시지·민원 수 · 최근 대화)
    POST   /api/admin/apartments                 건물 하나 추가 (추가 후 자동 매핑)
    POST   /api/admin/apartments/sync            다시 수집하기: 채팅방 자동 매핑 + 목록에 없는 별(★) 방 추가
    PUT    /api/admin/apartments/{id}            이름·주소 수정
    DELETE /api/admin/apartments/{id}            삭제
    GET    /api/admin/apartments/{id}/candidates 연결 후보 (건물 → 채팅방, 목록 외 방 → 건물), 유사도 순
    PUT    /api/admin/apartments/{id}/room       채팅방 직접 연결·해제
    GET    /api/admin/apartments/{id}/vendors    민원 분류별 담당 업체 설정 보기
    PUT    /api/admin/apartments/{id}/vendors    민원 분류별 담당 업체 저장

규칙
    · 건물 하나에 채팅방 하나, 채팅방 하나는 건물 하나에만 연결한다.
    · 자동 매핑은 이름이 확실히 비슷할 때만 한다. 후보가 비슷하게 둘 이상이면(예: 101동·102동) 연결하지 않고 남긴다.
    · 직접 연결·해제한 건물(room_mode=manual)은 자동 매핑이 건드리지 않는다.
    · 목록에 없는 '별 하나(★)로 시작하는 방'은 '목록 외 채팅방'(listed=0)으로 보여 준다.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import db, live
from .auth import SettingsDep, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

# 별 모양 문자 (★ ☆ ⭐ ✩ ✪ ✫ ✬ ✭ ✮ ✯ ✰ 🌟 *). 이모지 변형 선택자(U+FE0F)는 무시
STARS = "★☆⭐✩✪✫✬✭✮✯✰🌟*"
_STAR = f"[{re.escape(STARS)}]\ufe0f?"
_ONE_STAR_PREFIX = re.compile(rf"^\s*{_STAR}(?!\s*{_STAR})")
_ALL_STARS = re.compile(_STAR)


def is_apartment_room(room: str) -> bool:
    """별 '하나'로 시작하는 방 이름인지 (별 두 개 이상으로 시작하면 제외)"""
    return bool(_ONE_STAR_PREFIX.match(room or ""))


def apartment_name(room: str) -> str:
    """방 이름에서 별을 모두 빼고 앞뒤 공백 정리"""
    return re.sub(r"\s+", " ", _ALL_STARS.sub("", room)).strip()


# ---- 이름 유사도 ---------------------------------------------------------------------------

# 채팅방 이름 끝에 붙는 말 (예: '★다솜빌라 입주민방') — 끝에서만 반복해 뗀다
_ROOM_SUFFIXES = ("방", "입주민", "입주자", "입주자대표", "주민", "관리단", "관리소", "관리", "단톡", "톡", "공지", "민원",
                  "소통", "대화", "채팅", "모임", "가족", "님")
# 건물·방 이름 공통 끝말
_KIND_SUFFIXES = ("아파트", "빌라", "apt", "주택", "빌딩", "맨션")
_PAREN = re.compile(r"\([^)]*\)?|\[[^\]]*\]?")
MIN_SCORE = 0.78        # 별(★) 방
MIN_SCORE_PLAIN = 0.86  # 별 없는 방은 더 엄격하게
AMBIGUOUS_GAP = 0.04    # 1·2위 후보가 이만큼도 차이 나지 않으면 연결하지 않음


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def _strip_end(text: str, suffixes: tuple[str, ...]) -> str:
    changed = True
    while changed and text:
        changed = False
        for suf in suffixes:
            if text.endswith(suf) and len(text) > len(suf) + 1:
                text = text[: -len(suf)]
                changed = True
    return text


def name_keys(name: str, room: bool = False) -> set[str]:
    """비교용 이름들: 기호·공백 제거, 끝말 제거, 괄호 안 제거 판"""
    keys: set[str] = set()
    for variant in (name, _PAREN.sub(" ", name or "")):
        core = _plain(variant)
        if room:
            core = _strip_end(core, _ROOM_SUFFIXES)
        for k in (core, _strip_end(core, _KIND_SUFFIXES)):
            if k:
                keys.add(k)
    return keys


def similarity(room: str, building: str) -> float:
    """0~1. 같으면 1, 한쪽이 다른 쪽을 품으면 0.85 이상, 그 밖에는 글자 배열 유사도"""
    best = 0.0
    for r in name_keys(room, room=True):
        for b in name_keys(building):
            if r == b:
                return 1.0
            short, long_ = sorted((r, b), key=len)
            if len(short) >= 3 and short in long_:
                score = 0.85 + 0.1 * len(short) / len(long_)
            else:
                score = SequenceMatcher(None, r, b).ratio()
            best = max(best, score)
    return best


def common_run(room: str, building: str) -> int:
    """두 이름(비교용으로 다듬은 것)에서 이어서 같은 글자 수의 최댓값 — 비슷한 순 정렬의 보조 기준"""
    best = 0
    for r in name_keys(room, room=True):
        for b in name_keys(building):
            m = SequenceMatcher(None, r, b).find_longest_match(0, len(r), 0, len(b))
            best = max(best, m.size)
    return best


def best_matches(rooms: list[str], buildings: list[dict[str, Any]], open_ids: set[int] | None = None) -> list[tuple[str, int, float]]:
    """채팅방 ↔ 건물 1:1 자동 매핑. 확실한 것만 (방, 건물 id, 점수)로 돌려준다.

    애매함은 모든 건물을 놓고 판단하고(이미 연결된 건물 포함), 실제 연결은 open_ids(빈 건물)에만 한다.
    예: 101동만 연결된 뒤 102동이 추가돼도 동이 안 적힌 방은 102동에 붙지 않는다.
    """
    scored: dict[str, list[tuple[float, int]]] = {}
    for room in rooms:
        floor = MIN_SCORE if is_apartment_room(room) else MIN_SCORE_PLAIN
        cands = sorted(((similarity(room, b["name"]), b["id"]) for b in buildings), reverse=True)
        cands = [c for c in cands if c[0] >= floor - AMBIGUOUS_GAP]
        if cands and cands[0][0] >= floor:
            scored[room] = cands
    # 방 기준 애매함: 1·2위 건물 점수가 비슷하면 제외
    firm = {room: c[0] for room, c in scored.items() if len(c) == 1 or c[0][0] - c[1][0] >= AMBIGUOUS_GAP}
    # 건물 기준 애매함: 같은 건물을 가장 원하는 방이 비슷한 점수로 둘 이상이면 제외
    by_building: dict[int, list[tuple[float, str]]] = {}
    for room, (score, bid) in firm.items():
        by_building.setdefault(bid, []).append((score, room))
    result = []
    for bid, lst in by_building.items():
        if open_ids is not None and bid not in open_ids:
            continue
        lst.sort(reverse=True)
        if len(lst) > 1 and lst[0][0] - lst[1][0] < AMBIGUOUS_GAP:
            continue
        result.append((lst[0][1], bid, lst[0][0]))
    return sorted(result, key=lambda x: x[0])


def _now(settings) -> datetime:
    return datetime.now(settings.timezone).replace(tzinfo=None)


def _ser(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


class ApartmentBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    address: str | None = Field(default=None, max_length=255)


class RoomBody(BaseModel):
    room_name: str | None = Field(default=None, max_length=255)


def _vendor_rooms(cur) -> set[str]:
    cur.execute("SELECT room_name FROM vendors WHERE room_name IS NOT NULL")
    return {r["room_name"] for r in cur.fetchall()}


def _chat_rooms(cur) -> list[str]:
    """건물에 연결할 수 있는 채팅방 (업체 방 제외)"""
    from .vendors import is_vendor_room

    vendor_rooms = _vendor_rooms(cur)
    cur.execute("SELECT DISTINCT room_name FROM kakao_messages")
    return sorted(r["room_name"] for r in cur.fetchall() if r["room_name"] not in vendor_rooms and not is_vendor_room(r["room_name"]))


def _move_vendors(cur, src: int, dst: int) -> None:
    """목록 외 방(src)에 해 둔 업체 설정을 건물(dst)로 옮긴다. 건물에 이미 있는 분류는 건물 것을 유지."""
    cur.execute("SELECT category_id FROM apartment_vendors WHERE apartment_id = %s", (dst,))
    have = {r["category_id"] for r in cur.fetchall()}
    cur.execute("SELECT id, category_id FROM apartment_vendors WHERE apartment_id = %s", (src,))
    for r in cur.fetchall():
        if r["category_id"] in have:
            cur.execute("DELETE FROM apartment_vendors WHERE id = %s", (r["id"],))
        else:
            cur.execute("UPDATE apartment_vendors SET apartment_id = %s WHERE id = %s", (dst, r["id"]))


def _attach(cur, building_id: int, room: str, mode: str, score: float | None, now: datetime) -> None:
    """건물에 채팅방을 연결. 그 방을 가진 다른 행은 정리(목록 외 행은 합치고, 다른 건물은 연결 해제)."""
    cur.execute("DELETE FROM apartment_rooms WHERE room_name = %s", (room,))
    cur.execute("SELECT id, listed FROM apartments WHERE room_name = %s AND id <> %s", (room, building_id))
    for other in cur.fetchall():
        if other["listed"]:
            cur.execute("UPDATE apartments SET room_name = NULL, room_mode = 'manual', match_score = NULL, updated_at = %s WHERE id = %s", (now, other["id"]))
        else:
            _move_vendors(cur, other["id"], building_id)
            cur.execute("DELETE FROM apartments WHERE id = %s", (other["id"],))
    cur.execute(
        "UPDATE apartments SET room_name = %s, room_mode = %s, match_score = %s, updated_at = %s WHERE id = %s",
        (room, mode, None if score is None else round(score * 100), now, building_id),
    )


def _extra_rooms(cur) -> set[str]:
    cur.execute("SELECT room_name FROM apartment_rooms")
    return {r["room_name"] for r in cur.fetchall()}


def _automap(cur, now: datetime) -> dict[str, Any]:
    """미연결 건물(자동 대상)에 채팅방을 자동 연결하고, 남은 별(★) 방은 '목록 외 채팅방'으로 추가한다."""
    rooms = _chat_rooms(cur)
    cur.execute("SELECT id, name, room_name, listed, room_mode FROM apartments")
    rows = cur.fetchall()
    taken = {r["room_name"] for r in rows if r["listed"] and r["room_name"]} | _extra_rooms(cur)
    listed = [r for r in rows if r["listed"]]
    targets = [r for r in listed if not r["room_name"] and r["room_mode"] == "auto"]
    free = [room for room in rooms if room not in taken]
    mapped = []
    for room, bid, score in best_matches(free, listed, {t["id"] for t in targets}):
        _attach(cur, bid, room, "auto", score, now)
        name = next(t["name"] for t in targets if t["id"] == bid)
        mapped.append({"room_name": room, "name": name, "score": round(score * 100)})
    cur.execute("SELECT room_name FROM apartments WHERE room_name IS NOT NULL")
    known = {r["room_name"] for r in cur.fetchall()} | _extra_rooms(cur)
    added = []
    for room in rooms:
        if room in known or not is_apartment_room(room):
            continue
        name = apartment_name(room) or room
        try:
            cur.execute(
                "INSERT INTO apartments (name, room_name, listed, room_mode, created_at, updated_at) VALUES (%s, %s, 0, 'auto', %s, %s)",
                (name, room, now, now),
            )
        except db.IntegrityError as exc:  # 동시에 수집한 경우
            if not db.is_duplicate(exc):
                raise
            continue
        added.append({"name": name, "room_name": room})
    cur.execute("DELETE FROM app_settings WHERE name = 'apartments_synced_at'")
    cur.execute("INSERT INTO app_settings (name, value) VALUES ('apartments_synced_at', %s)", (now.isoformat(timespec="seconds"),))
    return {"mapped": mapped, "added": added, "rooms": len(rooms), "matched": sum(1 for r in rooms if is_apartment_room(r))}


def parse_building_list(text: str) -> list[tuple[str, str]]:
    """처음 받은 목록 파일(엑셀에서 복사한 '이름<탭>주소' 줄들 → [(이름, 주소)]. 머리글·빈 줄·같은 이름은 건너뜀."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        if "\t" in line:
            name, _, address = line.partition("\t")
        else:
            parts = re.split(r"\s{2,}", line.strip(), maxsplit=1)
            name, address = parts[0], parts[1] if len(parts) > 1 else ""
        name, address = _clean(name)[:100], _clean(address.replace("\t", " "))[:255]
        if not name or name in ("빌라명", "건물명", "건물 이름", "이름", "아파트명", "name"):
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append((name, address))
    return out


def _import_rows(cur, rows: list[tuple[str, str]], now: datetime) -> tuple[int, int]:
    """(이름, 주소) 목록을 건물로 등록. 같은 이름이 있으면 주소만 갱신. → (새로, 갱신)"""
    created = updated = 0
    cur.execute("SELECT id, name, listed FROM apartments")
    existing = cur.fetchall()
    listed = {r["name"]: r["id"] for r in existing if r["listed"]}
    unlisted = {r["name"]: r["id"] for r in existing if not r["listed"]}
    for name, address in rows:
        if name in listed:
            cur.execute("UPDATE apartments SET address = %s, updated_at = %s WHERE id = %s", (address or None, now, listed[name]))
            updated += 1
        elif name in unlisted:  # 이미 별(★) 방으로 들어와 있던 같은 이름 → 목록 건물로 전환 (연결·업체 설정 유지)
            cur.execute(
                "UPDATE apartments SET listed = 1, address = %s, room_mode = 'manual', match_score = 100, updated_at = %s WHERE id = %s",
                (address or None, now, unlisted.pop(name)),
            )
            updated += 1
        else:
            cur.execute(
                "INSERT INTO apartments (name, address, listed, room_mode, created_at, updated_at) VALUES (%s, %s, 1, 'auto', %s, %s)",
                (name, address or None, now, now),
            )
            created += 1
    return created, updated


# 처음 받은 건물 목록. 서버 시작 때 한 번만 등록한다(이후 웹에서 삭제·수정한 내용은 다시 덮어쓰지 않음).
SEED_FILE = Path(__file__).parent / "data" / "buildings.tsv"
SEED_VERSION = "1"


def seed_buildings(settings) -> dict[str, Any] | None:
    if not SEED_FILE.exists():
        return None
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM app_settings WHERE name = 'buildings_seed'")
        row = cur.fetchone()
        if row and row["value"] == SEED_VERSION:
            return None
        created, updated = _import_rows(cur, parse_building_list(SEED_FILE.read_text(encoding="utf-8")), now)
        result = _automap(cur, now)
        cur.execute("DELETE FROM app_settings WHERE name = 'buildings_seed'")
        cur.execute("INSERT INTO app_settings (name, value) VALUES ('buildings_seed', %s)", (SEED_VERSION,))
    live.bump("apartment")
    return {"created": created, "updated": updated, "mapped": len(result["mapped"])}


@router.get("/api/admin/apartments")
def list_apartments(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, name, room_name, address, listed, room_mode, match_score, created_at, updated_at
               FROM apartments ORDER BY listed DESC, name, id"""
        )
        items = [_ser(r) for r in cur.fetchall()]
        cur.execute("SELECT apartment_id, room_name FROM apartment_rooms ORDER BY id")
        extras: dict[int, list[str]] = {}
        for r in cur.fetchall():
            extras.setdefault(r["apartment_id"], []).append(r["room_name"])
        # 방별 메시지·민원 수 · 최근 대화 (건물은 기본 + 추가 채팅방을 합산)
        cur.execute(
            """SELECT m.room_name, COUNT(m.id) AS messages, MAX(m.posted_at) AS last_at,
                      COALESCE(SUM(CASE WHEN c.verdict = 1 THEN 1 ELSE 0 END), 0) AS complaints
               FROM kakao_messages m LEFT JOIN complaint_check c ON c.message_id = m.id
               GROUP BY m.room_name"""
        )
        per_room = {r["room_name"]: r for r in cur.fetchall()}
        cur.execute(
            """SELECT apartment_id, SUM(CASE WHEN vendor_id IS NOT NULL THEN 1 ELSE 0 END) AS assigned, COUNT(*) AS custom
               FROM apartment_vendors GROUP BY apartment_id"""
        )
        custom = {r["apartment_id"]: r for r in cur.fetchall()}
        cur.execute("SELECT value FROM app_settings WHERE name = 'apartments_synced_at'")
        row = cur.fetchone()
    for i in items:
        c = custom.get(i["id"])
        i["assigned"] = int(c["assigned"] or 0) if c else 0   # 이 건물에 따로 지정한 업체 수
        i["custom"] = int(c["custom"] or 0) if c else 0       # 따로 정한 분류 수('보내지 않음' 포함)
        i["extra_rooms"] = extras.get(i["id"], [])
        rooms = ([i["room_name"]] if i["room_name"] else []) + i["extra_rooms"]
        stats = [per_room[r] for r in rooms if r in per_room]
        i["messages"] = sum(int(x["messages"] or 0) for x in stats)
        i["complaints"] = sum(int(x["complaints"] or 0) for x in stats)
        lasts = [x["last_at"] for x in stats if x["last_at"]]
        last = max(lasts) if lasts else None
        i["last_at"] = last.isoformat() if isinstance(last, datetime) else last
        i["listed"] = bool(i["listed"])
    return {"ok": True, "items": items, "synced_at": row["value"] if row else None}


@router.post("/api/admin/apartments", status_code=201)
def create_apartment(body: ApartmentBody, settings: SettingsDep) -> dict[str, Any]:
    name = _clean(body.name)
    if not name:
        raise HTTPException(422, "건물 이름을 입력하십시오.")
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM apartments WHERE name = %s AND listed = 1", (name,))
        if cur.fetchone():
            raise HTTPException(409, f"'{name}' 건물이 이미 있습니다.")
        cur.execute(
            "INSERT INTO apartments (name, address, listed, room_mode, created_at, updated_at) VALUES (%s, %s, 1, 'auto', %s, %s)",
            (name, _clean(body.address or "") or None, now, now),
        )
        new_id = cur.lastrowid
        result = _automap(cur, now)
    live.bump("apartment")
    return {"ok": True, "id": new_id, **result}


@router.post("/api/admin/apartments/sync")
def sync_apartments(settings: SettingsDep) -> dict[str, Any]:
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        result = _automap(cur, now)
    live.bump("apartment")
    return {"ok": True, **result}


@router.put("/api/admin/apartments/{apartment_id}")
def rename_apartment(apartment_id: int, body: ApartmentBody, settings: SettingsDep) -> dict[str, Any]:
    name = _clean(body.name)
    if not name:
        raise HTTPException(422, "건물 이름을 입력하십시오.")
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, address FROM apartments WHERE id = %s", (apartment_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "건물이 없습니다.")
        address = row["address"] if body.address is None else (_clean(body.address) or None)
        cur.execute("UPDATE apartments SET name = %s, address = %s, updated_at = %s WHERE id = %s", (name, address, _now(settings), apartment_id))
    live.bump("apartment")
    return {"ok": True, "name": name, "address": address}


@router.delete("/api/admin/apartments/{apartment_id}")
def delete_apartment(apartment_id: int, settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM apartment_vendors WHERE apartment_id = %s", (apartment_id,))
        cur.execute("DELETE FROM apartment_vendor_contacts WHERE apartment_id = %s", (apartment_id,))
        cur.execute("DELETE FROM apartment_rooms WHERE apartment_id = %s", (apartment_id,))
        cur.execute("DELETE FROM apartments WHERE id = %s", (apartment_id,))
    live.bump("apartment")
    return {"ok": True}


@router.get("/api/admin/apartments/{apartment_id}/candidates")
def link_candidates(apartment_id: int, settings: SettingsDep) -> dict[str, Any]:
    """목록 건물이면 채팅방 후보, 목록 외 채팅방이면 건물 후보를 유사도 순으로"""
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, room_name, listed FROM apartments WHERE id = %s", (apartment_id,))
        me = cur.fetchone()
        if me is None:
            raise HTTPException(404, "건물이 없습니다.")
        cur.execute("SELECT id, name, room_name, listed FROM apartments")
        rows = cur.fetchall()
        if me["listed"]:
            holder = {r["room_name"]: r for r in rows if r["room_name"]}
            cur.execute("SELECT r.room_name, a.id, a.name, a.listed FROM apartment_rooms r JOIN apartments a ON a.id = r.apartment_id")
            for r in cur.fetchall():
                holder[r["room_name"]] = {"id": r["id"], "name": r["name"], "listed": r["listed"], "room_name": r["room_name"]}
            cur.execute("SELECT room_name, COUNT(*) AS n FROM kakao_messages GROUP BY room_name")
            counts = {r["room_name"]: int(r["n"]) for r in cur.fetchall()}
            items = []
            for room in _chat_rooms(cur):
                h = holder.get(room)
                items.append({
                    "room_name": room, "messages": counts.get(room, 0), "score": round(similarity(room, me["name"]) * 100),
                    "common": common_run(room, me["name"]),
                    "linked_to": h["name"] if h and h["listed"] and h["id"] != me["id"] else None,
                    "current": room == me["room_name"],
                })
            items.sort(key=lambda x: (-x["score"], -x["common"], x["room_name"]))
            return {"ok": True, "kind": "rooms", "items": items}
    extra_count: dict[int, int] = {}
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT apartment_id, COUNT(*) AS n FROM apartment_rooms GROUP BY apartment_id")
        extra_count = {r["apartment_id"]: int(r["n"]) for r in cur.fetchall()}
    items = [
        {"id": r["id"], "name": r["name"], "linked_room": r["room_name"], "extra_rooms": extra_count.get(r["id"], 0),
         "score": round(similarity(me["room_name"] or me["name"], r["name"]) * 100),
         "common": common_run(me["room_name"] or me["name"], r["name"])}
        for r in rows if r["listed"]
    ]
    items.sort(key=lambda x: (x["linked_room"] is not None, -x["score"], -x["common"], x["name"]))
    return {"ok": True, "kind": "buildings", "items": items}


@router.post("/api/admin/apartments/{apartment_id}/rooms")
def add_room(apartment_id: int, body: RoomBody, settings: SettingsDep) -> dict[str, Any]:
    """건물에 채팅방을 '추가로' 연결 (예: 세대방에 더해 상가방). 기본 채팅방이 없으면 기본으로 연결.
    목록 외 채팅방이면 그 행은 합치고(업체 설정 이어받음), 다른 건물에 연결된 방이면 거절."""
    room = (body.room_name or "").strip()
    if not room:
        raise HTTPException(422, "채팅방을 고르십시오.")
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, listed, room_name FROM apartments WHERE id = %s", (apartment_id,))
        me = cur.fetchone()
        if me is None:
            raise HTTPException(404, "건물이 없습니다.")
        if not me["listed"]:
            raise HTTPException(422, "목록의 건물에만 채팅방을 붙일 수 있습니다.")
        if room not in set(_chat_rooms(cur)):
            raise HTTPException(422, "수집된 채팅방이 아닙니다. 화면을 새로 고친 뒤 다시 고르십시오.")
        if room == me["room_name"]:
            return {"ok": True, "mode": "already"}
        cur.execute(
            """SELECT a.name FROM apartments a WHERE a.listed = 1 AND a.id <> %s AND
                 (a.room_name = %s OR a.id IN (SELECT apartment_id FROM apartment_rooms WHERE room_name = %s))""",
            (apartment_id, room, room),
        )
        other = cur.fetchone()
        if other:
            raise HTTPException(409, f"이 채팅방은 이미 '{other['name']}' 에 연결되어 있습니다.")
        if not me["room_name"]:
            _attach(cur, apartment_id, room, "manual", None, now)
            mode = "primary"
        else:
            cur.execute("SELECT id FROM apartments WHERE room_name = %s AND listed = 0", (room,))
            for holder in cur.fetchall():  # 목록 외 채팅방 행 → 합침
                _move_vendors(cur, holder["id"], apartment_id)
                cur.execute("DELETE FROM apartments WHERE id = %s", (holder["id"],))
            cur.execute("DELETE FROM apartment_rooms WHERE room_name = %s", (room,))
            cur.execute("INSERT INTO apartment_rooms (apartment_id, room_name, created_at) VALUES (%s, %s, %s)", (apartment_id, room, now))
            mode = "extra"
    live.bump("apartment")
    return {"ok": True, "mode": mode}


@router.delete("/api/admin/apartments/{apartment_id}/rooms")
def remove_room(apartment_id: int, settings: SettingsDep, room_name: str) -> dict[str, Any]:
    """추가 채팅방 연결 해제 (풀려난 별(★) 방은 다시 '연결 안 된 건물 톡방'에 보임)"""
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM apartment_rooms WHERE apartment_id = %s AND room_name = %s", (apartment_id, room_name))
        _automap(cur, now)
    live.bump("apartment")
    return {"ok": True}


@router.put("/api/admin/apartments/{apartment_id}/room")
def set_room(apartment_id: int, body: RoomBody, settings: SettingsDep) -> dict[str, Any]:
    """건물에 채팅방을 직접 연결(room_name) 또는 해제(null). 직접 정한 건물은 자동 매핑이 바꾸지 않는다."""
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, listed, room_name FROM apartments WHERE id = %s", (apartment_id,))
        me = cur.fetchone()
        if me is None:
            raise HTTPException(404, "건물이 없습니다.")
        if not me["listed"]:
            raise HTTPException(422, "목록 외 채팅방은 '건물 지정'으로 목록의 건물에 연결하십시오.")
        room = (body.room_name or "").strip() or None
        if room is None:
            cur.execute("UPDATE apartments SET room_name = NULL, room_mode = 'manual', match_score = NULL, updated_at = %s WHERE id = %s", (now, apartment_id))
        else:
            if room not in set(_chat_rooms(cur)):
                raise HTTPException(422, "수집된 채팅방이 아닙니다. 화면을 새로 고친 뒤 다시 고르십시오.")
            _attach(cur, apartment_id, room, "manual", None, now)
        _automap(cur, now)  # 풀려난 별(★) 방은 목록 외 채팅방으로 다시 보이게
    live.bump("apartment")
    return {"ok": True}


# ---- 건물별 민원 분류 담당 업체 ------------------------------------------------------------------

class Assignment(BaseModel):
    category_id: int
    mode: str = Field(pattern="^(default|vendor|none)$")  # default=기본 업체, vendor=지정 업체, none=보내지 않음
    vendor_id: int | None = None


class AssignmentsBody(BaseModel):
    assignments: list[Assignment] = Field(max_length=200)


def _apartment(cur, apartment_id: int) -> dict[str, Any]:
    cur.execute("SELECT id, name, room_name FROM apartments WHERE id = %s", (apartment_id,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "건물이 없습니다.")
    return row


@router.get("/api/admin/apartments/{apartment_id}/vendors")
def apartment_vendors(apartment_id: int, settings: SettingsDep) -> dict[str, Any]:
    from .categories import all_categories

    with db.transaction(settings) as conn, conn.cursor() as cur:
        apt = _apartment(cur, apartment_id)
        cats = all_categories(cur)
        cur.execute("SELECT id, name, room_name FROM vendors ORDER BY name, id")
        vendors = cur.fetchall()
        cur.execute("SELECT vendor_id, category_id FROM vendor_categories")
        vendor_cats: dict[int, set[int]] = {}
        for r in cur.fetchall():
            vendor_cats.setdefault(r["vendor_id"], set()).add(r["category_id"])
        cur.execute("SELECT category_id, vendor_id FROM apartment_vendors WHERE apartment_id = %s", (apartment_id,))
        current = {r["category_id"]: r["vendor_id"] for r in cur.fetchall()}
        cur.execute("SELECT DISTINCT room_name FROM kakao_messages")
        rooms = {r["room_name"] for r in cur.fetchall()}
        from .vendor_contacts import contacts_by_vendor

        directory = contacts_by_vendor(cur)
        assigned = apartment_contacts(cur, apartment_id)
    rows = []
    for c in cats:
        defaults = [v for v in vendors if c["id"] in vendor_cats.get(v["id"], set())]
        if c["id"] in current:
            mode, vendor_id = ("vendor", current[c["id"]]) if current[c["id"]] is not None else ("none", None)
        else:
            mode, vendor_id = "default", None
        rows.append({
            "category_id": c["id"], "category": c["name"], "enabled": c["enabled"], "protected": c["protected"],
            "mode": mode, "vendor_id": vendor_id,
            "defaults": [{"id": v["id"], "name": v["name"], "room_name": v["room_name"]} for v in defaults],
        })
    return {
        "ok": True,
        "apartment": apt,
        "categories": rows,
        "vendors": [
            {"id": v["id"], "name": v["name"], "room_name": v["room_name"], "has_messages": v["room_name"] in rooms,
             "directory": directory.get(v["id"], []), "contacts": assigned.get(v["id"], [])}
            for v in vendors
        ],
    }


# ---- 건물 × 업체 담당자 (업체 문자 받을 번호) ------------------------------------------------------------

def apartment_contacts(cur, apartment_id: int) -> dict[int, list[dict[str, str]]]:
    cur.execute(
        "SELECT vendor_id, phone, name FROM apartment_vendor_contacts WHERE apartment_id = %s ORDER BY vendor_id, sort_order, id",
        (apartment_id,),
    )
    out: dict[int, list[dict[str, str]]] = {}
    for r in cur.fetchall():
        out.setdefault(r["vendor_id"], []).append({"phone": r["phone"], "name": r["name"]})
    return out


class ContactBody(BaseModel):
    phone: str = Field(default="", max_length=30)  # 빈 줄은 건너뜀
    name: str = Field(default="", max_length=100)


class ContactsBody(BaseModel):
    contacts: list[ContactBody] = Field(default_factory=list, max_length=10)


@router.put("/api/admin/apartments/{apartment_id}/vendor-contacts/{vendor_id}")
def save_apartment_vendor_contacts(apartment_id: int, vendor_id: int, body: ContactsBody, settings: SettingsDep) -> dict[str, Any]:
    """이 건물에서 이 업체에 문자를 보낼 담당자 번호 (비우면 이 건물은 이 업체에 카톡방으로 보냄)"""
    from .vendor_contacts import norm_phone

    contacts, seen = [], set()
    for c in body.contacts:
        phone = norm_phone(c.phone)
        digits = re.sub(r"\D", "", phone)
        if not digits:
            continue
        if not re.fullmatch(r"0\d{8,10}", digits):
            raise HTTPException(422, f"휴대폰 번호를 확인하십시오: {c.phone}")
        if digits in seen:
            continue
        seen.add(digits)
        contacts.append({"phone": phone, "name": c.name.strip()})
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        _apartment(cur, apartment_id)
        cur.execute("SELECT id FROM vendors WHERE id = %s", (vendor_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "업체가 없습니다.")
        cur.execute("DELETE FROM apartment_vendor_contacts WHERE apartment_id = %s AND vendor_id = %s", (apartment_id, vendor_id))
        for i, c in enumerate(contacts):
            cur.execute(
                """INSERT INTO apartment_vendor_contacts (apartment_id, vendor_id, phone, name, sort_order, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (apartment_id, vendor_id, c["phone"], c["name"], i, now),
            )
    live.bump("apartment")
    return {"ok": True, "contacts": contacts}


@router.put("/api/admin/apartments/{apartment_id}/vendors")
def save_apartment_vendors(apartment_id: int, body: AssignmentsBody, settings: SettingsDep) -> dict[str, Any]:
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        _apartment(cur, apartment_id)
        cur.execute("SELECT id FROM complaint_categories")
        cat_ids = {r["id"] for r in cur.fetchall()}
        cur.execute("SELECT id FROM vendors")
        vendor_ids = {r["id"] for r in cur.fetchall()}
        for a in body.assignments:
            if a.category_id not in cat_ids:
                raise HTTPException(422, "없는 민원 카테고리입니다. 화면을 새로 고친 뒤 다시 저장하십시오.")
            if a.mode == "vendor" and a.vendor_id not in vendor_ids:
                raise HTTPException(422, "없는 업체입니다. 화면을 새로 고친 뒤 다시 저장하십시오.")
        cur.execute("DELETE FROM apartment_vendors WHERE apartment_id = %s", (apartment_id,))
        for a in body.assignments:
            if a.mode == "default":
                continue
            cur.execute(
                "INSERT INTO apartment_vendors (apartment_id, category_id, vendor_id, updated_at) VALUES (%s, %s, %s, %s)",
                (apartment_id, a.category_id, a.vendor_id if a.mode == "vendor" else None, now),
            )
    live.bump("apartment")
    return apartment_vendors(apartment_id, settings)



# ---- 처음 받은 건물별 담당 업체 표 (서버 시작 때 한 번) ----------------------------------------------------

VENDOR_SEED_FILE = Path(__file__).parent / "data" / "building_vendors.tsv"
VENDOR_SEED_VERSION = "2"  # 2: 빈칸도 '보내지 않음'으로 (1 을 적용한 서버는 표 전체를 다시 적용)
# 업체가 아니라 '보내지 않음'으로 둘 값
NO_VENDOR = {"없음", "자체처리", "입주민", "입주민 4명", "입주민지인", "입주민(자격증소유)", "기존업체", "0", "-"}
# 같은 업체의 다른 표기
VENDOR_ALIASES = {
    "티케이": "TK", "OTIS": "오티스", "GYG(금영)": "GYG", "새성파킹시스템": "새성파킹", "세한이앤씨": "세한이엔씨",
    "강남크린, 이미자": "강남크린", "유원 -> 교체공사중 26.10": "유원",
}


def vendor_cell(value: str) -> str:
    """표의 칸 → 업체 이름, 또는 '' (보내지 않음: 빈칸 포함)"""
    v = re.sub(r"\s+", " ", value or "").strip()
    if not v or v in NO_VENDOR or re.search(r"-\s*중단$", v):
        return ""
    return VENDOR_ALIASES.get(v, v)[:100]


def seed_building_vendors(settings) -> dict[str, Any] | None:
    if not VENDOR_SEED_FILE.exists():
        return None
    now = _now(settings)
    lines = [ln.split("\t") for ln in VENDOR_SEED_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    header, rows = lines[0], lines[1:]
    cat_names = header[2:]
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM app_settings WHERE name = 'building_vendors_seed'")
        row = cur.fetchone()
        if row and row["value"] == VENDOR_SEED_VERSION:
            return None
        from .categories import all_categories

        cats = {r["name"]: r["id"] for r in all_categories(cur)}  # 새 민원 카테고리 묶음이 먼저 적용되게
        if any(c not in cats for c in cat_names):
            raise RuntimeError(f"민원 카테고리가 표와 다릅니다: {[c for c in cat_names if c not in cats]}")
        cur.execute("SELECT id, name FROM apartments WHERE listed = 1")
        buildings: dict[str, list[int]] = {}
        for r in cur.fetchall():
            buildings.setdefault(r["name"], []).append(r["id"])
        cur.execute("SELECT id, name FROM vendors ORDER BY id")
        vendors: dict[str, int] = {}
        for r in cur.fetchall():
            vendors.setdefault(r["name"], r["id"])
        created = assigned = none = 0
        missing: set[str] = set()
        for cells in rows:
            name = _clean(cells[0])
            ids = buildings.get(name)
            if not ids:
                missing.add(name)
                continue
            for cat_name, raw in zip(cat_names, cells[2:]):
                value, cat_id = vendor_cell(raw), cats.get(cat_name)
                if value is None or cat_id is None:
                    continue
                vendor_id = None
                if value:
                    vendor_id = vendors.get(value)
                    if vendor_id is None:
                        cur.execute(
                            """INSERT INTO vendors (name, biz_no, phone, address, room_name, created_at, updated_at)
                               VALUES (%s, '', '', '', NULL, %s, %s)""",
                            (value, now, now),
                        )
                        vendor_id = vendors[value] = cur.lastrowid
                        created += 1
                for apt_id in ids:
                    cur.execute("DELETE FROM apartment_vendors WHERE apartment_id = %s AND category_id = %s", (apt_id, cat_id))
                    cur.execute(
                        "INSERT INTO apartment_vendors (apartment_id, category_id, vendor_id, updated_at) VALUES (%s, %s, %s, %s)",
                        (apt_id, cat_id, vendor_id, now),
                    )
                    if vendor_id is None:
                        none += 1
                    else:
                        assigned += 1
        if len(missing) == len({_clean(c[0]) for c in rows}):
            raise RuntimeError("건물 목록이 아직 등록되지 않아 담당 업체를 넣지 못했습니다(다음 시작 때 다시 시도).")
        cur.execute("DELETE FROM app_settings WHERE name = 'building_vendors_seed'")
        cur.execute("INSERT INTO app_settings (name, value) VALUES ('building_vendors_seed', %s)", (VENDOR_SEED_VERSION,))
    live.bump("apartment")
    live.bump("vendor")
    return {"vendors_created": created, "assigned": assigned, "none": none, "missing": sorted(missing)}
