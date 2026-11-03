"""FAQ 자동 답변: 건물 채팅방만 · 같은 답변 반복 생략 · 접수 안내 대신 · 켜기/끄기 · 처음 FAQ 등록"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import complaints as cp
from app import db, faq, store
from app.main import app

from .conftest import ADMIN_PASSWORD, ADMIN_USER, API_KEY

KEY = {"X-Api-Key": API_KEY}


@pytest.fixture
def admin(settings):
    c = TestClient(app)
    assert c.post("/admin/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD}, follow_redirects=False).status_code == 303
    return c


def _now(settings):
    return datetime.now(settings.timezone)


def _post(room, sender, text, when, device="faq-phone"):
    res = TestClient(app).post("/api/messages", headers=KEY, data={
        "device_id": device, "room_name": room, "sender_name": sender, "message_text": text,
        "message_type": "text", "is_group_chat": "true", "posted_at": str(int(when.timestamp() * 1000))})
    assert res.status_code == 201, res.text
    return res.json()["id"]


class FakeGemini:
    """'엘리베이터'가 들어가면 승강기 민원 + 승강기 FAQ, '계량기'는 민원 아님 + 계량기 FAQ"""

    def __init__(self, ids):
        self.ids = ids
        self.faq_calls = []

    def classify(self, items):
        return [cp.Verdict(i.id, "엘리베이터" in i.text, "승강기" if "엘리베이터" in i.text else "기타", "승강기 고장", 0.9) for i in items]

    def match_faq(self, items, faqs):
        self.faq_calls.append(([i.room for i in items], [f[0] for f in faqs]))
        out = []
        for i in items:
            if "엘리베이터" in i.text and self.ids["lift"] in [f[0] for f in faqs]:
                out.append(cp.FaqMatch(i.id, self.ids["lift"], 0.95))
            elif "계량기" in i.text:
                out.append(cp.FaqMatch(i.id, self.ids["meter"], 0.9))
        return out


def test_parse_faq_response():
    body = {"candidates": [{"content": {"parts": [{"text": json.dumps({"results": [
        {"id": 1, "faq_id": 10, "confidence": 0.9},
        {"id": 2, "faq_id": 10, "confidence": 0.5},     # 확신 낮음 → 제외
        {"id": 3, "faq_id": 99, "confidence": 0.99},    # 없는 FAQ → 제외
        {"id": 4, "faq_id": None, "confidence": 0.9},   # 해당 없음
        {"id": 7, "faq_id": 10, "confidence": 0.9},     # 요청하지 않은 id
    ]})}]}}]}
    assert cp.parse_faq_response(body, {1, 2, 3, 4}, {10}) == [cp.FaqMatch(1, 10, 0.9)]
    prompt = cp.build_faq_prompt([(10, "승강기", "승강기 고장 / 운행정지")])
    assert "10. [승강기] 승강기 고장 / 운행정지" in prompt and '"faq_id"' in prompt


def test_faq_answers_only_building_rooms(admin, settings):
    ids = {}
    for key, cat, q, a in (("lift", "승강기", "승강기 고장 / 운행정지", "승강기 업체 점검접수 하겠습니다."),
                           ("meter", "계량기문의", "수도 계량기 어디 있어요?", "세대 내 복도 쪽에 있습니다."),
                           ("off", "기타", "꺼 둔 문항", "보내지 않음")):
        res = admin.post("/api/admin/faq", json={"category": cat, "question": q, "answer": a, "enabled": key != "off"})
        assert res.status_code == 201
        ids[key] = res.json()["id"]
    now = _now(settings)
    # 건물 채팅방(★) · 업체 방(●) · 건물이 아닌 방
    for room in ("★FAQ빌라", "●FAQ승강기업체", "FAQ일반방"):
        _post(room, "관리", "처음 인사드립니다 반갑습니다", now)
    admin.post("/api/admin/apartments/sync")
    admin.post("/api/admin/vendors/sync")
    gem = FakeGemini(ids)
    store.classify_once(settings, client=gem)  # 인사 메시지 정리

    lift = _post("★FAQ빌라", "301호", "엘리베이터가 멈췄어요 확인 부탁드려요", now)
    _post("●FAQ승강기업체", "기사", "엘리베이터 점검 다녀왔습니다", now)
    _post("FAQ일반방", "누군가", "엘리베이터가 또 멈췄네요 확인 바랍니다", now)
    meter = _post("★FAQ빌라", "502호", "수도 계량기가 어디에 있나요?", now)
    r = store.classify_once(settings, client=gem)
    assert r["faq"] == 2
    rooms, faq_ids = gem.faq_calls[-1]
    assert set(rooms) == {"★FAQ빌라"}                       # 업체 방·일반 방은 판단하지 않음
    assert ids["off"] not in faq_ids                        # 꺼 둔 문항은 빠짐

    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT message_id, kind, text FROM reply_queue WHERE room_name = '★FAQ빌라' ORDER BY id")
        rows = cur.fetchall()
        cur.execute("SELECT reply_status, reply_note FROM complaint_check WHERE message_id = %s", (lift,))
        check = cur.fetchone()
    assert [(x["message_id"], x["kind"]) for x in rows] == [(lift, "faq"), (meter, "faq")]  # 접수 안내(ack) 대신 FAQ 답변
    assert rows[0]["text"] == "승강기 업체 점검접수 하겠습니다."
    assert check["reply_status"] == 4 and "FAQ" in check["reply_note"]

    # 같은 방 · 같은 질문은 묶음 시간 안에 다시 보내지 않음
    again = _post("★FAQ빌라", "401호", "엘리베이터 아직도 안 돼요 언제 고쳐요", now)
    store.classify_once(settings, client=gem)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM reply_queue WHERE message_id = %s AND kind = 'faq'", (again,))
        assert cur.fetchone()["n"] == 0  # (다른 세대의 새 민원이라 일반 접수 안내는 나갈 수 있음)
        # 휴대폰이 보내고 결과 보고
        items = store.pending_replies(cur, "faq-phone", store.now_local(settings))
        faq_reply = next(i for i in items if i["text"] == "승강기 업체 점검접수 하겠습니다.")
        assert store.finish_reply(cur, faq_reply["id"], "faq-phone", True, None, store.now_local(settings))
    hist = admin.get("/api/admin/faq/history").json()["items"]
    assert hist[0]["note"] and hist[0]["status"] is None  # 생략된 건
    assert any(h["status"] == "sent" and h["room_name"] == "★FAQ빌라" for h in hist)
    listing = admin.get("/api/admin/faq").json()
    assert listing["enabled"] is True and next(i for i in listing["items"] if i["id"] == ids["lift"])["sent"] == 1

    # 끄면 판단하지 않음
    assert admin.put("/api/admin/faq/settings", json={"enabled": False}).json()["enabled"] is False
    calls = len(gem.faq_calls)
    _post("★FAQ빌라", "101호", "수도 계량기 위치 알려주세요 부탁드려요", now)
    store.classify_once(settings, client=gem)
    assert len(gem.faq_calls) == calls
    assert admin.put("/api/admin/faq/settings", json={"enabled": True}).status_code == 200

    # 수정·검증·삭제
    assert admin.put(f"/api/admin/faq/{ids['meter']}", json={"category": "계량기", "question": "계량기 위치", "answer": "1층에 있습니다", "enabled": False}).status_code == 200
    item = next(i for i in admin.get("/api/admin/faq").json()["items"] if i["id"] == ids["meter"])
    assert item["enabled"] is False and item["answer"] == "1층에 있습니다"
    assert admin.post("/api/admin/faq", json={"question": "  ", "answer": "x"}).status_code == 422
    assert admin.put("/api/admin/faq/999999", json={"question": "a", "answer": "b"}).status_code == 404
    for fid in ids.values():
        assert admin.delete(f"/api/admin/faq/{fid}").status_code == 200
    assert TestClient(app).get("/api/admin/faq").status_code == 401


def test_seed_faq_once(admin, settings):
    seed = json.loads(faq.SEED_FILE.read_text(encoding="utf-8"))
    assert len(seed) >= 70 and all(i["question"] and i["answer"] for i in seed)
    assert faq.seed_faq(settings) == len(seed)
    assert faq.seed_faq(settings) is None
    items = admin.get("/api/admin/faq").json()["items"]
    assert len(items) == len(seed) and sum(1 for i in items if not i["enabled"]) == sum(1 for i in seed if not i["enabled"])
    with db.transaction(settings) as conn, conn.cursor() as cur:  # 뒤 테스트에 끼어들지 않도록 정리
        cur.execute("DELETE FROM faq_items")
