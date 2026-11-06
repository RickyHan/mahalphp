"""민원·참여자·안내 대기열·설정 저장소. MySQL/SQLite 공통 SQL 만 사용한다."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from . import complaints as cp
from . import db
from .config import Settings

SETTING_DEFAULTS = {
    "complaint_enabled": "1",
    "gemini_model": cp.DEFAULT_MODEL,
    "auto_reply_enabled": "1",
    "reply_template": cp.DEFAULT_REPLY_TEMPLATE,
    "group_minutes": str(cp.DEFAULT_GROUP_MINUTES),
    "vendor_enabled": "1",
    "vendor_template": cp.DEFAULT_VENDOR_TEMPLATE,
    "faq_enabled": "1",
    "staff_exclude_enabled": "1",
    "staff_prefixes": "동대표",
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
    conf = {k: values.get(k, v) for k, v in SETTING_DEFAULTS.items()}
    if conf["vendor_template"] in cp.OLD_VENDOR_TEMPLATES:  # 손대지 않은 이전 기본 문구 → 새 기본 문구
        conf["vendor_template"] = cp.DEFAULT_VENDOR_TEMPLATE
    return conf


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


# ---- 채팅방 고유 번호: 방 이름을 못 읽은 메시지 바로잡기 · 방 이름 변경 따라가기 ----------------------------

def resolve_room(cur, room_key: str | None, room_name: str, trusted: bool, at: datetime) -> str:
    """이 메시지를 저장할 방 이름.
    - 방 이름이 확실하면(trusted): 번호 → 이름을 기록. 같은 번호의 이름이 바뀌었으면 방 이름 변경으로 보고 따라간다.
      같은 번호로 엉뚱한 이름(보낸 사람 이름 등)에 들어간 지난 메시지도 바로잡는다.
    - 확실하지 않으면: 같은 번호로 알고 있는 방 이름을 쓴다."""
    if not room_key:
        return room_name
    cur.execute("SELECT room_name FROM chat_rooms WHERE room_key = %s", (room_key,))
    row = cur.fetchone()
    known = row["room_name"] if row else None
    if not trusted:
        return known or room_name
    if known and known != room_name:
        rename_room(cur, room_key, known, room_name)
    cur.execute("DELETE FROM chat_rooms WHERE room_key = %s", (room_key,))
    cur.execute("INSERT INTO chat_rooms (room_key, room_name, updated_at) VALUES (%s, %s, %s)", (room_key, room_name, at))
    cur.execute(
        "UPDATE kakao_messages SET room_name = %s, room_trusted = 1 WHERE room_key = %s AND room_name <> %s",
        (room_name, room_key, room_name),
    )
    return room_name


def rename_room(cur, room_key: str, old: str, new: str) -> None:
    """카톡에서 방 이름이 바뀜(예: ★중앙주택/9세대 → /10세대): 지난 메시지·건물·업체·예약·대기 메시지를 새 이름으로"""
    cur.execute(
        "UPDATE kakao_messages SET room_name = %s WHERE room_name = %s AND (room_key = %s OR room_key IS NULL)",
        (new, old, room_key),
    )
    # 건물 기본 채팅방: 새 이름으로 이미 '목록 외 채팅방'이 생겼으면 그 행은 정리
    cur.execute("SELECT id, listed FROM apartments WHERE room_name = %s", (new,))
    taken = cur.fetchone()
    cur.execute("SELECT id FROM apartments WHERE room_name = %s", (old,))
    holder = cur.fetchone()
    if holder:
        if taken and not taken["listed"]:
            cur.execute("SELECT category_id FROM apartment_vendors WHERE apartment_id = %s", (holder["id"],))
            have = {r["category_id"] for r in cur.fetchall()}
            cur.execute("SELECT id, category_id FROM apartment_vendors WHERE apartment_id = %s", (taken["id"],))
            for r in cur.fetchall():
                if r["category_id"] in have:
                    cur.execute("DELETE FROM apartment_vendors WHERE id = %s", (r["id"],))
                else:
                    cur.execute("UPDATE apartment_vendors SET apartment_id = %s WHERE id = %s", (holder["id"], r["id"]))
            cur.execute("DELETE FROM apartments WHERE id = %s", (taken["id"],))
            taken = None
        if not taken:
            cur.execute("UPDATE apartments SET room_name = %s WHERE id = %s", (new, holder["id"]))
    cur.execute("SELECT id FROM apartment_rooms WHERE room_name = %s", (new,))
    if cur.fetchone() is None:
        cur.execute("UPDATE apartment_rooms SET room_name = %s WHERE room_name = %s", (new, old))
    cur.execute("UPDATE vendors SET room_name = %s WHERE room_name = %s", (new, old))
    cur.execute("UPDATE scheduled_messages SET room_name = %s WHERE room_name = %s", (new, old))
    cur.execute("UPDATE reply_queue SET room_name = %s WHERE room_name = %s AND status = 'pending'", (new, old))
    from . import live

    live.bump("apartment")
    live.bump("vendor")


# ---- '(알 수 없음)' 보낸 사람 → 같은 사람(고유 키)의 실제 이름 ---------------------------------------------

# 카카오톡이 보낸 사람 프로필을 모를 때(나감·탈퇴·프로필 미수신) 알림에 넣는 이름
UNKNOWN_SENDERS = ("(알 수 없음)", "알 수 없음", "(Unknown)", "Unknown")


def is_unknown_sender(name: str | None) -> bool:
    return (name or "").strip() in UNKNOWN_SENDERS


def known_name(cur, sender_key: str | None) -> str | None:
    """같은 고유 키로 이름이 제대로 찍힌 가장 최근 이름"""
    if not sender_key:
        return None
    marks = ", ".join(["%s"] * len(UNKNOWN_SENDERS))
    cur.execute(
        f"SELECT sender_name FROM kakao_messages WHERE sender_key = %s AND sender_name NOT IN ({marks}) ORDER BY id DESC LIMIT 1",
        (sender_key, *UNKNOWN_SENDERS),
    )
    row = cur.fetchone()
    return row["sender_name"] if row else None


def same_message_other_name(cur, device_id: str, room: str, posted: datetime, text: str, sender: str) -> dict[str, Any] | None:
    """같은 휴대폰·방·시각·내용의 메시지가 이름만 '(알 수 없음)' ↔ 실제 이름으로 달리 이미 있으면 그 행"""
    cur.execute(
        "SELECT id, sender_name FROM kakao_messages WHERE device_id = %s AND room_name = %s AND posted_at = %s AND message_text = %s",
        (device_id, room, posted, text),
    )
    for row in cur.fetchall():
        if row["sender_name"] != sender and (is_unknown_sender(row["sender_name"]) or is_unknown_sender(sender)):
            return row
    return None


def fill_unknown(cur, sender_key: str | None, name: str) -> int:
    """이름이 제대로 들어온 사람의 지난 '(알 수 없음)' 메시지에 그 이름을 채운다"""
    if not sender_key or is_unknown_sender(name):
        return 0
    marks = ", ".join(["%s"] * len(UNKNOWN_SENDERS))
    cur.execute(
        f"SELECT COUNT(*) AS n FROM kakao_messages WHERE sender_key = %s AND sender_name IN ({marks})",
        (sender_key, *UNKNOWN_SENDERS),
    )
    n = int(cur.fetchone()["n"])
    if n:
        cur.execute(
            f"UPDATE kakao_messages SET sender_name = %s WHERE sender_key = %s AND sender_name IN ({marks})",
            (name, sender_key, *UNKNOWN_SENDERS),
        )
    return n


def fill_all_unknown(cur) -> int:
    """이미 쌓인 '(알 수 없음)' 메시지를 한꺼번에 정리 (서버 시작 때)"""
    marks = ", ".join(["%s"] * len(UNKNOWN_SENDERS))
    cur.execute(
        f"SELECT DISTINCT sender_key FROM kakao_messages WHERE sender_key IS NOT NULL AND sender_name IN ({marks})",
        UNKNOWN_SENDERS,
    )
    total = 0
    for r in cur.fetchall():
        name = known_name(cur, r["sender_key"])
        if name:
            total += fill_unknown(cur, r["sender_key"], name)
    return total


def track_members(cur, room: str, sender: str, sender_key: str | None, text: str, is_group: bool, at: datetime) -> None:
    if not is_group or sender == cp.SELF_SENDER or is_unknown_sender(sender):
        return  # '(알 수 없음)' 은 참여자 이름으로 기록하지 않음 (가짜 '대화명 변경' 방지)
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
    vendor_rooms = _vendor_rooms(cur)
    staff = staff_pattern(get_settings_map(cur))
    out, skip = [], []
    for r in rows:
        if r["room_name"] in vendor_rooms or _is_vendor_room(r["room_name"]):
            skip.append((r, "업체 톡방 — 민원 분류 안 함"))
        elif staff is not None and staff.match((r["sender_name"] or "").strip()):
            skip.append((r, "직원 닉네임 — 민원 분류 안 함"))
        elif cp.is_eligible(r["message_type"], r["message_text"], r["sender_name"]):
            out.append(r)
        else:
            skip.append((r, "대상 아님(5자 미만)"))
    # 대상이 아닌 메시지는 기록해 다시 조회되지 않게 한다
    for r, note in skip:
        cur.execute(
            "INSERT INTO complaint_check (message_id, verdict, checked_at, reply_note) VALUES (%s, 0, %s, %s)",
            (r["id"], at, note),
        )
    return out[:limit]


def clear_vendor_room_complaints(cur, at: datetime) -> int:
    """이미 업체 톡방에서 민원으로 잡힌 것을 민원에서 빼고, 아직 안 나간 접수 안내·FAQ 답변은 취소한다."""
    linked = _vendor_rooms(cur)
    cur.execute("SELECT DISTINCT room_name FROM kakao_messages")
    rooms = sorted(r["room_name"] for r in cur.fetchall() if r["room_name"] in linked or _is_vendor_room(r["room_name"]))
    if not rooms:
        return 0
    marks = ", ".join(["%s"] * len(rooms))
    cur.execute(
        f"""SELECT c.message_id FROM complaint_check c JOIN kakao_messages m ON m.id = c.message_id
            WHERE c.verdict = 1 AND m.room_name IN ({marks})""",
        rooms,
    )
    ids = [r["message_id"] for r in cur.fetchall()]
    for mid in ids:
        cur.execute("UPDATE complaint_check SET verdict = 0, reply_note = %s WHERE message_id = %s", ("업체 톡방 — 민원 아님", mid))
        cur.execute(
            "UPDATE reply_queue SET status = 'expired', done_at = %s, note = %s WHERE message_id = %s AND status = 'pending' AND kind IN ('ack', 'faq', 'vendor')",
            (at, "업체 톡방 메시지라 취소", mid),
        )
    return len(ids)


def staff_pattern(conf: dict[str, str]):
    """직원 닉네임(예: 동대표1, 동대표 7, (주)동대표3) — 설정의 이름 뒤에 숫자가 붙은 경우. 꺼져 있으면 None"""
    import re

    if conf.get("staff_exclude_enabled") != "1":
        return None
    names = [n.strip() for n in (conf.get("staff_prefixes") or "").replace("\n", ",").split(",") if n.strip()]
    if not names:
        return None
    alt = "|".join(re.escape(n) for n in names)
    return re.compile(rf"^(?:\(주\)|㈜)?\s*(?:{alt})\s*\d+$")


def is_staff_sender(conf: dict[str, str], sender: str | None) -> bool:
    pat = staff_pattern(conf)
    return bool(pat and pat.match((sender or "").strip()))


def _vendor_rooms(cur) -> set[str]:
    """민원처리업체(협력사)의 카톡방: 업체에 연결된 방"""
    cur.execute("SELECT room_name FROM vendors WHERE room_name IS NOT NULL AND room_name <> ''")
    return {r["room_name"] for r in cur.fetchall()}


def _is_vendor_room(room: str) -> bool:
    """● 로 시작하는 방 = 업체 톡방 (업체에 아직 연결 전이어도). 업체 톡방에서는 민원을 접수하지 않는다."""
    from .vendors import is_vendor_room

    return is_vendor_room(room)


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


def group_window(conf: dict[str, str]) -> timedelta:
    """설정의 '같은 문제로 묶는 시간'(분). 값이 이상하면 기본값"""
    try:
        minutes = int(conf.get("group_minutes", cp.DEFAULT_GROUP_MINUTES))
    except (TypeError, ValueError):
        minutes = cp.DEFAULT_GROUP_MINUTES
    lo, hi = cp.GROUP_MINUTES_RANGE
    return timedelta(minutes=min(max(minutes, lo), hi))


def current_group_window(cur) -> timedelta:
    return group_window(get_settings_map(cur))


def is_new_issue(cur, message_id: int, window: timedelta = cp.GROUP_WINDOW) -> bool:
    """같은 방·사람·분류의 이전 민원이 설정한 시간 안에 없으면 새 문제의 첫 민원"""
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
        (message_id, me["room_name"], me["sender_name"], me["category"], at, at - window),
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

def enqueue_reply(cur, message_id: int, device_id: str, room: str, text: str, at: datetime,
                  kind: str = "ack", vendor_id: int | None = None, vendor_name: str | None = None) -> int | None:
    """kind: ack = 민원이 올라온 방에 접수 안내, vendor = 담당 업체 카톡방에 처리 요청, faq = FAQ 자동 답변"""
    cur.execute(
        """INSERT INTO reply_queue (message_id, device_id, room_name, text, status, created_at, kind, vendor_id, vendor_name)
           VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s, %s)""",
        (message_id, device_id, room, text, at, kind, vendor_id, vendor_name),
    )
    return cur.lastrowid


def target_vendors(cur, room: str, category: str) -> list[dict[str, Any]]:
    """처리 요청을 받을 업체:
    민원 방이 아파트이고 그 아파트에 이 분류의 업체가 지정돼 있으면 그 업체('보내지 않음'이면 없음),
    지정이 없으면 기본 업체(민원처리업체의 담당 카테고리). 톡방이 없는 업체는 제외."""
    cur.execute(
        """SELECT av.vendor_id FROM apartment_vendors av
           JOIN apartments a ON a.id = av.apartment_id
           JOIN complaint_categories c ON c.id = av.category_id
           WHERE (a.room_name = %s OR a.id IN (SELECT apartment_id FROM apartment_rooms WHERE room_name = %s)) AND c.name = %s""",
        (room, room, category),
    )
    row = cur.fetchone()
    if row is not None:
        if row["vendor_id"] is None:
            return []
        cur.execute("SELECT id, name, room_name FROM vendors WHERE id = %s AND room_name IS NOT NULL AND room_name <> ''", (row["vendor_id"],))
        return cur.fetchall()
    cur.execute(
        """SELECT v.id, v.name, v.room_name FROM vendors v
           JOIN vendor_categories vc ON vc.vendor_id = v.id
           JOIN complaint_categories c ON c.id = vc.category_id
           WHERE c.name = %s AND v.room_name IS NOT NULL AND v.room_name <> ''""",
        (category,),
    )
    return cur.fetchall()


def enqueue_vendor_messages(cur, conf: dict[str, str], v: cp.Verdict, msg: dict[str, Any], now: datetime) -> int:
    """이 민원을 맡은 업체(아파트별 지정 → 기본 업체)의 카톡방으로 처리 요청 메시지를 대기열에 넣는다."""
    count = 0
    for vendor in target_vendors(cur, msg["room_name"], v.category):
        # 업체 방 알림을 받은 휴대폰(최근 수신 기준)이 보낸다. 없으면 민원을 받은 휴대폰
        cur.execute("SELECT device_id FROM kakao_messages WHERE room_name = %s ORDER BY id DESC LIMIT 1", (vendor["room_name"],))
        row = cur.fetchone()
        device = row["device_id"] if row else msg["device_id"]
        text = cp.format_vendor_message(
            conf["vendor_template"], vendor=vendor["name"], room=msg["room_name"], category=v.category, summary=v.summary,
            content=msg["message_text"] or "", sender=msg["sender_name"], at=to_dt(msg["posted_at"]),
        )
        enqueue_reply(cur, v.id, device, vendor["room_name"], text, now, kind="vendor", vendor_id=vendor["id"], vendor_name=vendor["name"])
        count += 1
    return count


def pending_replies(cur, device_id: str, now: datetime) -> list[dict[str, Any]]:
    """30분 넘게 전달되지 않은 안내는 만료 처리하고, 나머지를 돌려준다"""
    cur.execute(
        """SELECT id, message_id, kind FROM reply_queue WHERE status = 'pending' AND device_id = %s
           AND ((kind IN ('ack', 'faq') AND created_at < %s) OR (kind = 'vendor' AND created_at < %s)
                OR (kind = 'schedule' AND created_at < %s))""",
        (device_id, now - cp.REPLY_MAX_AGE, now - cp.VENDOR_MAX_AGE, now - cp.SCHEDULE_MAX_AGE),
    )
    for r in cur.fetchall():
        if r["kind"] == "vendor":
            note = "12시간 안에 업체 톡방에 새 글(알림)이 오지 않아 보내지 못함"
        elif r["kind"] == "schedule":
            note = "1시간 안에 보내지 못함 (그 방 알림을 받지 못했거나 앱이 꺼져 있음)"
        else:
            note = "30분 안에 보내지 못함 (그 방 알림을 받지 못했거나 앱이 꺼져 있음)"
        cur.execute("UPDATE reply_queue SET status = 'expired', done_at = %s, note = %s WHERE id = %s", (now, note, r["id"]))
        if r["kind"] in ("ack", "faq"):
            set_reply_status(cur, r["message_id"], -1, note)
    cur.execute(
        "SELECT id, room_name, text FROM reply_queue WHERE status = 'pending' AND device_id = %s ORDER BY id",
        (device_id,),
    )
    return cur.fetchall()


def finish_reply(cur, reply_id: int, device_id: str, sent: bool, note: str | None, now: datetime) -> bool:
    cur.execute("SELECT message_id, text, kind FROM reply_queue WHERE id = %s AND device_id = %s AND status = 'pending'", (reply_id, device_id))
    row = cur.fetchone()
    if not row:
        return False
    cur.execute(
        "UPDATE reply_queue SET status = %s, note = %s, done_at = %s WHERE id = %s",
        ("sent" if sent else "failed", (note or "")[:250], now, reply_id),
    )
    if row["kind"] in ("ack", "faq"):  # 업체 요청·예약 메시지 결과는 민원의 '접수 안내' 상태를 바꾸지 않는다
        set_reply_status(cur, row["message_id"], 1 if sent else -1, row["text"] if sent else (note or "전송 실패"))
    return True


# ---- 분류 1회 실행 (워커·수동 실행 공용) ----------------------------------------------------

def enabled_for_prompt(cur) -> list[tuple[str, str]]:
    from .categories import enabled_for_prompt as _f

    return _f(cur)


def classify_once(settings: Settings, client: cp.GeminiClient | None = None) -> dict[str, Any]:
    """대상 메시지 한 묶음을 분류하고, 새 문제의 첫 민원에 안내를 대기열에 넣는다."""
    with db.transaction(settings) as conn, conn.cursor() as cur:
        conf = get_settings_map(cur)
        if conf["complaint_enabled"] != "1" or not settings.gemini_api_key:
            return {"done": 0, "reason": "disabled"}
        clear_vendor_room_complaints(cur, now_local(settings))  # 업체 톡방에서 잘못 잡힌 민원 정리
        batch = unclassified(cur, cp.BATCH, now_local(settings))
        categories = enabled_for_prompt(cur) if batch else []
    if not batch:
        return {"done": 0}

    client = client or cp.GeminiClient(settings.gemini_api_key, conf["gemini_model"], categories=categories)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        items = [cp.Item(r["id"], r["room_name"], r["sender_name"], r["message_text"], room_context(cur, r)) for r in batch]
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
    matches, faq_error = faq_matches(settings, client, conf, items, by_id, now)
    complaints = 0
    vendor_count = 0
    with db.transaction(settings) as conn, conn.cursor() as cur:
        save_verdicts(cur, verdicts, conf["gemini_model"], now)
        missing = set(by_id) - {v.id for v in verdicts}
        if missing:
            mark_failed(cur, sorted(missing), "응답에 결과 없음", now)
        answered = enqueue_faq_answers(cur, conf, matches, by_id, now)
        for v in sorted((v for v in verdicts if v.is_complaint), key=lambda v: v.id):
            complaints += 1
            msg = by_id[v.id]
            fresh = now - to_dt(msg["posted_at"]) <= cp.REPLY_MAX_AGE
            new_issue = fresh and is_new_issue(cur, v.id, group_window(conf))
            if v.id in answered:
                set_reply_status(cur, v.id, 4, "FAQ 답변 전송 대기")  # FAQ 답변이 접수 안내를 대신함
            elif conf["auto_reply_enabled"] == "1":
                if not fresh:
                    set_reply_status(cur, v.id, 3, "30분 지난 메시지라 안내 생략")
                elif not new_issue:
                    set_reply_status(cur, v.id, 2, "같은 문제로 이미 안내")
                else:
                    # 1) 그 방에 카톡 접수 안내 먼저
                    text = cp.format_reply(conf["reply_template"], msg["sender_name"], v.category, v.summary, msg["room_name"])
                    enqueue_reply(cur, v.id, msg["device_id"], msg["room_name"], text, now)
                    set_reply_status(cur, v.id, 4, "휴대폰 전송 대기")
            # 2) 이 카테고리를 맡은 업체의 카톡방에 처리 요청 (접수 안내 뒤 순서로, 같은 문제 반복·오래된 메시지 제외)
            if conf["vendor_enabled"] == "1" and new_issue:
                vendor_count += enqueue_vendor_messages(cur, conf, v, msg, now)
    result = {"done": len(items), "complaints": complaints, "vendor": vendor_count, "faq": len(answered)}
    if faq_error:
        result["faq_error"] = faq_error
    return result


# ---- 대화 맥락 (민원 판정 때 함께 보내는 직전 대화) -------------------------------------------------

CONTEXT_MESSAGES = 4                     # 직전 몇 개
CONTEXT_WINDOW = timedelta(minutes=60)   # 얼마나 이전까지


def room_context(cur, msg: dict[str, Any]) -> tuple[str, ...]:
    """같은 방에서 이 메시지 바로 앞의 대화 몇 줄 ("보낸사람: 내용", 오래된 → 최근)"""
    posted = to_dt(msg["posted_at"])
    cur.execute(
        """SELECT sender_name, message_text, message_type FROM kakao_messages
           WHERE room_name = %s AND id <> %s AND posted_at <= %s AND posted_at >= %s
           ORDER BY posted_at DESC, id DESC LIMIT %s""",
        (msg["room_name"], msg["id"], posted, posted - CONTEXT_WINDOW, CONTEXT_MESSAGES),
    )
    rows = cur.fetchall()
    out = []
    for r in reversed(rows):
        text = (r["message_text"] or "").replace("\n", " ").strip()
        if r["message_type"] != "text":
            text = f"[{r['message_type']}] {text}".strip()
        if text:
            out.append(f"{r['sender_name']}: {text[:300]}")
    return tuple(out)


# ---- FAQ 자동 답변 --------------------------------------------------------------------------

def room_buildings(cur) -> dict[str, dict[str, Any]]:
    """채팅방 → 건물 {id, name}. 건물의 기본 채팅방과 추가 채팅방(예: 상가방) 모두."""
    cur.execute("SELECT id, name, room_name FROM apartments WHERE room_name IS NOT NULL")
    out = {r["room_name"]: {"id": r["id"], "name": r["name"]} for r in cur.fetchall()}
    cur.execute("SELECT r.room_name, a.id, a.name FROM apartment_rooms r JOIN apartments a ON a.id = r.apartment_id")
    for r in cur.fetchall():
        out[r["room_name"]] = {"id": r["id"], "name": r["name"]}
    return out


def building_rooms(cur) -> set[str]:
    """건물 관리에 등록된(연결된) 채팅방(추가 채팅방 포함). 협력사(업체) 방은 들어가지 않는다."""
    return set(room_buildings(cur))


def faq_list(cur) -> list[dict[str, Any]]:
    cur.execute("SELECT id, category, question, answer FROM faq_items WHERE enabled = 1 ORDER BY sort_order, id")
    return cur.fetchall()


def faq_matches(settings: Settings, client, conf: dict[str, str], items: list[cp.Item], by_id: dict[int, Any],
                now: datetime) -> tuple[list[cp.FaqMatch], str | None]:
    """건물 채팅방의 새(30분 이내) 메시지만 FAQ 와 대조한다. 실패해도 민원 분류는 계속한다."""
    if conf.get("faq_enabled") != "1" or not hasattr(client, "match_faq"):
        return [], None
    with db.transaction(settings) as conn, conn.cursor() as cur:
        rooms = building_rooms(cur)
        faqs = faq_list(cur)
    targets = [i for i in items if i.room in rooms and now - to_dt(by_id[i.id]["posted_at"]) <= cp.REPLY_MAX_AGE]
    if not targets or not faqs:
        return [], None
    try:
        return client.match_faq(targets, [(f["id"], f["category"], f["question"]) for f in faqs]), None
    except (cp.GeminiError, ValueError) as exc:
        return [], str(exc)


def enqueue_faq_answers(cur, conf: dict[str, str], matches: list[cp.FaqMatch], by_id: dict[int, Any], now: datetime) -> set[int]:
    """짝이 맞은 메시지에 FAQ 답변을 대기열에 넣는다. 같은 방에 같은 FAQ 를 묶음 시간 안에 이미 보냈으면 생략."""
    answered: set[int] = set()
    if not matches:
        return answered
    cur.execute("SELECT id, question, answer, enabled FROM faq_items")
    faqs = {r["id"]: r for r in cur.fetchall()}
    window = group_window(conf)
    for m in sorted(matches, key=lambda m: m.id):
        faq, msg = faqs.get(m.faq_id), by_id[m.id]
        if not faq or not faq["enabled"]:
            continue
        cur.execute(
            "SELECT id FROM faq_answers WHERE room_name = %s AND faq_id = %s AND reply_id IS NOT NULL AND created_at >= %s",
            (msg["room_name"], m.faq_id, now - window),
        )
        reply_id, note = None, None
        if cur.fetchone():
            note = "같은 방에 같은 답변을 최근에 보내 생략"
        else:
            reply_id = enqueue_reply(cur, m.id, msg["device_id"], msg["room_name"], faq["answer"], now, kind="faq")
            answered.add(m.id)
        cur.execute(
            """INSERT INTO faq_answers (message_id, faq_id, question, room_name, reply_id, confidence, note, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (m.id, m.faq_id, faq["question"][:500], msg["room_name"], reply_id, round(m.confidence, 3), note, now),
        )
    if answered:
        from . import live

        live.bump("faq")
    return answered


# ---- 휴대폰 동기화 ('지금 동기화' 요청 · 휴대폰 마지막 연락) --------------------------------------------

def touch_device(cur, device_id: str, at: datetime) -> None:
    name = f"device_seen:{device_id}"[:64]
    cur.execute("DELETE FROM app_settings WHERE name = %s", (name,))
    cur.execute("INSERT INTO app_settings (name, value) VALUES (%s, %s)", (name, at.isoformat(timespec="seconds")))


def sync_requested_at(cur) -> int:
    """관리자 웹에서 '지금 동기화'를 누른 시각 (epoch ms, 없으면 0)"""
    cur.execute("SELECT value FROM app_settings WHERE name = 'sync_requested_at'")
    row = cur.fetchone()
    try:
        return int(row["value"]) if row else 0
    except (TypeError, ValueError):
        return 0


def request_sync(cur, now_ms: int) -> None:
    cur.execute("DELETE FROM app_settings WHERE name = 'sync_requested_at'")
    cur.execute("INSERT INTO app_settings (name, value) VALUES ('sync_requested_at', %s)", (str(now_ms),))


def device_status(cur) -> list[dict[str, Any]]:
    """휴대폰별 마지막 연락(30초마다 확인) · 마지막으로 받은 메시지 시각"""
    cur.execute("SELECT name, value FROM app_settings WHERE name LIKE %s ESCAPE '!'", (db.like("device_seen:").replace("%", "", 1),))
    seen = {r["name"].split(":", 1)[1]: r["value"] for r in cur.fetchall()}
    cur.execute("SELECT device_id, MAX(received_at) AS last FROM kakao_messages GROUP BY device_id")
    received = {r["device_id"]: r["last"] for r in cur.fetchall()}
    out = []
    for dev in sorted(set(seen) | set(received)):
        last = received.get(dev)
        out.append({
            "device_id": dev,
            "last_seen": seen.get(dev),
            "last_received": last.isoformat() if isinstance(last, datetime) else last,
        })
    return out
