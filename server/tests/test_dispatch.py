"""민원 → ① 민원 방에 카톡 접수 안내 → ② 담당 업체 카톡방에 처리 요청 (휴대폰 앱이 카톡 '답장'으로 전송)"""

from __future__ import annotations

import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import complaints as cp
from app import db, store
from app.main import app

from .conftest import ADMIN_PASSWORD, ADMIN_USER, API_KEY

KEY = {"X-Api-Key": API_KEY}
DEVICE = "dispatch-phone"


class FakeGemini:
    def classify(self, items):
        return [cp.Verdict(i.id, "누수" in i.text, "누수" if "누수" in i.text else "기타", "누수 민원", 0.9) for i in items]


@pytest.fixture(autouse=True)
def sms_off(settings):
    """예전(카톡방만) 동작을 보는 테스트: 업체 문자는 끈 상태로"""
    with db.transaction(settings) as conn, conn.cursor() as cur:
        store.put_settings(cur, {"sms_enabled": "0"})
    yield
    with db.transaction(settings) as conn, conn.cursor() as cur:
        store.put_settings(cur, {"sms_enabled": "0"})


@pytest.fixture
def admin(settings):
    c = TestClient(app)
    assert c.post("/admin/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD}, follow_redirects=False).status_code == 303
    return c


def _post(room, sender, text, device=DEVICE):
    res = TestClient(app).post("/api/messages", headers=KEY, data={
        "device_id": device, "room_name": room, "sender_name": sender, "message_text": text,
        "is_group_chat": "true", "posted_at": str(int(time.time() * 1000))})
    assert res.status_code == 201
    return res.json()["id"]


def _pending(device=DEVICE):
    return TestClient(app).get("/api/replies/pending", params={"device_id": device}, headers=KEY).json()["items"]


def _report(reply_id, sent, note=None, device=DEVICE):
    data = {"device_id": device, "sent": "true" if sent else "false"}
    if note:
        data["note"] = note
    assert TestClient(app).post(f"/api/replies/{reply_id}/result", data=data, headers=KEY).status_code == 200


def test_vendor_message_template():
    text = cp.format_vendor_message(cp.DEFAULT_VENDOR_TEMPLATE, vendor="엘레벨레", room="OO아파트", category="누수", summary="천장 누수",
                                    content="물이   새요\n빨리", sender="1203호", at=datetime(2026, 11, 2, 9, 5))
    assert "엘레벨레 담당자님" in text and "'OO아파트' 대화방" in text and "물이 새요 빨리" in text
    assert "- 보낸 사람: 1203호" in text and "- 접수 시각: 11/02 09:05" in text
    assert "{" not in text


def test_complaint_ack_then_vendor_room(admin, settings):
    cat = next(c for c in admin.get("/api/admin/categories").json()["items"] if c["name"] == "누수")
    _post("엘레벨레 업무방", "엘레벨레", "안녕하세요 업체입니다")  # 업체 방 알림을 이 휴대폰이 받고 있음
    assert admin.post("/api/admin/vendors", data={"name": "엘레벨레", "room_name": "엘레벨레 업무방", "category_id": str(cat["id"])}).status_code == 201
    assert admin.post("/api/admin/vendors", data={"name": "방없는업체", "category_id": str(cat["id"])}).status_code == 201

    mid = _post("전달테스트방", "1203호", "욕실 천장 누수가 심해요 확인 부탁")
    result = store.classify_once(settings, client=FakeGemini())
    assert result["vendor"] == 1  # 방이 지정된 업체만

    items = _pending()
    assert [i["room_name"] for i in items] == ["전달테스트방", "엘레벨레 업무방"]  # 접수 안내가 먼저
    ack, vendor = items
    assert "민원이 접수되었습니다" in ack["text"]
    assert "엘레벨레 담당자님" in vendor["text"] and "'전달테스트방' 대화방" in vendor["text"] and "욕실 천장 누수" in vendor["text"]

    _report(ack["id"], True)
    _report(vendor["id"], False, "휴대폰에 이 방의 답장 가능한 카톡 알림이 없음")
    # 업체 전달 실패가 민원의 '접수 안내 보냄' 상태를 덮어쓰지 않는다
    group = next(g for g in admin.get("/api/admin/complaints").json()["items"] if g["ids"][0] == mid)
    assert group["reply_status"] == 1
    d = group["vendor_dispatch"][0]
    assert len(group["vendor_dispatch"]) == 1 and d["text"].startswith("[민원 처리 요청]") and d["ready"] is None
    assert {k: d[k] for k in ("vendor", "room", "status", "note")} == {"vendor": "엘레벨레", "room": "엘레벨레 업무방", "status": "failed", "note": "휴대폰에 이 방의 답장 가능한 카톡 알림이 없음"}

    # 이력 · 다시 보내기
    hist = admin.get("/api/admin/vendor-dispatch").json()
    row = next(h for h in hist["items"] if h["message_id"] == mid)
    assert row["complaint_room"] == "전달테스트방" and row["category"] == "누수" and row["status"] == "failed"
    assert admin.post(f"/api/admin/vendor-dispatch/{row['id']}/retry").status_code == 200
    assert admin.post(f"/api/admin/vendor-dispatch/{row['id']}/retry").status_code == 422  # 이미 대기 중
    again = _pending()
    assert [i["id"] for i in again] == [row["id"]]
    _report(row["id"], True)
    st = admin.get("/api/admin/stats", params={"period": "today"}).json()
    assert st["vendor_sent"] >= 1 and "sms_sent" not in st

    # 같은 문제 반복 민원은 업체에 다시 보내지 않는다
    _post("전달테스트방", "1203호", "누수 아직 그대로예요 빨리요")
    assert store.classify_once(settings, client=FakeGemini())["vendor"] == 0


def test_vendor_dispatch_setting(admin, settings):
    s = admin.get("/api/admin/settings").json()
    assert s["vendor_enabled"] is True and "{업체명}" in s["vendor_placeholders"]
    r = admin.put("/api/admin/settings", json={"vendor_enabled": False, "vendor_template": "{업체명} 확인 바랍니다"}).json()
    assert r["vendor_enabled"] is False and r["vendor_template"] == "{업체명} 확인 바랍니다"
    _post("전달끔방", "502호", "베란다 누수 생겼어요 확인 부탁")
    assert store.classify_once(settings, client=FakeGemini())["vendor"] == 0
    r = admin.put("/api/admin/settings", json={"vendor_enabled": True, "vendor_template": ""}).json()
    assert r["vendor_template"] == cp.DEFAULT_VENDOR_TEMPLATE
    # 이전 기본 문구로 저장돼 있으면 새 기본 문구(보낸 사람 줄 포함)로 보인다
    r = admin.put("/api/admin/settings", json={"vendor_template": cp.OLD_VENDOR_TEMPLATES[0]}).json()
    assert r["vendor_template"] == cp.DEFAULT_VENDOR_TEMPLATE
    # 남은 대기 안내 정리 (다른 테스트에 영향 없게)
    for item in _pending():
        _report(item["id"], True)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM reply_queue WHERE kind = 'vendor' AND status = 'pending'")
        assert cur.fetchone()["n"] == 0


def test_vendor_request_waits_for_room_notification(admin, settings):
    """업체 방 알림을 아직 못 받았으면 실패시키지 않고 기다린다: 접수 안내는 30분, 업체 요청은 12시간"""
    from datetime import timedelta

    now = store.now_local(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        store.enqueue_reply(cur, 999001, DEVICE, "업체방", "요청", now - timedelta(hours=2), kind="vendor", vendor_id=1, vendor_name="v")
        store.enqueue_reply(cur, 999002, DEVICE, "업체방", "요청", now - timedelta(hours=13), kind="vendor", vendor_id=1, vendor_name="v")
        store.enqueue_reply(cur, 999003, DEVICE, "민원방", "안내", now - timedelta(hours=2))
    ids = {i["text"] + str(i["id"]) for i in _pending()}
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT message_id, status, note FROM reply_queue WHERE message_id IN (999001, 999002, 999003)")
        rows = {r["message_id"]: r for r in cur.fetchall()}
    assert rows[999001]["status"] == "pending"           # 2시간 된 업체 요청은 계속 대기
    assert rows[999002]["status"] == "expired" and "12시간" in rows[999002]["note"]
    assert rows[999003]["status"] == "expired"           # 접수 안내는 30분
    assert ids  # 대기 중인 것은 휴대폰에 계속 전달된다
    for item in _pending():
        _report(item["id"], True)


def test_group_minutes_setting(admin, settings):
    """같은 문제로 묶는 시간: 기본 15분, 웹에서 변경 (1~240분)"""
    assert admin.get("/api/admin/settings").json()["group_minutes"] == 15
    assert admin.put("/api/admin/settings", json={"group_minutes": 0}).status_code == 422
    assert admin.put("/api/admin/settings", json={"group_minutes": 241}).status_code == 422

    def post_at(text, minutes_ago):
        at = int((time.time() - minutes_ago * 60) * 1000)
        res = TestClient(app).post("/api/messages", headers=KEY, data={
            "device_id": DEVICE, "room_name": "묶음시험방", "sender_name": "707호", "message_text": text,
            "is_group_chat": "true", "posted_at": str(at)})
        return res.json()["id"]

    first = post_at("화장실 누수 생겼어요 확인 부탁", 25)
    second = post_at("누수 아직 그대로예요 빨리요", 5)  # 첫 민원 20분 뒤
    store.classify_once(settings, client=FakeGemini())
    with db.transaction(settings) as conn, conn.cursor() as cur:
        assert store.is_new_issue(cur, first, store.current_group_window(cur))
        assert store.is_new_issue(cur, second, store.current_group_window(cur))  # 15분 기준: 별개 문제
    assert admin.put("/api/admin/settings", json={"group_minutes": 30}).json()["group_minutes"] == 30
    with db.transaction(settings) as conn, conn.cursor() as cur:
        assert not store.is_new_issue(cur, second, store.current_group_window(cur))  # 30분 기준: 같은 문제
    groups = [g for g in admin.get("/api/admin/complaints").json()["items"] if g["room_name"] == "묶음시험방"]
    assert [g["count"] for g in groups] == [2]
    assert admin.put("/api/admin/settings", json={"group_minutes": 15}).status_code == 200
    for item in _pending():
        _report(item["id"], True)


def test_apartment_specific_vendors(admin, settings):
    """같은 민원 분류라도 아파트마다 다른 업체로: 아파트 지정 → 없으면 기본 업체 → '보내지 않음'"""
    cat = next(c for c in admin.get("/api/admin/categories").json()["items"] if c["name"] == "기계주차")
    for room in ("●기본주차업체", "●동네주차업체", "★가나아파트", "★다라아파트"):
        _post(room, "관리", "안녕하세요 처음 인사드립니다")
    admin.post("/api/admin/vendors/sync")
    admin.post("/api/admin/apartments/sync")
    vendors = {v["room_name"]: v for v in admin.get("/api/admin/vendors").json()["items"] if v["room_name"]}
    base, local = vendors["●기본주차업체"], vendors["●동네주차업체"]
    # 기본 업체: 주차 담당
    assert admin.put(f"/api/admin/vendors/{base['id']}", data={"name": base["name"], "room_name": base["room_name"], "category_id": str(cat["id"])}).status_code == 200
    apts = {a["room_name"]: a for a in admin.get("/api/admin/apartments").json()["items"]}
    ga, da = apts["★가나아파트"], apts["★다라아파트"]

    view = admin.get(f"/api/admin/apartments/{ga['id']}/vendors").json()
    row = next(r for r in view["categories"] if r["category_id"] == cat["id"])
    assert row["mode"] == "default" and [d["name"] for d in row["defaults"]] == [base["name"]]

    # 가나아파트의 주차 → 동네주차업체
    r = admin.put(f"/api/admin/apartments/{ga['id']}/vendors", json={"assignments": [{"category_id": cat["id"], "mode": "vendor", "vendor_id": local["id"]}]})
    assert r.status_code == 200
    assert next(x for x in r.json()["categories"] if x["category_id"] == cat["id"])["mode"] == "vendor"
    with db.transaction(settings) as conn, conn.cursor() as cur:
        assert [v["name"] for v in store.target_vendors(cur, "★가나아파트", "기계주차")] == [local["name"]]
        assert [v["name"] for v in store.target_vendors(cur, "★다라아파트", "기계주차")] == [base["name"]]  # 지정 없음 → 기본
        assert [v["name"] for v in store.target_vendors(cur, "일반방", "기계주차")] == [base["name"]]
    apts = {a["room_name"]: a for a in admin.get("/api/admin/apartments").json()["items"]}
    assert apts["★가나아파트"]["assigned"] == 1 and apts["★다라아파트"]["assigned"] == 0

    # 실제 민원 흐름: 가나아파트 주차 민원 → 동네주차업체 톡방
    class ParkingGemini:
        def classify(self, items):
            return [cp.Verdict(i.id, True, "기계주차", "주차 민원", 0.9) for i in items]

    _post("★가나아파트", "101호", "주차장에 이중주차 차량 빼주세요")
    store.classify_once(settings, client=ParkingGemini())
    # 가나아파트에서 온 민원의 업체 요청만 본다
    rooms = [i["room_name"] for i in _pending() if "'★가나아파트' 대화방" in i["text"] and "이중주차" in i["text"]]
    assert rooms == ["●동네주차업체"]
    for item in _pending():
        _report(item["id"], True)

    # 다라아파트는 주차를 '보내지 않음'으로
    admin.put(f"/api/admin/apartments/{da['id']}/vendors", json={"assignments": [{"category_id": cat["id"], "mode": "none"}]})
    with db.transaction(settings) as conn, conn.cursor() as cur:
        assert store.target_vendors(cur, "★다라아파트", "기계주차") == [] or list(store.target_vendors(cur, "★다라아파트", "기계주차")) == []
    # 잘못된 값 거부, 업체 삭제 시 아파트 지정도 정리
    assert admin.put(f"/api/admin/apartments/{ga['id']}/vendors", json={"assignments": [{"category_id": cat["id"], "mode": "vendor", "vendor_id": 999999}]}).status_code == 422
    assert admin.delete(f"/api/admin/vendors/{local['id']}").status_code == 200
    view = admin.get(f"/api/admin/apartments/{ga['id']}/vendors").json()
    assert next(x for x in view["categories"] if x["category_id"] == cat["id"])["mode"] == "default"


def test_vendor_with_many_categories(admin, settings):
    """업체 하나가 여러 민원 분류를 기본으로 담당 (예: 동대표 = 기타 + 청소 + 승강기 …)"""
    cats = {c["name"]: c["id"] for c in admin.get("/api/admin/categories").json()["items"]}
    ids = f"{cats['기타']},{cats['청소']},{cats['승강기']}"
    r = admin.post("/api/admin/vendors", data={"name": "여러분류업체", "room_name": "", "category_ids": ids})
    assert r.status_code == 201
    vid = r.json()["id"]
    v = next(x for x in admin.get("/api/admin/vendors").json()["items"] if x["id"] == vid)
    assert set(v["category_names"]) == {"기타", "청소", "승강기"} and len(v["category_ids"]) == 3
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("UPDATE vendors SET room_name = '●여러분류업체' WHERE id = %s", (vid,))
        for cat in ("기타", "청소", "승강기"):
            assert "여러분류업체" in [x["name"] for x in store.target_vendors(cur, "일반방", cat)]
        assert "여러분류업체" not in [x["name"] for x in store.target_vendors(cur, "일반방", "누수")]
    # 수정: 분류 바꾸기 · 모두 빼기
    assert admin.put(f"/api/admin/vendors/{vid}", data={"name": "여러분류업체", "room_name": "●여러분류업체", "category_ids": str(cats["누수"])}).status_code == 200
    v = next(x for x in admin.get("/api/admin/vendors").json()["items"] if x["id"] == vid)
    assert v["category_names"] == ["누수"]
    cnt = {c["name"]: c["vendors"] for c in admin.get("/api/admin/categories").json()["items"]}
    assert cnt["누수"] >= 1
    assert admin.put(f"/api/admin/vendors/{vid}", data={"name": "여러분류업체", "room_name": "●여러분류업체", "category_ids": ""}).status_code == 200
    assert next(x for x in admin.get("/api/admin/vendors").json()["items"] if x["id"] == vid)["category_ids"] == []
    assert admin.post("/api/admin/vendors", data={"name": "x", "category_ids": "1,abc"}).status_code == 422
    assert admin.post("/api/admin/vendors", data={"name": "x", "category_ids": "999999"}).status_code == 422
    admin.delete(f"/api/admin/vendors/{vid}")


def test_reply_ready_rooms_and_manual_send(admin, settings):
    """앱이 보고한 '답장 가능한 방'으로 업체 톡방마다 보낼 수 있음/새 알림 필요 표시, 문구를 직접 보냈으면 보냄 처리"""
    ok_room, wait_room = "●준비테스트CCTV", "●준비테스트누수"
    for room in (ok_room, wait_room):
        _post(room, "기사", "업체 톡방 첫 메시지입니다")
        assert admin.post("/api/admin/vendors/from-room", json={"room_name": room}).status_code == 201

    def states():
        data = admin.get("/api/admin/vendors").json()
        return data, {v["room_name"]: v["reply_ready"] for v in data["items"] if v["room_name"] in (ok_room, wait_room)}

    data, st = states()
    assert data["reply_check"]["checked"] is False and st == {ok_room: "unknown", wait_room: "unknown"}  # 휴대폰 보고 전

    res = TestClient(app).post("/api/replies/ready", headers=KEY, json={"device_id": DEVICE, "rooms": [f"  {ok_room} ", "★어느빌라", ""]})
    assert res.status_code == 200 and res.json()["rooms"] == 2
    data, st = states()
    assert data["reply_check"]["checked"] is True and st == {ok_room: "ready", wait_room: "waiting"}
    assert TestClient(app).post("/api/replies/ready", json={"device_id": DEVICE, "rooms": []}).status_code in (401, 403)

    # 오래된 보고(10분 넘음)는 믿지 않음
    with db.transaction(settings) as conn, conn.cursor() as cur:
        later = store.reply_readiness(cur, store.now_local(settings) + store.READY_FRESH + store.READY_FRESH)
        assert later["checked"] is False and store.ready_state(later, ok_room) == "unknown"
        assert store.ready_state(later, None) is None
        msg = _post("★준비테스트빌라", "301호", "CCTV 안 나와요")
        rid = store.enqueue_reply(cur, msg, DEVICE, wait_room, "[민원 처리 요청] 테스트 문구", store.now_local(settings), kind="vendor", vendor_name="준비테스트누수")

    d = next(x for x in admin.get("/api/admin/vendor-dispatch").json()["items"] if x["id"] == rid)
    assert d["status"] == "pending" and d["reply_ready"] == "waiting" and d["text"] == "[민원 처리 요청] 테스트 문구"
    # 문구를 복사해 직접 보냄 → 보냄 처리, 휴대폰 대기열에서 빠짐
    assert admin.post(f"/api/admin/vendor-dispatch/{rid}/manual").status_code == 200
    d = next(x for x in admin.get("/api/admin/vendor-dispatch").json()["items"] if x["id"] == rid)
    assert d["status"] == "sent" and d["note"] == "관리자가 직접 보냄" and d["reply_ready"] is None
    assert rid not in {p["id"] for p in _pending()}
    assert admin.post(f"/api/admin/vendor-dispatch/{rid}/manual").status_code == 422
    assert admin.post("/api/admin/vendor-dispatch/999999/manual").status_code == 404
    # 휴대폰이 빈 목록을 보고하면(앱 재시작 직후 등) 모두 '새 알림 필요'
    TestClient(app).post("/api/replies/ready", headers=KEY, json={"device_id": DEVICE, "rooms": []})
    assert states()[1] == {ok_room: "waiting", wait_room: "waiting"}
