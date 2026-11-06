"""카카오톡 알림 수집 API.

    GET  /api/health               DB 연결 확인 (인증 불필요)
    POST /api/messages             메시지 1건 저장 (multipart, 선택적으로 image 첨부)
    GET  /api/messages             저장된 메시지 조회
    GET  /api/media/{media_id}     저장된 사진 내려받기
    /admin, /api/admin/*           관리자 웹 (app/admin.py, 민원처리업체: app/vendors.py)
    GET  /api/admin/stream         실시간 갱신 신호 (Server-Sent Events)
    /api/admin/categories          민원 카테고리 관리 (app/categories.py)
    /api/admin/data/*              수집 데이터 삭제 (app/datamgmt.py)
    /api/admin/apartments*         건물 관리 (app/apartments.py)
    /api/admin/faq*                FAQ 자동 답변 (app/faq.py)
    /api/admin/schedules*          예약 메시지 (app/schedules.py)

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

from . import admin, apartments, categories, datamgmt, db, examples, faq, live, media, schedules, store, vendors, worker
from .auth import SettingsDep, get_settings, require_api_key, require_reader  # noqa: F401 (get_settings: 테스트에서 사용)

MESSAGE_TYPES = {"text", "photo", "video", "emoticon", "file", "other"}
MAX_TEXT_BYTES = 65535  # MySQL TEXT 한도

app = FastAPI(title="Kakao Notification Collector", version="2.0.0", lifespan=worker.lifespan)
app.include_router(admin.router)
app.include_router(vendors.router)
app.include_router(categories.router)
app.include_router(datamgmt.router)
app.include_router(apartments.router)
app.include_router(schedules.router)
app.include_router(faq.router)
app.include_router(examples.router)
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
    room_key: Annotated[str | None, Form(max_length=64)] = None,
    room_trusted: Annotated[bool, Form()] = True,
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
        renamed = False
        # 채팅방 번호로 방 이름 확정 (방 이름을 못 읽은 알림 · 방 이름 변경)
        room_name = store.resolve_room(
            cur, room_key or None, room_name, room_trusted, datetime.now(settings.timezone).replace(tzinfo=None),
            sender=sender_name, is_group=is_group_chat,
        )
        stored_sender = sender_name
        if sender_key and store.is_unknown_sender(sender_name):
            stored_sender = store.known_name(cur, sender_key) or sender_name  # '(알 수 없음)' → 같은 사람의 실제 이름
        # 카톡이 다음 알림에 지난 메시지를 다시 보여 줄 때 이름이 바뀌어 올 수 있다('(알 수 없음)' ↔ 실제 이름).
        # 같은 방·같은 시각·같은 내용이면 같은 메시지: 새로 저장하지 않고, 이름을 알게 됐으면 기존 기록을 고친다.
        same = store.same_message_other_name(cur, device_id, room_name, posted, text, stored_sender)
        if same is None:
            # 방 이름이 바로잡힌 뒤 같은 메시지가 다시 오면(중복 키는 예전 방 이름 기준이라 다름) 같은 메시지
            cur.execute(
                """SELECT id, sender_name FROM kakao_messages
                   WHERE device_id = %s AND room_name = %s AND posted_at = %s AND message_text = %s AND sender_name = %s AND dedup_key <> %s""",
                (device_id, room_name, posted, text, stored_sender, key),
            )
            same = cur.fetchone()
        if same is not None:
            duplicate = True
            message_id = same["id"]
            if store.is_unknown_sender(same["sender_name"]) and not store.is_unknown_sender(stored_sender):
                cur.execute(
                    "UPDATE kakao_messages SET sender_name = %s, sender_key = COALESCE(%s, sender_key) WHERE id = %s",
                    (stored_sender, sender_key or None, message_id),
                )
                store.fill_unknown(cur, sender_key or None, stored_sender)
                renamed = True
        else:
            try:
                cur.execute(
                    """INSERT INTO kakao_messages
                       (device_id, room_name, sender_name, message_text, message_type, is_group_chat,
                        has_media, posted_at, received_at, notification_key, sender_key, dedup_key, room_key, room_trusted)
                       VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s)""",
                    (device_id, room_name, stored_sender, text, message_type, int(is_group_chat), posted,
                     datetime.now(settings.timezone).replace(tzinfo=None), notification_key, sender_key or None, key,
                     room_key or None, None if not room_key else int(bool(room_trusted))),
                )
                message_id = cur.lastrowid
                store.fill_unknown(cur, sender_key or None, stored_sender)  # 지난 '(알 수 없음)' 메시지에도 이름 채움
                # 참여자 이력: 입장·퇴장 안내 메시지 또는 발언자(첫 발언·대화명 변경)
                store.track_members(cur, room_name, stored_sender, sender_key or None, text, is_group_chat, posted)
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
    elif renamed:
        live.bump("message")  # '(알 수 없음)' 이 실제 이름으로 바뀜 → 화면 갱신
    return {"ok": True, "id": message_id, "duplicate": duplicate, "media_id": media_id}


# ---- 휴대폰 앱: 민원 접수 안내 대기열 -------------------------------------------------------

@app.get("/api/replies/pending", dependencies=[Authorized])
def replies_pending(settings: SettingsDep, device_id: Annotated[str, Query(min_length=1, max_length=64)]) -> dict[str, Any]:
    """서버가 결정한 안내 메시지 중 이 휴대폰이 카톡 '답장'으로 보내야 할 것.
    휴대폰은 30초마다 묻는다 → 마지막 연락 시각을 기록하고, 관리자 웹의 '지금 동기화' 요청 시각을 함께 알려 준다."""
    with db.transaction(settings) as conn, conn.cursor() as cur:
        items = store.pending_replies(cur, device_id, store.now_local(settings))
        store.touch_device(cur, device_id, store.now_local(settings))
        sync_at = store.sync_requested_at(cur)
    return {"ok": True, "items": items, "sync_requested_at": sync_at}


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
