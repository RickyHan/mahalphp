"""단말 수집 API 통합 테스트 (공통 설정: conftest.py)"""

from __future__ import annotations

import io
import time

from PIL import Image

from app import db
from app.main import get_settings

from .conftest import ADMIN_PASSWORD, ADMIN_USER, API_KEY

HEADERS = {"X-Api-Key": API_KEY}


def _admin(client):
    res = client.post("/admin/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD}, follow_redirects=False)
    assert res.status_code == 303
    return client


def _message(**overrides):
    data = {
        "device_id": "dev-1",
        "room_name": "개발팀 단톡방",
        "sender_name": "홍길동",
        "message_text": "안녕하세요 😀",
        "message_type": "text",
        "is_group_chat": "true",
        "posted_at": str(int(time.time() * 1000)),
    }
    data.update(overrides)
    return data


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (255, 200, 0)).save(buf, "PNG")
    return buf.getvalue()


def test_install_is_idempotent(client):
    assert {"kakao_messages", "kakao_media"} <= set(db.install_schema(get_settings()))


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200 and res.json()["ok"] is True


def test_rejects_missing_or_wrong_key(client):
    assert client.post("/api/messages", data=_message()).status_code == 401
    assert client.post("/api/messages", data=_message(), headers={"X-Api-Key": "wrong"}).status_code == 401


def test_text_message_saved_once(client):
    data = _message(posted_at="1761800000123")
    first = client.post("/api/messages", data=data, headers=HEADERS)
    assert first.status_code == 201, first.text
    assert first.json()["duplicate"] is False

    second = client.post("/api/messages", data=data, headers=HEADERS)
    assert second.json() == {**first.json(), "duplicate": True}

    # 단말 API 키로는 조회 불가 (APK 에 키가 있어도 대화가 새지 않게)
    assert client.get("/api/messages", params={"room": "개발팀 단톡방"}, headers=HEADERS).status_code == 401
    items = _admin(client).get("/api/messages", params={"room": "개발팀 단톡방"}).json()["items"]
    row = next(i for i in items if i["id"] == first.json()["id"])
    assert row["sender_name"] == "홍길동"
    assert row["message_text"] == "안녕하세요 😀"
    assert row["is_group_chat"] is True
    assert row["posted_at"].startswith("2025-10-30T")


def test_photo_message_with_image(client):
    png = _png()
    res = client.post(
        "/api/messages",
        data=_message(message_text="사진을 보냈습니다.", message_type="photo"),
        files={"image": ("photo.png", png, "image/png")},
        headers=HEADERS,
    )
    assert res.status_code == 201, res.text
    media_id = res.json()["media_id"]
    assert media_id

    assert client.get(f"/api/media/{media_id}", headers=HEADERS).status_code == 401
    downloaded = _admin(client).get(f"/api/media/{media_id}")
    assert downloaded.status_code == 200
    assert downloaded.content == png
    assert downloaded.headers["content-type"] == "image/png"


def test_rejects_non_image_upload(client):
    res = client.post(
        "/api/messages",
        data=_message(message_type="photo"),
        files={"image": ("x.png", b"not an image", "image/png")},
        headers=HEADERS,
    )
    assert res.status_code == 415


def test_rejects_unknown_message_type(client):
    assert client.post("/api/messages", data=_message(message_type="hack"), headers=HEADERS).status_code == 422
