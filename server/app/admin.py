"""관리자 웹: 로그인, 화면(/admin), 조회 API(/api/admin/*). 조회 전용이며 데이터를 바꾸지 않는다."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse

from pydantic import BaseModel, Field

from . import complaints as cp
from . import categories, db, live, store, worker
from .auth import (
    SESSION_COOKIE,
    SettingsDep,
    issue_session,
    login_throttle,
    require_admin,
    session_user,
    verify_credentials,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MESSAGE_TYPES = ("text", "photo", "video", "emoticon", "file", "other")
PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

router = APIRouter()
Admin = Depends(require_admin)


# ---- 화면 ---------------------------------------------------------------------------

@router.get("/admin", include_in_schema=False)
def admin_page(request: Request, settings: SettingsDep):
    if session_user(settings, request.cookies.get(SESSION_COOKIE)) is None:
        return RedirectResponse("/admin/login", status_code=303)
    return FileResponse(STATIC_DIR / "admin.html", headers=PAGE_HEADERS)


@router.get("/admin/login", include_in_schema=False)
def login_page(request: Request, settings: SettingsDep):
    if session_user(settings, request.cookies.get(SESSION_COOKIE)) is not None:
        return RedirectResponse("/admin", status_code=303)
    if not settings.admin_password:
        return HTMLResponse(
            "<meta charset=utf-8><p style='font-family:sans-serif;padding:2rem'>관리자 웹이 비활성 상태입니다. "
            "server/.env 에 ADMIN_PASSWORD 를 설정한 뒤 서버를 다시 시작하십시오.</p>",
            status_code=503,
        )
    return FileResponse(STATIC_DIR / "login.html", headers=PAGE_HEADERS)


@router.post("/admin/login", include_in_schema=False)
def login(
    request: Request,
    settings: SettingsDep,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
):
    client = request.client.host if request.client else "unknown"
    if login_throttle.blocked(client):
        return RedirectResponse("/admin/login?error=locked", status_code=303)
    if not verify_credentials(settings, username, password):
        login_throttle.fail(client)
        return RedirectResponse("/admin/login?error=invalid", status_code=303)

    login_throttle.reset(client)
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        issue_session(settings),
        max_age=settings.session_hours * 3600,
        httponly=True,
        samesite="strict",
        secure=_is_https(request),
        path="/",
    )
    return response


@router.post("/admin/logout", include_in_schema=False)
def logout():
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


# ---- 조회 API -------------------------------------------------------------------------

@router.get("/api/admin/me")
def me(user: Annotated[str, Admin]) -> dict[str, Any]:
    return {"ok": True, "user": user}


# 연결 하나를 이 시간만 유지하고 끊는다(20초). 브라우저(EventSource)가 곧바로 다시 연결하며,
# 서버 재시작이 열린 연결 때문에 오래 지연되지 않게 한다.
STREAM_SECONDS = 20.0
STREAM_TICK = 0.5
STREAM_PING = 15.0


@router.get("/api/admin/stream", dependencies=[Admin])
async def stream(request: Request) -> StreamingResponse:
    """실시간 갱신 신호 (Server-Sent Events). 새 메시지·민원 분류·업체 변경 시 'change' 이벤트."""

    async def events():
        version, _ = live.snapshot()
        yield "retry: 1000\nevent: hello\ndata: {}\n\n"
        started = last_sent = time.monotonic()
        while time.monotonic() - started < STREAM_SECONDS:
            if await request.is_disconnected():
                return
            await asyncio.sleep(STREAM_TICK)
            current, topics = live.snapshot()
            now = time.monotonic()
            if current != version:
                changed = sorted(k for k, v in topics.items() if v > version)
                version = current
                last_sent = now
                yield f"event: change\ndata: {json.dumps({'topics': changed})}\n\n"
            elif now - last_sent >= STREAM_PING:
                last_sent = now
                yield ": ping\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},  # nginx 가 모아 두지 않고 바로 전달
    )


@router.get("/api/admin/summary", dependencies=[Admin])
def summary(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        today = datetime.now(settings.timezone).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        cur.execute(
            """SELECT COUNT(*) AS messages, COUNT(DISTINCT room_name) AS rooms, COUNT(DISTINCT device_id) AS devices,
                      COALESCE(SUM(CASE WHEN posted_at >= %s THEN 1 ELSE 0 END), 0) AS today,
                      MAX(received_at) AS last_received_at
               FROM kakao_messages""",
            (today,),
        )
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS photos FROM kakao_media")
        row["photos"] = cur.fetchone()["photos"]
    row["today"] = int(row["today"])
    return {"ok": True, **row}


@router.get("/api/admin/rooms", dependencies=[Admin])
def rooms(settings: SettingsDep, q: str | None = None) -> dict[str, Any]:
    where, params = "", []
    if q:
        where, params = "WHERE room_name LIKE %s ESCAPE '!'", [db.like(q)]
    sql = f"""
        SELECT r.room_name, r.message_count, r.photo_count, m.id AS last_id, m.sender_name AS last_sender,
               m.message_text AS last_message, m.message_type AS last_type, m.posted_at AS last_posted_at,
               m.is_group_chat
        FROM (SELECT room_name, COUNT(*) AS message_count, SUM(has_media) AS photo_count, MAX(id) AS last_id
              FROM kakao_messages {where} GROUP BY room_name) r
        JOIN kakao_messages m ON m.id = r.last_id
        ORDER BY m.id DESC
        LIMIT 500"""
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        items = cur.fetchall()
    for item in items:
        item["photo_count"] = int(item["photo_count"] or 0)
        item["is_group_chat"] = bool(item["is_group_chat"])
        item["last_message"] = (item["last_message"] or "")[:120]
    return {"ok": True, "items": items}


@router.get("/api/admin/messages", dependencies=[Admin])
def messages(
    settings: SettingsDep,
    room: str | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sender: str | None = None,
    type: Annotated[str | None, Query(pattern="^(" + "|".join(MESSAGE_TYPES) + ")$")] = None,
    before_id: Annotated[int | None, Query(gt=0)] = None,
    after_id: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=300)] = 100,
) -> dict[str, Any]:
    """메시지 목록(항상 오래된 → 최신 순).
    before_id: 그보다 오래된 메시지(과거 더 보기) / after_id: 그보다 새 메시지(새 메시지 확인)."""
    where, params = [], []
    for column, value in (("room_name", room), ("sender_name", sender), ("message_type", type)):
        if value:
            where.append(f"{column} = %s")
            params.append(value)
    if q:
        where.append("(message_text LIKE %s ESCAPE '!' OR sender_name LIKE %s ESCAPE '!')")
        params += [db.like(q), db.like(q)]
    if before_id:
        where.append("id < %s")
        params.append(before_id)
    newer = after_id is not None
    if newer:
        where.append("id > %s")
        params.append(after_id)

    sql = (
        "SELECT id, room_name, sender_name, message_text, message_type, is_group_chat, has_media, posted_at, received_at"
        " FROM kakao_messages" + (" WHERE " + " AND ".join(where) if where else "")
        + f" ORDER BY id {'ASC' if newer else 'DESC'} LIMIT %s"
    )
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(sql, (*params, limit + 1))
        rows = cur.fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        if not newer:
            rows.reverse()

        media_by_message: dict[int, list[dict[str, Any]]] = {}
        ids = [r["id"] for r in rows if r["has_media"]]
        if ids:
            cur.execute(
                f"SELECT id, message_id, width, height, mime_type FROM kakao_media"
                f" WHERE message_id IN ({','.join(['%s'] * len(ids))}) ORDER BY id",
                ids,
            )
            for m in cur.fetchall():
                media_by_message.setdefault(m.pop("message_id"), []).append(m)

    for r in rows:
        r["is_group_chat"] = bool(r["is_group_chat"])
        r["media"] = media_by_message.get(r["id"], [])
        del r["has_media"]
    return {"ok": True, "items": rows, "has_more": has_more}


@router.get("/api/admin/photos", dependencies=[Admin])
def photos(
    settings: SettingsDep,
    room: str | None = None,
    before_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
) -> dict[str, Any]:
    where, params = [], []
    if room:
        where.append("m.room_name = %s")
        params.append(room)
    if before_id:
        where.append("md.id < %s")
        params.append(before_id)
    sql = (
        "SELECT md.id, md.width, md.height, md.mime_type, md.size_bytes, md.message_id,"
        " m.room_name, m.sender_name, m.message_text, m.posted_at"
        " FROM kakao_media md JOIN kakao_messages m ON m.id = md.message_id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY md.id DESC LIMIT %s"
    )
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(sql, (*params, limit + 1))
        rows = cur.fetchall()
    return {"ok": True, "items": rows[:limit], "has_more": len(rows) > limit}


# ---- 민원·통계·참여자·설정 (웹 화면용) ------------------------------------------------------

def _serialize(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _ser_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _serialize(v) for k, v in row.items()}


@router.get("/api/admin/complaints", dependencies=[Admin])
def complaint_groups(settings: SettingsDep, category: str | None = None) -> dict[str, Any]:
    """민원을 '문제' 단위로 묶어 최근 순으로"""
    with db.transaction(settings) as conn, conn.cursor() as cur:
        rows = store.complaint_rows(cur, category=category or None)
        counts = store.complaint_counts(cur)
        names = [c["name"] for c in categories.all_categories(cur)]
        window = store.current_group_window(cur)
    groups = [{k: _serialize(v) for k, v in g.to_dict().items()} for g in cp.group_complaints(rows, window)]
    # 문제(첫 민원)별 업체 카톡방 전달 결과
    firsts = [g["ids"][0] for g in groups][:500]
    sent_by_message: dict[int, list[dict[str, Any]]] = {}
    if firsts:
        with db.transaction(settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT message_id, vendor_name, room_name, status, note FROM reply_queue
                    WHERE kind = 'vendor' AND message_id IN ({', '.join(['%s'] * len(firsts))}) ORDER BY id""",
                firsts,
            )
            for r in cur.fetchall():
                sent_by_message.setdefault(r["message_id"], []).append(
                    {"vendor": r["vendor_name"], "room": r["room_name"], "status": r["status"], "note": r["note"]}
                )
    for g in groups:
        g["vendor_dispatch"] = sent_by_message.get(g["ids"][0], [])
    return {
        "ok": True,
        "items": groups,
        "messages": len(rows),
        "counts": counts,
        "worker": worker.status,
        "gemini_configured": bool(settings.gemini_api_key),
        "categories": names,
    }


@router.get("/api/admin/complaints/group", dependencies=[Admin])
def complaint_group(settings: SettingsDep, ids: Annotated[str, Query(pattern=r"^\d+(,\d+){0,199}$")]) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        rows = store.complaint_rows(cur, ids=[int(x) for x in ids.split(",")])
    return {"ok": True, "items": [_ser_row(r) for r in rows]}


@router.get("/api/admin/stats", dependencies=[Admin])
def complaint_stats(settings: SettingsDep, period: Annotated[str, Query(pattern="^(today|week|month|all)$")] = "week") -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        rows = store.complaint_rows(cur)
        order = [c["name"] for c in categories.all_categories(cur)]
        stats = cp.compute_stats(cp.group_complaints(rows, store.current_group_window(cur)), period, store.now_local(settings))
        # 업체 카톡방 전달
        start = stats.pop("range_start")
        where, params = "kind = 'vendor'", []
        if start is not None:
            where += " AND created_at >= %s"
            params.append(start)
        cur.execute(f"SELECT status, COUNT(*) AS n FROM reply_queue WHERE {where} GROUP BY status", params)
        vendor_counts = {r["status"]: int(r["n"]) for r in cur.fetchall()}
    return {
        "ok": True,
        **stats,
        "category_order": order,  # 색은 이 순서로 고정 배정 (기간이 바뀌어도 같은 분류는 같은 색)
        "vendor_sent": vendor_counts.get("sent", 0),
        "vendor_failed": vendor_counts.get("failed", 0) + vendor_counts.get("expired", 0),
        "vendor_waiting": vendor_counts.get("pending", 0),
    }


@router.get("/api/admin/member-events", dependencies=[Admin])
def member_events(
    settings: SettingsDep,
    room: str | None = None,
    type: Annotated[str | None, Query(pattern="^(join|leave|kick|invite|rename|first_seen)$")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> dict[str, Any]:
    where, params = [], []
    if room:
        where.append("room_name = %s")
        params.append(room)
    if type:
        where.append("type = %s")
        params.append(type)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, room_name, type, member, old_name, actor, at, text FROM member_events"
            + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY at DESC, id DESC LIMIT %s",
            (*params, limit),
        )
        items = [_ser_row(r) for r in cur.fetchall()]
        cur.execute("SELECT room_name, MAX(at) AS t FROM member_events GROUP BY room_name ORDER BY t DESC")
        rooms = [r["room_name"] for r in cur.fetchall()]
    for i in items:
        i["label"] = cp.EVENT_LABELS.get(i["type"], i["type"])
    return {"ok": True, "items": items, "rooms": rooms}


@router.get("/api/admin/members", dependencies=[Admin])
def members(settings: SettingsDep, room: Annotated[str, Query(min_length=1)]) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT display_name, keyed, first_seen, last_seen, message_count FROM room_members WHERE room_name = %s ORDER BY last_seen DESC",
            (room,),
        )
        items = [_ser_row(r) for r in cur.fetchall()]
    for i in items:
        i["keyed"] = bool(i["keyed"])
    return {"ok": True, "items": items}


class SettingsBody(BaseModel):
    complaint_enabled: bool | None = None
    gemini_model: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9._\-]+$")
    auto_reply_enabled: bool | None = None
    reply_template: str | None = Field(default=None, max_length=300)
    group_minutes: int | None = Field(default=None, ge=cp.GROUP_MINUTES_RANGE[0], le=cp.GROUP_MINUTES_RANGE[1])
    vendor_enabled: bool | None = None
    vendor_template: str | None = Field(default=None, max_length=1000)


@router.get("/api/admin/settings", dependencies=[Admin])
def get_app_settings(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        conf = store.get_settings_map(cur)
    return {
        "ok": True,
        "complaint_enabled": conf["complaint_enabled"] == "1",
        "gemini_model": conf["gemini_model"],
        "auto_reply_enabled": conf["auto_reply_enabled"] == "1",
        "reply_template": conf["reply_template"],
        "group_minutes": int(store.group_window(conf).total_seconds() // 60),
        "vendor_enabled": conf["vendor_enabled"] == "1",
        "vendor_template": conf["vendor_template"],
        "vendor_default_template": cp.DEFAULT_VENDOR_TEMPLATE,
        "vendor_placeholders": cp.VENDOR_PLACEHOLDERS,
        "gemini_configured": bool(settings.gemini_api_key),
        "worker": worker.status,
    }


@router.put("/api/admin/settings", dependencies=[Admin])
def put_app_settings(body: SettingsBody, settings: SettingsDep) -> dict[str, Any]:
    values: dict[str, str] = {}
    if body.complaint_enabled is not None:
        values["complaint_enabled"] = "1" if body.complaint_enabled else "0"
    if body.auto_reply_enabled is not None:
        values["auto_reply_enabled"] = "1" if body.auto_reply_enabled else "0"
    if body.gemini_model:
        values["gemini_model"] = body.gemini_model
    if body.reply_template is not None:
        values["reply_template"] = body.reply_template.strip() or cp.DEFAULT_REPLY_TEMPLATE
    if body.group_minutes is not None:
        values["group_minutes"] = str(body.group_minutes)
    if body.vendor_enabled is not None:
        values["vendor_enabled"] = "1" if body.vendor_enabled else "0"
    if body.vendor_template is not None:
        values["vendor_template"] = body.vendor_template.strip() or cp.DEFAULT_VENDOR_TEMPLATE
    with db.transaction(settings) as conn, conn.cursor() as cur:
        store.put_settings(cur, values)
    worker.wake()
    return get_app_settings(settings)


@router.post("/api/admin/classify", dependencies=[Admin])
def classify_now(settings: SettingsDep, retry_failed: bool = False) -> dict[str, Any]:
    reset = 0
    if retry_failed:
        with db.transaction(settings) as conn, conn.cursor() as cur:
            reset = store.reset_failed(cur)
    worker.wake()
    return {"ok": True, "reset": reset}
