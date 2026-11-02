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
        return [cp.Verdict(i.id, "누수" in i.text, "누수·하자" if "누수" in i.text else "기타", "누수 민원", 0.9) for i in items]


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
    text = cp.format_vendor_message(cp.DEFAULT_VENDOR_TEMPLATE, vendor="엘레벨레", room="OO아파트", category="누수·하자", summary="천장 누수",
                                    content="물이   새요\n빨리", sender="1203호", at=datetime(2026, 11, 2, 9, 5))
    assert "엘레벨레 담당자님" in text and "'OO아파트' 대화방" in text and "물이 새요 빨리" in text
    assert "- 보낸 사람: 1203호" in text and "- 접수 시각: 11/02 09:05" in text
    assert "{" not in text


def test_complaint_ack_then_vendor_room(admin, settings):
    cat = next(c for c in admin.get("/api/admin/categories").json()["items"] if c["name"] == "누수·하자")
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
    assert group["vendor_dispatch"] == [{"vendor": "엘레벨레", "room": "엘레벨레 업무방", "status": "failed", "note": "휴대폰에 이 방의 답장 가능한 카톡 알림이 없음"}]

    # 이력 · 다시 보내기
    hist = admin.get("/api/admin/vendor-dispatch").json()
    row = next(h for h in hist["items"] if h["message_id"] == mid)
    assert row["complaint_room"] == "전달테스트방" and row["category"] == "누수·하자" and row["status"] == "failed"
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
