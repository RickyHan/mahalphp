"""관리자 웹: 로그인, 화면(/admin), 조회 API(/api/admin/*). 조회 전용이며 데이터를 바꾸지 않는다."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from . import db
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
