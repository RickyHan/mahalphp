"""업체 문자 보내기 (분류 워커가 주기마다 호출).

- 대기(pending) 문자를 뿌리오로 보낸다. 일시 오류는 1분 뒤 다시(최대 sms.MAX_ATTEMPTS 번).
- 끝내 못 보내면 실패로 남기고, 실제 업체 문자(테스트가 아님)이고 업체에 카톡방이 있으면 카톡 처리 요청으로 대신 보낸다.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from . import db, live, sms, store

log = logging.getLogger("kakao.sms")
BATCH = 20


def _fallback(cur, row: dict[str, Any], now: datetime) -> int | None:
    """문자를 못 보낸 업체 요청을 그 업체 카톡방으로 (테스트 문자는 대신 보내지 않음)"""
    if row["test"] or row["vendor_id"] is None or row["message_id"] is None:
        return None
    cur.execute("SELECT id, name, room_name FROM vendors WHERE id = %s", (row["vendor_id"],))
    vendor = cur.fetchone()
    if not vendor or not vendor["room_name"]:
        return None
    # 같은 민원·업체로 이미 카톡 요청이 있으면 또 넣지 않음 (같은 업체 번호가 여럿일 때)
    cur.execute("SELECT id FROM reply_queue WHERE kind = 'vendor' AND message_id = %s AND vendor_id = %s", (row["message_id"], vendor["id"]))
    existing = cur.fetchone()
    if existing:
        return existing["id"]
    cur.execute("SELECT device_id FROM kakao_messages WHERE id = %s", (row["message_id"],))
    msg = cur.fetchone() or {"device_id": ""}
    device = store.vendor_device(cur, vendor, msg)
    return store.enqueue_reply(cur, row["message_id"], device, vendor["room_name"], row["text"], now,
                               kind="vendor", vendor_id=vendor["id"], vendor_name=vendor["name"])


def _finish(cur, row: dict[str, Any], status: str, note: str | None, now: datetime, *, key: str | None = None,
            kind: str | None = None, attempts: int) -> None:
    fallback_id = None
    if status == "failed":
        fallback_id = _fallback(cur, row, now)
        if fallback_id:
            note = f"{note} → 업체 카톡방으로 대신 보냄"
    cur.execute(
        """UPDATE sms_queue SET status = %s, attempts = %s, note = %s, message_key = %s, msg_type = %s, done_at = %s,
                                fallback_id = %s WHERE id = %s""",
        (status, attempts, (note or None) and note[:250], key, kind, now, fallback_id, row["id"]),
    )


def send_pending(settings, client: sms.PpurioClient | None = None) -> dict[str, int]:
    now = store.now_local(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, message_id, vendor_id, phone, text, attempts, test FROM sms_queue
               WHERE status = 'pending' AND send_after <= %s ORDER BY id LIMIT %s""",
            (now, BATCH),
        )
        rows = cur.fetchall()
        conf = store.get_settings_map(cur) if rows else {}
    if not rows:
        return {"sent": 0, "failed": 0}
    client = client or sms.client_for(settings, conf)
    sender = sms.normalize_phone(conf.get("sms_from", ""))
    problem = None
    if client is None:
        problem = "뿌리오 연동 정보 없음 (시스템 설정 > 업체 문자에서 계정·인증키 입력)"
    elif not sender:
        problem = "발신번호 미설정 (시스템 설정 > 업체 문자)"
    off = conf.get("sms_enabled") != "1"
    sent = failed = 0
    for row in rows:
        now = store.now_local(settings)
        attempts = int(row["attempts"]) + 1
        if off and row["message_id"] is not None:
            # 문자를 끈 뒤 남아 있던 민원 문자: 보내지 않고, 실제 업체 문자면 카톡방으로 대신
            with db.transaction(settings) as conn, conn.cursor() as cur:
                _finish(cur, row, "failed", "업체 문자 꺼짐 — 보내지 않음", now, attempts=int(row["attempts"]))
            failed += 1
            continue
        if problem:
            with db.transaction(settings) as conn, conn.cursor() as cur:
                _finish(cur, row, "failed", problem, now, attempts=attempts)
            failed += 1
            continue
        try:
            result = client.send(sender=sender, to=row["phone"], text=row["text"], ref_key=sms.new_ref_key(row["id"]))
        except sms.PpurioError as exc:
            with db.transaction(settings) as conn, conn.cursor() as cur:
                if exc.retry and attempts < sms.MAX_ATTEMPTS:
                    cur.execute(
                        "UPDATE sms_queue SET attempts = %s, note = %s, send_after = %s WHERE id = %s",
                        (attempts, str(exc)[:250], now + sms.RETRY_DELAY, row["id"]),
                    )
                else:
                    _finish(cur, row, "failed", str(exc), now, attempts=attempts)
                    failed += 1
            continue
        except Exception as exc:  # 예상 못 한 오류도 이력에 남긴다
            log.exception("문자 발송 오류")
            with db.transaction(settings) as conn, conn.cursor() as cur:
                _finish(cur, row, "failed", f"{type(exc).__name__}: {exc}", now, attempts=attempts)
            failed += 1
            continue
        with db.transaction(settings) as conn, conn.cursor() as cur:
            _finish(cur, row, "sent", None, now, key=result.message_key, kind=result.message_type, attempts=attempts)
        sent += 1
    if sent or failed:
        live.bump("vendor")
        live.bump("complaint")
        log.info("업체 문자: 보냄 %s · 실패 %s", sent, failed)
    return {"sent": sent, "failed": failed}
