"""민원처리업체: 업체명 · 사업자등록번호 · 사업자등록증(이미지/PDF) · 전화번호 · 주소 · 담당 카톡 대화방.

    GET    /api/admin/vendors              목록
    POST   /api/admin/vendors              등록 (multipart)
    PUT    /api/admin/vendors/{id}         수정 (multipart, remove_doc=true 면 등록증 삭제)
    DELETE /api/admin/vendors/{id}         삭제
    GET    /api/admin/vendors/{id}/doc     사업자등록증 보기
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

from . import db, live
from .auth import SettingsDep, require_admin
from .config import Settings

router = APIRouter(dependencies=[Depends(require_admin)])

IMAGE_TYPES = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp")}
BIZ_NO = re.compile(r"^\d{3}-?\d{2}-?\d{5}$")
PHONE = re.compile(r"^[0-9+\-() ]{0,20}$")
COLUMNS = "id, name, biz_no, phone, address, room_name, doc_mime, doc_name, created_at, updated_at"


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


def _row(r: dict[str, Any]) -> dict[str, Any]:
    out = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()}
    out["has_doc"] = bool(out.pop("doc_mime", None))
    return out


def _now(settings: Settings) -> datetime:
    return datetime.now(settings.timezone).replace(tzinfo=None)


@router.get("/api/admin/vendors")
def list_vendors(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {COLUMNS} FROM vendors ORDER BY name, id")
        items = [_row(r) for r in cur.fetchall()]
    return {"ok": True, "items": items}


@router.post("/api/admin/vendors", status_code=201)
async def create_vendor(
    settings: SettingsDep,
    name: Annotated[str, Form(max_length=100)],
    biz_no: Annotated[str, Form(max_length=20)] = "",
    phone: Annotated[str, Form(max_length=20)] = "",
    address: Annotated[str, Form(max_length=300)] = "",
    room_name: Annotated[str, Form(max_length=255)] = "",
    doc: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    values = _clean(name, biz_no, phone, address, room_name)
    stored = _store_doc(settings, doc, await doc.read()) if doc is not None and doc.filename else (None, None, None)
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO vendors (name, biz_no, phone, address, room_name, doc_path, doc_mime, doc_name, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (*values.values(), *stored, now, now),
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
            "UPDATE vendors SET name = %s, biz_no = %s, phone = %s, address = %s, room_name = %s, updated_at = %s WHERE id = %s",
            (*values.values(), _now(settings), vendor_id),
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
