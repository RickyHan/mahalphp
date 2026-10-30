"""DB 접속 및 스키마 생성. DB_ENGINE=mysql(운영) 또는 sqlite(테스트·단일 PC).

API 코드는 두 엔진 모두 같은 방식으로 쓴다:
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("... WHERE id = %s", (1,))   # 자리표시자는 %s
        row = cur.fetchone()                      # dict
SQL 은 두 엔진에서 공통으로 동작하는 문법만 사용한다(LIKE 는 ESCAPE '!' 명시, 날짜 함수 대신 파라미터).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

from .config import SERVER_ROOT, Settings

SCHEMA_FILE = SERVER_ROOT / "sql" / "schema.sql"
SQLITE_SCHEMA_FILE = SERVER_ROOT / "sql" / "schema_sqlite.sql"
DEFAULT_SCHEMA = "kakao_collector"
LIKE_ESCAPE = "!"

DatabaseError = (pymysql.MySQLError, sqlite3.Error)
IntegrityError = (pymysql.err.IntegrityError, sqlite3.IntegrityError)
MYSQL_DUP_ENTRY = 1062


def is_duplicate(exc: Exception) -> bool:
    """UNIQUE 제약 위반(중복 메시지) 여부"""
    if isinstance(exc, sqlite3.IntegrityError):
        return "UNIQUE" in str(exc)
    return bool(exc.args) and exc.args[0] == MYSQL_DUP_ENTRY


def like(term: str) -> str:
    """LIKE '%term%' 파라미터. 사용처에서 `LIKE %s ESCAPE '!'` 로 쓴다."""
    for ch in (LIKE_ESCAPE, "%", "_"):
        term = term.replace(ch, LIKE_ESCAPE + ch)
    return f"%{term}%"


def to_db_time(value: datetime) -> str:
    """두 엔진 공통 시각 문자열 (밀리초까지)"""
    return value.isoformat(sep="T", timespec="milliseconds")


# ---- SQLite 어댑터: pymysql DictCursor 와 같은 사용법을 제공 ------------------------------

class _SqliteCursor:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._cur = conn.cursor()

    def execute(self, sql: str, params: Any = ()) -> None:
        values = [to_db_time(p) if isinstance(p, datetime) else p for p in (params or ())]
        self._cur.execute(sql.replace("%s", "?"), values)

    def fetchone(self) -> dict[str, Any] | None:
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._cur.fetchall()]

    @property
    def lastrowid(self) -> int | None:
        return self._cur.lastrowid

    def __enter__(self) -> "_SqliteCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        self._cur.close()


class _SqliteConnection:
    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path, timeout=15)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 15000")

    def cursor(self) -> _SqliteCursor:
        return _SqliteCursor(self._conn)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def executescript(self, sql: str) -> None:
        self._conn.executescript(sql)


# ---- 공통 진입점 -----------------------------------------------------------------------

def _utc_offset(settings: Settings) -> str:
    """APP_TIMEZONE 의 현재 UTC 오프셋, 예: +09:00"""
    return datetime.now(settings.timezone).isoformat()[-6:]


def connect(settings: Settings, with_schema: bool = True):
    if settings.db_engine == "sqlite":
        settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        return _SqliteConnection(str(settings.sqlite_path))

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
def transaction(settings: Settings) -> Iterator[Any]:
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
    if settings.db_engine == "sqlite":
        conn = connect(settings)
        try:
            conn.executescript(SQLITE_SCHEMA_FILE.read_text(encoding="utf-8"))
            # 여러 요청이 동시에 읽고 쓰도록 WAL 모드 (DB 파일에 영구 설정됨)
            conn.executescript("PRAGMA journal_mode = WAL;")
            with conn.cursor() as cur:
                cur.execute("SELECT name AS t FROM sqlite_master WHERE type = 'table' AND name LIKE 'kakao!_%' ESCAPE '!' ORDER BY name")
                return [row["t"] for row in cur.fetchall()]
        finally:
            conn.close()

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
