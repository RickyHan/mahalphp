"""아파트 관리: 별(★ 등) 하나로 시작하는 카톡 채팅방을 아파트로 모아 이름을 관리한다.

    GET    /api/admin/apartments            목록 (원래 채팅방 이름 · 메시지·민원 수 · 최근 대화)
    POST   /api/admin/apartments/sync       다시 수집하기: 새로 생긴 '별 하나로 시작하는' 방을 추가 (수정한 이름은 유지)
    PUT    /api/admin/apartments/{id}       아파트 이름 수정
    DELETE /api/admin/apartments/{id}       목록에서 삭제 (다시 수집하면 다시 추가됨)
    GET    /api/admin/apartments/{id}/vendors   민원 분류별 담당 업체 설정 보기
    PUT    /api/admin/apartments/{id}/vendors   민원 분류별 담당 업체 저장
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import db, live
from .auth import SettingsDep, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

# 별 모양 문자 (★ ☆ ⭐ ✩ ✪ ✫ ✬ ✭ ✮ ✯ ✰ 🌟 *). 이모지 변형 선택자(U+FE0F)는 무시
STARS = "★☆⭐✩✪✫✬✭✮✯✰🌟*"
_STAR = f"[{re.escape(STARS)}]️?"
_ONE_STAR_PREFIX = re.compile(rf"^\s*{_STAR}(?!\s*{_STAR})")
_ALL_STARS = re.compile(_STAR)


def is_apartment_room(room: str) -> bool:
    """별 '하나'로 시작하는 방 이름인지 (별 두 개 이상으로 시작하면 제외)"""
    return bool(_ONE_STAR_PREFIX.match(room or ""))


def apartment_name(room: str) -> str:
    """아파트 이름: 별을 모두 빼고 앞뒤 공백 정리"""
    return re.sub(r"\s+", " ", _ALL_STARS.sub("", room)).strip()


def _now(settings) -> datetime:
    return datetime.now(settings.timezone).replace(tzinfo=None)


def _ser(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


class ApartmentBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.get("/api/admin/apartments")
def list_apartments(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT a.id, a.name, a.room_name, a.created_at, a.updated_at,
                      COUNT(m.id) AS messages, MAX(m.posted_at) AS last_at,
                      COALESCE(SUM(CASE WHEN c.verdict = 1 THEN 1 ELSE 0 END), 0) AS complaints
               FROM apartments a
               LEFT JOIN kakao_messages m ON m.room_name = a.room_name
               LEFT JOIN complaint_check c ON c.message_id = m.id
               GROUP BY a.id, a.name, a.room_name, a.created_at, a.updated_at
               ORDER BY a.name, a.id"""
        )
        items = [_ser(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT apartment_id, SUM(CASE WHEN vendor_id IS NOT NULL THEN 1 ELSE 0 END) AS assigned, COUNT(*) AS custom
               FROM apartment_vendors GROUP BY apartment_id"""
        )
        custom = {r["apartment_id"]: r for r in cur.fetchall()}
        cur.execute("SELECT value FROM app_settings WHERE name = 'apartments_synced_at'")
        row = cur.fetchone()
    for i in items:
        c = custom.get(i["id"])
        i["assigned"] = int(c["assigned"] or 0) if c else 0   # 이 아파트에 따로 지정한 업체 수
        i["custom"] = int(c["custom"] or 0) if c else 0       # 따로 정한 분류 수('보내지 않음' 포함)
        i["messages"] = int(i["messages"] or 0)
        i["complaints"] = int(i["complaints"] or 0)
    return {"ok": True, "items": items, "synced_at": row["value"] if row else None}


@router.post("/api/admin/apartments/sync")
def sync_apartments(settings: SettingsDep) -> dict[str, Any]:
    now = _now(settings)
    added: list[dict[str, str]] = []
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT room_name FROM kakao_messages")
        rooms = [r["room_name"] for r in cur.fetchall()]
        cur.execute("SELECT room_name FROM apartments")
        known = {r["room_name"] for r in cur.fetchall()}
        matched = [r for r in rooms if is_apartment_room(r)]
        for room in sorted(matched):
            if room in known:
                continue
            name = apartment_name(room) or room
            try:
                cur.execute(
                    "INSERT INTO apartments (name, room_name, created_at, updated_at) VALUES (%s, %s, %s, %s)",
                    (name, room, now, now),
                )
            except db.IntegrityError as exc:  # 동시에 수집한 경우
                if not db.is_duplicate(exc):
                    raise
                continue
            added.append({"name": name, "room_name": room})
        cur.execute("DELETE FROM app_settings WHERE name = 'apartments_synced_at'")
        cur.execute("INSERT INTO app_settings (name, value) VALUES ('apartments_synced_at', %s)", (now.isoformat(timespec="seconds"),))
    live.bump("apartment")
    return {"ok": True, "added": added, "matched": len(matched), "rooms": len(rooms)}


@router.put("/api/admin/apartments/{apartment_id}")
def rename_apartment(apartment_id: int, body: ApartmentBody, settings: SettingsDep) -> dict[str, Any]:
    name = re.sub(r"\s+", " ", body.name).strip()
    if not name:
        raise HTTPException(422, "아파트 이름을 입력하십시오.")
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM apartments WHERE id = %s", (apartment_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "아파트가 없습니다.")
        cur.execute("UPDATE apartments SET name = %s, updated_at = %s WHERE id = %s", (name, _now(settings), apartment_id))
    live.bump("apartment")
    return {"ok": True, "name": name}


@router.delete("/api/admin/apartments/{apartment_id}")
def delete_apartment(apartment_id: int, settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM apartment_vendors WHERE apartment_id = %s", (apartment_id,))
        cur.execute("DELETE FROM apartments WHERE id = %s", (apartment_id,))
    live.bump("apartment")
    return {"ok": True}


# ---- 아파트별 민원 분류 담당 업체 ------------------------------------------------------------------

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
        raise HTTPException(404, "아파트가 없습니다.")
    return row


@router.get("/api/admin/apartments/{apartment_id}/vendors")
def apartment_vendors(apartment_id: int, settings: SettingsDep) -> dict[str, Any]:
    from .categories import all_categories

    with db.transaction(settings) as conn, conn.cursor() as cur:
        apt = _apartment(cur, apartment_id)
        cats = all_categories(cur)
        cur.execute("SELECT id, name, room_name, category_id FROM vendors ORDER BY name, id")
        vendors = cur.fetchall()
        cur.execute("SELECT category_id, vendor_id FROM apartment_vendors WHERE apartment_id = %s", (apartment_id,))
        current = {r["category_id"]: r["vendor_id"] for r in cur.fetchall()}
        cur.execute("SELECT DISTINCT room_name FROM kakao_messages")
        rooms = {r["room_name"] for r in cur.fetchall()}
    rows = []
    for c in cats:
        defaults = [v for v in vendors if v["category_id"] == c["id"]]
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
        "vendors": [{"id": v["id"], "name": v["name"], "room_name": v["room_name"], "has_messages": v["room_name"] in rooms} for v in vendors],
    }


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

