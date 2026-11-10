"""업체 처리 요청 이력(문자·카톡) · 업체 문자 설정.

    GET  /api/admin/dispatch-history?view=all|sms|kakao|pending|failed|test   이력 + 건수
    GET  /api/admin/sms                 문자 설정 + 연동 상태
    PUT  /api/admin/sms                 저장 (사용 · 발신번호 · 테스트 모드/번호 · 뿌리오 계정/인증키)
    POST /api/admin/sms/check           뿌리오 연결 확인 (토큰 발급 · 서버 IP)
    POST /api/admin/sms/test            테스트 문자 1통 (테스트 번호 또는 입력한 번호)
    POST /api/admin/sms/{id}/retry      실패한 문자 다시 보내기
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import db, live, sms, store, worker
from .auth import SettingsDep, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

VIEWS = ("all", "sms", "kakao", "pending", "failed", "test")


def _ser(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


def _sms_rows(cur, limit: int) -> list[dict[str, Any]]:
    cur.execute(
        """SELECT s.id, s.message_id, s.vendor_id, s.vendor_name, s.phone, s.room_name, s.category, s.text, s.status, s.attempts,
                  s.note, s.msg_type, s.created_at, s.done_at, s.test, s.target_phone, s.fallback_id, c.summary
           FROM sms_queue s LEFT JOIN complaint_check c ON c.message_id = s.message_id
           ORDER BY s.id DESC LIMIT %s""",
        (limit,),
    )
    out = []
    for r in cur.fetchall():
        r = _ser(r)
        r["channel"] = "sms"
        r["test"] = bool(r["test"])
        out.append(r)
    return out


def _kakao_rows(cur, limit: int, now: datetime) -> list[dict[str, Any]]:
    cur.execute(
        """SELECT q.id, q.message_id, q.vendor_id, q.vendor_name, q.room_name AS vendor_room, q.text, q.status, q.note,
                  q.created_at, q.done_at, m.room_name, c.category, c.summary
           FROM reply_queue q
           LEFT JOIN kakao_messages m ON m.id = q.message_id
           LEFT JOIN complaint_check c ON c.message_id = q.message_id
           WHERE q.kind = 'vendor' ORDER BY q.id DESC LIMIT %s""",
        (limit,),
    )
    rows = [_ser(r) for r in cur.fetchall()]
    ids = [r["id"] for r in rows]
    via_sms: set[int] = set()
    if ids:
        cur.execute(f"SELECT fallback_id FROM sms_queue WHERE fallback_id IN ({', '.join(['%s'] * len(ids))})", ids)
        via_sms = {r["fallback_id"] for r in cur.fetchall()}
    readiness = store.reply_readiness(cur, now)
    for r in rows:
        r["channel"] = "kakao"
        r["after_sms_failed"] = r["id"] in via_sms
        r["reply_ready"] = store.ready_state(readiness, r["vendor_room"]) if r["status"] == "pending" else None
    return rows


def _in_view(r: dict[str, Any], view: str) -> bool:
    if view == "all":
        return True
    if view == "test":
        return r["channel"] == "sms" and r["test"]
    if view == "sms":
        return r["channel"] == "sms" and not r["test"] and r["status"] == "sent"
    if view == "kakao":
        return r["channel"] == "kakao" and r["status"] == "sent"
    if view == "pending":
        return r["status"] == "pending"
    if view == "failed":
        return r["status"] in ("failed", "expired")
    return False


@router.get("/api/admin/dispatch-history")
def dispatch_history(settings: SettingsDep, view: str = Query("all"), limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    if view not in VIEWS:
        raise HTTPException(422, "알 수 없는 보기입니다.")
    with db.transaction(settings) as conn, conn.cursor() as cur:
        rows = _sms_rows(cur, 1000) + _kakao_rows(cur, 1000, store.now_local(settings))
    rows.sort(key=lambda r: r["created_at"] or "", reverse=True)
    counts = {v: sum(1 for r in rows if _in_view(r, v)) for v in VIEWS}
    return {"ok": True, "items": [r for r in rows if _in_view(r, view)][:limit], "counts": counts}


# ---- 문자 설정 -------------------------------------------------------------------------------------

class SmsBody(BaseModel):
    enabled: bool | None = None
    sender: str | None = Field(default=None, max_length=20)
    test_mode: bool | None = None
    test_number: str | None = Field(default=None, max_length=20)
    account: str | None = Field(default=None, max_length=60)
    api_key: str | None = Field(default=None, max_length=200)


class TestBody(BaseModel):
    to: str = Field(default="", max_length=20)


def _phone_or_422(raw: str, label: str) -> str:
    digits = sms.normalize_phone(raw)
    if digits and not re.fullmatch(r"0\d{7,10}", digits):
        raise HTTPException(422, f"{label}를 숫자로 정확히 입력하십시오 (예: 010-1234-5678).")
    return digits


@router.get("/api/admin/sms")
def get_sms(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        conf = store.get_settings_map(cur)
        cur.execute("SELECT status, COUNT(*) AS n FROM sms_queue GROUP BY status")
        counts = {r["status"]: int(r["n"]) for r in cur.fetchall()}
        cur.execute("SELECT COUNT(*) AS n FROM apartment_vendor_contacts")
        building_contacts = int(cur.fetchone()["n"])
    account, key = sms.credentials(settings, conf)
    return {
        "ok": True,
        "enabled": conf["sms_enabled"] == "1",
        "sender": conf["sms_from"],
        "test_mode": conf["sms_test_mode"] == "1",
        "test_number": conf["sms_test_number"],
        "configured": bool(account and key),
        "account": (account[:3] + "*" * max(0, len(account) - 3)) if account else "",
        "from_env": bool(settings.ppurio_account and settings.ppurio_api_key),
        "counts": counts,
        "building_contacts": building_contacts,
    }


@router.put("/api/admin/sms")
def put_sms(body: SmsBody, settings: SettingsDep) -> dict[str, Any]:
    values: dict[str, str] = {}
    if body.enabled is not None:
        values["sms_enabled"] = "1" if body.enabled else "0"
    if body.sender is not None:
        values["sms_from"] = _phone_or_422(body.sender, "발신번호")
    if body.test_mode is not None:
        values["sms_test_mode"] = "1" if body.test_mode else "0"
    if body.test_number is not None:
        values["sms_test_number"] = _phone_or_422(body.test_number, "테스트 번호")
    if body.account is not None and body.account.strip():
        values["ppurio_account"] = body.account.strip()
    if body.api_key is not None and body.api_key.strip():
        values["ppurio_api_key"] = body.api_key.strip()
    with db.transaction(settings) as conn, conn.cursor() as cur:
        current = {**store.get_settings_map(cur), **values}
        if current["sms_test_mode"] == "1" and current["sms_enabled"] == "1" and not current["sms_test_number"]:
            raise HTTPException(422, "테스트 모드에서는 테스트 번호가 필요합니다.")
        store.put_settings(cur, values)
    live.bump("vendor")
    return get_sms(settings)


@router.post("/api/admin/sms/check")
def check(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        conf = store.get_settings_map(cur)
    client = sms.client_for(settings, conf)
    if client is None:
        return {"ok": False, "message": "뿌리오 계정·인증키가 없습니다. 아래에 입력하고 저장하십시오."}
    try:
        client.token(force=True)
    except sms.PpurioError as exc:
        return {"ok": False, "message": str(exc), "user_ip": client.user_ip}
    except Exception as exc:  # 예상 못 한 오류도 화면에 보이게
        return {"ok": False, "message": f"확인 중 오류: {type(exc).__name__}: {exc}", "user_ip": client.user_ip}
    return {"ok": True, "message": "뿌리오 연결 정상 (토큰 발급 성공)", "user_ip": client.user_ip}


@router.post("/api/admin/sms/test")
def test_send(body: TestBody, settings: SettingsDep) -> dict[str, Any]:
    """테스트 문자 1통을 대기열에 넣고 바로 보낸다 (결과는 이력에도 남음)"""
    with db.transaction(settings) as conn, conn.cursor() as cur:
        conf = store.get_settings_map(cur)
    to = _phone_or_422(body.to, "받을 번호") or sms.normalize_phone(conf["sms_test_number"])
    if not to:
        raise HTTPException(422, "받을 번호를 입력하십시오.")
    now = store.now_local(settings)
    text = f"[테스트] 민원 자동처리 시스템 업체 문자 시험입니다. ({now:%m/%d %H:%M})"
    with db.transaction(settings) as conn, conn.cursor() as cur:
        sms_id = store.enqueue_sms(cur, message_id=None, vendor=None, phone=to, room="", category="", text=text, now=now, test=True)
    from . import sms_dispatch

    sms_dispatch.send_pending(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, note FROM sms_queue WHERE id = %s", (sms_id,))
        row = cur.fetchone()
    live.bump("vendor")
    if row["status"] == "sent":
        return {"ok": True, "message": f"{to} 로 테스트 문자를 보냈습니다."}
    return {"ok": False, "message": row["note"] or "아직 보내지 못했습니다 (잠시 뒤 다시 시도됨)."}


@router.post("/api/admin/sms/{sms_id}/retry")
def retry(sms_id: int, settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, fallback_id FROM sms_queue WHERE id = %s", (sms_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "문자 이력이 없습니다.")
        if row["status"] != "failed":
            raise HTTPException(422, "실패한 문자만 다시 보낼 수 있습니다.")
        if row["fallback_id"]:
            raise HTTPException(422, "이미 업체 카톡방으로 대신 보낸 요청입니다.")
        cur.execute(
            "UPDATE sms_queue SET status = 'pending', attempts = 0, note = '다시 보내기', send_after = %s, done_at = NULL WHERE id = %s",
            (store.now_local(settings), sms_id),
        )
    worker.wake()
    live.bump("vendor")
    return {"ok": True}
