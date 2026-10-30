"""통합 테스트. 실제 MySQL/MariaDB 가 필요하며, 없으면 건너뛴다.

    TEST_DB_HOST=127.0.0.1 TEST_DB_USER=root TEST_DB_PASS=... pytest -q
테스트용 스키마(kakao_collector_test)를 만들고 끝나면 삭제한다.
"""

from __future__ import annotations

import io
import os
import time

import pytest

TEST_DB = "kakao_collector_test"
API_KEY = "test-key"

os.environ.update(
    DB_HOST=os.getenv("TEST_DB_HOST", "127.0.0.1"),
    DB_PORT=os.getenv("TEST_DB_PORT", "3306"),
    DB_USER=os.getenv("TEST_DB_USER", "root"),
    DB_PASS=os.getenv("TEST_DB_PASS", ""),
    DB_NAME=TEST_DB,
    API_KEY=API_KEY,
)

import pymysql  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app import db  # noqa: E402
from app.main import app, get_settings  # noqa: E402

HEADERS = {"X-Api-Key": API_KEY}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["MEDIA_ROOT"] = str(tmp_path_factory.mktemp("media"))
    get_settings.cache_clear()
    settings = get_settings()
    try:
        tables = db.install_schema(settings)
    except pymysql.MySQLError as exc:
        pytest.skip(f"테스트 DB 에 연결할 수 없습니다: {exc}")
    assert {"kakao_messages", "kakao_media"} <= set(tables)
    yield TestClient(app)
    conn = db.connect(settings, with_schema=False)
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE `{TEST_DB}`")
    conn.close()


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

    items = client.get("/api/messages", params={"room": "개발팀 단톡방"}, headers=HEADERS).json()["items"]
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

    downloaded = client.get(f"/api/media/{media_id}", headers=HEADERS)
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
