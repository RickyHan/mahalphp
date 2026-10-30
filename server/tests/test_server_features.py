"""서버로 옮긴 기능: 참여자 이력, Gemini 민원 분류(가짜 클라이언트), 안내 대기열, 민원·통계·설정 API"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import complaints as cp
from app import db, store
from app.main import app

from .conftest import ADMIN_PASSWORD, ADMIN_USER, API_KEY

KEY = {"X-Api-Key": API_KEY}


class FakeGemini:
    """'소음'·'주차' 가 들어가면 민원, 아니면 아님. 호출 기록을 남긴다."""

    def __init__(self, fail: cp.GeminiError | None = None):
        self.calls: list[list[cp.Item]] = []
        self.fail = fail

    def classify(self, items):
        self.calls.append(items)
        if self.fail:
            raise self.fail
        out = []
        for i in items:
            cat = "소음" if "소음" in i.text else "주차" if "주차" in i.text else None
            out.append(cp.Verdict(i.id, cat is not None, cat or "기타", f"{cat} 민원" if cat else "", 0.9))
        return out


def _post(client, room, sender, text, when: datetime, *, group=True, sender_key=None, device="phone-1"):
    data = {
        "device_id": device, "room_name": room, "sender_name": sender, "message_text": text,
        "message_type": "text", "is_group_chat": str(group).lower(), "posted_at": str(int(when.timestamp() * 1000)),
    }
    if sender_key:
        data["sender_key"] = sender_key
    res = client.post("/api/messages", data=data, headers=KEY)
    assert res.status_code == 201, res.text
    return res.json()["id"]


@pytest.fixture
def admin(settings):
    c = TestClient(app)
    assert c.post("/admin/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD}, follow_redirects=False).status_code == 303
    return c


def _now(settings):
    return datetime.now(settings.timezone)


# ---- 순수 로직 ---------------------------------------------------------------------------

def test_eligibility_and_member_event_detection():
    assert cp.is_eligible("text", "주차 문제요", "301호")
    assert not cp.is_eligible("text", "넵 알겠어", "301호")
    assert not cp.is_eligible("text", "[소음] 민원이 접수되었습니다.", cp.SELF_SENDER)
    assert not cp.is_eligible("photo", "사진을 보냈습니다.", "301호")
    assert cp.detect_member_events("버디왕님이 들어왔습니다.") == [("join", "버디왕", None)]
    assert cp.detect_member_events("방장님이 광고봇님을 내보냈습니다.") == [("kick", "광고봇", "방장")]
    assert cp.detect_member_events("홍길동님이 김철수님, 이영희님과 박민수님을 초대했습니다.") == [
        ("invite", "김철수", "홍길동"), ("invite", "이영희", "홍길동"), ("invite", "박민수", "홍길동")]
    assert cp.detect_member_events("다들 나갔습니까") == []


def test_parse_response_and_grouping_and_stats():
    body = {"candidates": [{"content": {"parts": [{"text": '{"results":[{"id":1,"is_complaint":true,"category":"없는분류","summary":"x","confidence":7},{"id":9,"is_complaint":true}]}'}]}}]}
    v = cp.parse_response(body, {1, 2})
    assert [(x.id, x.category, x.confidence) for x in v] == [(1, "기타", 1.0)]
    with pytest.raises(ValueError):
        cp.parse_response({"candidates": []}, {1})

    t0 = datetime(2025, 10, 30, 9, 0)
    row = lambda i, s, c, m: {"id": i, "room_name": "A", "sender_name": s, "category": c, "posted_at": t0 + timedelta(minutes=m),
                              "message_text": "t", "summary": "", "confidence": 0.5, "reply_status": 0, "reply_note": None}
    groups = cp.group_complaints([row(1, "301", "소음", 0), row(2, "301", "소음", 20), row(3, "301", "소음", 51), row(4, "502", "소음", 1)])
    assert sorted(len(g.items) for g in groups) == [1, 1, 2]
    stats = cp.compute_stats(groups, "today", t0 + timedelta(hours=5))
    assert stats["issues"] == 3 and stats["messages"] == 4 and stats["repeated_issues"] == 1
    assert stats["by_category"] == [["소음", 3]] and stats["hourly"][9] == 3


# ---- 수집 → 참여자 이력 ----------------------------------------------------------------------

def test_ingest_tracks_members_and_events(settings, admin):
    c = TestClient(app)
    now = _now(settings)
    room = "참여자테스트방"
    _post(c, room, "버디왕", "안녕하세요", now - timedelta(minutes=5), sender_key="u1")
    _post(c, room, "⛳버디킹⛳", "이름 바꿨어요", now - timedelta(minutes=4), sender_key="u1")
    _post(c, room, "이글", "반가워요", now - timedelta(minutes=3))
    _post(c, room, "이글", "신입님이 들어왔습니다.", now - timedelta(minutes=2))
    _post(c, "1:1방", "홍길동", "개인 대화", now, group=False)

    events = admin.get("/api/admin/member-events", params={"room": room}).json()
    assert [e["type"] for e in events["items"]] == ["join", "first_seen", "rename", "first_seen"]
    rename = next(e for e in events["items"] if e["type"] == "rename")
    assert (rename["old_name"], rename["member"]) == ("버디왕", "⛳버디킹⛳")
    assert room in events["rooms"] and "1:1방" not in events["rooms"]

    members = admin.get("/api/admin/members", params={"room": room}).json()["items"]
    assert {m["display_name"]: m["keyed"] for m in members} == {"이글": False, "⛳버디킹⛳": True}


# ---- 민원 분류 → 안내 대기열 → 휴대폰 전송 결과 ------------------------------------------------

def test_classify_enqueues_one_reply_per_issue_and_phone_reports(settings, admin):
    c = TestClient(app)
    now = _now(settings)
    room = "민원테스트방"
    first = _post(c, room, "301호", "윗집 소음이 너무 심해요", now - timedelta(minutes=10))
    _post(c, room, "301호", "아직도 소음이 계속돼요", now - timedelta(minutes=5))   # 같은 문제 → 안내 생략
    _post(c, room, "1203호", "지하 주차장 이중주차 차 빼주세요", now - timedelta(minutes=3))
    _post(c, room, "502호", "좋은 아침입니다 여러분", now - timedelta(minutes=2))      # 민원 아님
    _post(c, room, "805호", "옛날 소음 민원입니다", now - timedelta(hours=2))          # 30분 지남 → 안내 생략
    _post(c, room, "1102호", "넵", now)                                              # 5자 미만 → 대상 아님

    fake = FakeGemini()
    while store.classify_once(settings, fake)["done"]:
        pass
    sent_texts = [i.text for call in fake.calls for i in call]
    assert "넵" not in sent_texts

    pending = c.get("/api/replies/pending", params={"device_id": "phone-1"}, headers=KEY).json()["items"]
    mine = [p for p in pending if p["room_name"] == room]
    assert sorted(p["text"] for p in mine) == ["[소음] 민원이 접수되었습니다.", "[주차] 민원이 접수되었습니다."]
    assert c.get("/api/replies/pending", params={"device_id": "other-phone"}, headers=KEY).json()["items"] == []
    assert c.get("/api/replies/pending", params={"device_id": "phone-1"}).status_code == 401

    noise = next(p for p in mine if "소음" in p["text"])
    parking = next(p for p in mine if "주차" in p["text"])
    assert c.post(f"/api/replies/{noise['id']}/result", data={"device_id": "phone-1", "sent": "true"}, headers=KEY).status_code == 200
    assert c.post(f"/api/replies/{parking['id']}/result", data={"device_id": "phone-1", "sent": "false", "note": "알림 만료"}, headers=KEY).status_code == 200
    assert c.post(f"/api/replies/{noise['id']}/result", data={"device_id": "phone-1", "sent": "true"}, headers=KEY).status_code == 404

    data = admin.get("/api/admin/complaints").json()
    groups = [g for g in data["items"] if g["room_name"] == room]
    by_sender = {g["sender_name"]: g for g in groups}
    assert by_sender["301호"]["count"] == 2 and by_sender["301호"]["reply_status"] == 1
    assert by_sender["1203호"]["reply_status"] == -1 and by_sender["1203호"]["reply_note"] == "알림 만료"
    assert by_sender["805호"]["reply_status"] == 3
    assert "502호" not in by_sender

    detail = admin.get("/api/admin/complaints/group", params={"ids": ",".join(map(str, by_sender["301호"]["ids"]))}).json()["items"]
    assert [d["id"] for d in detail][0] == first and len(detail) == 2

    stats = admin.get("/api/admin/stats", params={"period": "all"}).json()
    assert stats["issues"] >= 3 and stats["reply_sent"] >= 1


def test_gemini_errors_retry_or_stop(settings):
    c = TestClient(app)
    _post(c, "오류테스트방", "301호", "엘리베이터 소음이 심해요", _now(settings))
    assert store.classify_once(settings, FakeGemini(cp.GeminiError("503", retry=True))) == {"done": 0, "retry": True, "reason": "503"}
    assert store.classify_once(settings, FakeGemini(cp.GeminiError("키 오류", retry=False)))["stop"] is True
    assert store.classify_once(settings, FakeGemini())["done"] >= 1   # 다음 시도에 정상 분류


def test_settings_roundtrip_and_disable(settings, admin):
    got = admin.get("/api/admin/settings").json()
    assert got["gemini_configured"] is True and got["reply_template"] == cp.DEFAULT_REPLY_TEMPLATE
    put = admin.put("/api/admin/settings", json={"auto_reply_enabled": False, "reply_template": "{보낸사람}님 [{분류}] 접수", "gemini_model": "gemini-x"}).json()
    assert put["auto_reply_enabled"] is False and put["gemini_model"] == "gemini-x"
    assert admin.put("/api/admin/settings", json={"gemini_model": "bad model!"}).status_code == 422

    admin.put("/api/admin/settings", json={"complaint_enabled": False})
    assert store.classify_once(settings, FakeGemini()) == {"done": 0, "reason": "disabled"}
    admin.put("/api/admin/settings", json={"complaint_enabled": True, "auto_reply_enabled": True, "reply_template": "", "gemini_model": cp.DEFAULT_MODEL})
    assert admin.get("/api/admin/settings").json()["reply_template"] == cp.DEFAULT_REPLY_TEMPLATE
    assert TestClient(app).get("/api/admin/settings").status_code == 401


def test_retry_failed_resets(settings, admin):
    c = TestClient(app)
    mid = _post(c, "실패재시도방", "301호", "복도 소음 너무 심합니다", _now(settings))
    with db.transaction(settings) as conn, conn.cursor() as cur:
        store.mark_failed(cur, [mid], "응답에 결과 없음", store.now_local(settings))
    assert admin.get("/api/admin/complaints").json()["counts"]["failed"] >= 1
    assert admin.post("/api/admin/classify", params={"retry_failed": "true"}).json()["reset"] >= 1
