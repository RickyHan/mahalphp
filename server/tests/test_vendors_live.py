"""민원처리업체 관리 · 실시간 갱신 신호 테스트 (공통 설정: conftest.py)"""

from __future__ import annotations

import io
import threading
import time

from PIL import Image

from app import admin, live
from app.auth import SESSION_COOKIE, issue_session

from .conftest import API_KEY


def _admin(client, settings):
    client.cookies.set(SESSION_COOKIE, issue_session(settings))
    return client


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(buf, "PNG")
    return buf.getvalue()


def test_vendor_requires_admin(client):
    assert client.get("/api/admin/vendors").status_code == 401
    assert client.post("/api/admin/vendors", data={"name": "x"}).status_code == 401
    # 단말 API 키로도 불가
    assert client.get("/api/admin/vendors", headers={"X-Api-Key": API_KEY}).status_code == 401


def test_vendor_crud_with_doc(client, settings):
    c = _admin(client, settings)
    res = c.post(
        "/api/admin/vendors",
        data={"name": "  한빛설비  ", "biz_no": "1234567890", "phone": "02-123-4567", "address": "서울시 중구 1", "room_name": "OO아파트 입주민방"},
        files={"doc": ("등록증.png", _png(), "image/png")},
    )
    assert res.status_code == 201, res.text
    vid = res.json()["id"]

    items = {v["id"]: v for v in c.get("/api/admin/vendors").json()["items"]}
    v = items[vid]
    assert v["name"] == "한빛설비" and v["biz_no"] == "123-45-67890" and v["room_name"] == "OO아파트 입주민방"
    assert v["has_doc"] is True and "doc_path" not in v  # 서버 경로는 노출하지 않음

    doc = c.get(f"/api/admin/vendors/{vid}/doc")
    assert doc.status_code == 200 and doc.headers["content-type"] == "image/png"
    assert doc.headers["content-security-policy"] == "sandbox" and doc.content[:4] == b"\x89PNG"

    # 수정: 방 지정 해제, PDF 로 교체
    res = c.put(
        f"/api/admin/vendors/{vid}",
        data={"name": "한빛설비(주)", "biz_no": "123-45-67890", "phone": "", "address": "", "room_name": ""},
        files={"doc": ("cert.pdf", b"%PDF-1.4\n%test\n", "application/pdf")},
    )
    assert res.status_code == 200, res.text
    v = {x["id"]: x for x in c.get("/api/admin/vendors").json()["items"]}[vid]
    assert v["name"] == "한빛설비(주)" and v["room_name"] is None
    doc = c.get(f"/api/admin/vendors/{vid}/doc")
    assert doc.headers["content-type"] == "application/pdf" and "content-security-policy" not in doc.headers

    # 등록증 삭제
    res = c.put(f"/api/admin/vendors/{vid}", data={"name": "한빛설비(주)", "remove_doc": "true"})
    assert res.status_code == 200
    assert c.get(f"/api/admin/vendors/{vid}/doc").status_code == 404

    assert c.delete(f"/api/admin/vendors/{vid}").status_code == 200
    assert vid not in {x["id"] for x in c.get("/api/admin/vendors").json()["items"]}
    assert c.put(f"/api/admin/vendors/{vid}", data={"name": "x"}).status_code == 404


def test_vendor_validation(client, settings):
    c = _admin(client, settings)
    assert c.post("/api/admin/vendors", data={"name": "   "}).status_code == 422
    assert c.post("/api/admin/vendors", data={"name": "a", "biz_no": "12-34"}).status_code == 422
    assert c.post("/api/admin/vendors", data={"name": "a", "phone": "<script>"}).status_code == 422
    # 이미지·PDF 가 아닌 등록증 거부 (HTML 위장 포함)
    res = c.post("/api/admin/vendors", data={"name": "a"}, files={"doc": ("x.png", b"<html><script>alert(1)</script>", "image/png")})
    assert res.status_code == 415


def test_live_signal_on_new_message(client, settings):
    before, _ = live.snapshot()
    res = client.post(
        "/api/messages",
        data={"device_id": "live-test", "room_name": "실시간방", "sender_name": "가", "message_text": "안녕", "posted_at": str(int(time.time() * 1000))},
        headers={"X-Api-Key": API_KEY},
    )
    assert res.status_code == 201
    after, topics = live.snapshot()
    assert after > before and topics["message"] == after


def test_stream_sends_change_event(client, settings, monkeypatch):
    assert client.get("/api/admin/stream").status_code == 401
    monkeypatch.setattr(admin, "STREAM_SECONDS", 1.0)
    monkeypatch.setattr(admin, "STREAM_TICK", 0.05)
    c = _admin(client, settings)
    timer = threading.Timer(0.3, live.bump, args=("vendor",))  # 연결이 열린 뒤 변경 발생
    timer.start()
    with c.stream("GET", "/api/admin/stream") as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        assert res.headers["x-accel-buffering"] == "no"
        body = "".join(res.iter_text())
    timer.join()
    assert "event: hello" in body
    assert 'event: change\ndata: {"topics": ["vendor"]}' in body
