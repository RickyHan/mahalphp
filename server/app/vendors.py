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

from . import db, live
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


def _category(cur, raw: str) -> int | None:
    """선택한 민원 카테고리 id (빈 값이면 지정 안 함)"""
    if not raw.strip():
        return None
    if not raw.strip().isdigit():
        raise HTTPException(422, "민원 카테고리가 올바르지 않습니다.")
    cur.execute("SELECT id FROM complaint_categories WHERE id = %s", (int(raw),))
    if cur.fetchone() is None:
        raise HTTPException(422, "없는 민원 카테고리입니다. 화면을 새로 고친 뒤 다시 선택하십시오.")
    return int(raw)


def _row(r: dict[str, Any]) -> dict[str, Any]:
    out = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()}
    out["has_doc"] = bool(out.pop("doc_mime", None))
    return out


def _now(settings: Settings) -> datetime:
    return datetime.now(settings.timezone).replace(tzinfo=None)


@router.get("/api/admin/vendors")
def list_vendors(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {COLUMNS} FROM {FROM} ORDER BY v.name, v.id")
        items = [_row(r) for r in cur.fetchall()]
        # 연결된 톡방의 메시지 수·최근 대화
        cur.execute("SELECT room_name, COUNT(*) AS n, MAX(posted_at) AS last_at FROM kakao_messages GROUP BY room_name")
        rooms = {r["room_name"]: r for r in cur.fetchall()}
        cur.execute("SELECT value FROM app_settings WHERE name = 'vendors_synced_at'")
        synced = cur.fetchone()
    for v in items:
        r = rooms.get(v["room_name"]) if v["room_name"] else None
        v["messages"] = int(r["n"]) if r else 0
        last = r["last_at"] if r else None
        v["last_at"] = last.isoformat() if isinstance(last, datetime) else last
    return {"ok": True, "items": items, "synced_at": synced["value"] if synced else None}


@router.post("/api/admin/vendors/sync")
def sync_vendors(settings: SettingsDep) -> dict[str, Any]:
    """꽉 찬 동그라미로 시작하는 방 중 아직 어느 업체에도 연결되지 않은 방을 업체로 추가.
    같은 이름의 업체가 톡방 없이 등록돼 있으면 새로 만들지 않고 그 업체에 방을 연결한다. 고친 업체 이름은 유지."""
    now = _now(settings)
    added: list[dict[str, str]] = []
    linked: list[dict[str, str]] = []
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT room_name FROM kakao_messages")
        rooms = [r["room_name"] for r in cur.fetchall()]
        cur.execute("SELECT id, name, room_name FROM vendors")
        vendors = cur.fetchall()
        used = {v["room_name"] for v in vendors if v["room_name"]}
        roomless = {v["name"]: v["id"] for v in vendors if not v["room_name"]}
        matched = sorted(r for r in rooms if is_vendor_room(r))
        for room in matched:
            if room in used:
                continue
            name = vendor_name_from_room(room) or room
            if name in roomless:
                cur.execute("UPDATE vendors SET room_name = %s, updated_at = %s WHERE id = %s", (room, now, roomless.pop(name)))
                linked.append({"name": name, "room_name": room})
            else:
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
    return {"ok": True, "added": added, "linked": linked, "matched": len(matched), "rooms": len(rooms)}


class NameBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


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
    doc: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    values = _clean(name, biz_no, phone, address, room_name)
    stored = _store_doc(settings, doc, await doc.read()) if doc is not None and doc.filename else (None, None, None)
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO vendors (name, biz_no, phone, address, room_name, category_id, doc_path, doc_mime, doc_name, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (*values.values(), _category(cur, category_id), *stored, now, now),
        )
        vendor_id = cur.lastrowid
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
    remove_doc: Annotated[bool, Form()] = False,
    doc: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    values = _clean(name, biz_no, phone, address, room_name)
    stored = _store_doc(settings, doc, await doc.read()) if doc is not None and doc.filename else None
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM vendors WHERE id = %s", (vendor_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "업체가 없습니다.")
        cur.execute(
            "UPDATE vendors SET name = %s, biz_no = %s, phone = %s, address = %s, room_name = %s, category_id = %s, updated_at = %s WHERE id = %s",
            (*values.values(), _category(cur, category_id), _now(settings), vendor_id),
        )
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
    return {"ok": True, "items": items, "counts": counts}


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

