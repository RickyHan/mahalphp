"""민원 카테고리 관리. Gemini 분류는 '사용' 중인 카테고리만 고르며, '기타'는 항상 있어야 해서 삭제·이름 변경할 수 없다.

    GET    /api/admin/categories               목록 (민원 건수 · 담당 업체 수 포함)
    POST   /api/admin/categories               등록
    PUT    /api/admin/categories/{id}          수정 (이름 바꾸면 기존 민원 분류도 함께 변경)
    DELETE /api/admin/categories/{id}          삭제 (기존 민원은 '기타'로, 업체 지정은 해제)
    POST   /api/admin/categories/order         순서 저장
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import complaints as cp
from . import db, live
from .auth import SettingsDep, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

# Gemini 지시문(JSON)에 그대로 들어가므로 따옴표·구분자는 쓰지 않는다
NAME = re.compile(r'^[^"|\\{}<>\n\r\t]{1,30}$')


# ---- 저장소 함수 (store·admin 에서도 사용) ---------------------------------------------------

def ensure_defaults(cur) -> None:
    """처음 사용할 때 기본 분류를 넣고, '기타'가 없으면 다시 만든다."""
    cur.execute("SELECT COUNT(*) AS n FROM complaint_categories")
    empty = cur.fetchone()["n"] == 0
    rows = cp.DEFAULT_CATEGORIES if empty else [(cp.FALLBACK_CATEGORY, "위 분류에 해당하지 않는 민원")]
    for order, (name, desc) in enumerate(rows):
        cur.execute("SELECT id FROM complaint_categories WHERE name = %s", (name,))
        if cur.fetchone() is None:
            sort = order if empty else 9999
            try:
                cur.execute(
                    "INSERT INTO complaint_categories (name, description, sort_order, enabled, created_at) VALUES (%s, %s, %s, 1, %s)",
                    (name, desc, sort, datetime.now()),
                )
            except db.IntegrityError as exc:  # 동시에 다른 요청이 넣은 경우
                if not db.is_duplicate(exc):
                    raise


def all_categories(cur) -> list[dict[str, Any]]:
    ensure_defaults(cur)
    cur.execute("SELECT id, name, description, sort_order, enabled FROM complaint_categories ORDER BY sort_order, id")
    rows = cur.fetchall()
    for r in rows:
        r["enabled"] = bool(r["enabled"])
        r["protected"] = r["name"] == cp.FALLBACK_CATEGORY
    return rows


def enabled_for_prompt(cur) -> list[tuple[str, str]]:
    """Gemini 에 넘길 (이름, 설명) — 사용 중인 것만, '기타'는 항상 마지막"""
    rows = [r for r in all_categories(cur) if r["enabled"] or r["protected"]]
    rows.sort(key=lambda r: (r["protected"], r["sort_order"], r["id"]))
    return [(r["name"], r["description"]) for r in rows]


# ---- API --------------------------------------------------------------------------------

class CategoryBody(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    description: str = Field(default="", max_length=200)
    enabled: bool = True


class OrderBody(BaseModel):
    ids: list[int] = Field(max_length=200)


def _check_name(name: str) -> str:
    name = name.strip()
    if not NAME.match(name):
        raise HTTPException(422, '카테고리 이름은 1~30자이며 " | \\ { } < > 는 쓸 수 없습니다.')
    return name


def _get(cur, category_id: int) -> dict[str, Any]:
    cur.execute("SELECT id, name, sort_order FROM complaint_categories WHERE id = %s", (category_id,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "카테고리가 없습니다.")
    return row


def _duplicate_check(cur, name: str, exclude_id: int | None = None) -> None:
    cur.execute("SELECT id FROM complaint_categories WHERE name = %s", (name,))
    row = cur.fetchone()
    if row and row["id"] != exclude_id:
        raise HTTPException(409, f"'{name}' 카테고리가 이미 있습니다.")


@router.get("/api/admin/categories")
def list_categories(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        items = all_categories(cur)
        cur.execute("SELECT category, COUNT(*) AS n FROM complaint_check WHERE verdict = 1 GROUP BY category")
        complaints = {r["category"]: int(r["n"]) for r in cur.fetchall()}
        cur.execute("SELECT category_id, COUNT(*) AS n FROM vendors WHERE category_id IS NOT NULL GROUP BY category_id")
        vendors = {r["category_id"]: int(r["n"]) for r in cur.fetchall()}
    for r in items:
        r["complaints"] = complaints.get(r["name"], 0)
        r["vendors"] = vendors.get(r["id"], 0)
    return {"ok": True, "items": items}


@router.post("/api/admin/categories", status_code=201)
def create_category(body: CategoryBody, settings: SettingsDep) -> dict[str, Any]:
    name = _check_name(body.name)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        ensure_defaults(cur)
        _duplicate_check(cur, name)
        cur.execute("SELECT COALESCE(MAX(sort_order), 0) AS m FROM complaint_categories WHERE name <> %s", (cp.FALLBACK_CATEGORY,))
        order = int(cur.fetchone()["m"]) + 1
        cur.execute(
            "INSERT INTO complaint_categories (name, description, sort_order, enabled, created_at) VALUES (%s, %s, %s, %s, %s)",
            (name, body.description.strip(), order, int(body.enabled), datetime.now()),
        )
        new_id = cur.lastrowid
    live.bump("category")
    return {"ok": True, "id": new_id}


@router.put("/api/admin/categories/{category_id}")
def update_category(category_id: int, body: CategoryBody, settings: SettingsDep) -> dict[str, Any]:
    name = _check_name(body.name)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        row = _get(cur, category_id)
        protected = row["name"] == cp.FALLBACK_CATEGORY
        if protected and name != row["name"]:
            raise HTTPException(422, f"'{cp.FALLBACK_CATEGORY}' 카테고리는 이름을 바꿀 수 없습니다.")
        _duplicate_check(cur, name, exclude_id=category_id)
        cur.execute(
            "UPDATE complaint_categories SET name = %s, description = %s, enabled = %s WHERE id = %s",
            (name, body.description.strip(), int(body.enabled or protected), category_id),
        )
        renamed = 0
        if name != row["name"]:
            cur.execute("SELECT COUNT(*) AS n FROM complaint_check WHERE category = %s", (row["name"],))
            renamed = int(cur.fetchone()["n"])
            cur.execute("UPDATE complaint_check SET category = %s WHERE category = %s", (name, row["name"]))
    live.bump("category")
    return {"ok": True, "renamed_complaints": renamed}


@router.delete("/api/admin/categories/{category_id}")
def delete_category(category_id: int, settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        row = _get(cur, category_id)
        if row["name"] == cp.FALLBACK_CATEGORY:
            raise HTTPException(422, f"'{cp.FALLBACK_CATEGORY}' 카테고리는 삭제할 수 없습니다.")
        cur.execute("SELECT COUNT(*) AS n FROM complaint_check WHERE category = %s", (row["name"],))
        moved = int(cur.fetchone()["n"])
        cur.execute("UPDATE complaint_check SET category = %s WHERE category = %s", (cp.FALLBACK_CATEGORY, row["name"]))
        cur.execute("UPDATE vendors SET category_id = NULL WHERE category_id = %s", (category_id,))
        cur.execute("DELETE FROM complaint_categories WHERE id = %s", (category_id,))
    live.bump("category")
    return {"ok": True, "moved_complaints": moved}


@router.post("/api/admin/categories/order")
def reorder_categories(body: OrderBody, settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        for order, category_id in enumerate(body.ids):
            cur.execute("UPDATE complaint_categories SET sort_order = %s WHERE id = %s", (order, category_id))
    live.bump("category")
    return {"ok": True}
