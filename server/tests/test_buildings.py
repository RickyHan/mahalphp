"""건물 관리: 건물 목록 붙여넣기 · 채팅방 자동 매핑(애매하면 남김) · 직접 연결/해제 · 목록 외 채팅방"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import apartments as ap
from app import db
from app.main import app

from .conftest import ADMIN_PASSWORD, ADMIN_USER, API_KEY


@pytest.fixture
def admin(settings):
    c = TestClient(app)
    assert c.post("/admin/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD}, follow_redirects=False).status_code == 303
    return c


def _post(room, text="안녕하세요"):
    res = TestClient(app).post("/api/messages", headers={"X-Api-Key": API_KEY}, data={
        "device_id": "bld", "room_name": room, "sender_name": "관리소", "message_text": text,
        "is_group_chat": "true", "posted_at": str(int(time.time() * 1000) + hash(room + text) % 1000)})
    assert res.status_code == 201


def test_parse_building_list():
    text = "빌라명\t주소\n솔숲빌라\t서울 강서구 1-1 \n\n 무지개맨션 \t 경기 김포시  2-2\n솔숲빌라\t중복 줄\n까사미아A,B\t서울 강남구\n주소없는건물\n"
    assert ap.parse_building_list(text) == [
        ("솔숲빌라", "서울 강서구 1-1"), ("무지개맨션", "경기 김포시 2-2"), ("까사미아A,B", "서울 강남구"), ("주소없는건물", ""),
    ]


def test_similarity_rules():
    assert ap.similarity("★다솜빌라 입주민방", "다솜빌라") == 1.0
    assert ap.similarity("★동부에이스 부천", "동부에이스 (부천)") == 1.0
    assert ap.similarity("★한양트라움102", "한양트라움102동") >= 0.9
    assert ap.similarity("홍길동", "다솜빌라") < 0.5
    b = [{"id": 1, "name": "어반팰리스101동"}, {"id": 2, "name": "어반팰리스102동"}, {"id": 3, "name": "하이빌"}]
    # 동이 안 적힌 방은 101동·102동 중 애매 → 연결 안 함. 같은 건물을 원하는 방이 둘이면 둘 다 남김
    assert ap.best_matches(["★어반팰리스", "★어반팰리스 102동", "★하이빌", "★하이빌 관리방"], b) == [("★어반팰리스 102동", 2, 1.0)]


def test_import_automap_and_manual_link(admin, settings):
    for room in ("★한빛하이츠 101동 입주민방", "★한빛하이츠", "★솔숲 단톡방", "★별빛마을", "은하수타워 관리방", "●한빛설비업체"):
        _post(room)
    admin.post("/api/admin/apartments/sync")  # 목록 등록 전: 별 방은 목록 외 채팅방으로
    before = {a["room_name"]: a for a in admin.get("/api/admin/apartments").json()["items"] if a["room_name"]}
    assert before["★솔숲 단톡방"]["listed"] is False
    # 목록 외 방에 해 둔 업체 설정은 건물로 옮겨져야 한다
    cats = admin.get("/api/admin/categories").json()["items"]
    admin.put(f"/api/admin/apartments/{before['★솔숲 단톡방']['id']}/vendors", json={"assignments": [{"category_id": cats[0]["id"], "mode": "none"}]})

    mapped = []
    for name, addr in (("한빛하이츠101동", "서울 1"), ("한빛하이츠102동", "서울 2"), ("솔숲빌라", "서울 3"), ("은하수타워", "서울 4"), ("무지개맨션", "서울 5")):
        res = admin.post("/api/admin/apartments", json={"name": name, "address": addr})
        assert res.status_code == 201, res.text
        mapped += res.json()["mapped"]
    assert sorted((m["room_name"], m["name"]) for m in mapped) == [
        ("★솔숲 단톡방", "솔숲빌라"), ("★한빛하이츠 101동 입주민방", "한빛하이츠101동"), ("은하수타워 관리방", "은하수타워"),
    ]
    items = admin.get("/api/admin/apartments").json()["items"]
    by_name = {a["name"]: a for a in items if a["listed"]}
    assert by_name["한빛하이츠102동"]["room_name"] is None and by_name["무지개맨션"]["room_name"] is None
    assert by_name["솔숲빌라"]["address"] == "서울 3" and by_name["솔숲빌라"]["custom"] == 1  # 업체 설정 이어받음
    extra = {a["room_name"] for a in items if not a["listed"]}
    assert {"★한빛하이츠", "★별빛마을"} <= extra and "★솔숲 단톡방" not in extra and "●한빛설비업체" not in extra
    assert not any(a["room_name"] == "●한빛설비업체" for a in items)

    # 주소 수정
    assert admin.put(f"/api/admin/apartments/{by_name['솔숲빌라']['id']}", json={"name": "솔숲빌라", "address": "서울 3-1"}).json()["address"] == "서울 3-1"
    assert admin.post("/api/admin/apartments", json={"name": "  "}).status_code == 422

    # 후보: 102동에는 '★한빛하이츠'가 비슷한 이름으로 위에
    b102 = by_name["한빛하이츠102동"]
    cands = admin.get(f"/api/admin/apartments/{b102['id']}/candidates").json()
    assert cands["kind"] == "rooms" and cands["items"][0]["room_name"].startswith("★한빛하이츠")
    assert all(c["room_name"] != "●한빛설비업체" for c in cands["items"])

    # 목록 외 방 '★한빛하이츠' 를 102동에 직접 지정 → 목록 외 행은 사라짐
    assert admin.put(f"/api/admin/apartments/{b102['id']}/room", json={"room_name": "★한빛하이츠"}).status_code == 200
    items = admin.get("/api/admin/apartments").json()["items"]
    assert next(a for a in items if a["id"] == b102["id"])["room_mode"] == "manual"
    assert sum(1 for a in items if a["room_name"] == "★한빛하이츠") == 1

    # 다른 건물에 연결된 방을 옮겨 오면 원래 건물은 연결 해제
    mu = by_name["무지개맨션"]
    assert admin.put(f"/api/admin/apartments/{mu['id']}/room", json={"room_name": "은하수타워 관리방"}).status_code == 200
    items = {a["name"]: a for a in admin.get("/api/admin/apartments").json()["items"] if a["listed"]}
    assert items["무지개맨션"]["room_name"] == "은하수타워 관리방" and items["은하수타워"]["room_name"] is None

    # 직접 해제한 건물은 다시 수집해도 자동으로 다시 연결하지 않는다
    sol = items["솔숲빌라"]
    assert admin.put(f"/api/admin/apartments/{sol['id']}/room", json={"room_name": None}).status_code == 200
    admin.post("/api/admin/apartments/sync")
    items = admin.get("/api/admin/apartments").json()["items"]
    assert next(a for a in items if a["id"] == sol["id"])["room_name"] is None
    assert any(a["room_name"] == "★솔숲 단톡방" and not a["listed"] for a in items)  # 풀려난 별 방은 목록 외로 보임

    # 잘못된 요청
    assert admin.put(f"/api/admin/apartments/{sol['id']}/room", json={"room_name": "없는방"}).status_code == 422
    assert admin.put(f"/api/admin/apartments/{sol['id']}/room", json={"room_name": "●한빛설비업체"}).status_code == 422
    assert admin.post("/api/admin/apartments", json={"name": "솔숲빌라"}).status_code == 409

    # 목록 외 방 → 건물 후보
    star = next(a for a in items if a["room_name"] == "★솔숲 단톡방")
    cands = admin.get(f"/api/admin/apartments/{star['id']}/candidates").json()
    assert cands["kind"] == "buildings" and cands["items"][0]["name"] == "솔숲빌라"

    # 정리 (뒤 테스트의 자동 매핑에 끼어들지 않도록)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM apartments WHERE listed = 1")


def test_seed_buildings_once(admin, settings):
    """처음 받은 건물 목록(app/data/buildings.tsv)은 한 번만 등록되고, 이후 삭제한 건물은 다시 생기지 않는다"""
    rows = ap.parse_building_list(ap.SEED_FILE.read_text(encoding="utf-8"))
    assert len(rows) > 400 and all(n and "\t" not in n for n, _ in rows)
    r = ap.seed_buildings(settings)
    assert r["created"] == len(rows)
    assert ap.seed_buildings(settings) is None
    items = [a for a in admin.get("/api/admin/apartments").json()["items"] if a["listed"]]
    assert len(items) == len(rows) and sum(1 for a in items if a["address"]) >= len(rows) - 5
    first = items[0]
    admin.delete(f"/api/admin/apartments/{first['id']}")
    assert ap.seed_buildings(settings) is None
    assert all(a["id"] != first["id"] for a in admin.get("/api/admin/apartments").json()["items"])
    with db.transaction(settings) as conn, conn.cursor() as cur:  # 뒤 테스트에 끼어들지 않도록 정리
        cur.execute("DELETE FROM apartments WHERE listed = 1")


def test_seed_building_vendors(admin, settings):
    """처음 받은 건물별 담당 업체 표: 업체 생성·지정, '입주민·없음·중단'은 보내지 않음, 같은 업체 표기 합침"""
    assert ap.vendor_cell("") == "" and ap.vendor_cell(" 입주민  ") == "" and ap.vendor_cell("늘푸른환경 - 중단") == ""
    assert ap.vendor_cell("티케이") == "TK" and ap.vendor_cell("TK ") == "TK" and ap.vendor_cell("유원 -> 교체공사중 26.10") == "유원"
    with db.transaction(settings) as conn, conn.cursor() as cur:  # 앞 테스트가 남긴 '등록 완료' 표시 지움
        cur.execute("DELETE FROM app_settings WHERE name IN ('buildings_seed', 'building_vendors_seed')")
    with pytest.raises(RuntimeError):  # 건물이 하나도 없으면 완료로 표시하지 않음
        ap.seed_building_vendors(settings)
    ap.seed_buildings(settings)
    r = ap.seed_building_vendors(settings)
    assert r["missing"] == [] and r["assigned"] > 3000 and r["none"] > 900 and r["vendors_created"] > 150
    assert ap.seed_building_vendors(settings) is None  # 한 번만

    apts = {a["name"]: a for a in admin.get("/api/admin/apartments").json()["items"] if a["listed"]}
    view = admin.get(f"/api/admin/apartments/{apts['DH팰리스A동']['id']}/vendors").json()
    vendors = {v["id"]: v["name"] for v in view["vendors"]}
    got = {c["category"]: (c["mode"], vendors.get(c["vendor_id"])) for c in view["categories"]}
    assert got["청소"] == ("vendor", "단비크린") and got["승강기"] == ("vendor", "성도") and got["누수"] == ("vendor", "수자원누수탐지")
    assert got["소방"] == ("none", None) and got["기타"] == ("vendor", "동대표")  # 빈칸은 보내지 않음
    assert {c["mode"] for c in view["categories"]} <= {"vendor", "none"}  # 표의 모든 분야가 정해짐
    cw = admin.get(f"/api/admin/apartments/{apts['청우파스텔시티ABCD동']['id']}/vendors").json()
    cw_names = {v["id"]: v["name"] for v in cw["vendors"]}
    cw_got = {c["category"]: (c["mode"], cw_names.get(c["vendor_id"])) for c in cw["categories"]}
    assert cw_got["소방"] == ("vendor", "동일전기안전관리") and cw_got["전기"] == ("none", None)  # 표 그대로
    view = admin.get(f"/api/admin/apartments/{apts['골든하우스']['id']}/vendors").json()
    got = {c["category"]: c["mode"] for c in view["categories"]}
    assert got["청소"] == "none" and got["기계주차"] == "vendor"   # '입주민 4명' → 보내지 않음
    names = [v["name"] for v in admin.get("/api/admin/vendors").json()["items"]]
    assert "티케이" not in names and names.count("TK") == 1
    with db.transaction(settings) as conn, conn.cursor() as cur:  # 뒤 테스트에 끼어들지 않도록 정리
        cur.execute("DELETE FROM apartment_vendors")
        cur.execute("DELETE FROM apartments WHERE listed = 1")
        cur.execute("DELETE FROM vendors WHERE room_name IS NULL")
        cur.execute("DELETE FROM app_settings WHERE name IN ('buildings_seed', 'building_vendors_seed')")
