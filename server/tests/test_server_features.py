"""서버로 옮긴 기능: 참여자 이력, Gemini 민원 분류(가짜 클라이언트), 안내 대기열, 민원·통계·설정 API"""

from __future__ import annotations

import json

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
    groups = cp.group_complaints([row(1, "301", "소음", 0), row(2, "301", "소음", 20), row(3, "301", "소음", 51), row(4, "502", "소음", 1)], timedelta(minutes=30))
    # 기본 15분: 0분·20분은 서로 다른 문제
    assert sorted(len(g.items) for g in cp.group_complaints([row(1, "301", "소음", 0), row(2, "301", "소음", 20)])) == [1, 1]
    assert sorted(len(g.items) for g in groups) == [1, 1, 2]
    stats = cp.compute_stats(groups, "today", t0 + timedelta(hours=5))
    assert stats["issues"] == 3 and stats["messages"] == 4 and stats["repeated_issues"] == 1
    assert stats["by_category"] == [["소음", 3]] and stats["hourly"][9] == 3
    # 2025-10-30 은 목요일(3): 요일×시간, 일별·방별 분류 세부, 직전 기간 비교
    assert stats["weekday_hour"][3][9] == 3 and sum(map(sum, stats["weekday_hour"])) == 3
    assert stats["daily_detail"] == [["10/30", 3, {"소음": 3}, 3]]
    assert stats["room_detail"] == [["A", 3, {"소음": 3}]] and stats["prev_issues"] == 0
    assert cp.compute_stats(groups, "all", t0 + timedelta(hours=5))["prev_issues"] is None


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


def test_admin_page_has_new_tabs(admin):
    page = admin.get("/admin").text
    for tab in ("complaints", "stats", "members", "settings"):
        assert f'data-tab="{tab}"' in page and f'id="view-{tab}"' in page
    assert "/admin/static/admin-ops.js" in page
    assert admin.get("/admin/static/admin-ops.js").status_code == 200


def test_same_text_in_many_rooms_all_kept(admin, settings):
    """같은 사람이 같은 내용을 같은 시각에 여러 방에 보내도 방마다 모두 저장 (방이 다르면 다른 메시지)"""
    c = TestClient(app)
    when = _now(settings)
    rooms = ["★동시공지A", "★동시공지B", "★동시공지C"]
    ids = [_post(c, r, "관리소", "내일 오전 10시 단수 예정입니다", when) for r in rooms]
    assert len(set(ids)) == 3
    again = _post(c, rooms[0], "관리소", "내일 오전 10시 단수 예정입니다", when)  # 같은 방·같은 글을 다시 올리면 중복
    assert again == ids[0]


def test_sync_request_reaches_phone(admin, settings):
    """관리자 웹 '지금 동기화' → 휴대폰이 30초마다 묻는 응답에 요청 시각이 실림, 휴대폰 연락 기록"""
    before = admin.get("/api/admin/sync").json()
    assert TestClient(app).post("/api/admin/sync").status_code == 401
    r = admin.post("/api/admin/sync").json()
    assert r["requested_at"] > (before["requested_at"] or 0)
    res = TestClient(app).get("/api/replies/pending", params={"device_id": "sync-phone"}, headers=KEY).json()
    assert res["sync_requested_at"] == r["requested_at"]
    devs = {d["device_id"]: d for d in admin.get("/api/admin/sync").json()["devices"]}
    assert devs["sync-phone"]["last_seen"]


def test_rooms_sorted_by_latest_message_time(admin, settings):
    """대화방 목록: 늦게 올라온 옛 메시지가 아니라 메시지 시각이 최근인 방부터"""
    c = TestClient(app)
    now = _now(settings)
    _post(c, "정렬테스트-최근방", "가", "지금 막 보낸 메시지입니다", now - timedelta(minutes=1))
    _post(c, "정렬테스트-옛방", "나", "이틀 전 메시지가 늦게 올라옴", now - timedelta(days=2))  # 나중에 받음(id 가 더 큼)
    items = [r for r in admin.get("/api/admin/rooms", params={"q": "정렬테스트"}).json()["items"]]
    assert [r["room_name"] for r in items] == ["정렬테스트-최근방", "정렬테스트-옛방"]
    _post(c, "정렬테스트-최근방", "가", "더 예전 메시지가 늦게 도착", now - timedelta(days=5))
    top = admin.get("/api/admin/rooms", params={"q": "정렬테스트"}).json()["items"][0]
    assert top["room_name"] == "정렬테스트-최근방" and top["last_message"] == "지금 막 보낸 메시지입니다"  # 미리보기도 최신 시각 메시지


def test_vendor_rooms_are_not_classified(admin, settings):
    """업체 톡방(● 로 시작 · 업체에 연결된 방)의 메시지는 민원 분류·접수 안내 대상이 아님"""
    c = TestClient(app)
    now = _now(settings)
    vendor_msg = _post(c, "●분류제외누수업체", "업체", "누수 소음 현장 확인 부탁드립니다", now)
    linked = admin.post("/api/admin/vendors", data={"name": "분류제외연결업체"}).json()["id"]
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("UPDATE vendors SET room_name = '분류제외연결방' WHERE id = %s", (linked,))
    linked_msg = _post(c, "분류제외연결방", "업체", "주차 소음 처리 완료했습니다", now)
    resident = _post(c, "★분류제외빌라", "301호", "윗집 소음이 너무 심해요", now)
    gem = FakeGemini()
    store.classify_once(settings, client=gem)
    sent = {i.id for batch in gem.calls for i in batch}
    assert resident in sent and vendor_msg not in sent and linked_msg not in sent
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT message_id, verdict, reply_note FROM complaint_check WHERE message_id IN (%s, %s)", (vendor_msg, linked_msg))
        rows = cur.fetchall()
        cur.execute("SELECT COUNT(*) AS n FROM reply_queue WHERE message_id IN (%s, %s)", (vendor_msg, linked_msg))
        assert cur.fetchone()["n"] == 0
    assert len(rows) == 2 and all(r["verdict"] == 0 and "업체 톡방" in r["reply_note"] for r in rows)


def test_existing_vendor_room_complaints_are_cleared(admin, settings):
    """예전에 업체 톡방에서 민원으로 잡힌 것: 민원에서 빼고 안 나간 접수 안내는 취소"""
    c = TestClient(app)
    mid = _post(c, "●정리대상업체", "업체", "누수 현장 확인했습니다", _now(settings))
    with db.transaction(settings) as conn, conn.cursor() as cur:
        now = store.now_local(settings)
        cur.execute("INSERT INTO complaint_check (message_id, verdict, category, checked_at) VALUES (%s, 1, '누수', %s)", (mid, now))
        store.enqueue_reply(cur, mid, "phone-1", "●정리대상업체", "[누수] 민원이 접수되었습니다.", now)
    store.classify_once(settings, client=FakeGemini())
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT verdict FROM complaint_check WHERE message_id = %s", (mid,))
        assert cur.fetchone()["verdict"] == 0
        cur.execute("SELECT status FROM reply_queue WHERE message_id = %s", (mid,))
        assert [r["status"] for r in cur.fetchall()] == ["expired"]


def _post_key(c, room, sender, text, when, key):
    return _post(c, room, sender, text, when, sender_key=key)


def test_unknown_sender_uses_real_name_by_key(admin, settings):
    """'(알 수 없음)' 으로 온 메시지: 같은 고유 키의 실제 이름으로 (앞뒤 어느 쪽에 이름이 와도), 키가 없으면 그대로"""
    c = TestClient(app)
    now = _now(settings)
    room = "●알수없음테스트업체"
    early = _post_key(c, room, "(알 수 없음)", "안녕하세요 11월 05일 청소마감현황입니다", now - timedelta(minutes=5), "key-cleaner")
    nokey = _post(c, room, "(알 수 없음)", "키 없는 메시지입니다 확인", now - timedelta(minutes=4))
    _post_key(c, room, "김청소", "사진 확인 부탁드립니다", now - timedelta(minutes=3), "key-cleaner")   # 이름이 제대로 옴 → 지난 것도 채움
    late = _post_key(c, room, "(알 수 없음)", "추가로 보냅니다 확인 부탁", now - timedelta(minutes=2), "key-cleaner")  # 이후 것도
    names = {m["id"]: m["sender_name"] for m in admin.get("/api/admin/messages", params={"room": room}).json()["items"]}
    assert names[early] == "김청소" and names[late] == "김청소" and names[nokey] == "(알 수 없음)"
    # 같은 메시지를 휴대폰이 다시 보내도 중복 저장되지 않음 (원래 이름으로 중복 판단)
    again = _post_key(c, room, "(알 수 없음)", "추가로 보냅니다 확인 부탁", now - timedelta(minutes=2), "key-cleaner")
    assert again == late
    # 참여자 이력에 '(알 수 없음)' 이 이름으로 남지 않음
    members = admin.get("/api/admin/members", params={"room": room}).json()["items"]
    assert all(m["display_name"] != "(알 수 없음)" for m in members)

    # 서버 시작 때 일괄 정리: 예전 데이터
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("UPDATE kakao_messages SET sender_name = '(알 수 없음)' WHERE id = %s", (early,))
        assert store.fill_all_unknown(cur) >= 1
        cur.execute("SELECT sender_name FROM kakao_messages WHERE id = %s", (early,))
        assert cur.fetchone()["sender_name"] == "김청소"


def test_unknown_sender_fixed_when_message_reappears(admin, settings):
    """카톡이 다음 알림에 지난 메시지를 실제 이름으로 다시 보여 주면: 새로 저장하지 않고 그 기록의 이름을 고침"""
    c = TestClient(app)
    when = _now(settings) - timedelta(minutes=3)
    room = "●다시보임테스트업체"
    first = _post(c, room, "(알 수 없음)", "안녕하세요 오늘 청소 마감했습니다", when, sender_key="k-room-a")
    again = _post(c, room, "블루오션 김반장", "안녕하세요 오늘 청소 마감했습니다", when, sender_key="k-room-a")
    assert again == first
    items = admin.get("/api/admin/messages", params={"room": room}).json()["items"]
    assert [(m["id"], m["sender_name"]) for m in items] == [(first, "블루오션 김반장")]
    # 반대로 실제 이름으로 저장된 뒤 '(알 수 없음)'으로 다시 와도 새로 저장하지 않음
    back = _post(c, room, "(알 수 없음)", "안녕하세요 오늘 청소 마감했습니다", when)
    assert back == first and len(admin.get("/api/admin/messages", params={"room": room}).json()["items"]) == 1
    # 이름만 다른 진짜 다른 사람(둘 다 실제 이름)은 따로 저장
    other = _post(c, room, "다른사람", "안녕하세요 오늘 청소 마감했습니다", when)
    assert other != first


def test_staff_nickname_not_classified(admin, settings):
    """'동대표1'·'동대표 7'·'(주)동대표3' 같은 직원 닉네임의 메시지는 민원 분류 안 함 (옵션으로 켜고 끔)"""
    assert store.is_staff_sender({"staff_exclude_enabled": "1", "staff_prefixes": "동대표"}, "동대표7")
    conf = {"staff_exclude_enabled": "1", "staff_prefixes": "동대표, 관리소"}
    for name in ("동대표1", "동대표 12", "(주)동대표3", "㈜동대표4", "관리소2"):
        assert store.is_staff_sender(conf, name), name
    for name in ("동대표", "동대표님", "302호 동대표", "홍길동", "동대표1호"):
        assert not store.is_staff_sender(conf, name), name
    assert not store.is_staff_sender({"staff_exclude_enabled": "0", "staff_prefixes": "동대표"}, "동대표1")

    s = admin.get("/api/admin/settings").json()
    assert s["staff_exclude_enabled"] is True and s["staff_prefixes"] == "동대표"
    c = TestClient(app)
    now = _now(settings)
    staff = _post(c, "★직원제외빌라", "동대표7", "윗집 소음 민원 확인했습니다 조치하겠습니다", now)
    resident = _post(c, "★직원제외빌라", "301호", "윗집 소음이 너무 심해요", now)
    gem = FakeGemini()
    store.classify_once(settings, client=gem)
    sent = {i.id for batch in gem.calls for i in batch}
    assert resident in sent and staff not in sent
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT verdict, reply_note FROM complaint_check WHERE message_id = %s", (staff,))
        row = cur.fetchone()
    assert row["verdict"] == 0 and "직원 닉네임" in row["reply_note"]

    # 끄면 다시 분류 대상
    assert admin.put("/api/admin/settings", json={"staff_exclude_enabled": False, "staff_prefixes": " 동대표 , 동대표,관리소 "}).json()["staff_prefixes"] == "동대표, 관리소"
    staff2 = _post(c, "★직원제외빌라", "동대표8", "주차 소음 확인 부탁드립니다", now)
    gem2 = FakeGemini()
    store.classify_once(settings, client=gem2)
    assert staff2 in {i.id for batch in gem2.calls for i in batch}
    admin.put("/api/admin/settings", json={"staff_exclude_enabled": True, "staff_prefixes": "동대표"})


def test_classification_gets_room_context(admin, settings):
    """민원 판정에 같은 방의 직전 대화(맥락)를 함께 보냄 + 비판·하소연은 민원 아님 기준이 지시문에 있음"""
    c = TestClient(app)
    now = _now(settings)
    room = "★맥락테스트빌라"
    _post(c, room, "동대표4", "이번 달 공사비 분담 안내드립니다", now - timedelta(minutes=10))
    _post(c, room, "302호", "공사비가 너무 비싼 것 같아요", now - timedelta(minutes=5))
    target = _post(c, room, "401호", "날림공사라 돈만 내고 소송은 왜 안 하나요 주차장도 새는데", now - timedelta(minutes=1))
    _post(c, "★다른방맥락", "101호", "다른 방 메시지는 맥락에 안 들어감", now - timedelta(minutes=2))
    gem = FakeGemini()
    store.classify_once(settings, client=gem)
    item = next(i for batch in gem.calls for i in batch if i.id == target)
    assert item.context == ("동대표4: 이번 달 공사비 분담 안내드립니다", "302호: 공사비가 너무 비싼 것 같아요")
    body = cp.request_body([item])
    sent = json.loads(body["contents"][0]["parts"][0]["text"])[0]
    assert sent["context"][-1] == "302호: 공사비가 너무 비싼 것 같아요" and sent["text"].startswith("날림공사")
    prompt = body["systemInstruction"]["parts"][0]["text"]
    assert "비판·항의·하소연" in prompt and "context" in prompt and "지금 겪고 있는" in prompt
    # 맥락이 없으면 context 칸을 넣지 않음
    assert "context" not in json.loads(cp.request_body([cp.Item(1, "r", "s", "t")])["contents"][0]["parts"][0]["text"])[0]


def _post_room(c, room, sender, text, when, key, trusted=True):
    data = {"device_id": "phone-1", "room_name": room, "sender_name": sender, "message_text": text, "message_type": "text",
            "is_group_chat": "true", "posted_at": str(int(when.timestamp() * 1000)), "room_key": key, "room_trusted": str(trusted).lower()}
    res = c.post("/api/messages", data=data, headers=KEY)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_room_key_fixes_wrong_room_and_follows_rename(admin, settings):
    """채팅방 번호: 방 이름을 못 읽어 보낸 사람 이름으로 들어간 메시지를 바로잡고, 방 이름이 바뀌면 연결까지 따라감"""
    c = TestClient(app)
    now = _now(settings)
    # 1) 처음부터 방 이름을 못 읽음(보낸 사람 이름) → 나중에 같은 번호로 진짜 이름이 오면 바로잡음
    wrong = _post_room(c, "702호 박용덕", "702호 박용덕", "걍 자체적으로 해볼께요", now - timedelta(minutes=9), "chat-777", trusted=False)
    ok = _post_room(c, "★번호테스트빌라/12세대", "702호 박용덕", "출장은 안 오셔도 될듯", now - timedelta(minutes=8), "chat-777")
    rooms = {m["id"]: m["room_name"] for m in admin.get("/api/admin/messages", params={"room": "★번호테스트빌라/12세대"}).json()["items"]}
    assert rooms == {wrong: "★번호테스트빌라/12세대", ok: "★번호테스트빌라/12세대"}
    # 같은 메시지가 진짜 방 이름으로 다시 와도 중복 저장 안 함
    again = _post_room(c, "★번호테스트빌라/12세대", "702호 박용덕", "걍 자체적으로 해볼께요", now - timedelta(minutes=9), "chat-777")
    assert again == wrong
    # 2) 이후 방 이름을 못 읽은 알림은 알고 있는 이름으로
    later = _post_room(c, "702호 박용덕", "702호 박용덕", "사진 확인 부탁", now - timedelta(minutes=7), "chat-777", trusted=False)
    assert next(m for m in admin.get("/api/admin/messages", params={"room": "★번호테스트빌라/12세대"}).json()["items"] if m["id"] == later)

    # 3) 방 이름 변경(12세대 → 13세대): 지난 메시지·건물 연결·업체·예약이 새 이름으로
    assert admin.post("/api/admin/apartments", json={"name": "번호테스트빌라"}).status_code == 201
    b = next(a for a in admin.get("/api/admin/apartments").json()["items"] if a["name"] == "번호테스트빌라")
    assert admin.put(f"/api/admin/apartments/{b['id']}/room", json={"room_name": "★번호테스트빌라/12세대"}).status_code == 200
    renamed = _post_room(c, "★번호테스트빌라/13세대", "302호", "엘리베이터 소리가 커요", now - timedelta(minutes=1), "chat-777")
    items = admin.get("/api/admin/messages", params={"room": "★번호테스트빌라/13세대"}).json()["items"]
    assert {m["id"] for m in items} >= {wrong, ok, later, renamed}
    assert admin.get("/api/admin/messages", params={"room": "★번호테스트빌라/12세대"}).json()["items"] == []
    b = next(a for a in admin.get("/api/admin/apartments").json()["items"] if a["id"] == b["id"])
    assert b["room_name"] == "★번호테스트빌라/13세대"
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM apartments WHERE id = %s", (b["id"],))


def test_teach_not_complaint_examples(admin, settings, monkeypatch):
    """민원 아님 학습: 기본 사례 등록·기존 오분류 바로잡기, 대시보드 '민원 아님' 버튼, 지시문에 사례 포함"""
    from app import examples

    c = TestClient(app)
    now = _now(settings)
    room = "★학습테스트빌라"
    parking = _post(c, room, "301호", "203호님 차량 2대 주차하셨는데 1대만 해주시기 바랍니다. 1대 주차 원칙입니다", now - timedelta(minutes=3))
    noise = _post(c, room, "402호", "윗집 소음 때문에 못 살겠어요 그냥 넋두리입니다", now - timedelta(minutes=2))
    store.classify_once(settings, client=FakeGemini())
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT verdict FROM complaint_check WHERE message_id = %s", (parking,))
        assert cur.fetchone()["verdict"] == 1  # 학습 전: 잘못 민원으로 잡힘
        cur.execute("DELETE FROM app_settings WHERE name = 'examples_seed'")

    # 1) 기본 사례 등록 + 이미 잘못 잡힌 같은 내용 바로잡기 (한 번만)
    assert examples.seed_examples(settings) == 1
    assert examples.seed_examples(settings) is None
    items = admin.get("/api/admin/examples").json()["items"]
    assert any("역류 원인" in x["text"] for x in items) and any("1대 주차 원칙" in x["text"] for x in items)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT verdict, reply_note FROM complaint_check WHERE message_id = %s", (parking,))
        row = cur.fetchone()
        assert row["verdict"] == 0 and "민원 아님" in row["reply_note"]
        cur.execute("SELECT status FROM reply_queue WHERE message_id = %s", (parking,))
        assert all(r["status"] != "pending" for r in cur.fetchall())

    # 2) 대시보드 '민원 아님' 버튼
    assert any(noise in g["ids"] for g in admin.get("/api/admin/complaints").json()["items"])
    r = admin.post("/api/admin/complaints/not-complaint", json={"ids": [noise], "note": "하소연"})
    assert r.status_code == 200 and r.json()["fixed"] == 1
    assert not any(noise in g["ids"] for g in admin.get("/api/admin/complaints").json()["items"])
    assert admin.post("/api/admin/complaints/not-complaint", json={"ids": [noise]}).status_code == 404  # 이미 바로잡음
    top = admin.get("/api/admin/examples").json()["items"][0]
    assert top["text"].startswith("윗집 소음") and top["note"] == "하소연" and top["is_complaint"] is False

    # 3) 직접 추가 · 같은 문장은 한 번만 · 삭제
    assert admin.post("/api/admin/examples", json={"text": "  엘리베이터  멈췄어요 ", "is_complaint": True}).status_code == 201
    admin.post("/api/admin/examples", json={"text": "엘리베이터 멈췄어요", "is_complaint": True, "note": "고장 신고"})
    added = [x for x in admin.get("/api/admin/examples").json()["items"] if x["text"] == "엘리베이터 멈췄어요"]
    assert len(added) == 1 and added[0]["note"] == "고장 신고"
    assert admin.delete(f"/api/admin/examples/{added[0]['id']}").status_code == 200
    assert admin.delete(f"/api/admin/examples/{added[0]['id']}").status_code == 404

    # 4) 분류할 때 사례가 Gemini 지시문에 들어감
    captured = {}

    class Capture(FakeGemini):
        def __init__(self, *a, **kw):
            super().__init__()
            captured.update(kw)

    monkeypatch.setattr(cp, "GeminiClient", Capture)
    _post(c, room, "502호", "현관 자동문이 안 열려요", now)
    store.classify_once(settings)
    taught = captured["examples"]
    assert taught[0][0].startswith("윗집 소음") and any("1대 주차 원칙" in t for t, _, _ in taught)
    prompt = cp.build_prompt(None, taught)
    assert "관리자가 직접 바로잡은 판정 사례" in prompt and '[민원 아님] "203호님 차량 2대' in prompt
    assert "입주민끼리 서로에게 하는 요청" in prompt and "미팅" in prompt
    assert "관리자가 직접 바로잡은" not in cp.build_prompt()
