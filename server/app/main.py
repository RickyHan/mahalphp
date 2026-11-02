"""카카오톡 알림 수집 API.

    GET  /api/health               DB 연결 확인 (인증 불필요)
    POST /api/messages             메시지 1건 저장 (multipart, 선택적으로 image 첨부)
    GET  /api/messages             저장된 메시지 조회
    GET  /api/media/{media_id}     저장된 사진 내려받기
    /admin, /api/admin/*           관리자 웹 (app/admin.py, 민원처리업체: app/vendors.py)
    GET  /api/admin/stream         실시간 갱신 신호 (Server-Sent Events)
    /api/admin/categories          민원 카테고리 관리 (app/categories.py)
    /api/admin/data/*              수집 데이터 삭제 (app/datamgmt.py)

POST /api/messages 는 헤더 X-Api-Key 가 .env 의 API_KEY 와 같아야 한다.
조회 API(/api/messages, /api/media)는 관리자 로그인 세션으로만 접근한다(단말 API 키로는 읽기 불가).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import admin, categories, datamgmt, db, live, media, store, vendors, worker
from .auth import SettingsDep, get_settings, require_api_key, require_reader  # noqa: F401 (get_settings: 테스트에서 사용)

MESSAGE_TYPES = {"text", "photo", "video", "emoticon", "file", "other"}
MAX_TEXT_BYTES = 65535  # MySQL TEXT 한도

app = FastAPI(title="Kakao Notification Collector", version="2.0.0", lifespan=worker.lifespan)
app.include_router(admin.router)
app.include_router(vendors.router)
app.include_router(categories.router)
app.include_router(datamgmt.router)
app.mount("/admin/static", StaticFiles(directory=admin.STATIC_DIR), name="admin-static")


Authorized = Depends(require_api_key)
Reader = Depends(require_reader)


def _clip(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    return value if len(encoded) <= max_bytes else encoded[:max_bytes].decode("utf-8", "ignore")


def dedup_key(device_id: str, room: str, sender: str, text: str, posted_at_ms: int) -> str:
    return hashlib.sha256(f"{device_id}|{room}|{sender}|{text}|{posted_at_ms}".encode("utf-8")).hexdigest()


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """주소만 입력해도 관리자 웹으로"""
    return RedirectResponse("/admin", status_code=307)


@app.get("/api/health")
def health(settings: SettingsDep) -> dict[str, Any]:
    try:
        conn = db.connect(settings)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM kakao_messages")
            count = cur.fetchone()["n"]
        conn.close()
    except db.DatabaseError as exc:
        raise HTTPException(503, f"DB 연결 실패: {exc.args[0] if exc.args else exc}") from exc
    return {"ok": True, "db": settings.db_name, "messages": count}


@app.post("/api/messages", dependencies=[Authorized], status_code=201)
async def create_message(
    settings: SettingsDep,
    device_id: Annotated[str, Form(min_length=1, max_length=64)],
    room_name: Annotated[str, Form(min_length=1, max_length=255)],
    sender_name: Annotated[str, Form(min_length=1, max_length=255)],
    posted_at: Annotated[int, Form(description="수신 시각, epoch milliseconds", gt=0)],
    message_text: Annotated[str, Form()] = "",
    message_type: Annotated[str, Form()] = "text",
    is_group_chat: Annotated[bool, Form()] = False,
    notification_key: Annotated[str | None, Form(max_length=255)] = None,
    sender_key: Annotated[str | None, Form(max_length=255)] = None,
    image: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    if message_type not in MESSAGE_TYPES:
        raise HTTPException(422, f"message_type 은 {sorted(MESSAGE_TYPES)} 중 하나여야 합니다.")

    text = _clip(message_text, MAX_TEXT_BYTES)
    posted = datetime.fromtimestamp(posted_at / 1000, settings.timezone).replace(tzinfo=None)
    key = dedup_key(device_id, room_name, sender_name, text, posted_at)
    stored = None
    if image is not None and image.filename:
        stored = media.store_image(await image.read(), settings.media_root, settings.max_image_bytes, posted)

    with db.transaction(settings) as conn, conn.cursor() as cur:
        duplicate = False
        try:
            cur.execute(
                """INSERT INTO kakao_messages
                   (device_id, room_name, sender_name, message_text, message_type, is_group_chat,
                    has_media, posted_at, received_at, notification_key, sender_key, dedup_key)
                   VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s)""",
                (device_id, room_name, sender_name, text, message_type, int(is_group_chat), posted,
                 datetime.now(settings.timezone).replace(tzinfo=None), notification_key, sender_key or None, key),
            )
            message_id = cur.lastrowid
            # 참여자 이력: 입장·퇴장 안내 메시지 또는 발언자(첫 발언·대화명 변경)
            store.track_members(cur, room_name, sender_name, sender_key or None, text, is_group_chat, posted)
        except db.IntegrityError as exc:
            if not db.is_duplicate(exc):
                raise
            duplicate = True
            cur.execute("SELECT id FROM kakao_messages WHERE dedup_key = %s", (key,))
            message_id = cur.fetchone()["id"]

        media_id = None
        if stored is not None:
            cur.execute("SELECT id FROM kakao_media WHERE message_id = %s AND sha256 = %s", (message_id, stored.sha256))
            existing = cur.fetchone()
            if existing:
                media_id = existing["id"]
            else:
                cur.execute(
                    """INSERT INTO kakao_media (message_id, file_path, mime_type, size_bytes, width, height, sha256)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (message_id, stored.file_path, stored.mime_type, stored.size_bytes, stored.width, stored.height, stored.sha256),
                )
                media_id = cur.lastrowid
                cur.execute("UPDATE kakao_messages SET has_media = 1 WHERE id = %s", (message_id,))

    if not duplicate:
        worker.wake()
        live.bump("message")
    return {"ok": True, "id": message_id, "duplicate": duplicate, "media_id": media_id}


# ---- 휴대폰 앱: 민원 접수 안내 대기열 -------------------------------------------------------

@app.get("/api/replies/pending", dependencies=[Authorized])
def replies_pending(settings: SettingsDep, device_id: Annotated[str, Query(min_length=1, max_length=64)]) -> dict[str, Any]:
    """서버가 결정한 안내 메시지 중 이 휴대폰이 카톡 '답장'으로 보내야 할 것"""
    with db.transaction(settings) as conn, conn.cursor() as cur:
        items = store.pending_replies(cur, device_id, store.now_local(settings))
    return {"ok": True, "items": items}


@app.post("/api/replies/{reply_id}/result", dependencies=[Authorized])
def reply_result(
    reply_id: int,
    settings: SettingsDep,
    device_id: Annotated[str, Form(min_length=1, max_length=64)],
    sent: Annotated[bool, Form()],
    note: Annotated[str | None, Form(max_length=250)] = None,
) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        ok = store.finish_reply(cur, reply_id, device_id, sent, note, store.now_local(settings))
    if not ok:
        raise HTTPException(404, "대기 중인 안내가 아닙니다.")
    live.bump("complaint")
    worker.wake()  # 카톡 안내가 끝났으니 기다리던 업체 문자를 바로 보낸다
    return {"ok": True}


@app.get("/api/messages", dependencies=[Reader])
def list_messages(
    settings: SettingsDep,
    room: str | None = None,
    sender: str | None = None,
    before_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict[str, Any]:
    where, params = [], []
    for column, value in (("m.room_name", room), ("m.sender_name", sender)):
        if value:
            where.append(f"{column} = %s")
            params.append(value)
    if before_id:
        where.append("m.id < %s")
        params.append(before_id)
    sql = (
        "SELECT m.id, m.room_name, m.sender_name, m.message_text, m.message_type, m.is_group_chat,"
        " m.posted_at, m.received_at, GROUP_CONCAT(md.id) AS media_ids"
        " FROM kakao_messages m LEFT JOIN kakao_media md ON md.message_id = m.id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " GROUP BY m.id ORDER BY m.id DESC LIMIT %s"
    )
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(sql, (*params, limit))
        rows = cur.fetchall()
    for row in rows:
        row["is_group_chat"] = bool(row["is_group_chat"])
        row["media_ids"] = sorted(int(x) for x in str(row["media_ids"]).split(",")) if row["media_ids"] else []
    return {"ok": True, "items": rows}


@app.get("/api/media/{media_id}", dependencies=[Reader])
def get_media(media_id: int, settings: SettingsDep) -> FileResponse:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT file_path, mime_type FROM kakao_media WHERE id = %s", (media_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "사진이 없습니다.")
    # 파일명이 내용의 SHA-256 이므로 내용이 바뀌지 않는다 → 브라우저 캐시 허용(개인 캐시만)
    return FileResponse(
        media.resolve(settings.media_root, row["file_path"]),
        media_type=row["mime_type"],
        headers={"Cache-Control": "private, max-age=86400"},
    )
