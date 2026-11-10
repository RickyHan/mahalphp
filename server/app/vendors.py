"""민원처리업체: 업체명 · 사업자등록번호 · 사업자등록증(이미지/PDF) · 전화번호 · 주소 · 담당 카톡 대화방 · 담당 민원 카테고리.

    GET    /api/admin/vendors              목록
    POST   /api/admin/vendors              등록 (multipart)
    PUT    /api/admin/vendors/{id}         수정 (multipart, remove_doc=true 면 등록증 삭제)
    DELETE /api/admin/vendors/{id}         삭제
    GET    /api/admin/vendors/{id}/doc     사업자등록증 보기
    POST   /api/admin/vendors/sync         다시 수집하기: 꽉 찬 동그라미(●)로 시작하는 채팅방을 업체로 추가
    PATCH  /api/admin/vendors/{id}/name    업체 이름만 바로 수정
    GET    /api/admin/vendor-dispatch      업체 카톡방 전달 이력
    POST   /api/admin/vendor-dispatch/{id}/retry   실패·만료된 전달 다시 보내기
"""

from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from . import db, live, store
from .auth import SettingsDep, require_admin
from .config import Settings

router = APIRouter(dependencies=[Depends(require_admin)])

IMAGE_TYPES = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp")}
BIZ_NO = re.compile(r"^\d{3}-?\d{2}-?\d{5}$")
PHONE = re.compile(r"^[0-9+\-() ]{0,20}$")
COLUMNS = ("v.id, v.name, v.biz_no, v.phone, v.address, v.room_name, v.category_id, c.name AS category_name, "
           "v.doc_mime, v.doc_name, v.created_at, v.updated_at")
FROM = "vendors v LEFT JOIN complaint_categories c ON c.id = v.category_id"

# 꽉 찬 동그라미 (● ⚫ ⬤ ⏺ 🔴🟠🟡🟢🔵🟣🟤). 빈 동그라미(○ ◯ ⚪)는 해당 없음. 이모지 변형 선택자(U+FE0F)는 무시
CIRCLES = "●⚫⬤⏺🔴🟠🟡🟢🔵🟣🟤"
_CIRCLE = f"[{re.escape(CIRCLES)}]\ufe0f?"
_CIRCLE_PREFIX = re.compile(rf"^\s*{_CIRCLE}")
_ALL_CIRCLES = re.compile(_CIRCLE)


def is_vendor_room(room: str) -> bool:
    """꽉 찬 동그라미로 시작하는 채팅방 = 민원처리업체 방"""
    return bool(_CIRCLE_PREFIX.match(room or ""))


def vendor_name_from_room(room: str) -> str:
    return re.sub(r"\s+", " ", _ALL_CIRCLES.sub("", room)).strip()


# ---- 업체 톡방 ↔ 등록된 업체 이름 유사도 (다시 수집하기 때 자동 연결) ------------------------------------

_ROOM_TAIL = ("방", "단톡", "톡", "업체", "협력사", "협력", "공지", "소통", "대화", "채팅", "님")
_COMPANY = re.compile(r"\(주\)|㈜|주식회사|\(유\)|유한회사")
VENDOR_MIN_SCORE = 0.8
VENDOR_GAP = 0.04


def _vkey(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKC", _COMPANY.sub("", text or "")).lower()
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def _room_keys(room: str) -> set[str]:
    from .apartments import _strip_end

    base = vendor_name_from_room(room)
    keys = set()
    for variant in (base, re.sub(r"\([^)]*\)", " ", base)):
        k = _vkey(variant)
        for kk in (k, _strip_end(k, _ROOM_TAIL)):
            if kk:
                keys.add(kk)
    return keys


def vendor_similarity(room: str, name: str) -> float:
    """1 = 같음. 톡방 이름이 업체 이름을 품으면(예: ●성도엘리베이터 ⊃ 성도) 0.85 이상, 그 밖에는 글자 배열 유사도"""
    from difflib import SequenceMatcher

    best = 0.0
    for v in {_vkey(name), _vkey(re.sub(r"\([^)]*\)", " ", name))} - {""}:
        for r in _room_keys(room):
            if r == v:
                return 1.0
            if len(v) >= 2 and v in r:
                best = max(best, 0.85 + 0.1 * len(v) / len(r))
            elif len(r) >= 3 and r in v:
                best = max(best, 0.82 + 0.1 * len(r) / len(v))
            else:
                best = max(best, SequenceMatcher(None, r, v).ratio())
    return best


def match_vendor_rooms(rooms: list[str], vendors: list[dict[str, Any]]) -> tuple[list[tuple[str, int, float]], list[str]]:
    """톡방 ↔ 톡방 없는 업체 1:1. (확실한 짝, 후보가 비슷하게 여럿이라 남긴 방)"""
    firm: dict[str, tuple[float, int]] = {}
    ambiguous: list[str] = []
    for room in rooms:
        cands = sorted(((vendor_similarity(room, v["name"]), v["id"]) for v in vendors), reverse=True)
        if not cands or cands[0][0] < VENDOR_MIN_SCORE:
            continue
        if len(cands) > 1 and cands[0][0] - cands[1][0] < VENDOR_GAP:
            ambiguous.append(room)
            continue
        firm[room] = cands[0]
    by_vendor: dict[int, list[tuple[float, str]]] = {}
    for room, (score, vid) in firm.items():
        by_vendor.setdefault(vid, []).append((score, room))
    pairs = []
    for vid, lst in by_vendor.items():
        lst.sort(reverse=True)
        if len(lst) > 1 and lst[0][0] - lst[1][0] < VENDOR_GAP:
            ambiguous.extend(r for _, r in lst)
            continue
        pairs.append((lst[0][1], vid, lst[0][0]))
    return sorted(pairs), sorted(ambiguous)


def docs_root(settings: Settings) -> Path:
    return settings.media_root.parent / "vendor-docs"


def _clean(name: str, biz_no: str, phone: str, address: str, room_name: str) -> dict[str, Any]:
    name, biz_no, phone, address, room_name = (v.strip() for v in (name, biz_no, phone, address, room_name))
    if not name:
        raise HTTPException(422, "업체명을 입력하십시오.")
    if biz_no:
        if not BIZ_NO.match(biz_no):
            raise HTTPException(422, "사업자등록번호는 10자리 숫자입니다 (예: 123-45-67890).")
        digits = biz_no.replace("-", "")
        biz_no = f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    if not PHONE.match(phone):
        raise HTTPException(422, "전화번호는 숫자와 - 만 입력하십시오.")
    return {"name": name, "biz_no": biz_no, "phone": phone, "address": address, "room_name": room_name or None}


def _store_doc(settings: Settings, upload: UploadFile, data: bytes) -> tuple[str, str, str]:
    """사업자등록증 파일 검증·저장 → (상대 경로, MIME, 원래 파일명)"""
    if not data or len(data) > settings.max_image_bytes:
        raise HTTPException(413, f"사업자등록증 파일은 {settings.max_image_bytes // (1024 * 1024)}MB 이하여야 합니다.")
    if data[:5] == b"%PDF-":
        mime, ext = "application/pdf", "pdf"
    else:
        try:
            with Image.open(io.BytesIO(data)) as img:
                fmt = img.format
                img.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise HTTPException(415, "사업자등록증은 이미지(JPG·PNG·WEBP) 또는 PDF 파일이어야 합니다.") from exc
        if fmt not in IMAGE_TYPES:
            raise HTTPException(415, "사업자등록증은 이미지(JPG·PNG·WEBP) 또는 PDF 파일이어야 합니다.")
        mime, ext = IMAGE_TYPES[fmt]
    relative = f"{hashlib.sha256(data).hexdigest()}.{ext}"
    target = docs_root(settings) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        tmp = target.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(target)
    original = Path(upload.filename or f"등록증.{ext}").name[:200]
    return relative, mime, original


def _categories(cur, raw: str) -> list[int]:
    """선택한 민원 카테고리 id 목록 ('1,3,5' 형식, 빈 값이면 지정 안 함)"""
    ids: list[int] = []
    for part in (raw or "").replace(" ", "").split(","):
        if not part:
            continue
        if not part.isdigit():
            raise HTTPException(422, "민원 카테고리가 올바르지 않습니다.")
        if int(part) not in ids:
            ids.append(int(part))
    if ids:
        cur.execute(f"SELECT id FROM complaint_categories WHERE id IN ({', '.join(['%s'] * len(ids))})", ids)
        if len(cur.fetchall()) != len(ids):
            raise HTTPException(422, "없는 민원 카테고리입니다. 화면을 새로 고친 뒤 다시 선택하십시오.")
    return ids


def set_vendor_categories(cur, vendor_id: int, ids: list[int]) -> None:
    """업체의 담당 분류를 바꾼다 (vendors.category_id 는 첫 번째 분류로 맞춰 둠 — 예전 화면·호환용)"""
    cur.execute("DELETE FROM vendor_categories WHERE vendor_id = %s", (vendor_id,))
    for cid in ids:
        cur.execute("INSERT INTO vendor_categories (vendor_id, category_id) VALUES (%s, %s)", (vendor_id, cid))
    cur.execute("UPDATE vendors SET category_id = %s WHERE id = %s", (ids[0] if ids else None, vendor_id))


def backfill_vendor_categories(cur) -> int:
    """예전 단일 담당 분류(vendors.category_id)를 vendor_categories 로 옮긴다 (없는 것만)"""
    cur.execute(
        """SELECT v.id, v.category_id FROM vendors v
           WHERE v.category_id IS NOT NULL AND NOT EXISTS
             (SELECT 1 FROM vendor_categories vc WHERE vc.vendor_id = v.id AND vc.category_id = v.category_id)"""
    )
    rows = cur.fetchall()
    for r in rows:
        cur.execute("INSERT INTO vendor_categories (vendor_id, category_id) VALUES (%s, %s)", (r["id"], r["category_id"]))
    return len(rows)


def _row(r: dict[str, Any]) -> dict[str, Any]:
    out = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()}
    out["has_doc"] = bool(out.pop("doc_mime", None))
    return out


def _now(settings: Settings) -> datetime:
    return datetime.now(settings.timezone).replace(tzinfo=None)


@router.get("/api/admin/vendors")
def list_vendors(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        backfill_vendor_categories(cur)
        cur.execute(f"SELECT {COLUMNS} FROM {FROM} ORDER BY v.name, v.id")
        items = [_row(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT vc.vendor_id, c.id, c.name FROM vendor_categories vc JOIN complaint_categories c ON c.id = vc.category_id
               ORDER BY c.sort_order, c.id"""
        )
        cats: dict[int, list[dict[str, Any]]] = {}
        for r in cur.fetchall():
            cats.setdefault(r["vendor_id"], []).append({"id": r["id"], "name": r["name"]})
        # 연결된 톡방의 메시지 수·최근 대화
        cur.execute("SELECT room_name, COUNT(*) AS n, MAX(posted_at) AS last_at FROM kakao_messages GROUP BY room_name")
        rooms = {r["room_name"]: r for r in cur.fetchall()}
        cur.execute("SELECT value FROM app_settings WHERE name = 'vendors_synced_at'")
        synced = cur.fetchone()
        readiness = store.reply_readiness(cur, _now(settings))
        from .vendor_contacts import contacts_by_vendor

        contacts = contacts_by_vendor(cur)
    for v in items:
        v["reply_ready"] = store.ready_state(readiness, v["room_name"])
        v["contacts"] = contacts.get(v["id"]) or ([{"phone": v["phone"], "device": "", "region": ""}] if v["phone"] else [])
        vc = cats.get(v["id"], [])
        v["category_ids"] = [c["id"] for c in vc]
        v["category_names"] = [c["name"] for c in vc]
        v["category_id"] = vc[0]["id"] if vc else None          # 예전 화면 호환
        v["category_name"] = vc[0]["name"] if vc else None
        r = rooms.get(v["room_name"]) if v["room_name"] else None
        v["messages"] = int(r["n"]) if r else 0
        last = r["last_at"] if r else None
        v["last_at"] = last.isoformat() if isinstance(last, datetime) else last
    return {
        "ok": True, "items": items, "synced_at": synced["value"] if synced else None,
        "reply_check": {"checked": readiness["checked"], "reported_at": readiness["reported_at"]},
    }


@router.post("/api/admin/vendors/sync")
def sync_vendors(settings: SettingsDep) -> dict[str, Any]:
    """꽉 찬 동그라미로 시작하는 방 중 아직 어느 업체에도 연결되지 않은 방을 정리한다.
    톡방 없이 등록된 업체 중 이름이 같거나 확실히 비슷한 업체가 있으면 그 업체에 방을 연결하고(예: ●성도엘리베이터 → 성도),
    비슷한 업체가 없으면 새 업체로 추가한다. 후보가 비슷하게 여럿이면 연결도 추가도 하지 않고 남긴다(직접 연결). 고친 업체 이름은 유지."""
    now = _now(settings)
    added: list[dict[str, str]] = []
    linked: list[dict[str, str]] = []
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT room_name FROM kakao_messages")
        rooms = [r["room_name"] for r in cur.fetchall()]
        cur.execute("SELECT id, name, room_name FROM vendors")
        vendors = cur.fetchall()
        used = {v["room_name"] for v in vendors if v["room_name"]}
        roomless = [v for v in vendors if not v["room_name"]]
        matched = sorted(r for r in rooms if is_vendor_room(r))
        free = [r for r in matched if r not in used]
        pairs, ambiguous = match_vendor_rooms(free, roomless)
        names = {v["id"]: v["name"] for v in roomless}
        for room, vid, score in pairs:
            cur.execute("UPDATE vendors SET room_name = %s, updated_at = %s WHERE id = %s", (room, now, vid))
            linked.append({"name": names[vid], "room_name": room, "exact": score >= 1.0})
            used.add(room)
        skip = set(ambiguous)
        for room in free:
            if room in used or room in skip:
                continue
            name = vendor_name_from_room(room) or room
            cur.execute(
                """INSERT INTO vendors (name, biz_no, phone, address, room_name, created_at, updated_at)
                   VALUES (%s, '', '', '', %s, %s, %s)""",
                (name[:100], room, now, now),
            )
            added.append({"name": name, "room_name": room})
            used.add(room)
        cur.execute("DELETE FROM app_settings WHERE name = 'vendors_synced_at'")
        cur.execute("INSERT INTO app_settings (name, value) VALUES ('vendors_synced_at', %s)", (now.isoformat(timespec="seconds"),))
    live.bump("vendor")
    return {"ok": True, "added": added, "linked": linked, "ambiguous": ambiguous, "matched": len(matched), "rooms": len(rooms)}


class NameBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class RoomBody(BaseModel):
    room_name: str = Field(min_length=1, max_length=255)


@router.get("/api/admin/vendors/unlinked-rooms")
def unlinked_rooms(settings: SettingsDep) -> dict[str, Any]:
    """● 로 시작하는데 어느 업체에도 연결되지 않은 톡방 + 연결 후보(톡방 없는 업체, 비슷한 순)"""
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT room_name, COUNT(*) AS n, MAX(posted_at) AS last_at FROM kakao_messages GROUP BY room_name")
        stats = {r["room_name"]: r for r in cur.fetchall()}
        cur.execute("SELECT id, name, room_name FROM vendors ORDER BY name, id")
        vendors = cur.fetchall()
    used = {v["room_name"] for v in vendors if v["room_name"]}
    roomless = [v for v in vendors if not v["room_name"]]
    items = []
    for room in sorted(r for r in stats if is_vendor_room(r) and r not in used):
        st = stats[room]
        cands = sorted(((vendor_similarity(room, v["name"]), v) for v in roomless), key=lambda x: (-x[0], x[1]["name"]))
        items.append({
            "room_name": room,
            "messages": int(st["n"]),
            "last_at": st["last_at"].isoformat() if isinstance(st["last_at"], datetime) else st["last_at"],
            "suggested": [{"id": v["id"], "name": v["name"]} for score, v in cands[:5] if score >= 0.5],
        })
    return {"ok": True, "items": items, "vendors": [{"id": v["id"], "name": v["name"]} for v in roomless]}


@router.post("/api/admin/vendors/{vendor_id}/room")
def link_room(vendor_id: int, body: RoomBody, settings: SettingsDep) -> dict[str, Any]:
    """업체에 톡방 연결 (연결 안 된 업체 톡방 목록에서)"""
    room = body.room_name.strip()
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM vendors WHERE id = %s", (vendor_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "업체가 없습니다.")
        cur.execute("SELECT name FROM vendors WHERE room_name = %s AND id <> %s", (room, vendor_id))
        other = cur.fetchone()
        if other:
            raise HTTPException(409, f"이 톡방은 이미 '{other['name']}' 에 연결되어 있습니다.")
        cur.execute("SELECT 1 FROM kakao_messages WHERE room_name = %s LIMIT 1", (room,))
        if cur.fetchone() is None:
            raise HTTPException(422, "수집된 채팅방이 아닙니다.")
        cur.execute("UPDATE vendors SET room_name = %s, updated_at = %s WHERE id = %s", (room, _now(settings), vendor_id))
    live.bump("vendor")
    return {"ok": True}


@router.delete("/api/admin/vendors/{vendor_id}/room")
def unlink_room(vendor_id: int, settings: SettingsDep) -> dict[str, Any]:
    """업체의 처리 요청 톡방 연결 해제"""
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM vendors WHERE id = %s", (vendor_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "업체가 없습니다.")
        cur.execute("UPDATE vendors SET room_name = NULL, updated_at = %s WHERE id = %s", (_now(settings), vendor_id))
    live.bump("vendor")
    return {"ok": True}


@router.post("/api/admin/vendors/from-room", status_code=201)
def vendor_from_room(body: RoomBody, settings: SettingsDep) -> dict[str, Any]:
    """연결 안 된 톡방을 새 업체로 추가 (업체 이름 = 방 이름에서 ● 를 뺀 것)"""
    room = body.room_name.strip()
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT name FROM vendors WHERE room_name = %s", (room,))
        other = cur.fetchone()
        if other:
            raise HTTPException(409, f"이 톡방은 이미 '{other['name']}' 에 연결되어 있습니다.")
        cur.execute("SELECT 1 FROM kakao_messages WHERE room_name = %s LIMIT 1", (room,))
        if cur.fetchone() is None:
            raise HTTPException(422, "수집된 채팅방이 아닙니다.")
        name = (vendor_name_from_room(room) or room)[:100]
        cur.execute(
            """INSERT INTO vendors (name, biz_no, phone, address, room_name, created_at, updated_at)
               VALUES (%s, '', '', '', %s, %s, %s)""",
            (name, room, now, now),
        )
        new_id = cur.lastrowid
    live.bump("vendor")
    return {"ok": True, "id": new_id, "name": name}


@router.patch("/api/admin/vendors/{vendor_id}/name")
def rename_vendor(vendor_id: int, body: NameBody, settings: SettingsDep) -> dict[str, Any]:
    name = re.sub(r"\s+", " ", body.name).strip()
    if not name:
        raise HTTPException(422, "업체 이름을 입력하십시오.")
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("UPDATE vendors SET name = %s, updated_at = %s WHERE id = %s", (name, _now(settings), vendor_id))
        cur.execute("SELECT id FROM vendors WHERE id = %s", (vendor_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "업체가 없습니다.")
    live.bump("vendor")
    return {"ok": True, "name": name}


@router.post("/api/admin/vendors", status_code=201)
async def create_vendor(
    settings: SettingsDep,
    name: Annotated[str, Form(max_length=100)],
    biz_no: Annotated[str, Form(max_length=20)] = "",
    phone: Annotated[str, Form(max_length=20)] = "",
    address: Annotated[str, Form(max_length=300)] = "",
    room_name: Annotated[str, Form(max_length=255)] = "",
    category_id: Annotated[str, Form(max_length=20)] = "",
    category_ids: Annotated[str | None, Form(max_length=1000)] = None,
    doc: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    values = _clean(name, biz_no, phone, address, room_name)
    stored = _store_doc(settings, doc, await doc.read()) if doc is not None and doc.filename else (None, None, None)
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        ids = _categories(cur, category_ids if category_ids is not None else category_id)
        cur.execute(
            """INSERT INTO vendors (name, biz_no, phone, address, room_name, category_id, doc_path, doc_mime, doc_name, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s)""",
            (*values.values(), *stored, now, now),
        )
        vendor_id = cur.lastrowid
        set_vendor_categories(cur, vendor_id, ids)
    live.bump("vendor")
    return {"ok": True, "id": vendor_id}


@router.put("/api/admin/vendors/{vendor_id}")
async def update_vendor(
    vendor_id: int,
    settings: SettingsDep,
    name: Annotated[str, Form(max_length=100)],
    biz_no: Annotated[str, Form(max_length=20)] = "",
    phone: Annotated[str, Form(max_length=20)] = "",
    address: Annotated[str, Form(max_length=300)] = "",
    room_name: Annotated[str, Form(max_length=255)] = "",
    category_id: Annotated[str, Form(max_length=20)] = "",
    category_ids: Annotated[str | None, Form(max_length=1000)] = None,
    remove_doc: Annotated[bool, Form()] = False,
    doc: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    values = _clean(name, biz_no, phone, address, room_name)
    stored = _store_doc(settings, doc, await doc.read()) if doc is not None and doc.filename else None
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM vendors WHERE id = %s", (vendor_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "업체가 없습니다.")
        ids = _categories(cur, category_ids if category_ids is not None else category_id)
        cur.execute(
            "UPDATE vendors SET name = %s, biz_no = %s, phone = %s, address = %s, room_name = %s, updated_at = %s WHERE id = %s",
            (*values.values(), _now(settings), vendor_id),
        )
        set_vendor_categories(cur, vendor_id, ids)
        # 대표 번호를 상세에서 바꾸면 담당자 연락처의 첫 번호도 같이 (연락처 목록이 있을 때)
        cur.execute("SELECT id, phone FROM vendor_contacts WHERE vendor_id = %s ORDER BY sort_order, id LIMIT 1", (vendor_id,))
        first = cur.fetchone()
        if first and values["phone"] and first["phone"] != values["phone"]:
            cur.execute("UPDATE vendor_contacts SET phone = %s WHERE id = %s", (values["phone"], first["id"]))
        if stored is not None or remove_doc:
            cur.execute(
                "UPDATE vendors SET doc_path = %s, doc_mime = %s, doc_name = %s WHERE id = %s",
                (*(stored or (None, None, None)), vendor_id),
            )
    live.bump("vendor")
    return {"ok": True, "id": vendor_id}


@router.delete("/api/admin/vendors/{vendor_id}")
def delete_vendor(vendor_id: int, settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM apartment_vendors WHERE vendor_id = %s", (vendor_id,))
        cur.execute("DELETE FROM vendor_categories WHERE vendor_id = %s", (vendor_id,))
        cur.execute("DELETE FROM vendor_contacts WHERE vendor_id = %s", (vendor_id,))
        cur.execute("DELETE FROM vendors WHERE id = %s", (vendor_id,))
    live.bump("vendor")
    return {"ok": True}


@router.get("/api/admin/vendors/{vendor_id}/doc")
def vendor_doc(vendor_id: int, settings: SettingsDep) -> FileResponse:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT doc_path, doc_mime, doc_name FROM vendors WHERE id = %s", (vendor_id,))
        row = cur.fetchone()
    if not row or not row["doc_path"]:
        raise HTTPException(404, "등록된 사업자등록증이 없습니다.")
    root = docs_root(settings).resolve()
    path = (root / row["doc_path"]).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "파일이 없습니다.")
    return FileResponse(
        path,
        media_type=row["doc_mime"],
        filename=row["doc_name"],
        content_disposition_type="inline",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            # 이미지는 샌드박스로 격리 (PDF 는 브라우저 PDF 보기가 샌드박스에서 막히므로 제외)
            **({} if row["doc_mime"] == "application/pdf" else {"Content-Security-Policy": "sandbox"}),
        },
    )


# ---- 업체 카톡방 전달 이력 ------------------------------------------------------------------

@router.get("/api/admin/vendor-dispatch")
def dispatch_history(settings: SettingsDep, limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT q.id, q.message_id, q.vendor_id, q.vendor_name, q.room_name AS vendor_room, q.text, q.status, q.note,
                      q.created_at, q.done_at, m.room_name AS complaint_room, c.category
               FROM reply_queue q
               LEFT JOIN kakao_messages m ON m.id = q.message_id
               LEFT JOIN complaint_check c ON c.message_id = q.message_id
               WHERE q.kind = 'vendor' ORDER BY q.id DESC LIMIT %s""",
            (limit,),
        )
        items = [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()} for r in cur.fetchall()]
        cur.execute("SELECT status, COUNT(*) AS n FROM reply_queue WHERE kind = 'vendor' GROUP BY status")
        counts = {r["status"]: int(r["n"]) for r in cur.fetchall()}
        readiness = store.reply_readiness(cur, _now(settings))
    for d in items:
        d["reply_ready"] = store.ready_state(readiness, d["vendor_room"]) if d["status"] == "pending" else None
    return {"ok": True, "items": items, "counts": counts}


@router.post("/api/admin/vendor-dispatch/{dispatch_id}/manual")
def dispatch_manual(dispatch_id: int, settings: SettingsDep) -> dict[str, Any]:
    """문구를 복사해 직접 보냈음 → 보냄으로 처리 (휴대폰이 나중에 또 보내지 않도록)"""
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM reply_queue WHERE id = %s AND kind = 'vendor'", (dispatch_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "전달 기록이 없습니다.")
        if row["status"] == "sent":
            raise HTTPException(422, "이미 보낸 요청입니다.")
        cur.execute(
            "UPDATE reply_queue SET status = 'sent', note = '관리자가 직접 보냄', done_at = %s WHERE id = %s",
            (_now(settings), dispatch_id),
        )
    live.bump("vendor")
    live.bump("complaint")
    return {"ok": True}


@router.post("/api/admin/vendor-dispatch/{dispatch_id}/retry")
def dispatch_retry(dispatch_id: int, settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM reply_queue WHERE id = %s AND kind = 'vendor'", (dispatch_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "전달 기록이 없습니다.")
        if row["status"] not in ("failed", "expired"):
            raise HTTPException(422, "실패하거나 만료된 전달만 다시 보낼 수 있습니다.")
        cur.execute(
            "UPDATE reply_queue SET status = 'pending', note = '다시 보내기', created_at = %s, done_at = NULL WHERE id = %s",
            (_now(settings), dispatch_id),
        )
    live.bump("vendor")
    return {"ok": True}

