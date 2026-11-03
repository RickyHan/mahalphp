"""예약 메시지: 다음 발송 시각 계산 · 웹 API · 시각이 되면 대기열에 넣기"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db, schedules as sc, store
from app.main import app

from .conftest import ADMIN_PASSWORD, ADMIN_USER, API_KEY

KEY = {"X-Api-Key": API_KEY}


def test_next_run_rules():
    base = datetime(2026, 11, 3, 10, 0)  # 화요일 10:00
    assert sc.next_run({"kind": "daily", "run_time": "09:00"}, base) == datetime(2026, 11, 4, 9, 0)
    assert sc.next_run({"kind": "daily", "run_time": "11:30"}, base) == datetime(2026, 11, 3, 11, 30)
    # 매주 월·금 09:00 → 금요일
    assert sc.next_run({"kind": "weekly", "weekdays": "0,4", "run_time": "09:00"}, base) == datetime(2026, 11, 6, 9, 0)
    # 오늘(화) 10:00 정각은 지난 것으로 보고 다음 주
    assert sc.next_run({"kind": "weekly", "weekdays": "1", "run_time": "10:00"}, base) == datetime(2026, 11, 10, 10, 0)
    # 매월 31일 → 11월은 말일(30일)
    assert sc.next_run({"kind": "monthly", "month_day": 31, "run_time": "08:00"}, base) == datetime(2026, 11, 30, 8, 0)
    assert sc.next_run({"kind": "monthly", "month_day": 1, "run_time": "08:00"}, base) == datetime(2026, 12, 1, 8, 0)
    assert sc.next_run({"kind": "once", "run_date": "2026-11-05", "run_time": "14:00"}, base) == datetime(2026, 11, 5, 14, 0)
    assert sc.next_run({"kind": "once", "run_date": "2026-11-01", "run_time": "14:00"}, base) is None
    assert sc.describe({"kind": "weekly", "weekdays": "0,1,2,3,4", "run_time": "09:00"}) == "매주 평일 09:00"
    assert sc.render_text("{날짜}({요일}) {시각} 공지", base) == "11월 3일(화요일) 10:00 공지"


@pytest.fixture
def admin(settings):
    c = TestClient(app)
    assert c.post("/admin/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD}, follow_redirects=False).status_code == 303
    return c


def _post(room, device="sched-phone"):
    res = TestClient(app).post("/api/messages", headers=KEY, data={
        "device_id": device, "room_name": room, "sender_name": "관리소", "message_text": "안녕하세요",
        "is_group_chat": "true", "posted_at": str(int(time.time() * 1000))})
    assert res.status_code == 201


def test_schedule_api_and_run(admin, settings):
    assert TestClient(app).get("/api/admin/schedules").status_code == 401
    _post("★예약시험아파트")
    tomorrow = (datetime.now(settings.timezone) + timedelta(days=1)).strftime("%Y-%m-%d")
    bad = [
        {"room_name": "★예약시험아파트", "text": "x", "kind": "weekly", "run_time": "09:00", "weekdays": []},
        {"room_name": "★예약시험아파트", "text": "x", "kind": "once", "run_time": "09:00", "run_date": "2020-01-01"},
        {"room_name": "★예약시험아파트", "text": "x", "kind": "daily", "run_time": "25:00"},
        {"room_name": "★예약시험아파트", "text": "x", "kind": "monthly", "run_time": "09:00"},
    ]
    for b in bad:
        assert admin.post("/api/admin/schedules", json=b).status_code == 422, b
    r = admin.post("/api/admin/schedules", json={"title": "아침 공지", "room_name": "★예약시험아파트", "text": "{날짜} {요일} 분리수거 날입니다",
                                                "kind": "weekly", "run_time": "08:30", "weekdays": [0, 2, 4]})
    assert r.status_code == 201
    sid = r.json()["id"]
    once = admin.post("/api/admin/schedules", json={"room_name": "★예약시험아파트", "text": "내일 단수 안내", "kind": "once", "run_time": "09:00", "run_date": tomorrow}).json()["id"]
    items = {i["id"]: i for i in admin.get("/api/admin/schedules").json()["items"]}
    assert items[sid]["summary"] == "매주 월·수·금 08:30" and items[sid]["next_run_at"] and items[sid]["room_has_messages"]
    assert items[once]["title"] == "내일 단수 안내" and items[once]["next_run_at"].startswith(tomorrow)

    # 시각이 되면 대기열에 들어가고 휴대폰이 가져간다
    now = store.now_local(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("UPDATE scheduled_messages SET next_run_at = %s WHERE id = %s", (now - timedelta(minutes=1), sid))
    assert sc.run_due(settings, now) == 1
    pending = TestClient(app).get("/api/replies/pending", params={"device_id": "sched-phone"}, headers=KEY).json()["items"]
    mine = [p for p in pending if p["room_name"] == "★예약시험아파트"]
    assert len(mine) == 1 and "분리수거 날입니다" in mine[0]["text"] and "{날짜}" not in mine[0]["text"]
    assert TestClient(app).post(f"/api/replies/{mine[0]['id']}/result", data={"device_id": "sched-phone", "sent": "true"}, headers=KEY).status_code == 200
    item = next(i for i in admin.get("/api/admin/schedules").json()["items"] if i["id"] == sid)
    assert item["last_send"]["status"] == "sent" and item["next_run_at"] > now.isoformat()

    # 서버가 꺼져 있어 30분 넘게 지난 회차는 건너뜀
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("UPDATE scheduled_messages SET next_run_at = %s WHERE id = %s", (now - timedelta(hours=2), sid))
    assert sc.run_due(settings, now) == 0
    item = next(i for i in admin.get("/api/admin/schedules").json()["items"] if i["id"] == sid)
    assert "건너뜀" in item["last_note"] and item["enabled"]

    # 한 번 예약은 보내고 나면 꺼짐
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("UPDATE scheduled_messages SET next_run_at = %s WHERE id = %s", (now, once))
    assert sc.run_due(settings, now) == 1
    item = next(i for i in admin.get("/api/admin/schedules").json()["items"] if i["id"] == once)
    assert item["enabled"] is False and item["next_run_at"] is None

    # 지금 보내기 · 끄기/켜기 · 수정 · 이력 · 삭제
    assert admin.post(f"/api/admin/schedules/{sid}/send-now").status_code == 200
    assert admin.post(f"/api/admin/schedules/{sid}/enabled", json={"enabled": False}).json()["next_run_at"] is None
    # 아직 날짜가 남은 한 번 예약은 다시 켤 수 있고, 지난 날짜면 켤 수 없다
    assert admin.post(f"/api/admin/schedules/{once}/enabled", json={"enabled": True}).json()["next_run_at"].startswith(tomorrow)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("UPDATE scheduled_messages SET run_date = '2020-01-01' WHERE id = %s", (once,))
    assert admin.post(f"/api/admin/schedules/{once}/enabled", json={"enabled": True}).status_code == 422
    assert admin.put(f"/api/admin/schedules/{sid}", json={"room_name": "★예약시험아파트", "text": "매일 공지", "kind": "daily", "run_time": "07:00"}).status_code == 200
    hist = admin.get("/api/admin/schedules/history").json()["items"]
    assert any(h["schedule_id"] == sid for h in hist)
    assert admin.delete(f"/api/admin/schedules/{sid}").status_code == 200
    # 남은 대기 정리
    for p in TestClient(app).get("/api/replies/pending", params={"device_id": "sched-phone"}, headers=KEY).json()["items"]:
        TestClient(app).post(f"/api/replies/{p['id']}/result", data={"device_id": "sched-phone", "sent": "true"}, headers=KEY)
