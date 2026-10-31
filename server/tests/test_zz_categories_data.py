"""민원 카테고리 관리 · 업체 카테고리 지정 · 수집 데이터 삭제 (공통 설정: conftest.py)

파일 이름을 test_zz_ 로 시작해 마지막에 실행한다(전체 삭제 테스트가 다른 테스트 데이터를 지우므로).
"""

from __future__ import annotations

import io
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import categories, db, store
from app import complaints as cp
from app.main import app

from .conftest import ADMIN_PASSWORD, ADMIN_USER, API_KEY

KEY = {"X-Api-Key": API_KEY}


@pytest.fixture
def admin(settings):
    c = TestClient(app)
    assert c.post("/admin/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD}, follow_redirects=False).status_code == 303
    return c


def _post(client, room, text, image: bytes | None = None, sender="301호"):
    data = {"device_id": "zz", "room_name": room, "sender_name": sender, "message_text": text,
            "message_type": "photo" if image else "text", "is_group_chat": "true",
            "posted_at": str(int(time.time() * 1000) + hash((room, text)) % 100000)}
    files = {"image": ("p.png", image, "image/png")} if image else None
    res = client.post("/api/messages", data=data, files=files, headers=KEY)
    assert res.status_code == 201, res.text
    return res.json()


def _png(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (30, 20), color).save(buf, "PNG")
    return buf.getvalue()


def _cat(admin, name):
    return next(c for c in admin.get("/api/admin/categories").json()["items"] if c["name"] == name)


# ---- 카테고리 -----------------------------------------------------------------------------

def test_categories_require_admin(client):
    assert client.get("/api/admin/categories").status_code == 401
    assert client.post("/api/admin/categories", json={"name": "x"}).status_code == 401
    assert client.post("/api/admin/data/delete", json={"scope": "all", "confirm": "전체 삭제"}).status_code == 401
    assert client.post("/api/admin/data/delete", json={"scope": "all", "confirm": "전체 삭제"}, headers=KEY).status_code == 401


def test_category_crud_and_rules(admin, settings):
    items = admin.get("/api/admin/categories").json()["items"]
    names = [c["name"] for c in items]
    assert names[: len(cp.CATEGORIES)] == cp.CATEGORIES  # 처음엔 기본 분류
    etc = _cat(admin, "기타")
    assert etc["protected"] is True

    res = admin.post("/api/admin/categories", json={"name": " 엘리베이터 ", "description": "승강기 고장·갇힘"})
    assert res.status_code == 201
    new_id = res.json()["id"]
    assert admin.post("/api/admin/categories", json={"name": "엘리베이터"}).status_code == 409
    assert admin.post("/api/admin/categories", json={"name": 'a"b'}).status_code == 422

    # '기타'는 이름 변경·삭제 불가
    assert admin.put(f"/api/admin/categories/{etc['id']}", json={"name": "그외"}).status_code == 422
    assert admin.delete(f"/api/admin/categories/{etc['id']}").status_code == 422

    # 이름 변경 시 기존 민원 분류도 함께
    with db.transaction(settings) as conn, conn.cursor() as cur:
        mid = _post(TestClient(app), "카테고리방", "엘리베이터가 멈췄어요 빨리요")["id"]
        store.save_verdicts(cur, [cp.Verdict(mid, True, "엘리베이터", "엘리베이터 고장", 0.9)], "m", store.now_local(settings))
    res = admin.put(f"/api/admin/categories/{new_id}", json={"name": "승강기", "description": "승강기 고장", "enabled": True})
    assert res.json()["renamed_complaints"] == 1
    assert _cat(admin, "승강기")["complaints"] == 1

    # 순서 변경
    ids = [c["id"] for c in admin.get("/api/admin/categories").json()["items"]]
    assert admin.post("/api/admin/categories/order", json={"ids": [new_id] + [i for i in ids if i != new_id]}).status_code == 200
    assert admin.get("/api/admin/categories").json()["items"][0]["name"] == "승강기"

    # 삭제 시 기존 민원은 '기타'로
    res = admin.delete(f"/api/admin/categories/{new_id}")
    assert res.json()["moved_complaints"] == 1
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT category FROM complaint_check WHERE message_id = %s", (mid,))
        assert cur.fetchone()["category"] == "기타"


def test_prompt_uses_enabled_categories(admin, settings, monkeypatch):
    admin.post("/api/admin/categories", json={"name": "택배", "description": "택배 분실·오배송"})
    off = admin.post("/api/admin/categories", json={"name": "임시분류"}).json()["id"]
    admin.put(f"/api/admin/categories/{off}", json={"name": "임시분류", "enabled": False})
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cats = categories.enabled_for_prompt(cur)
    names = [n for n, _ in cats]
    assert "택배" in names and "임시분류" not in names and names[-1] == "기타"
    prompt = cp.build_prompt(cats)
    assert '"택배": 택배 분실·오배송' in prompt and "임시분류" not in prompt

    # 실제 요청 본문과 응답 해석이 DB 분류를 따른다
    sent = {}

    def fake_post(url, headers, json, timeout):
        sent["prompt"] = json["systemInstruction"]["parts"][0]["text"]
        text = '{"results":[{"id":1,"is_complaint":true,"category":"택배","summary":"택배 분실","confidence":0.9},' \
               '{"id":2,"is_complaint":true,"category":"임시분류","summary":"x","confidence":0.5}]}'
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})

    monkeypatch.setattr(cp.httpx, "post", fake_post)
    client = cp.GeminiClient("k", "m", categories=cats)
    verdicts = {v.id: v for v in client.classify([cp.Item(1, "r", "s", "택배가 없어졌어요"), cp.Item(2, "r", "s", "기타 문의")])}
    assert "택배" in sent["prompt"]
    assert verdicts[1].category == "택배" and verdicts[2].category == "기타"  # 사용 안 함 분류는 '기타'로


def test_vendor_category(admin):
    cat = _cat(admin, "소음")
    res = admin.post("/api/admin/vendors", data={"name": "방음공사", "category_id": str(cat["id"])})
    assert res.status_code == 201, res.text
    vid = res.json()["id"]
    v = next(x for x in admin.get("/api/admin/vendors").json()["items"] if x["id"] == vid)
    assert v["category_id"] == cat["id"] and v["category_name"] == "소음"
    assert admin.post("/api/admin/vendors", data={"name": "x", "category_id": "999999"}).status_code == 422
    assert admin.put(f"/api/admin/vendors/{vid}", data={"name": "방음공사", "category_id": ""}).status_code == 200
    v = next(x for x in admin.get("/api/admin/vendors").json()["items"] if x["id"] == vid)
    assert v["category_id"] is None and v["category_name"] is None


# ---- 데이터 삭제 ---------------------------------------------------------------------------

def _count(settings, sql, params=()):
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return int(cur.fetchone()["n"])


def test_delete_selected_messages_and_room(admin, settings):
    c = TestClient(app)
    keep = _post(c, "삭제방A", "남길 메시지")
    gone = _post(c, "삭제방A", "지울 사진", image=_png("red"))
    _post(c, "삭제방B", "방째로 지울 메시지 1")
    _post(c, "삭제방B", "방째로 지울 메시지 2", image=_png("blue"))
    photo = settings.media_root / _media_path(settings, gone["id"])
    assert photo.exists()

    rooms = {r["room_name"]: r for r in admin.get("/api/admin/data/rooms").json()["items"]}
    assert rooms["삭제방A"]["messages"] == 2 and rooms["삭제방B"]["photos"] == 1

    assert admin.post("/api/admin/data/delete", json={"scope": "messages", "ids": []}).status_code == 422
    res = admin.post("/api/admin/data/delete", json={"scope": "messages", "ids": [gone["id"]]}).json()
    assert res["messages"] == 1 and res["photos"] == 1 and res["files_removed"] == 1
    assert not photo.exists()
    assert _count(settings, "SELECT COUNT(*) AS n FROM kakao_messages WHERE id = %s", (keep["id"],)) == 1

    res = admin.post("/api/admin/data/delete", json={"scope": "rooms", "rooms": ["삭제방B"]}).json()
    assert res["messages"] == 2
    assert _count(settings, "SELECT COUNT(*) AS n FROM kakao_messages WHERE room_name = %s", ("삭제방B",)) == 0
    assert _count(settings, "SELECT COUNT(*) AS n FROM room_members WHERE room_name = %s", ("삭제방B",)) == 0
    assert _count(settings, "SELECT COUNT(*) AS n FROM kakao_messages WHERE room_name = %s", ("삭제방A",)) == 1


def _media_path(settings, message_id):
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT file_path FROM kakao_media WHERE message_id = %s", (message_id,))
        return cur.fetchone()["file_path"]


def test_delete_all_requires_confirmation_and_keeps_setup(admin, settings):
    _post(TestClient(app), "전체삭제방", "아무 메시지")
    assert admin.post("/api/admin/data/delete", json={"scope": "all"}).status_code == 422
    assert admin.post("/api/admin/data/delete", json={"scope": "all", "confirm": "삭제"}).status_code == 422
    vendors_before = len(admin.get("/api/admin/vendors").json()["items"])
    cats_before = len(admin.get("/api/admin/categories").json()["items"])

    res = admin.post("/api/admin/data/delete", json={"scope": "all", "confirm": "전체 삭제"})
    assert res.status_code == 200 and res.json()["messages"] > 0
    for table in ("kakao_messages", "kakao_media", "complaint_check", "member_events", "room_members", "reply_queue"):
        assert _count(settings, f"SELECT COUNT(*) AS n FROM {table}") == 0, table
    assert admin.get("/api/admin/data/rooms").json()["items"] == []
    # 업체·카테고리는 유지
    assert len(admin.get("/api/admin/vendors").json()["items"]) == vendors_before
    assert len(admin.get("/api/admin/categories").json()["items"]) == cats_before
    # 삭제 후에도 수집은 정상
    assert _post(TestClient(app), "새방", "삭제 후 첫 메시지")["duplicate"] is False
    json.dumps(res.json())
