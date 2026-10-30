"""관리자 웹 통합 테스트 (공통 설정: conftest.py)"""

from __future__ import annotations

import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.auth import SESSION_COOKIE, issue_session, login_throttle
from app.main import app

from .conftest import ADMIN_PASSWORD, ADMIN_USER, API_KEY

ROOM_A = "관리자테스트 A방"
ROOM_B = "관리자테스트 B방"
BASE_MS = 1_761_800_000_000  # 2025-10-30 13:53:20 KST


def _post(client, room, sender, text, offset_s, message_type="text", image=None, group=True):
    data = {
        "device_id": "admin-test",
        "room_name": room,
        "sender_name": sender,
        "message_text": text,
        "message_type": message_type,
        "is_group_chat": str(group).lower(),
        "posted_at": str(BASE_MS + offset_s * 1000),
    }
    files = {"image": ("p.png", image, "image/png")} if image else None
    res = client.post("/api/messages", data=data, files=files, headers={"X-Api-Key": API_KEY})
    assert res.status_code == 201, res.text
    return res.json()


def _png(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def seeded(settings):
    c = TestClient(app)
    ids = {
        "a1": _post(c, ROOM_A, "홍길동", "첫 메시지", 0)["id"],
        "a2": _post(c, ROOM_A, "김철수", "할인율 100% 이벤트", 10)["id"],
        "a3": _post(c, ROOM_A, "홍길동", "사진을 보냈습니다.", 20, "photo", _png((255, 0, 0))),
        "b1": _post(c, ROOM_B, "이영희", "B방 메시지", 5, group=False)["id"],
    }
    ids["a3_media"] = ids["a3"]["media_id"]
    ids["a3"] = ids["a3"]["id"]
    return ids


def _login(client, username=ADMIN_USER, password=ADMIN_PASSWORD):
    return client.post("/admin/login", data={"username": username, "password": password}, follow_redirects=False)


@pytest.fixture
def admin(client):
    res = _login(client)
    assert res.status_code == 303 and res.headers["location"] == "/admin"
    return client


# ---- 인증 ------------------------------------------------------------------------------

def test_admin_page_requires_login(client):
    res = client.get("/admin", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/admin/login"
    login = client.get("/admin/login")
    assert login.status_code == 200 and "로그인" in login.text
    assert "frame-ancestors 'none'" in login.headers["content-security-policy"]


def test_login_success_sets_httponly_cookie(client):
    res = _login(client)
    cookie = res.headers["set-cookie"]
    assert SESSION_COOKIE in cookie and "HttpOnly" in cookie and "samesite=strict" in cookie.lower()
    page = client.get("/admin")
    assert page.status_code == 200 and 'id="room-list"' in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert client.get("/admin/login", follow_redirects=False).headers["location"] == "/admin"


def test_wrong_password_and_lockout(client):
    login_throttle.reset("testclient")
    try:
        res = _login(client, password="wrong")
        assert res.headers["location"] == "/admin/login?error=invalid"
        for _ in range(4):
            _login(client, password="wrong")
        # 5회 실패 후에는 올바른 비밀번호도 차단
        assert _login(client).headers["location"] == "/admin/login?error=locked"
    finally:
        login_throttle.reset("testclient")
    assert _login(client).headers["location"] == "/admin"


def test_admin_api_rejects_missing_tampered_or_expired_session(client, settings):
    assert client.get("/api/admin/summary").status_code == 401
    # 단말 API 키로는 관리자 API 에 접근할 수 없다
    assert client.get("/api/admin/summary", headers={"X-Api-Key": API_KEY}).status_code == 401

    valid = issue_session(settings)
    user, expires, sig = valid.rsplit(".", 2)
    tampered = f"{user}.{int(expires) + 999999}.{sig}"
    expired = issue_session(settings, now=time.time() - settings.session_hours * 3600 - 10)
    for value in (tampered, expired, "garbage"):
        c = TestClient(app, cookies={SESSION_COOKIE: value})
        assert c.get("/api/admin/summary").status_code == 401
    assert TestClient(app, cookies={SESSION_COOKIE: valid}).get("/api/admin/summary").status_code == 200


def test_logout_clears_cookie(admin):
    res = admin.post("/admin/logout", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/admin/login"
    assert admin.get("/api/admin/me").status_code == 401


def test_static_assets_served(client):
    for path in ("/admin/static/admin.js", "/admin/static/admin.css", "/admin/static/login.js"):
        assert client.get(path).status_code == 200


# ---- 조회 API -----------------------------------------------------------------------------

def test_summary(admin, seeded):
    s = admin.get("/api/admin/summary").json()
    assert s["messages"] >= 4 and s["rooms"] >= 2 and s["photos"] >= 1
    assert s["last_received_at"]


def test_rooms_aggregate_latest_first(admin, seeded):
    items = admin.get("/api/admin/rooms", params={"q": "관리자테스트"}).json()["items"]
    assert [r["room_name"] for r in items] == [ROOM_B, ROOM_A]  # B방이 가장 마지막에 저장됨(id 기준)
    room_a = items[1]
    assert room_a["message_count"] == 3
    assert room_a["photo_count"] == 1
    assert room_a["last_sender"] == "홍길동" and room_a["last_type"] == "photo"
    assert room_a["is_group_chat"] is True and items[0]["is_group_chat"] is False


def test_messages_paging_and_media(admin, seeded):
    page = admin.get("/api/admin/messages", params={"room": ROOM_A, "limit": 2}).json()
    assert [m["id"] for m in page["items"]] == [seeded["a2"], seeded["a3"]]  # 오래된 → 최신
    assert page["has_more"] is True
    assert page["items"][1]["media"][0]["id"] == seeded["a3_media"]
    assert page["items"][1]["media"][0]["width"] == 40

    older = admin.get("/api/admin/messages", params={"room": ROOM_A, "before_id": seeded["a2"]}).json()
    assert [m["id"] for m in older["items"]] == [seeded["a1"]] and older["has_more"] is False

    newer = admin.get("/api/admin/messages", params={"room": ROOM_A, "after_id": seeded["a1"], "limit": 1}).json()
    assert [m["id"] for m in newer["items"]] == [seeded["a2"]] and newer["has_more"] is True


def test_search_escapes_like_wildcards(admin, seeded):
    hits = admin.get("/api/admin/messages", params={"q": "100%"}).json()["items"]
    assert [m["id"] for m in hits] == [seeded["a2"]]
    # '%' 는 와일드카드가 아니라 글자 그대로 검색된다
    assert [m["id"] for m in admin.get("/api/admin/messages", params={"q": "%"}).json()["items"]] == [seeded["a2"]]
    by_sender = admin.get("/api/admin/messages", params={"q": "이영희"}).json()["items"]
    assert [m["id"] for m in by_sender] == [seeded["b1"]]
    photos = admin.get("/api/admin/messages", params={"room": ROOM_A, "type": "photo"}).json()["items"]
    assert [m["id"] for m in photos] == [seeded["a3"]]
    assert admin.get("/api/admin/messages", params={"type": "bogus"}).status_code == 422


def test_photos_and_media_download_with_session(admin, seeded):
    items = admin.get("/api/admin/photos", params={"room": ROOM_A}).json()["items"]
    assert [p["id"] for p in items] == [seeded["a3_media"]]
    assert items[0]["sender_name"] == "홍길동"
    img = admin.get(f"/api/media/{seeded['a3_media']}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert "private" in img.headers["cache-control"]
    # 세션 쿠키로 기존 조회 API 도 사용 가능
    assert admin.get("/api/messages", params={"room": ROOM_A}).status_code == 200


def test_media_requires_auth(client, seeded):
    assert client.get(f"/api/media/{seeded['a3_media']}").status_code == 401


def test_root_redirects_to_admin(client):
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 307 and res.headers["location"] == "/admin"
