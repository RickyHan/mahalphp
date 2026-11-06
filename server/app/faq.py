"""FAQ 자동 답변: 건물 채팅방에서 FAQ 질문과 같은 의도의 메시지가 오면 정해 둔 답변을 보낸다.

    GET    /api/admin/faq              사용 여부 · 문항 목록(보낸 횟수 포함)
    PUT    /api/admin/faq/settings     기능 켜기·끄기 {enabled}
    POST   /api/admin/faq              문항 추가
    PUT    /api/admin/faq/{id}         문항 수정 (분야·질문·답변·사용)
    DELETE /api/admin/faq/{id}         문항 삭제
    GET    /api/admin/faq/history      최근 자동 답변 이력

판정은 민원 분류와 같은 주기에 Gemini 가 한다(store.classify_once). 협력사(업체) 방은 대상이 아니다.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import db, live, store
from .auth import SettingsDep, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

# 처음 받은 FAQ(엑셀). 서버 시작 때 한 번만 넣는다(이후 웹에서 고친 내용은 덮어쓰지 않음).
SEED_FILE = Path(__file__).parent / "data" / "faq.json"
SEED_VERSION = "3"  # 2: 신고형/문의형(report) 구분 · 3: QA 문서 추가 문항(음식물 봉투 미사용, CCTV 비밀번호)


def _now(settings) -> datetime:
    return datetime.now(settings.timezone).replace(tzinfo=None)


def _ser(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            v = v.isoformat()
        elif isinstance(v, Decimal):
            v = float(v)
        out[k] = v
    return out


def seed_faq(settings) -> int | None:
    """FAQ 기본 문항 등록 (서버 시작 때, 버전이 오를 때 한 번).
    - 처음: 파일의 문항을 모두 넣는다.
    - 업그레이드: 새 버전에서 추가된 문항(since > 이전 버전)만 넣는다. 웹에서 고친 문항·사용 여부는 건드리지 않는다.
      (v1 → v2 에서만 기존 문항의 신고형/문의형 구분을 한 번 맞춘다)"""
    if not SEED_FILE.exists():
        return None
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM app_settings WHERE name = 'faq_seed'")
        row = cur.fetchone()
        if row and row["value"] == SEED_VERSION:
            return None
        try:
            prev = int(row["value"]) if row else 0
        except (TypeError, ValueError):
            prev = 0
        cur.execute("SELECT question FROM faq_items")
        have = {" ".join(r["question"].split()) for r in cur.fetchall()}
        added = 0
        for order, item in enumerate(json.loads(SEED_FILE.read_text(encoding="utf-8"))):
            report = int(bool(item.get("report", True)))
            question = item["question"][:500]
            if " ".join(question.split()) in have:
                if prev and prev < 2:  # v1 로 넣은 문항: 신고형/문의형만 처음 한 번 맞춤 (사용 여부는 그대로)
                    cur.execute("UPDATE faq_items SET report = %s WHERE question = %s", (report, question))
                continue
            if prev and int(item.get("since", 1)) <= prev:
                continue  # 예전 버전에 있던 문항인데 없으면 관리자가 지우거나 고친 것 → 다시 넣지 않음
            cur.execute(
                """INSERT INTO faq_items (category, question, answer, enabled, report, sort_order, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (item.get("category") or "", question, item["answer"], int(bool(item.get("enabled", True))), report, order, now, now),
            )
            added += 1
        cur.execute("DELETE FROM app_settings WHERE name = 'faq_seed'")
        cur.execute("INSERT INTO app_settings (name, value) VALUES ('faq_seed', %s)", (SEED_VERSION,))
    live.bump("faq")
    return added


class FaqBody(BaseModel):
    category: str = Field(default="", max_length=50)
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=5000)
    enabled: bool = True
    # 신고형(True): 고장·불량처럼 처리가 필요한 문항 → FAQ 답변과 함께 민원도 접수(업체 처리 요청 포함)
    # 문의형(False): 방법·절차·정보를 묻는 문항 → FAQ 답변만 보내고 민원으로 접수하지 않음
    report: bool = True


class FaqSettingsBody(BaseModel):
    enabled: bool


def _clean(body: FaqBody) -> tuple[str, str, str]:
    category, question, answer = body.category.strip(), body.question.strip(), body.answer.strip()
    if not question or not answer:
        raise HTTPException(422, "질문과 답변을 모두 입력하십시오.")
    return category, question, answer


@router.get("/api/admin/faq")
def list_faq(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        conf = store.get_settings_map(cur)
        cur.execute("SELECT id, category, question, answer, enabled, report, sort_order, updated_at FROM faq_items ORDER BY sort_order, id")
        items = [_ser(r) for r in cur.fetchall()]
        cur.execute("SELECT faq_id, COUNT(*) AS n, MAX(created_at) AS last_at FROM faq_answers WHERE reply_id IS NOT NULL GROUP BY faq_id")
        sent = {r["faq_id"]: r for r in cur.fetchall()}
        rooms = len(store.building_rooms(cur))
    for i in items:
        i["enabled"] = bool(i["enabled"])
        i["report"] = bool(i["report"])
        s = sent.get(i["id"])
        i["sent"] = int(s["n"]) if s else 0
        i["last_at"] = _ser({"t": s["last_at"]})["t"] if s and s["last_at"] else None
    return {
        "ok": True,
        "enabled": conf["faq_enabled"] == "1",
        "ai_ready": conf["complaint_enabled"] == "1" and bool(settings.gemini_api_key),
        "building_rooms": rooms,
        "items": items,
    }


@router.put("/api/admin/faq/settings")
def faq_settings(body: FaqSettingsBody, settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        store.put_settings(cur, {"faq_enabled": "1" if body.enabled else "0"})
    live.bump("faq")
    return {"ok": True, "enabled": body.enabled}


@router.post("/api/admin/faq", status_code=201)
def create_faq(body: FaqBody, settings: SettingsDep) -> dict[str, Any]:
    category, question, answer = _clean(body)
    now = _now(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(sort_order), 0) AS m FROM faq_items")
        order = int(cur.fetchone()["m"]) + 1
        cur.execute(
            """INSERT INTO faq_items (category, question, answer, enabled, report, sort_order, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (category, question, answer, int(body.enabled), int(body.report), order, now, now),
        )
        new_id = cur.lastrowid
    live.bump("faq")
    return {"ok": True, "id": new_id}


@router.put("/api/admin/faq/{faq_id}")
def update_faq(faq_id: int, body: FaqBody, settings: SettingsDep) -> dict[str, Any]:
    category, question, answer = _clean(body)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM faq_items WHERE id = %s", (faq_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "FAQ 문항이 없습니다.")
        cur.execute(
            "UPDATE faq_items SET category = %s, question = %s, answer = %s, enabled = %s, report = %s, updated_at = %s WHERE id = %s",
            (category, question, answer, int(body.enabled), int(body.report), _now(settings), faq_id),
        )
    live.bump("faq")
    return {"ok": True}


@router.delete("/api/admin/faq/{faq_id}")
def delete_faq(faq_id: int, settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM faq_items WHERE id = %s", (faq_id,))
    live.bump("faq")
    return {"ok": True}


@router.get("/api/admin/faq/history")
def faq_history(settings: SettingsDep, limit: int = 50, room: str | None = None, category: str | None = None) -> dict[str, Any]:
    """FAQ 자동 답변 이력 (최근 순). 보낸 것·생략한 것·확신이 낮아 보내지 않은 것 모두"""
    limit = max(1, min(limit, 300))
    where, params = [], []
    if room:
        where.append("a.room_name = %s")
        params.append(room)
    if category:
        where.append("f.category = %s")
        params.append(category)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT a.id, a.message_id, a.created_at, a.room_name, a.question, a.confidence, a.note, a.faq_id,
                      f.category, f.report, m.sender_name, m.message_text, m.posted_at,
                      q.status, q.note AS send_note, q.done_at
               FROM faq_answers a
               LEFT JOIN faq_items f ON f.id = a.faq_id
               LEFT JOIN kakao_messages m ON m.id = a.message_id
               LEFT JOIN reply_queue q ON q.id = a.reply_id"""
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY a.id DESC LIMIT %s",
            (*params, limit),
        )
        items = [_ser(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN q.status = 'sent' THEN 1 ELSE 0 END), 0) AS sent,
                      COALESCE(SUM(CASE WHEN q.status = 'pending' THEN 1 ELSE 0 END), 0) AS pending,
                      COALESCE(SUM(CASE WHEN a.reply_id IS NULL THEN 1 ELSE 0 END), 0) AS skipped
               FROM faq_answers a LEFT JOIN reply_queue q ON q.id = a.reply_id"""
        )
        counts = {k: int(v or 0) for k, v in cur.fetchone().items()}
        cur.execute("SELECT DISTINCT category FROM faq_items WHERE category <> '' ORDER BY category")
        cats = [r["category"] for r in cur.fetchall()]
    for i in items:
        i["report"] = None if i["report"] is None else bool(i["report"])
    return {"ok": True, "items": items, "counts": counts, "categories": cats}
