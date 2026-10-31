"""수집 데이터 삭제 (관리자 웹 '데이터 관리' · 대화 화면 '선택 삭제').

    GET  /api/admin/data/rooms     방별 메시지·사진·민원 건수
    POST /api/admin/data/delete    {"scope": "messages"|"rooms"|"all", "ids": [...], "rooms": [...], "confirm": "..."}

삭제는 되돌릴 수 없다. 메시지와 함께 사진(파일 포함)·민원 분류·안내 대기열을 지우고,
방 단위·전체 삭제일 때는 그 방의 참여자 이력도 지운다. 업체 문자 이력도 해당 메시지 것을 지운다(전체 삭제 시 테스트 발송 포함 전부). 민원처리업체·카테고리·설정은 유지한다.
전체 삭제는 confirm 에 "전체 삭제" 를 정확히 넣어야 실행된다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import db, live
from .auth import SettingsDep, require_admin
from .config import Settings

log = logging.getLogger("kakao.data")
router = APIRouter(dependencies=[Depends(require_admin)])

CONFIRM_ALL = "전체 삭제"
CHUNK = 500


class DeleteBody(BaseModel):
    scope: Literal["messages", "rooms", "all"]
    ids: list[int] = Field(default_factory=list, max_length=5000)
    rooms: list[str] = Field(default_factory=list, max_length=500)
    confirm: str = ""


def _chunks(values: list[Any]):
    for i in range(0, len(values), CHUNK):
        yield values[i : i + CHUNK]


def _in(values: list[Any]) -> str:
    return ", ".join(["%s"] * len(values))


def _message_ids(cur, scope: str, ids: list[int], rooms: list[str]) -> list[int]:
    if scope == "messages":
        return sorted(set(ids))
    if scope == "rooms":
        out: list[int] = []
        for part in _chunks(rooms):
            cur.execute(f"SELECT id FROM kakao_messages WHERE room_name IN ({_in(part)})", part)
            out += [r["id"] for r in cur.fetchall()]
        return out
    cur.execute("SELECT id FROM kakao_messages")
    return [r["id"] for r in cur.fetchall()]


def delete_data(cur, scope: str, ids: list[int], rooms: list[str]) -> dict[str, Any]:
    """메시지와 딸린 데이터를 지우고, 더는 쓰이지 않는 사진 파일 경로 목록을 돌려준다(커밋 후 파일 삭제)."""
    targets = _message_ids(cur, scope, ids, rooms)
    paths: set[str] = set()
    counts = {"messages": 0, "photos": 0, "complaints": 0, "member_events": 0}
    for part in _chunks(targets):
        marks = _in(part)
        cur.execute(f"SELECT file_path FROM kakao_media WHERE message_id IN ({marks})", part)
        media = [r["file_path"] for r in cur.fetchall()]
        paths.update(media)
        counts["photos"] += len(media)
        cur.execute(f"SELECT COUNT(*) AS n FROM complaint_check WHERE verdict = 1 AND message_id IN ({marks})", part)
        counts["complaints"] += int(cur.fetchone()["n"])
        cur.execute(f"DELETE FROM reply_queue WHERE message_id IN ({marks})", part)
        cur.execute(f"DELETE FROM sms_queue WHERE message_id IN ({marks})", part)
        cur.execute(f"DELETE FROM complaint_check WHERE message_id IN ({marks})", part)
        cur.execute(f"DELETE FROM kakao_media WHERE message_id IN ({marks})", part)
        cur.execute(f"SELECT COUNT(*) AS n FROM kakao_messages WHERE id IN ({marks})", part)
        counts["messages"] += int(cur.fetchone()["n"])
        cur.execute(f"DELETE FROM kakao_messages WHERE id IN ({marks})", part)

    # 방 단위·전체 삭제: 참여자 이력도 함께
    if scope in ("rooms", "all"):
        if scope == "all":
            cur.execute("SELECT COUNT(*) AS n FROM member_events")
            counts["member_events"] = int(cur.fetchone()["n"])
            cur.execute("DELETE FROM member_events")
            cur.execute("DELETE FROM room_members")
            cur.execute("DELETE FROM reply_queue")
            cur.execute("DELETE FROM sms_queue")
        else:
            for part in _chunks(rooms):
                marks = _in(part)
                cur.execute(f"SELECT COUNT(*) AS n FROM member_events WHERE room_name IN ({marks})", part)
                counts["member_events"] += int(cur.fetchone()["n"])
                cur.execute(f"DELETE FROM member_events WHERE room_name IN ({marks})", part)
                cur.execute(f"DELETE FROM room_members WHERE room_name IN ({marks})", part)

    # 다른 메시지가 아직 쓰는 사진 파일(같은 사진 중복 저장)은 남긴다
    orphaned = []
    for path in sorted(paths):
        cur.execute("SELECT 1 AS x FROM kakao_media WHERE file_path = %s LIMIT 1", (path,))
        if cur.fetchone() is None:
            orphaned.append(path)
    return {**counts, "files": orphaned}


def remove_files(settings: Settings, paths: list[str]) -> int:
    root = settings.media_root.resolve()
    removed = 0
    for rel in paths:
        target = (root / rel).resolve()
        if root not in target.parents:
            continue
        try:
            target.unlink()
            removed += 1
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("사진 파일 삭제 실패: %s", target)
    return removed


@router.get("/api/admin/data/rooms")
def data_rooms(settings: SettingsDep) -> dict[str, Any]:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT m.room_name, COUNT(*) AS messages, COALESCE(SUM(m.has_media), 0) AS photos,
                      MIN(m.posted_at) AS first_at, MAX(m.posted_at) AS last_at,
                      COALESCE(SUM(CASE WHEN c.verdict = 1 THEN 1 ELSE 0 END), 0) AS complaints
               FROM kakao_messages m LEFT JOIN complaint_check c ON c.message_id = m.id
               GROUP BY m.room_name ORDER BY MAX(m.id) DESC"""
        )
        rows = cur.fetchall()
    items = [
        {
            "room_name": r["room_name"],
            "messages": int(r["messages"]),
            "photos": int(r["photos"]),
            "complaints": int(r["complaints"]),
            "first_at": r["first_at"].isoformat() if hasattr(r["first_at"], "isoformat") else r["first_at"],
            "last_at": r["last_at"].isoformat() if hasattr(r["last_at"], "isoformat") else r["last_at"],
        }
        for r in rows
    ]
    return {"ok": True, "items": items, "total": sum(i["messages"] for i in items)}


@router.post("/api/admin/data/delete")
def delete(body: DeleteBody, settings: SettingsDep) -> dict[str, Any]:
    if body.scope == "messages" and not body.ids:
        raise HTTPException(422, "삭제할 메시지를 선택하십시오.")
    if body.scope == "rooms" and not body.rooms:
        raise HTTPException(422, "삭제할 대화방을 선택하십시오.")
    if body.scope == "all" and body.confirm.strip() != CONFIRM_ALL:
        raise HTTPException(422, f"전체 삭제는 확인란에 '{CONFIRM_ALL}' 를 입력해야 합니다.")
    with db.transaction(settings) as conn, conn.cursor() as cur:
        result = delete_data(cur, body.scope, body.ids, body.rooms)
    files = result.pop("files")
    result["files_removed"] = remove_files(settings, files)
    log.warning("수집 데이터 삭제: scope=%s rooms=%d %s", body.scope, len(body.rooms), result)
    live.bump("delete")
    return {"ok": True, **result}
