"""예약 메시지: 정한 날짜·요일·시각에 특정 카톡 채팅방으로 메시지를 보낸다.

    GET    /api/admin/schedules                 목록 (다음 발송·최근 결과)
    POST   /api/admin/schedules                 추가
    PUT    /api/admin/schedules/{id}            수정
    DELETE /api/admin/schedules/{id}            삭제
    POST   /api/admin/schedules/{id}/enabled    사용 켜기/끄기
    POST   /api/admin/schedules/{id}/send-now   지금 한 번 보내기 (예약은 그대로)
    GET    /api/admin/schedules/history         발송 이력

반복: once(한 번: 날짜+시각) · daily(매일) · weekly(요일 여러 개, 0=월~6=일) · monthly(매월 n일, 그 달에 없으면 말일).
서버 스케줄러가 20초마다 확인해 시각이 되면 휴대폰 앱의 카톡 답장 대기열(reply_queue, kind='schedule')에 넣는다.
서버가 꺼져 있어 30분 넘게 지난 회차는 보내지 않고 다음 회차로 넘긴다.
"""

from __future__ import annotations

import calendar
import logging
import re
import threading
from datetime import date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import db, live
from .auth import SettingsDep, get_settings, require_admin

log = logging.getLogger("kakao.schedule")
router = APIRouter(dependencies=[Depends(require_admin)])

KINDS = ("once", "daily", "weekly", "monthly")
MISS_LIMIT = timedelta(minutes=30)      # 이보다 늦게 발견한 회차는 건너뜀
SEND_MAX_AGE = timedelta(hours=1)       # 휴대폰이 이 시간 안에 못 보내면 만료
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PLACEHOLDERS = {"{날짜}": "보내는 날 (예: 11월 3일)", "{요일}": "보내는 요일 (예: 화요일)", "{시각}": "보내는 시각 (예: 09:00)"}


# ---- 순수 계산 -------------------------------------------------------------------------------

def _at(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(d, time(h, m))


def _weekdays(raw: str | None) -> list[int]:
    return sorted({int(x) for x in (raw or "").split(",") if x.strip().isdigit() and 0 <= int(x) <= 6})


def next_run(s: dict[str, Any], after: datetime) -> datetime | None:
    """after 보다 뒤의 다음 발송 시각 (없으면 None)"""
    hhmm = s["run_time"]
    kind = s["kind"]
    if kind == "once":
        when = _at(date.fromisoformat(s["run_date"]), hhmm)
        return when if when > after else None
    if kind == "daily":
        when = _at(after.date(), hhmm)
        return when if when > after else when + timedelta(days=1)
    if kind == "weekly":
        days = _weekdays(s.get("weekdays"))
        for i in range(0, 8):
            d = after.date() + timedelta(days=i)
            if d.weekday() in days and _at(d, hhmm) > after:
                return _at(d, hhmm)
        return None
    if kind == "monthly":
        day = int(s["month_day"])
        y, m = after.year, after.month
        for _ in range(14):
            last = calendar.monthrange(y, m)[1]
            when = _at(date(y, m, min(day, last)), hhmm)
            if when > after:
                return when
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return None


def describe(s: dict[str, Any]) -> str:
    """목록에 보여 줄 반복 요약"""
    t = s["run_time"]
    if s["kind"] == "once":
        return f"{s['run_date']} {t} 한 번"
    if s["kind"] == "daily":
        return f"매일 {t}"
    if s["kind"] == "weekly":
        days = _weekdays(s.get("weekdays"))
        label = "평일" if days == [0, 1, 2, 3, 4] else "주말" if days == [5, 6] else "·".join(WEEKDAY_KO[d] for d in days)
        return f"매주 {label} {t}"
    return f"매월 {s['month_day']}일 {t}"


def render_text(text: str, at: datetime) -> str:
    return (text.replace("{날짜}", f"{at.month}월 {at.day}일")
                .replace("{요일}", WEEKDAY_KO[at.weekday()] + "요일")
                .replace("{시각}", at.strftime("%H:%M"))).strip()


# ---- 발송 ------------------------------------------------------------------------------------

def _now(settings) -> datetime:
    return datetime.now(settings.timezone).replace(tzinfo=None)


def _to_dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def _device_for(cur, room: str) -> str | None:
    """그 방 알림을 받은 휴대폰(최근 기준). 없으면 가장 최근에 수집한 휴대폰"""
    cur.execute("SELECT device_id FROM kakao_messages WHERE room_name = %s ORDER BY id DESC LIMIT 1", (room,))
    row = cur.fetchone()
    if row:
        return row["device_id"]
    cur.execute("SELECT device_id FROM kakao_messages ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    return row["device_id"] if row else None


def enqueue(cur, s: dict[str, Any], at: datetime) -> bool:
    device = _device_for(cur, s["room_name"])
    if device is None:
        return False
    cur.execute(
        """INSERT INTO reply_queue (message_id, device_id, room_name, text, status, created_at, kind, schedule_id, vendor_name)
           VALUES (0, %s, %s, %s, 'pending', %s, 'schedule', %s, %s)""",
        (device, s["room_name"], render_text(s["text"], at), at, s["id"], s["title"]),
    )
    return True


def run_due(settings, now: datetime | None = None) -> int:
    """보낼 때가 된 예약을 대기열에 넣는다. 반환: 넣은 건수"""
    now = now or _now(settings)
    queued = 0
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM scheduled_messages WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= %s", (now,))
        for s in cur.fetchall():
            due = _to_dt(s["next_run_at"])
            if now - due > MISS_LIMIT:
                note = f"{due:%m-%d %H:%M} 회차를 서버가 꺼져 있어 건너뜀"
            elif enqueue(cur, s, now):
                note = f"{now:%m-%d %H:%M} 휴대폰 전송 대기"
                queued += 1
            else:
                note = "보낼 휴대폰이 없음 (아직 수집된 메시지 없음)"
            nxt = None if s["kind"] == "once" else next_run(s, max(now, due))  # 한 번 예약은 한 번 처리 후 끝
            cur.execute(
                "UPDATE scheduled_messages SET last_run_at = %s, last_note = %s, next_run_at = %s, enabled = %s WHERE id = %s",
                (now, note, nxt, 1 if nxt else 0, s["id"]),
            )
    if queued:
        live.bump("schedule")
    return queued


_stop = threading.Event()


def _loop() -> None:
    while not _stop.is_set():
        try:
            run_due(get_settings())
        except Exception:  # DB 일시 오류 등 — 스케줄러는 계속 돈다
            log.exception("예약 메시지 확인 실패")
        _stop.wait(20)


def start() -> threading.Thread:
    _stop.clear()
    t = threading.Thread(target=_loop, name="schedule-runner", daemon=True)
    t.start()
    return t


def stop() -> None:
    _stop.set()


# ---- API -------------------------------------------------------------------------------------

class ScheduleBody(BaseModel):
    title: str = Field(default="", max_length=100)
    room_name: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=1000)
    kind: str = Field(pattern="^(once|daily|weekly|monthly)$")
    run_time: str
    run_date: str | None = None
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    month_day: int | None = Field(default=None, ge=1, le=31)
    enabled: bool = True


def _clean(body: ScheduleBody, now: datetime) -> dict[str, Any]:
    if not TIME_RE.match(body.run_time):
        raise HTTPException(422, "보낼 시각을 HH:MM 형식으로 입력하십시오.")
    if not body.text.strip():
        raise HTTPException(422, "보낼 메시지를 입력하십시오.")
    values: dict[str, Any] = {
        "title": body.title.strip() or body.text.strip().split("\n")[0][:30],
        "room_name": body.room_name.strip(),
        "text": body.text.strip(),
        "kind": body.kind,
        "run_time": body.run_time,
        "run_date": None,
        "weekdays": None,
        "month_day": None,
        "enabled": 1 if body.enabled else 0,
    }
    if body.kind == "once":
        if not body.run_date or not DATE_RE.match(body.run_date):
            raise HTTPException(422, "보낼 날짜를 선택하십시오.")
        values["run_date"] = body.run_date
        if _at(date.fromisoformat(body.run_date), body.run_time) <= now and body.enabled:
            raise HTTPException(422, "이미 지난 날짜·시각입니다. 앞으로의 시각을 선택하십시오.")
    elif body.kind == "weekly":
        days = sorted({d for d in body.weekdays if 0 <= d <= 6})
        if not days:
            raise HTTPException(422, "보낼 요일을 하나 이상 고르십시오.")
        values["weekdays"] = ",".join(map(str, days))
    elif body.kind == "monthly":
        if not body.month_day:
            raise HTTPException(422, "매월 보낼 날짜(1~31일)를 입력하십시오.")
        values["month_day"] = body.month_day
    values["next_run_at"] = next_run(values, now) if values["enabled"] else None
    return values


def _ser(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}
    out["weekdays"] = _weekdays(row.get("weekdays"))
    out["enabled"] = bool(row["enabled"])
    out["summary"] = describe({**row, "weekdays": row.get("weekdays")})
    return out


def _get(cur, schedule_id: int) -> dict[str, Any]:
    cur.execute("SELECT * FROM scheduled_messages WHERE id = %s", (schedule_id,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "예약이 없습니다.")
    return row


@router.get("/api/admin/schedules")
def list_schedules(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM scheduled_messages ORDER BY enabled DESC, next_run_at IS NULL, next_run_at, id")
        rows = cur.fetchall()
        cur.execute(
            """SELECT q.schedule_id, q.status, q.note, q.created_at, q.done_at FROM reply_queue q
               JOIN (SELECT schedule_id, MAX(id) AS id FROM reply_queue WHERE kind = 'schedule' GROUP BY schedule_id) t ON t.id = q.id"""
        )
        last = {r["schedule_id"]: r for r in cur.fetchall()}
        cur.execute("SELECT DISTINCT room_name FROM kakao_messages")
        rooms = {r["room_name"] for r in cur.fetchall()}
    items = []
    for r in rows:
        item = _ser(r)
        lr = last.get(r["id"])
        item["last_send"] = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in lr.items()} if lr else None
        item["room_has_messages"] = r["room_name"] in rooms
        items.append(item)
    return {"ok": True, "items": items, "placeholders": PLACEHOLDERS}


@router.post("/api/admin/schedules", status_code=201)
def create_schedule(body: ScheduleBody, settings: SettingsDep) -> dict[str, Any]:
    now = _now(settings)
    v = _clean(body, now)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO scheduled_messages (title, room_name, text, kind, run_date, weekdays, month_day, run_time, enabled,
                                               next_run_at, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (v["title"], v["room_name"], v["text"], v["kind"], v["run_date"], v["weekdays"], v["month_day"], v["run_time"],
             v["enabled"], v["next_run_at"], now, now),
        )
        new_id = cur.lastrowid
    live.bump("schedule")
    return {"ok": True, "id": new_id}


@router.put("/api/admin/schedules/{schedule_id}")
def update_schedule(schedule_id: int, body: ScheduleBody, settings: SettingsDep) -> dict[str, Any]:
    now = _now(settings)
    v = _clean(body, now)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        _get(cur, schedule_id)
        cur.execute(
            """UPDATE scheduled_messages SET title = %s, room_name = %s, text = %s, kind = %s, run_date = %s, weekdays = %s,
                      month_day = %s, run_time = %s, enabled = %s, next_run_at = %s, updated_at = %s WHERE id = %s""",
            (v["title"], v["room_name"], v["text"], v["kind"], v["run_date"], v["weekdays"], v["month_day"], v["run_time"],
             v["enabled"], v["next_run_at"], now, schedule_id),
        )
    live.bump("schedule")
    return {"ok": True}


class EnabledBody(BaseModel):
    enabled: bool


@router.post("/api/admin/schedules/{schedule_id}/enabled")
def set_enabled(schedule_id: int, body: EnabledBody, settings: SettingsDep) -> dict[str, Any]:
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        s = _get(cur, schedule_id)
        nxt = next_run(s, now) if body.enabled else None
        if body.enabled and nxt is None:
            raise HTTPException(422, "이미 지난 한 번 예약입니다. 날짜·시각을 수정한 뒤 켜십시오.")
        cur.execute("UPDATE scheduled_messages SET enabled = %s, next_run_at = %s, updated_at = %s WHERE id = %s",
                    (1 if body.enabled else 0, nxt, now, schedule_id))
    live.bump("schedule")
    return {"ok": True, "next_run_at": nxt.isoformat() if nxt else None}


@router.post("/api/admin/schedules/{schedule_id}/send-now")
def send_now(schedule_id: int, settings: SettingsDep) -> dict[str, Any]:
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        s = _get(cur, schedule_id)
        if not enqueue(cur, s, now):
            raise HTTPException(422, "보낼 휴대폰이 없습니다. 휴대폰 앱에서 카톡이 한 번 이상 수집되어야 합니다.")
    live.bump("schedule")
    return {"ok": True}


@router.delete("/api/admin/schedules/{schedule_id}")
def delete_schedule(schedule_id: int, settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM scheduled_messages WHERE id = %s", (schedule_id,))
    live.bump("schedule")
    return {"ok": True}


@router.get("/api/admin/schedules/history")
def history(settings: SettingsDep, limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(limit, 300))
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, schedule_id, vendor_name AS title, room_name, text, status, note, created_at, done_at
               FROM reply_queue WHERE kind = 'schedule' ORDER BY id DESC LIMIT %s""",
            (limit,),
        )
        items = [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()} for r in cur.fetchall()]
    return {"ok": True, "items": items}
