"""MySQL 접속 및 스키마 생성."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

import pymysql
from pymysql.cursors import DictCursor

from .config import SERVER_ROOT, Settings

SCHEMA_FILE = SERVER_ROOT / "sql" / "schema.sql"
DEFAULT_SCHEMA = "kakao_collector"


def _utc_offset(settings: Settings) -> str:
    """APP_TIMEZONE 의 현재 UTC 오프셋, 예: +09:00"""
    return datetime.now(settings.timezone).isoformat()[-6:]


def connect(settings: Settings, with_schema: bool = True) -> pymysql.connections.Connection:
    conn = pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_pass,
        database=settings.db_name if with_schema else None,
        charset="utf8mb4",
        cursorclass=DictCursor,
        connect_timeout=10,
        autocommit=False,
    )
    with conn.cursor() as cur:
        cur.execute("SET time_zone = %s", (_utc_offset(settings),))
    return conn


@contextmanager
def transaction(settings: Settings) -> Iterator[pymysql.connections.Connection]:
    conn = connect(settings)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def schema_statements(db_name: str) -> list[str]:
    """schema.sql 을 문장 단위로 나누고, 스키마명을 DB_NAME 으로 치환한다."""
    lines = [ln for ln in SCHEMA_FILE.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("--")]
    sql = "\n".join(lines).replace(f"`{DEFAULT_SCHEMA}`", f"`{db_name}`")
    return [stmt.strip() for stmt in sql.split(";") if stmt.strip()]


def install_schema(settings: Settings) -> list[str]:
    """스키마와 테이블을 생성(이미 있으면 유지)하고, 생성된 테이블 목록을 반환한다."""
    conn = connect(settings, with_schema=False)
    try:
        with conn.cursor() as cur:
            for stmt in schema_statements(settings.db_name):
                cur.execute(stmt)
            conn.commit()
            cur.execute(
                "SELECT table_name AS t FROM information_schema.tables WHERE table_schema = %s ORDER BY table_name",
                (settings.db_name,),
            )
            return [row["t"] for row in cur.fetchall()]
    finally:
        conn.close()
