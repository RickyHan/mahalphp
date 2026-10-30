"""민원·참여자·안내 대기열·설정 저장소. MySQL/SQLite 공통 SQL 만 사용한다."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import complaints as cp
from . import db
from .config import Settings

SETTING_DEFAULTS = {
    "complaint_enabled": "1",
    "gemini_model": cp.DEFAULT_MODEL,
    "auto_reply_enabled": "1",
    "reply_template": cp.DEFAULT_REPLY_TEMPLATE,
}


def to_dt(value: Any) -> datetime:
    """MySQL 은 datetime, SQLite 는 ISO 문자열로 돌려준다 → datetime 으로 통일"""
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def now_local(settings: Settings) -> datetime:
    return datetime.now(settings.timezone).replace(tzinfo=None)


# ---- 설정 ---------------------------------------------------------------------------------

def get_settings_map(cur) -> dict[str, str]:
    cur.execute("SELECT name, value FROM app_settings")
    values = {r["name"]: r["value"] for r in cur.fetchall()}
    return {k: values.get(k, v) for k, v in SETTING_DEFAULTS.items()}


def put_settings(cur, values: dict[str, str]) -> None:
    for name, value in values.items():
        if name not in SETTING_DEFAULTS:
            continue
        cur.execute("DELETE FROM app_settings WHERE name = %s", (name,))
        cur.execute("INSERT INTO app_settings (name, value) VALUES (%s, %s)", (name, value))


# ---- 참여자 -------------------------------------------------------------------------------

def add_event(cur, room: str, type_: str, member: str, at: datetime, old_name: str | None = None,
              actor: str | None = None, text: str | None = None) -> None:
    cur.execute(
        "INSERT INTO member_events (room_name, type, member, old_name, actor, at, text) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (room, type_, member, old_name, actor, at, text),
    )


def record_sender(cur, room: str, sender_key: str | None, name: str, at: datetime) -> None:
    """처음 보는 사람이면 '첫 발언', 같은 고유 키인데 이름이 다르면 '대화명 변경' 이력을 남긴다."""
    keyed = bool(sender_key)
    key = f"k:{sender_key}" if keyed else f"n:{name}"
    cur.execute("SELECT display_name FROM room_members WHERE room_name = %s AND member_key = %s", (room, key))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO room_members (room_name, member_key, display_name, keyed, first_seen, last_seen, message_count) "
            "VALUES (%s, %s, %s, %s, %s, %s, 1)",
            (room, key, name, int(keyed), at, at),
        )
        add_event(cur, room, "first_seen", name, at)
        return
    if row["display_name"] != name:
        add_event(cur, room, "rename", name, at, old_name=row["display_name"])
    cur.execute(
        "UPDATE room_members SET display_name = %s, message_count = message_count + 1, "
        "last_seen = CASE WHEN last_seen < %s THEN %s ELSE last_seen END, "
        "first_seen = CASE WHEN first_seen > %s THEN %s ELSE first_seen END "
        "WHERE room_name = %s AND member_key = %s",
        (name, at, at, at, at, room, key),
    )


def track_members(cur, room: str, sender: str, sender_key: str | None, text: str, is_group: bool, at: datetime) -> None:
    if not is_group or sender == cp.SELF_SENDER:
        return
    events = cp.detect_member_events(text)
    if events:
        for type_, member, actor in events:
            add_event(cur, room, type_, member, at, actor=actor, text=text)
    else:
        record_sender(cur, room, sender_key, name=sender, at=at)


# ---- 민원 분류 ---------------------------------------------------------------------------

def unclassified(cur, limit: int, at: datetime) -> list[dict[str, Any]]:
    """분류 전 대상 후보 (SQL 로 거른 뒤 Python 규칙으로 최종 판정)"""
    cur.execute(
        "SELECT m.id, m.room_name, m.sender_name, m.message_text, m.message_type, m.posted_at, m.device_id "
        "FROM kakao_messages m LEFT JOIN complaint_check c ON c.message_id = m.id "
        "WHERE c.message_id IS NULL AND m.message_type = 'text' AND m.sender_name <> %s "
        "ORDER BY m.id LIMIT %s",
        (cp.SELF_SENDER, limit * 3),
    )
    rows = cur.fetchall()
    out, skip = [], []
    for r in rows:
        (out if cp.is_eligible(r["message_type"], r["message_text"], r["sender_name"]) else skip).append(r)
    # 짧은 메시지는 대상 아님으로 기록해 다시 조회되지 않게 한다
    for r in skip:
        cur.execute(
            "INSERT INTO complaint_check (message_id, verdict, checked_at, reply_note) VALUES (%s, 0, %s, %s)",
            (r["id"], at, "대상 아님(5자 미만)"),
        )
    return out[:limit]


def save_verdicts(cur, verdicts: list[cp.Verdict], model: str, at: datetime) -> None:
    for v in verdicts:
        cur.execute("DELETE FROM complaint_check WHERE message_id = %s", (v.id,))
        cur.execute(
            "INSERT INTO complaint_check (message_id, verdict, category, summary, confidence, model, checked_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (v.id, 1 if v.is_complaint else 0, v.category, v.summary, v.confidence, model, at),
        )


def mark_failed(cur, ids: list[int], reason: str, at: datetime) -> None:
    for i in ids:
        cur.execute("DELETE FROM complaint_check WHERE message_id = %s", (i,))
        cur.execute(
            "INSERT INTO complaint_check (message_id, verdict, checked_at, reply_note) VALUES (%s, -1, %s, %s)",
            (i, at, reason[:250]),
        )


def reset_failed(cur) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM complaint_check WHERE verdict = -1")
    n = cur.fetchone()["n"]
    cur.execute("DELETE FROM complaint_check WHERE verdict = -1")
    return int(n)


def is_new_issue(cur, message_id: int) -> bool:
    """같은 방·사람·분류의 이전 민원이 30분 이내에 없으면 새 문제의 첫 민원"""
    cur.execute(
        "SELECT m.room_name, m.sender_name, m.posted_at, c.category FROM kakao_messages m "
        "JOIN complaint_check c ON c.message_id = m.id WHERE m.id = %s",
        (message_id,),
    )
    me = cur.fetchone()
    if not me:
        return False
    at = to_dt(me["posted_at"])
    cur.execute(
        "SELECT COUNT(*) AS n FROM kakao_messages m JOIN complaint_check c ON c.message_id = m.id "
        "WHERE c.verdict = 1 AND m.id <> %s AND m.room_name = %s AND m.sender_name = %s AND c.category = %s "
        "AND m.posted_at <= %s AND m.posted_at >= %s",
        (message_id, me["room_name"], me["sender_name"], me["category"], at, at - cp.GROUP_WINDOW),
    )
    return cur.fetchone()["n"] == 0


def set_reply_status(cur, message_id: int, status: int, note: str | None) -> None:
    cur.execute("UPDATE complaint_check SET reply_status = %s, reply_note = %s WHERE message_id = %s", (status, note, message_id))


def complaint_rows(cur, category: str | None = None, ids: list[int] | None = None) -> list[dict[str, Any]]:
    where, params = ["c.verdict = 1"], []
    if category:
        where.append("c.category = %s")
        params.append(category)
    if ids:
        where.append(f"m.id IN ({','.join(['%s'] * len(ids))})")
        params += ids
    cur.execute(
        "SELECT m.id, m.room_name, m.sender_name, m.message_text, m.posted_at, "
        "c.category, c.summary, c.confidence, c.reply_status, c.reply_note "
        "FROM complaint_check c JOIN kakao_messages m ON m.id = c.message_id WHERE " + " AND ".join(where) +
        " ORDER BY m.id",
        params,
    )
    rows = cur.fetchall()
    for r in rows:
        r["posted_at"] = to_dt(r["posted_at"])
    return rows


def complaint_counts(cur) -> dict[str, int]:
    cur.execute(
        "SELECT COUNT(*) AS checked, COALESCE(SUM(CASE WHEN verdict = 1 THEN 1 ELSE 0 END), 0) AS complaints, "
        "COALESCE(SUM(CASE WHEN verdict = -1 THEN 1 ELSE 0 END), 0) AS failed FROM complaint_check"
    )
    r = cur.fetchone()
    cur.execute(
        "SELECT COUNT(*) AS n FROM kakao_messages m LEFT JOIN complaint_check c ON c.message_id = m.id "
        "WHERE c.message_id IS NULL AND m.message_type = 'text' AND m.sender_name <> %s",
        (cp.SELF_SENDER,),
    )
    return {"checked": int(r["checked"]), "complaints": int(r["complaints"]), "failed": int(r["failed"]), "waiting": int(cur.fetchone()["n"])}


# ---- 안내 발송 대기열 -------------------------------------------------------------------

def enqueue_reply(cur, message_id: int, device_id: str, room: str, text: str, at: datetime) -> None:
    cur.execute(
        "INSERT INTO reply_queue (message_id, device_id, room_name, text, status, created_at) VALUES (%s, %s, %s, %s, 'pending', %s)",
        (message_id, device_id, room, text, at),
    )


def pending_replies(cur, device_id: str, now: datetime) -> list[dict[str, Any]]:
    """30분 넘게 전달되지 않은 안내는 만료 처리하고, 나머지를 돌려준다"""
    cur.execute(
        "SELECT id, message_id FROM reply_queue WHERE status = 'pending' AND device_id = %s AND created_at < %s",
        (device_id, now - cp.REPLY_MAX_AGE),
    )
    for r in cur.fetchall():
        cur.execute("UPDATE reply_queue SET status = 'expired', done_at = %s, note = %s WHERE id = %s", (now, "앱이 30분 안에 가져가지 않음", r["id"]))
        set_reply_status(cur, r["message_id"], -1, "휴대폰 앱이 30분 안에 안내를 가져가지 않음")
    cur.execute(
        "SELECT id, room_name, text FROM reply_queue WHERE status = 'pending' AND device_id = %s ORDER BY id",
        (device_id,),
    )
    return cur.fetchall()


def finish_reply(cur, reply_id: int, device_id: str, sent: bool, note: str | None, now: datetime) -> bool:
    cur.execute("SELECT message_id, text FROM reply_queue WHERE id = %s AND device_id = %s AND status = 'pending'", (reply_id, device_id))
    row = cur.fetchone()
    if not row:
        return False
    cur.execute(
        "UPDATE reply_queue SET status = %s, note = %s, done_at = %s WHERE id = %s",
        ("sent" if sent else "failed", (note or "")[:250], now, reply_id),
    )
    set_reply_status(cur, row["message_id"], 1 if sent else -1, row["text"] if sent else (note or "전송 실패"))
    return True


# ---- 분류 1회 실행 (워커·수동 실행 공용) ----------------------------------------------------

def classify_once(settings: Settings, client: cp.GeminiClient | None = None) -> dict[str, Any]:
    """대상 메시지 한 묶음을 분류하고, 새 문제의 첫 민원에 안내를 대기열에 넣는다."""
    with db.transaction(settings) as conn, conn.cursor() as cur:
        conf = get_settings_map(cur)
        if conf["complaint_enabled"] != "1" or not settings.gemini_api_key:
            return {"done": 0, "reason": "disabled"}
        batch = unclassified(cur, cp.BATCH, now_local(settings))
    if not batch:
        return {"done": 0}

    client = client or cp.GeminiClient(settings.gemini_api_key, conf["gemini_model"])
    items = [cp.Item(r["id"], r["room_name"], r["sender_name"], r["message_text"]) for r in batch]
    now = now_local(settings)
    try:
        verdicts = client.classify(items)
    except cp.GeminiError as exc:
        if exc.retry:
            return {"done": 0, "retry": True, "reason": str(exc)}
        return {"done": 0, "stop": True, "reason": str(exc)}
    except ValueError as exc:
        with db.transaction(settings) as conn, conn.cursor() as cur:
            mark_failed(cur, [i.id for i in items], str(exc), now)
        return {"done": len(items), "failed": len(items)}

    by_id = {r["id"]: r for r in batch}
    complaints = 0
    with db.transaction(settings) as conn, conn.cursor() as cur:
        save_verdicts(cur, verdicts, conf["gemini_model"], now)
        missing = set(by_id) - {v.id for v in verdicts}
        if missing:
            mark_failed(cur, sorted(missing), "응답에 결과 없음", now)
        for v in sorted((v for v in verdicts if v.is_complaint), key=lambda v: v.id):
            complaints += 1
            msg = by_id[v.id]
            if conf["auto_reply_enabled"] != "1":
                continue
            if now - to_dt(msg["posted_at"]) > cp.REPLY_MAX_AGE:
                set_reply_status(cur, v.id, 3, "30분 지난 메시지라 안내 생략")
            elif not is_new_issue(cur, v.id):
                set_reply_status(cur, v.id, 2, "같은 문제로 이미 안내")
            else:
                text = cp.format_reply(conf["reply_template"], msg["sender_name"], v.category, v.summary, msg["room_name"])
                enqueue_reply(cur, v.id, msg["device_id"], msg["room_name"], text, now)
                set_reply_status(cur, v.id, 4, "휴대폰 전송 대기")
    return {"done": len(items), "complaints": complaints}
