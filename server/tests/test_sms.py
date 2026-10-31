"""업체 문자 발송(뿌리오) 테스트: 문구·길이, 뿌리오 요청 규격, 민원 → 카톡 안내 → 업체 문자 흐름, 웹 API"""

from __future__ import annotations

import base64
import sys
import time
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app import complaints as cp
from app import db, sms, store
from app.main import app

from .conftest import ADMIN_PASSWORD, ADMIN_USER, API_KEY

KEY = {"X-Api-Key": API_KEY}


# ---- 문구 ----------------------------------------------------------------------------------

def test_render_and_length():
    text = sms.render(sms.DEFAULT_TEMPLATE, vendor="한빛설비", room="OO아파트", category="누수·하자", summary="천장 누수",
                      content="물이   새요\n빨리요", sender="1203호", at=datetime(2026, 10, 31, 9, 5))
    assert "한빛설비 담당자님" in text and "'OO아파트' 대화방" in text and "[누수·하자]" in text
    assert "물이 새요 빨리요" in text and "10/31 09:05 (1203호)" in text and "{" not in text
    assert sms.byte_len("가a") == 3
    assert sms.message_type("가" * 45) == "SMS" and sms.message_type("가" * 46) == "LMS"
    assert sms.clip_bytes("가나다", 5) == "가나"
    assert sms.normalize_phone("010-1234-5678") == "01012345678" and sms.PHONE.match("01012345678")
    long = sms.render("{내용}", vendor="", room="", category="", summary="", content="가" * 500, sender="", at=datetime.now())
    assert len(long) == sms.CONTENT_MAX_CHARS


# ---- 뿌리오 클라이언트 (요청 규격) -------------------------------------------------------------------

class FakePpurio:
    """httpx.post 대역: /v1/token, /v1/message 요청을 기록하고 정해진 응답을 돌려준다."""

    def __init__(self, message_codes=("1000",)):
        self.calls: list[tuple[str, dict, dict | None]] = []
        self.codes = list(message_codes)
        self.tokens = 0

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append((url, headers or {}, json))
        if url.endswith("/v1/token"):
            self.tokens += 1
            return httpx.Response(200, json={"token": f"tok{self.tokens}", "type": "Bearer", "expired": "29991231235959", "userIp": "61.83.189.246"})
        code = self.codes.pop(0) if self.codes else "1000"
        if code == "1000":
            return httpx.Response(200, json={"code": "1000", "description": "success", "refKey": json["refKey"], "messageKey": "MK1"})
        status = 401 if code.startswith("3") else 400
        return httpx.Response(status, json={"code": code, "description": "error"})


def test_client_request_format(monkeypatch):
    fake = FakePpurio()
    monkeypatch.setattr(sms.httpx, "post", fake)
    client = sms.PpurioClient("myid", "secret-key")
    r = client.send(sender="02-123-4567", to="010-1111-2222", text="짧은 문자", ref_key="kc1-abc")
    assert r.message_type == "SMS" and r.message_key == "MK1"
    url, headers, _ = fake.calls[0]
    assert url == "https://message.ppurio.com/v1/token"
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"myid:secret-key").decode()
    url, headers, body = fake.calls[1]
    assert url == "https://message.ppurio.com/v1/message" and headers["Authorization"] == "Bearer tok1"
    assert body == {
        "account": "myid", "messageType": "SMS", "content": "짧은 문자", "from": "021234567", "duplicateFlag": "N",
        "targetCount": 1, "targets": [{"to": "01011112222"}], "refKey": "kc1-abc",
    }  # SMS 에는 subject 를 넣지 않는다 (추가 필드 불허)
    # 장문은 LMS + 제목, 토큰은 재사용
    r = client.send(sender="0212345678", to="01011112222", text="가" * 100, ref_key="kc2")
    assert r.message_type == "LMS" and fake.calls[-1][2]["subject"] == sms.DEFAULT_SUBJECT and fake.tokens == 1


def test_success_response_variants(monkeypatch):
    # 규격 예시: code 가 숫자, 키 이름이 messsageKey
    def post(url, headers=None, json=None, timeout=None):
        if url.endswith("/v1/token"):
            return httpx.Response(200, json={"token": "t", "type": "Bearer", "expired": 29991231235959, "userIp": "1.1.1.1"})
        return httpx.Response(200, json={"code": 1000, "description": "ok", "refKey": "r", "messsageKey": "221128133505801SMS"})

    monkeypatch.setattr(sms.httpx, "post", post)
    r = sms.PpurioClient("a", "b").send(sender="01087387240", to="01087387240", text="테스트", ref_key="r")
    assert r.message_key == "221128133505801SMS" and r.message_type == "SMS"


def test_client_errors(monkeypatch):
    fake = FakePpurio(message_codes=("3005", "1000"))  # 토큰 만료 → 새 토큰으로 한 번 더
    monkeypatch.setattr(sms.httpx, "post", fake)
    client = sms.PpurioClient("a", "b")
    assert client.send(sender="0212345678", to="01011112222", text="x", ref_key="r").message_key == "MK1"
    assert fake.tokens == 2
    fake.codes = ["3003"]
    with pytest.raises(sms.PpurioError) as exc:
        client.send(sender="0212345678", to="01011112222", text="x", ref_key="r")
    assert not exc.value.retry and "서버 IP" in str(exc.value)

    # 실패 응답의 userIp 를 기억 (연결 확인 화면에 표시)
    monkeypatch.setattr(sms.httpx, "post", lambda *a, **k: httpx.Response(400, json={"code": "3004", "description": "invalid account", "userIp": "1.2.3.4"}))
    bad = sms.PpurioClient("x", "y")
    with pytest.raises(sms.PpurioError) as exc:
        bad.token()
    assert exc.value.code == "3004" and not exc.value.retry and bad.user_ip == "1.2.3.4"


# ---- 민원 → 카톡 안내 → 업체 문자 ---------------------------------------------------------------

class FakeGemini:
    def classify(self, items):
        return [cp.Verdict(i.id, "누수" in i.text, "누수·하자" if "누수" in i.text else "기타", "누수 민원", 0.9) for i in items]


class FakeSender:
    def __init__(self):
        self.sent: list[dict] = []

    def send(self, *, sender, to, text, ref_key, subject=sms.DEFAULT_SUBJECT):
        self.sent.append({"from": sender, "to": to, "text": text})
        return sms.SendResult("MK", sms.message_type(text))


@pytest.fixture
def admin(settings):
    c = TestClient(app)
    assert c.post("/admin/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD}, follow_redirects=False).status_code == 303
    return c


def _post(room, sender, text):
    res = TestClient(app).post("/api/messages", headers=KEY, data={
        "device_id": "sms-phone", "room_name": room, "sender_name": sender, "message_text": text,
        "is_group_chat": "true", "posted_at": str(int(time.time() * 1000))})
    assert res.status_code == 201
    return res.json()["id"]


def _sms_rows(settings, message_id):
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM sms_queue WHERE message_id = %s ORDER BY id", (message_id,))
        return cur.fetchall()


def test_complaint_reply_first_then_vendor_sms(admin, settings):
    cat = next(c for c in admin.get("/api/admin/categories").json()["items"] if c["name"] == "누수·하자")
    assert admin.post("/api/admin/vendors", data={"name": "한빛설비", "phone": "010-2222-3333", "category_id": str(cat["id"])}).status_code == 201
    assert admin.post("/api/admin/vendors", data={"name": "번호없음업체", "phone": "", "category_id": str(cat["id"])}).status_code == 201
    assert admin.put("/api/admin/sms", json={"enabled": True, "sender": "02-123-4567"}).status_code == 200

    mid = _post("문자테스트방", "1203호", "욕실 천장 누수가 심해요 확인 부탁")
    _post("문자테스트방", "다른사람", "안녕하세요 반갑습니다")
    result = store.classify_once(settings, client=FakeGemini())
    assert result["sms"] == 2

    rows = _sms_rows(settings, mid)
    good = next(r for r in rows if r["vendor_name"] == "한빛설비")
    assert good["status"] == "waiting" and good["phone"] == "01022223333"
    assert "'문자테스트방' 대화방" in good["text"] and "욕실 천장 누수" in good["text"]
    assert next(r for r in rows if r["vendor_name"] == "번호없음업체")["status"] == "failed"

    # 카톡 안내가 아직이면 문자는 기다린다
    sender = FakeSender()
    assert store.process_sms(settings, client=sender)["sent"] == 0

    # 휴대폰이 카톡 안내 결과를 보고하면 → 문자 발송
    reply = TestClient(app).get("/api/replies/pending", params={"device_id": "sms-phone"}, headers=KEY).json()["items"][0]
    assert TestClient(app).post(f"/api/replies/{reply['id']}/result", data={"device_id": "sms-phone", "sent": "true"}, headers=KEY).status_code == 200
    assert store.process_sms(settings, client=sender)["sent"] == 1
    assert sender.sent[0]["to"] == "01022223333" and sender.sent[0]["from"] == "021234567"
    assert next(r for r in _sms_rows(settings, mid) if r["vendor_name"] == "한빛설비")["status"] == "sent"

    # 같은 문제의 반복 민원에는 문자를 다시 보내지 않는다
    again = _post("문자테스트방", "1203호", "누수 아직도 그대로예요 빨리요")
    store.classify_once(settings, client=FakeGemini())
    assert not _sms_rows(settings, again)

    # 민원 카드에 문자 결과 표시
    group = next(g for g in admin.get("/api/admin/complaints").json()["items"] if g["ids"][0] == mid)
    assert {"vendor": "한빛설비", "status": "sent", "note": None} in group["sms"]


def test_sms_retry_and_failure(admin, settings):
    with db.transaction(settings) as conn, conn.cursor() as cur:
        now = store.now_local(settings)
        cur.execute(
            "INSERT INTO sms_queue (message_id, vendor_name, phone, text, status, send_after, created_at) VALUES (%s, 'x', '01011112222', 't', 'pending', %s, %s)",
            (1, now, now),
        )
        sid = cur.lastrowid

    class Flaky:
        def send(self, **kw):
            raise sms.PpurioError("뿌리오 9001: 서버 오류", retry=True)

    store.process_sms(settings, client=Flaky())
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, attempts, send_after FROM sms_queue WHERE id = %s", (sid,))
        row = cur.fetchone()
    assert row["status"] == "pending" and row["attempts"] == 1  # 나중에 재시도

    class Denied:
        def send(self, **kw):
            raise sms.PpurioError("뿌리오 3003: 등록되지 않은 서버 IP", retry=False)

    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("UPDATE sms_queue SET send_after = %s WHERE id = %s", (store.now_local(settings), sid))
    store.process_sms(settings, client=Denied())
    hist = {m["id"]: m for m in admin.get("/api/admin/sms/history").json()["items"]}
    assert hist[sid]["status"] == "failed" and "IP" in hist[sid]["note"]
    assert admin.post(f"/api/admin/sms/{sid}/retry").status_code == 200
    assert {m["id"]: m for m in admin.get("/api/admin/sms/history").json()["items"]}[sid]["status"] == "pending"


def test_sms_admin_api(admin, settings, monkeypatch):
    assert TestClient(app).get("/api/admin/sms").status_code == 401
    s = admin.get("/api/admin/sms").json()
    assert s["configured"] is False and "{방}" in s["placeholders"] and s["preview"]["type"] in ("SMS", "LMS")
    assert admin.put("/api/admin/sms", json={"sender": "12"}).status_code == 422
    assert admin.put("/api/admin/sms", json={"template": "가" * 1001}).status_code == 422

    p = admin.post("/api/admin/sms/preview", json={"template": "{업체명} {방} 확인"}).json()
    assert p["text"] == "한빛설비 OO아파트 입주민방 확인" and p["type"] == "SMS"

    assert admin.post("/api/admin/sms/check").json()["ok"] is False  # 연동 정보 없음
    assert admin.post("/api/admin/sms/test", json={"to": "0101"}).status_code == 422
    # 설정이 덜 된 테스트 발송도 이력에 실패 사유로 남는다
    r = admin.post("/api/admin/sms/test", json={"to": "010-5555-6666"}).json()
    assert r["ok"] is False
    top = admin.get("/api/admin/sms/history").json()["items"][0]
    assert top["status"] == "failed" and top["note"] == r["message"]

    sender = FakeSender()
    monkeypatch.setattr(sms, "client_for", lambda _s: sender)
    r = admin.post("/api/admin/sms/test", json={"to": "010-5555-6666"}).json()
    assert r["ok"] is True and sender.sent[0]["text"].startswith("[테스트]")
    assert admin.get("/api/admin/sms/history").json()["items"][0]["vendor_name"] == "테스트 발송"
    # 다른 테스트에 영향이 없도록 끈다
    assert admin.put("/api/admin/sms", json={"enabled": False}).status_code == 200


def test_token_expired_as_number_and_bad_values(monkeypatch):
    # 가이드 예시처럼 expired 가 숫자로 와도 동작해야 한다
    monkeypatch.setattr(sms.httpx, "post", lambda *a, **k: httpx.Response(
        200, json={"token": "t", "type": "Bearer", "expired": 29991231235959, "userIp": "61.83.189.246"}))
    c = sms.PpurioClient("nvr_0718444053", "key")
    assert c.token() == "t" and c.user_ip == "61.83.189.246" and c._expires.year == 2999
    with pytest.raises(sms.PpurioError) as exc:
        sms.PpurioClient("아이디", "key").token()
    assert not exc.value.retry and ".env" in str(exc.value)


def test_cli_check_and_test_send(settings, monkeypatch, capsys):
    from scripts import sms_check

    fake = FakePpurio()
    monkeypatch.setattr(sms.httpx, "post", fake)
    monkeypatch.setattr(sms_check.Settings, "load", classmethod(lambda cls: settings))
    monkeypatch.setattr(sys, "argv", ["sms_check"])
    assert sms_check.main() == 1  # 연동 정보 없음
    assert "PPURIO_API_KEY : (없음)" in capsys.readouterr().out

    import dataclasses
    configured = dataclasses.replace(settings, ppurio_account="nvr_0718444053", ppurio_api_key="k")
    monkeypatch.setattr(sms_check.Settings, "load", classmethod(lambda cls: configured))
    assert sms_check.main() == 0
    assert "서버 IP 61.83.189.246" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["sms_check", "010 8738 7240", "--from", "010-8738-7240"])
    assert sms_check.main() == 0
    body = fake.calls[-1][2]
    assert body["targets"] == [{"to": "01087387240"}] and body["from"] == "01087387240" and body["content"].startswith("[테스트]")
    assert "발송 요청     : 성공" in capsys.readouterr().out

    fake.codes = ["3003"]
    assert sms_check.main() == 1
    assert "연동관리에서" in capsys.readouterr().out
