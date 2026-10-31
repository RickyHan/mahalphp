"""관리자 웹 '문자 발송' 탭: 뿌리오 연동 상태 · 발신번호 · 문자 템플릿 · 미리보기 · 테스트 발송 · 발송 이력.

    GET  /api/admin/sms                 설정 + 연동 상태
    PUT  /api/admin/sms                 설정 저장 (사용 여부 · 발신번호 · 템플릿)
    POST /api/admin/sms/preview         템플릿 미리보기 (예시 값으로 치환, 길이·SMS/LMS)
    POST /api/admin/sms/check           뿌리오 연결 확인 (토큰 발급, 서버 IP 표시)
    POST /api/admin/sms/test            테스트 발송 (입력한 번호로 미리보기 문구)
    GET  /api/admin/sms/history         발송 이력
    POST /api/admin/sms/{id}/retry      실패한 문자 다시 보내기
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import db, live, sms, store, worker
from .auth import SettingsDep, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

SAMPLE = {
    "vendor": "한빛설비",
    "room": "OO아파트 입주민방",
    "category": "누수·하자",
    "summary": "욕실 천장 누수",
    "content": "1203호인데요 욕실 천장에서 물이 계속 떨어집니다. 윗집 확인 부탁드려요",
    "sender": "1203호",
}


class SmsBody(BaseModel):
    enabled: bool | None = None
    sender: str | None = Field(default=None, max_length=20)
    template: str | None = Field(default=None, max_length=1500)


class PreviewBody(BaseModel):
    template: str = Field(max_length=1500)


class TestBody(BaseModel):
    to: str = Field(max_length=20)
    template: str | None = Field(default=None, max_length=1500)


def _preview(template: str, now: datetime) -> dict[str, Any]:
    text = sms.render(template, vendor=SAMPLE["vendor"], room=SAMPLE["room"], category=SAMPLE["category"],
                      summary=SAMPLE["summary"], content=SAMPLE["content"], sender=SAMPLE["sender"], at=now)
    size = sms.byte_len(text)
    return {"text": text, "bytes": size, "type": sms.message_type(text), "too_long": size > sms.LMS_MAX_BYTES}


def _ser(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


@router.get("/api/admin/sms")
def get_sms(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        conf = store.get_settings_map(cur)
        cur.execute(
            "SELECT COUNT(*) AS n FROM vendors v JOIN complaint_categories c ON c.id = v.category_id WHERE v.phone <> ''"
        )
        linked = int(cur.fetchone()["n"])
    account = settings.ppurio_account
    return {
        "ok": True,
        "enabled": conf["sms_enabled"] == "1",
        "sender": conf["sms_from"],
        "template": conf["sms_template"],
        "default_template": sms.DEFAULT_TEMPLATE,
        "placeholders": sms.PLACEHOLDERS,
        "configured": bool(account and settings.ppurio_api_key),
        "account": (account[:3] + "*" * max(0, len(account) - 3)) if account else "",
        "linked_vendors": linked,
        "last": worker.sms_status.get("last", ""),
        "preview": _preview(conf["sms_template"], store.now_local(settings)),
        "limits": {"sms": sms.SMS_MAX_BYTES, "lms": sms.LMS_MAX_BYTES},
    }


@router.put("/api/admin/sms")
def put_sms(body: SmsBody, settings: SettingsDep) -> dict[str, Any]:
    values: dict[str, str] = {}
    if body.enabled is not None:
        values["sms_enabled"] = "1" if body.enabled else "0"
    if body.sender is not None:
        sender = sms.normalize_phone(body.sender)
        if sender and not (8 <= len(sender) <= 11):
            raise HTTPException(422, "발신번호는 뿌리오에 등록한 번호를 숫자로 입력하십시오 (예: 0212345678).")
        values["sms_from"] = sender
    if body.template is not None:
        template = body.template.strip() or sms.DEFAULT_TEMPLATE
        if sms.byte_len(template) > sms.LMS_MAX_BYTES:
            raise HTTPException(422, f"문구가 너무 깁니다 (최대 {sms.LMS_MAX_BYTES}byte).")
        values["sms_template"] = template
    if values.get("sms_enabled") == "1":
        with db.transaction(settings) as conn, conn.cursor() as cur:
            current = store.get_settings_map(cur)
        if not (values.get("sms_from") or current["sms_from"]):
            raise HTTPException(422, "문자 발송을 켜려면 발신번호를 먼저 입력하십시오.")
    with db.transaction(settings) as conn, conn.cursor() as cur:
        store.put_settings(cur, values)
    live.bump("sms")
    return get_sms(settings)


@router.post("/api/admin/sms/preview")
def preview(body: PreviewBody, settings: SettingsDep) -> dict[str, Any]:
    return {"ok": True, **_preview(body.template.strip() or sms.DEFAULT_TEMPLATE, store.now_local(settings))}


@router.post("/api/admin/sms/check")
def check(settings: SettingsDep) -> dict[str, Any]:
    client = sms.client_for(settings)
    if client is None:
        return {"ok": False, "message": "서버 .env 에 PPURIO_ACCOUNT 와 PPURIO_API_KEY 가 없습니다."}
    try:
        client.token(force=True)
    except sms.PpurioError as exc:
        return {"ok": False, "message": str(exc), "user_ip": client.user_ip}
    return {"ok": True, "message": "뿌리오 연결 정상 (토큰 발급 성공)", "user_ip": client.user_ip}


@router.post("/api/admin/sms/test")
def test_send(body: TestBody, settings: SettingsDep) -> dict[str, Any]:
    to = sms.normalize_phone(body.to)
    if not sms.PHONE.match(to):
        raise HTTPException(422, "받을 휴대폰 번호를 정확히 입력하십시오 (예: 010-1234-5678).")
    with db.transaction(settings) as conn, conn.cursor() as cur:
        conf = store.get_settings_map(cur)
    if not conf["sms_from"]:
        raise HTTPException(422, "발신번호를 먼저 저장하십시오.")
    client = sms.client_for(settings)
    if client is None:
        raise HTTPException(422, "서버 .env 에 PPURIO_ACCOUNT 와 PPURIO_API_KEY 를 넣고 서비스를 재시작하십시오.")
    now = store.now_local(settings)
    text = "[테스트] " + _preview(body.template or conf["sms_template"], now)["text"]
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO sms_queue (message_id, vendor_id, vendor_name, phone, room_name, category, text, status, note, send_after, created_at)
               VALUES (NULL, NULL, '테스트 발송', %s, %s, %s, %s, 'sending', NULL, %s, %s)""",
            (to, SAMPLE["room"], SAMPLE["category"], text, now, now),
        )
        sms_id = cur.lastrowid
    try:
        result = client.send(sender=conf["sms_from"], to=to, text=text, ref_key=sms.new_ref_key(f"t{sms_id}"))
        row = ("sent", None, result.message_key, result.message_type)
    except sms.PpurioError as exc:
        row = ("failed", str(exc)[:250], None, None)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE sms_queue SET status = %s, attempts = 1, note = %s, message_key = %s, msg_type = %s, done_at = %s WHERE id = %s",
            (*row, store.now_local(settings), sms_id),
        )
    live.bump("sms")
    if row[0] != "sent":
        return {"ok": False, "message": row[1], "user_ip": client.user_ip}
    return {"ok": True, "message": f"테스트 문자를 보냈습니다 ({row[3]}). 뿌리오 발송결과에서도 확인할 수 있습니다."}


@router.get("/api/admin/sms/history")
def history(settings: SettingsDep, limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, message_id, vendor_name, phone, room_name, category, text, status, attempts, note,
                      message_key, msg_type, created_at, done_at
               FROM sms_queue ORDER BY id DESC LIMIT %s""",
            (limit,),
        )
        items = [_ser(r) for r in cur.fetchall()]
        cur.execute("SELECT status, COUNT(*) AS n FROM sms_queue GROUP BY status")
        counts = {r["status"]: int(r["n"]) for r in cur.fetchall()}
    return {"ok": True, "items": items, "counts": counts}


@router.post("/api/admin/sms/{sms_id}/retry")
def retry(sms_id: int, settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, message_id FROM sms_queue WHERE id = %s", (sms_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "문자 이력이 없습니다.")
        if row["status"] != "failed" or row["message_id"] is None:
            raise HTTPException(422, "실패한 민원 문자만 다시 보낼 수 있습니다.")
        cur.execute(
            "UPDATE sms_queue SET status = 'pending', attempts = 0, note = '다시 보내기', send_after = %s, done_at = NULL WHERE id = %s",
            (store.now_local(settings), sms_id),
        )
    worker.wake()
    live.bump("sms")
    return {"ok": True}
