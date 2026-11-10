"""업체 담당자 연락처: 엑셀(업체 번호표) 미리보기·반영, 여러 번호, 이름 맞추기, 직접 수정 (번호는 가짜)"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import vendor_contacts as vc
from app.main import app

from .conftest import ADMIN_PASSWORD, ADMIN_USER


@pytest.fixture
def admin(settings):
    c = TestClient(app)
    assert c.post("/admin/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD}, follow_redirects=False).status_code == 303
    return c


def _xlsx(rows: list[list[str]]) -> bytes:
    """공유 문자열을 쓰는 최소 xlsx (엑셀이 저장하는 형식과 같은 구조)"""
    strings: list[str] = []
    sheet_rows = []
    for r_i, row in enumerate(rows, start=2):  # 엑셀처럼 1행 비우고 B열부터
        cells = []
        for c_i, v in enumerate(row):
            if v == "":
                continue
            strings.append(v)
            cells.append(f'<c r="{chr(65 + c_i)}{r_i}" t="s"><v>{len(strings) - 1}</v></c>')
        sheet_rows.append(f'<row r="{r_i}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
    return buf.getvalue()


ROWS = [
    ["", "업체", "기종", "번호", "지역"],
    ["누수", "번호테스트누수", "안드로이드", "010-0000-0001", "서울, 경기"],
    ["승강기", "번호테스트승강기", "안드로이드", "010 0000 0002", "김포"],
    ["", "", "아이폰", "01000000003 ", "종로"],
    ["", "", "안드로이드", "010-0000-0004", ""],
    ["", "(주) 번호테스트기존", "아이폰", "010-0000-0005", ""],
]


def test_phone_and_name_rules():
    assert vc.norm_phone("010 9966 8071") == "010-9966-8071" and vc.norm_phone(" 01012345678 ") == "010-1234-5678"
    assert vc.norm_phone("02-123-4567") == "02-123-4567"
    assert vc.name_key("(주) 하이엔드 이앤씨") == vc.name_key("●하이엔드이앤씨") == vc.name_key("㈜하이엔드이앤씨")
    assert vc.name_key("대명") != vc.name_key("대명엠엔피")  # 비슷하기만 하면 묶지 않음


def test_import_preview_then_apply_and_edit(admin, settings):
    # 기존 업체(이름만 같고 표기가 다름) — 여기에 연결돼야 함
    res = admin.post("/api/admin/vendors", data={"name": "번호테스트기존", "phone": "010-9999-9999"})
    assert res.status_code == 201
    existing = res.json()["id"]
    data = _xlsx(ROWS)

    pre = admin.post("/api/admin/vendors/contacts/import", files={"file": ("업체번호.xlsx", data)}).json()
    assert pre["applied"] is False and pre["contacts"] == 5
    by = {v["name"]: v for v in pre["vendors"]}
    assert by["번호테스트승강기"]["category"] == "승강기" and len(by["번호테스트승강기"]["contacts"]) == 3
    assert by["번호테스트승강기"]["contacts"][1] == {"phone": "010-0000-0003", "device": "아이폰", "region": "종로"}
    assert by["(주) 번호테스트기존"]["matched"] == "번호테스트기존" and by["번호테스트누수"]["matched"] is None
    names = {v["name"] for v in admin.get("/api/admin/vendors").json()["items"]}
    assert "번호테스트승강기" not in names  # 미리보기는 저장 안 함

    done = admin.post("/api/admin/vendors/contacts/import", params={"apply": "true"}, files={"file": ("업체번호.xlsx", data)}).json()
    assert done["applied"] is True and {v["name"] for v in done["vendors"] if v["created"]} == {"번호테스트누수", "번호테스트승강기"}
    items = {v["name"]: v for v in admin.get("/api/admin/vendors").json()["items"]}
    lift = items["번호테스트승강기"]
    assert lift["phone"] == "010-0000-0002" and [c["phone"] for c in lift["contacts"]] == ["010-0000-0002", "010-0000-0003", "010-0000-0004"]
    assert lift["room_name"] is None and lift["category_ids"] == []      # 새 업체는 톡방·카테고리 비워 둠
    assert items["번호테스트기존"]["id"] == existing and items["번호테스트기존"]["phone"] == "010-0000-0005"

    # 다시 올려도 중복으로 늘지 않음 (파일 내용으로 바뀜)
    admin.post("/api/admin/vendors/contacts/import", params={"apply": "true"}, files={"file": ("업체번호.xlsx", data)})
    items = {v["name"]: v for v in admin.get("/api/admin/vendors").json()["items"]}
    assert len(items["번호테스트승강기"]["contacts"]) == 3 and list(items).count("번호테스트승강기") == 1

    # 직접 수정 · CSV 도 읽음 · 잘못된 파일
    r = admin.put(f"/api/admin/vendors/{lift['id']}/contacts", json={"contacts": [{"phone": "010 0000 0009", "region": "강남"}, {"phone": "  "}]})
    assert r.status_code == 422 or r.json()["contacts"] == [{"phone": "010-0000-0009", "device": "", "region": "강남"}]
    r = admin.put(f"/api/admin/vendors/{lift['id']}/contacts", json={"contacts": [{"phone": "010 0000 0009", "region": "강남"}]})
    assert r.json()["contacts"] == [{"phone": "010-0000-0009", "device": "", "region": "강남"}]
    assert next(v for v in admin.get("/api/admin/vendors").json()["items"] if v["id"] == lift["id"])["phone"] == "010-0000-0009"
    csv_body = "분류,업체,번호\n누수,번호테스트누수,010-0000-0011\n,,010-0000-0012\n".encode()
    r = admin.post("/api/admin/vendors/contacts/import", files={"file": ("a.csv", csv_body)}).json()
    assert [c["phone"] for c in r["vendors"][0]["contacts"]] == ["010-0000-0011", "010-0000-0012"]
    assert admin.post("/api/admin/vendors/contacts/import", files={"file": ("x.xlsx", b"PKnot-a-zip")}).status_code == 422
    assert admin.post("/api/admin/vendors/contacts/import", files={"file": ("x.csv", "이름,값\na,b\n".encode())}).status_code == 422
    assert admin.put("/api/admin/vendors/99999999/contacts", json={"contacts": []}).status_code == 404
    # 업체 삭제 때 연락처도 지움
    assert admin.delete(f"/api/admin/vendors/{lift['id']}").status_code == 200
    assert TestClient(app).post("/api/admin/vendors/contacts/import", files={"file": ("a.csv", csv_body)}).status_code in (401, 403)
