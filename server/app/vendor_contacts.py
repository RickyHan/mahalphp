"""업체 담당자 연락처 (한 업체에 여러 명) · 엑셀(업체 번호표)로 한꺼번에 등록

    POST /api/admin/vendors/contacts/import?apply=false   엑셀·CSV 올리기 → 미리보기(apply=false) / 반영(apply=true)
    PUT  /api/admin/vendors/{id}/contacts                 한 업체의 연락처 목록을 통째로 바꿈

엑셀 형식 (머리글 줄에 '업체'·'번호' 칸이 있으면 됨, '기종'·'지역'은 있으면 함께 저장):
    분류 | 업체 | 기종 | 번호 | 지역
    누수 | 수자원누수탐지 | 안드로이드 | 010-2538-4819 | 서울, 경기
         |               | 아이폰     | 010-...       |            ← 업체 칸이 비면 위 업체의 담당자
반영하면 파일에 있는 업체의 연락처는 파일 내용으로 바뀌고(파일에 없는 업체는 그대로), 대표 번호는 첫 번호가 된다.
업체 이름이 기존 업체와 같으면(공백·(주)·㈜·● 무시) 그 업체에, 없으면 새 업체로 추가한다(톡방·담당 카테고리는 비워 둠).
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from . import db, live
from .auth import SettingsDep, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

MAX_FILE = 2 * 1024 * 1024
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _now(settings) -> datetime:
    return datetime.now(settings.timezone).replace(tzinfo=None)


# ---- 값 정리 ---------------------------------------------------------------------------------------

def norm_phone(value: Any) -> str:
    """'010 9966 8071' · '01099668071' → '010-9966-8071' (휴대폰·서울 번호), 그 밖은 앞뒤 공백만 정리"""
    s = str(value or "").strip()
    digits = re.sub(r"\D", "", s)
    if len(digits) == 11 and digits.startswith("01"):
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    if len(digits) == 10 and digits.startswith("01"):
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return re.sub(r"\s+", " ", s)[:30]


def name_key(name: str | None) -> str:
    s = re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사", "", name or "")
    s = re.sub(r"^[●⚫⬤⏺🔴🟠🟡🟢🔵🟣🟤\s]+", "", s)
    return re.sub(r"\s+", "", s).lower()


# ---- 파일 읽기 (xlsx 는 표준 라이브러리로 첫 시트만) ------------------------------------------------------

def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(data: bytes) -> list[list[str]]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        first = wb.find("m:sheets/m:sheet", _NS)
        target = "xl/worksheets/sheet1.xml"
        if first is not None and "xl/_rels/workbook.xml.rels" in z.namelist():
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            for rel in rels:
                if rel.get("Id") == first.get(_REL):
                    t = rel.get("Target", "")
                    target = t.lstrip("/") if t.startswith("/") else "xl/" + t
        sheet = ET.fromstring(z.read(target))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise HTTPException(422, f"엑셀 파일을 읽지 못했습니다: {exc}") from exc
    rows: list[list[str]] = []
    for row in sheet.iter(f"{{{_NS['m']}}}row"):
        cells: dict[int, str] = {}
        for c in row.findall("m:c", _NS):
            kind, ref = c.get("t"), c.get("r", "A1")
            if kind == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
            else:
                v = c.find("m:v", _NS)
                text = v.text if v is not None and v.text is not None else ""
                if kind == "s" and text:
                    text = shared[int(text)]
            cells[_col(ref)] = text.strip()
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return rows


def read_table(filename: str, data: bytes) -> list[list[str]]:
    if data[:2] == b"PK" or filename.lower().endswith((".xlsx", ".xlsm")):
        return read_xlsx(data)
    text = data.decode("utf-8-sig", errors="replace")
    delim = "\t" if text.count("\t") > text.count(",") else ","
    return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]


def parse_contacts(rows: list[list[str]]) -> list[dict[str, Any]]:
    """[{vendor, category, contacts:[{phone, device, region}]}] — 업체 칸이 비면 위 업체를 이어 받는다"""
    head = next((i for i, r in enumerate(rows) if "업체" in r and any("번호" in c or "전화" in c for c in r)), None)
    if head is None:
        raise HTTPException(422, "머리글 줄('업체'·'번호' 칸)을 찾지 못했습니다.")
    h = rows[head]
    idx = {"vendor": h.index("업체"), "phone": next(i for i, c in enumerate(h) if "번호" in c or "전화" in c)}
    for key, word in (("device", "기종"), ("region", "지역")):
        idx[key] = next((i for i, c in enumerate(h) if word in c), None)
    cat_col = 0 if idx["vendor"] > 0 and not h[0] else None
    out: list[dict[str, Any]] = []
    current, category = None, ""
    for r in rows[head + 1:]:
        cell = lambda k: (r[idx[k]] if idx.get(k) is not None and idx[k] < len(r) else "").strip()  # noqa: E731
        if cat_col is not None and cat_col < len(r) and r[cat_col].strip():
            category = r[cat_col].strip()
        name, phone = cell("vendor"), cell("phone")
        if name:
            current = {"vendor": name, "category": category, "contacts": []}
            out.append(current)
        if phone and current is not None:
            current["contacts"].append({"phone": norm_phone(phone), "device": cell("device")[:20], "region": cell("region")[:200]})
    return [v for v in out if v["contacts"]]


# ---- 저장 ------------------------------------------------------------------------------------------

def contacts_by_vendor(cur) -> dict[int, list[dict[str, Any]]]:
    cur.execute("SELECT vendor_id, phone, device, region FROM vendor_contacts ORDER BY vendor_id, sort_order, id")
    out: dict[int, list[dict[str, Any]]] = {}
    for r in cur.fetchall():
        out.setdefault(r["vendor_id"], []).append({"phone": r["phone"], "device": r["device"], "region": r["region"]})
    return out


def replace_contacts(cur, vendor_id: int, contacts: list[dict[str, Any]], at: datetime) -> None:
    cur.execute("DELETE FROM vendor_contacts WHERE vendor_id = %s", (vendor_id,))
    for i, c in enumerate(contacts):
        cur.execute(
            "INSERT INTO vendor_contacts (vendor_id, phone, device, region, sort_order, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (vendor_id, c["phone"], c.get("device", ""), c.get("region", ""), i, at),
        )
    first = contacts[0]["phone"][:20] if contacts else ""
    cur.execute("UPDATE vendors SET phone = %s, updated_at = %s WHERE id = %s", (first, at, vendor_id))


def plan_import(cur, parsed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cur.execute("SELECT id, name FROM vendors ORDER BY id")
    by_key: dict[str, dict[str, Any]] = {}
    for v in cur.fetchall():
        by_key.setdefault(name_key(v["name"]), v)
    plan = []
    for item in parsed:
        hit = by_key.get(name_key(item["vendor"]))
        plan.append({**item, "vendor_id": hit["id"] if hit else None, "matched_name": hit["name"] if hit else None})
    return plan


@router.post("/api/admin/vendors/contacts/import")
async def import_contacts(settings: SettingsDep, file: UploadFile = File(...), apply: bool = False) -> dict[str, Any]:
    data = await file.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise HTTPException(413, "파일이 너무 큽니다 (2MB 이하).")
    parsed = parse_contacts(read_table(file.filename or "", data))
    if not parsed:
        raise HTTPException(422, "등록할 업체·번호가 없습니다.")
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        plan = plan_import(cur, parsed)
        if apply:
            for p in plan:
                if p["vendor_id"] is None:
                    cur.execute(
                        """INSERT INTO vendors (name, biz_no, phone, address, room_name, created_at, updated_at)
                           VALUES (%s, '', '', '', NULL, %s, %s)""",
                        (p["vendor"][:100], now, now),
                    )
                    p["vendor_id"] = cur.lastrowid
                    p["created"] = True
                replace_contacts(cur, p["vendor_id"], p["contacts"], now)
            cur.execute("DELETE FROM app_settings WHERE name = 'vendor_contacts_imported_at'")
            cur.execute("INSERT INTO app_settings (name, value) VALUES ('vendor_contacts_imported_at', %s)", (now.isoformat(timespec="seconds"),))
    if apply:
        live.bump("vendor")
    return {
        "ok": True,
        "applied": apply,
        "vendors": [
            {"name": p["vendor"], "category": p["category"], "matched": p["matched_name"], "created": bool(p.get("created")),
             "contacts": p["contacts"]}
            for p in plan
        ],
        "contacts": sum(len(p["contacts"]) for p in plan),
    }


class ContactBody(BaseModel):
    phone: str = Field(min_length=1, max_length=30)
    device: str = Field(default="", max_length=20)
    region: str = Field(default="", max_length=200)


class ContactsBody(BaseModel):
    contacts: list[ContactBody] = Field(default_factory=list, max_length=50)


@router.put("/api/admin/vendors/{vendor_id}/contacts")
def put_contacts(vendor_id: int, body: ContactsBody, settings: SettingsDep) -> dict[str, Any]:
    contacts = [
        {"phone": norm_phone(c.phone), "device": c.device.strip(), "region": c.region.strip()}
        for c in body.contacts if norm_phone(c.phone)
    ]
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM vendors WHERE id = %s", (vendor_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "업체가 없습니다.")
        replace_contacts(cur, vendor_id, contacts, _now(settings))
    live.bump("vendor")
    return {"ok": True, "contacts": contacts}
