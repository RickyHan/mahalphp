"""아파트 관리: 별 하나로 시작하는 채팅방 수집 · 이름 수정 · 다시 수집하기"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import apartments as ap
from app.main import app

from .conftest import ADMIN_PASSWORD, ADMIN_USER, API_KEY


@pytest.fixture
def admin(settings):
    c = TestClient(app)
    assert c.post("/admin/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD}, follow_redirects=False).status_code == 303
    return c


def _post(room, text="안녕하세요"):
    res = TestClient(app).post("/api/messages", headers={"X-Api-Key": API_KEY}, data={
        "device_id": "apt", "room_name": room, "sender_name": "관리소", "message_text": text,
        "is_group_chat": "true", "posted_at": str(int(time.time() * 1000) + hash(room + text) % 1000)})
    assert res.status_code == 201


def test_star_rules():
    assert ap.is_apartment_room("★래미안아파트") and ap.apartment_name("★래미안아파트") == "래미안아파트"
    assert ap.is_apartment_room(" ☆ 힐스테이트 1단지") and ap.apartment_name(" ☆ 힐스테이트 1단지") == "힐스테이트 1단지"
    assert ap.is_apartment_room("⭐️자이아파트⭐️") and ap.apartment_name("⭐️자이아파트⭐️") == "자이아파트"
    assert not ap.is_apartment_room("★★공지방")        # 별 두 개로 시작 → 제외
    assert not ap.is_apartment_room("★ ★공지방")
    assert not ap.is_apartment_room("푸르지오★")        # 별로 시작하지 않음
    assert not ap.is_apartment_room("⛳골프는 노터치⛳")


def test_sync_rename_and_resync(admin):
    assert TestClient(app).get("/api/admin/apartments").status_code == 401
    for room in ("★래미안아파트", "☆힐스테이트", "★★공지방", "일반방"):
        _post(room)
    r = admin.post("/api/admin/apartments/sync").json()
    assert sorted(a["room_name"] for a in r["added"]) == sorted(["☆힐스테이트", "★래미안아파트"])
    items = {i["room_name"]: i for i in admin.get("/api/admin/apartments").json()["items"]}
    assert items["★래미안아파트"]["name"] == "래미안아파트" and items["★래미안아파트"]["messages"] == 1
    assert "★★공지방" not in items and "일반방" not in items

    apt = items["★래미안아파트"]
    assert admin.put(f"/api/admin/apartments/{apt['id']}", json={"name": "  래미안 1차  "}).json()["name"] == "래미안 1차"
    assert admin.put(f"/api/admin/apartments/{apt['id']}", json={"name": "   "}).status_code == 422

    # 채팅이 더 모인 뒤 다시 수집: 새 방만 추가, 수정한 이름은 유지
    _post("★자이아파트")
    _post("★래미안아파트", "두번째 메시지")
    r = admin.post("/api/admin/apartments/sync").json()
    assert [a["room_name"] for a in r["added"]] == ["★자이아파트"]
    data = admin.get("/api/admin/apartments").json()
    items = {i["room_name"]: i for i in data["items"]}
    assert items["★래미안아파트"]["name"] == "래미안 1차" and items["★래미안아파트"]["messages"] == 2
    assert data["synced_at"]

    # 삭제 후 다시 수집하면 다시 추가된다
    assert admin.delete(f"/api/admin/apartments/{items['★자이아파트']['id']}").status_code == 200
    assert [a["room_name"] for a in admin.post("/api/admin/apartments/sync").json()["added"]] == ["★자이아파트"]


# ---- 민원처리업체: 꽉 찬 동그라미로 시작하는 방 ---------------------------------------------------

def test_vendor_room_rules():
    from app import vendors as vd

    assert vd.is_vendor_room("●엘레벨레") and vd.vendor_name_from_room("●엘레벨레") == "엘레벨레"
    assert vd.is_vendor_room("⚫ 한빛설비") and vd.vendor_name_from_room("⚫ 한빛설비") == "한빛설비"
    assert vd.is_vendor_room("🔴️청소업체") and vd.vendor_name_from_room("🔴️청소업체") == "청소업체"
    assert not vd.is_vendor_room("○빈동그라미방") and not vd.is_vendor_room("⚪흰동그라미")
    assert not vd.is_vendor_room("★래미안아파트") and not vd.is_vendor_room("업체●")


def test_vendor_sync_links_and_keeps_names(admin):
    # 톡방 없이 먼저 등록된 업체는 같은 이름의 ● 방에 연결된다
    assert admin.post("/api/admin/vendors", data={"name": "기존업체", "phone": "010-1111-2222"}).status_code == 201
    for room in ("●기존업체", "●신규설비", "○빈방", "★아파트"):
        _post(room)
    r = admin.post("/api/admin/vendors/sync").json()
    assert [a["room_name"] for a in r["linked"]] == ["●기존업체"]
    assert [a["room_name"] for a in r["added"]] == ["●신규설비"]
    data = admin.get("/api/admin/vendors").json()
    items = {v["room_name"]: v for v in data["items"] if v["room_name"]}
    assert items["●기존업체"]["phone"] == "010-1111-2222" and items["●기존업체"]["messages"] == 1
    assert items["●신규설비"]["name"] == "신규설비" and data["synced_at"]
    assert "○빈방" not in items and "★아파트" not in items

    vid = items["●신규설비"]["id"]
    assert admin.patch(f"/api/admin/vendors/{vid}/name", json={"name": " 신규설비(주) "}).json()["name"] == "신규설비(주)"
    assert admin.patch(f"/api/admin/vendors/{vid}/name", json={"name": "  "}).status_code == 422
    assert admin.patch("/api/admin/vendors/999999/name", json={"name": "x"}).status_code == 404
    # 다시 수집: 이미 연결된 방은 건너뛰고 고친 이름 유지
    _post("●세번째업체")
    r = admin.post("/api/admin/vendors/sync").json()
    assert [a["room_name"] for a in r["added"]] == ["●세번째업체"] and r["linked"] == []
    names = {v["room_name"]: v["name"] for v in admin.get("/api/admin/vendors").json()["items"] if v["room_name"]}
    assert names["●신규설비"] == "신규설비(주)"


def test_stats_by_apartment(admin, settings):
    from app import complaints as cp
    from app import store

    class Fake:
        def classify(self, items):
            return [cp.Verdict(i.id, True, "소음" if "소음" in i.text else "주차", "민원", 0.9) for i in items]

    for room, text in (("★통계아파트A", "층간소음 심해요 조치 바랍니다"), ("★통계아파트A", "주차장 이중주차 빼주세요"),
                       ("★통계아파트B", "윗집 소음 너무 심합니다"), ("통계일반방", "주차 문제 해결해 주세요")):
        res = TestClient(app).post("/api/messages", headers={"X-Api-Key": API_KEY}, data={
            "device_id": "stat", "room_name": room, "sender_name": f"{hash(text) % 900 + 100}호", "message_text": text,
            "is_group_chat": "true", "posted_at": str(int(time.time() * 1000) + hash(text) % 1000)})
        assert res.status_code == 201
    store.classify_once(settings, client=Fake())
    admin.post("/api/admin/apartments/sync")
    apts = {a["room_name"]: a for a in admin.get("/api/admin/apartments").json()["items"]}
    a_id = apts["★통계아파트A"]["id"]
    admin.put(f"/api/admin/apartments/{a_id}", json={"name": "통계A단지"})

    whole = admin.get("/api/admin/stats", params={"period": "today"}).json()
    groups = {g[0]: g for g in whole["group_detail"]}
    assert groups["통계A단지"][1] == 2 and groups["통계A단지"][2] == {"소음": 1, "주차": 1}
    assert "통계아파트B" in groups and groups["건물 외 방"][1] >= 1
    assert whole["apartment"] is None and any(a["id"] == a_id for a in whole["apartments"])

    one = admin.get("/api/admin/stats", params={"period": "today", "apartment_id": a_id}).json()
    assert one["issues"] == 2 and dict(one["by_category"]) == {"소음": 1, "주차": 1}
    assert one["apartment"]["name"] == "통계A단지" and one["group_detail"] == []
    assert admin.get("/api/admin/stats", params={"apartment_id": 999999}).status_code == 404


def test_vendor_sync_links_similar_names(admin):
    """다시 수집하기: 톡방 없는 업체에 비슷한 이름의 ● 방을 자동 연결, 애매하면 남김, 없으면 새 업체"""
    from app import vendors as vd

    assert vd.vendor_similarity("●성도엘리베이터", "성도") >= 0.85
    assert vd.vendor_similarity("●꽃청소닷컴 단톡방", "꽃청소닷컴") == 1.0
    assert vd.vendor_similarity("●(주)하이엔드소방", "하이엔드") >= 0.85
    assert vd.vendor_similarity("●한빛설비", "하이엔드") < 0.8
    for name in ("유사성도", "유사꽃청소", "유사꽃청소닷컴", "유사하이", "유사하이엔드", "유사GYG"):
        assert admin.post("/api/admin/vendors", data={"name": name}).status_code == 201
    for room in ("●유사성도엘리베이터", "●유사꽃청소닷컴 단톡방", "●유사하이 엔드", "●유사gyg", "●유사신규설비"):
        _post(room)
    r = admin.post("/api/admin/vendors/sync").json()
    linked = {x["room_name"]: (x["name"], x["exact"]) for x in r["linked"]}
    assert linked["●유사성도엘리베이터"] == ("유사성도", False)
    assert linked["●유사꽃청소닷컴 단톡방"] == ("유사꽃청소닷컴", True)        # 더 긴(정확한) 이름이 이김
    assert linked["●유사gyg"] == ("유사GYG", True)                          # 대소문자·공백 무시
    assert linked["●유사하이 엔드"] == ("유사하이엔드", True)
    assert [a["room_name"] for a in r["added"]] == ["●유사신규설비"]            # 비슷한 업체 없음 → 새 업체
    items = {v["name"]: v for v in admin.get("/api/admin/vendors").json()["items"]}
    assert items["유사성도"]["room_name"] == "●유사성도엘리베이터" and items["유사꽃청소"]["room_name"] is None


def test_unlinked_vendor_rooms(admin):
    """● 톡방 중 연결 안 된 방 목록 · 업체에 연결 · 새 업체로 추가"""
    assert admin.post("/api/admin/vendors", data={"name": "미연결테스트설비"}).status_code == 201
    for room in ("●미연결테스트설비 공지", "●미연결전혀다른방"):
        _post(room)
    data = admin.get("/api/admin/vendors/unlinked-rooms").json()
    rows = {r["room_name"]: r for r in data["items"]}
    assert {"●미연결테스트설비 공지", "●미연결전혀다른방"} <= set(rows)
    target = rows["●미연결테스트설비 공지"]["suggested"][0]
    assert target["name"] == "미연결테스트설비" and rows["●미연결테스트설비 공지"]["messages"] == 1
    assert admin.post(f"/api/admin/vendors/{target['id']}/room", json={"room_name": "●미연결테스트설비 공지"}).status_code == 200
    r = admin.post("/api/admin/vendors/from-room", json={"room_name": "●미연결전혀다른방"})
    assert r.status_code == 201 and r.json()["name"] == "미연결전혀다른방"
    left = {x["room_name"] for x in admin.get("/api/admin/vendors/unlinked-rooms").json()["items"]}
    assert "●미연결테스트설비 공지" not in left and "●미연결전혀다른방" not in left
    # 이미 연결된 방·없는 방·없는 업체
    assert admin.post("/api/admin/vendors/from-room", json={"room_name": "●미연결전혀다른방"}).status_code == 409
    assert admin.post(f"/api/admin/vendors/{target['id']}/room", json={"room_name": "●없는방"}).status_code == 422
    assert admin.post("/api/admin/vendors/999999/room", json={"room_name": "●미연결전혀다른방"}).status_code == 404
    assert TestClient(app).get("/api/admin/vendors/unlinked-rooms").status_code == 401
    # 목록에서 바로 해제 → 다시 '연결 안 된 톡방'으로
    assert admin.delete(f"/api/admin/vendors/{target['id']}/room").status_code == 200
    assert "●미연결테스트설비 공지" in {x["room_name"] for x in admin.get("/api/admin/vendors/unlinked-rooms").json()["items"]}
    assert admin.delete("/api/admin/vendors/999999/room").status_code == 404
