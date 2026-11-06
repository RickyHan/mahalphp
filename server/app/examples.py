"""민원 판정 학습: 관리자가 바로잡은 판정을 사례로 저장해 Gemini 지시문에 함께 보낸다.

    GET    /api/admin/examples                  저장된 판정 사례 (최근 순)
    POST   /api/admin/examples                  사례 직접 추가 {text, is_complaint, note}
    DELETE /api/admin/examples/{id}             사례 삭제
    POST   /api/admin/complaints/not-complaint  민원 아님으로 바로잡기 {ids, note}
           → 판정 0, 아직 안 보낸 접수 안내·FAQ 답변·업체 처리 요청 취소, 사례로 저장

사례는 최근 것부터 complaints.EXAMPLE_LIMIT 개까지 지시문에 들어간다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import complaints as cp
from . import db, live, store
from .auth import SettingsDep, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

SEED_VERSION = "2"  # 2: 이웃에게 묻는 대화(녹물·확인 가능한 세대)
# 처음부터 넣어 두는 사례: (찾을 문구들, 사례 문장, 이유). 이미 받은 메시지 중 문구가 모두 들어 있는 민원도 바로잡는다.
SEED: list[tuple[tuple[str, ...], str, str]] = [
    (
        ("역류 원인", "미팅"),
        "저희도 피해 입은 세대인 만큼, 배관 구조와 역류 원인, 향후 대책에 대해 직접 설명을 듣고 싶습니다. 미팅시간 알려주시면 참석하겠습니다",
        "이미 알려진 사고에 대한 설명·미팅 요청 — 새로 조치할 문제를 알리는 민원이 아님",
    ),
    (
        ("1대 주차 원칙",),
        "203호님 차량 2대 주차하셨는데 1대만 해주시기 바랍니다. 1대 주차 원칙입니다",
        "입주민끼리 서로에게 하는 요청·지적 — 관리 측에 조치를 요청하는 민원이 아님",
    ),
    (
        ("녹물", "괜찮으신가요"),
        "A동 녹물나오는데 다른세대 괜찮으신가요?",
        "다른 입주민에게 묻는 대화 — 관리 측에 조치를 요청하지 않음",
    ),
    (
        ("확인가능한세대",),
        "확인가능한세대가 아무도 없나요?",
        "다른 입주민에게 묻는 대화 — 관리 측에 조치를 요청하지 않음",
    ),
]


def recent(cur, limit: int = cp.EXAMPLE_LIMIT) -> list[tuple[str, bool, str]]:
    """지시문에 넣을 사례 (최근 순)"""
    cur.execute(
        "SELECT text, is_complaint, note FROM classification_examples ORDER BY id DESC LIMIT %s", (limit,)
    )
    return [(r["text"], bool(r["is_complaint"]), r["note"] or "") for r in cur.fetchall()]


def add_example(cur, text: str, is_complaint: bool, note: str, at: datetime, message_id: int | None = None) -> bool:
    """같은 문장의 사례가 있으면 판정·이유만 새로 고친다. 새로 넣었으면 True"""
    text = " ".join((text or "").split())[:1000]
    if not text:
        return False
    cur.execute("SELECT id FROM classification_examples WHERE text = %s", (text,))
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE classification_examples SET is_complaint = %s, note = %s, created_at = %s WHERE id = %s",
            (int(is_complaint), note[:255], at, row["id"]),
        )
        return False
    cur.execute(
        "INSERT INTO classification_examples (message_id, text, is_complaint, note, created_at) VALUES (%s, %s, %s, %s, %s)",
        (message_id, text, int(is_complaint), note[:255], at),
    )
    return True


def mark_not_complaint(cur, ids: list[int], note: str, at: datetime) -> int:
    """민원으로 잘못 잡힌 메시지를 바로잡는다. 바로잡은 메시지 수"""
    if not ids:
        return 0
    marks = ", ".join(["%s"] * len(ids))
    cur.execute(
        f"""SELECT m.id, m.message_text FROM kakao_messages m JOIN complaint_check c ON c.message_id = m.id
            WHERE c.verdict = 1 AND m.id IN ({marks}) ORDER BY m.id""",
        ids,
    )
    rows = cur.fetchall()
    reason = note.strip() or "관리자가 민원 아님으로 판정"
    for r in rows:
        cur.execute(
            "UPDATE complaint_check SET verdict = 0, reply_note = %s WHERE message_id = %s",
            ("관리자: 민원 아님", r["id"]),
        )
        cur.execute(
            "UPDATE reply_queue SET status = 'expired', done_at = %s, note = %s"
            " WHERE message_id = %s AND status = 'pending' AND kind IN ('ack', 'faq', 'vendor')",
            (at, "관리자가 민원 아님으로 판정해 취소", r["id"]),
        )
        add_example(cur, r["message_text"] or "", False, reason, at, r["id"])
    return len(rows)


def seed_examples(settings) -> int | None:
    """처음 한 번: 기본 사례를 넣고, 이미 민원으로 잡힌 같은 내용의 메시지를 바로잡는다."""
    now = store.now_local(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM app_settings WHERE name = 'examples_seed'")
        row = cur.fetchone()
        if row and row["value"] == SEED_VERSION:
            return None
        fixed = 0
        for needles, text, note in SEED:
            add_example(cur, text, False, note, now)
            where = " AND ".join(["m.message_text LIKE %s ESCAPE '!'"] * len(needles))
            cur.execute(
                f"SELECT m.id FROM kakao_messages m JOIN complaint_check c ON c.message_id = m.id WHERE c.verdict = 1 AND {where}",
                [db.like(n) for n in needles],
            )
            ids = [r["id"] for r in cur.fetchall()]
            fixed += mark_not_complaint(cur, ids, note, now)
        cur.execute("DELETE FROM app_settings WHERE name = 'examples_seed'")
        cur.execute("INSERT INTO app_settings (name, value) VALUES ('examples_seed', %s)", (SEED_VERSION,))
    live.bump("complaint")
    return fixed


def _ser(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


class ExampleBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    is_complaint: bool = False
    note: str = Field(default="", max_length=255)


class NotComplaintBody(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=255)


@router.get("/api/admin/examples")
def list_examples(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, message_id, text, is_complaint, note, created_at FROM classification_examples ORDER BY id DESC"
        )
        rows = [_ser(r) for r in cur.fetchall()]
    for r in rows:
        r["is_complaint"] = bool(r["is_complaint"])
    return {"ok": True, "items": rows, "limit": cp.EXAMPLE_LIMIT}


@router.post("/api/admin/examples", status_code=201)
def create_example(body: ExampleBody, settings: SettingsDep) -> dict[str, Any]:
    if not body.text.strip():
        raise HTTPException(422, "메시지 내용을 입력하십시오.")
    with db.transaction(settings) as conn, conn.cursor() as cur:
        add_example(cur, body.text, body.is_complaint, body.note.strip(), store.now_local(settings))
    live.bump("complaint")
    return {"ok": True}


@router.delete("/api/admin/examples/{example_id}")
def delete_example(example_id: int, settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM classification_examples WHERE id = %s", (example_id,))
        if not cur.fetchone():
            raise HTTPException(404, "사례를 찾을 수 없습니다.")
        cur.execute("DELETE FROM classification_examples WHERE id = %s", (example_id,))
    live.bump("complaint")
    return {"ok": True}


@router.post("/api/admin/complaints/not-complaint")
def not_complaint(body: NotComplaintBody, settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        n = mark_not_complaint(cur, body.ids, body.note, store.now_local(settings))
    if not n:
        raise HTTPException(404, "민원으로 분류된 메시지가 없습니다.")
    live.bump("complaint")
    return {"ok": True, "fixed": n}
