"""통합 테스트 공통 설정. 같은 테스트를 두 DB 엔진으로 실행할 수 있다.

    pytest -q                                                          # SQLite (기본, 설치 불필요)
    TEST_DB_ENGINE=mysql TEST_DB_HOST=127.0.0.1 TEST_DB_USER=root TEST_DB_PASS=... pytest -q
MySQL 은 테스트용 스키마(kakao_collector_test)를 만들고 끝나면 삭제하며, 연결할 수 없으면 건너뛴다.
"""

from __future__ import annotations

import os

import pytest

TEST_DB = "kakao_collector_test"
API_KEY = "test-key"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin-pass-1234"

ENGINE = os.getenv("TEST_DB_ENGINE", "sqlite")

os.environ.update(
    DB_ENGINE=ENGINE,
    DB_HOST=os.getenv("TEST_DB_HOST", "127.0.0.1"),
    DB_PORT=os.getenv("TEST_DB_PORT", "3306"),
    DB_USER=os.getenv("TEST_DB_USER", "root"),
    DB_PASS=os.getenv("TEST_DB_PASS", ""),
    DB_NAME=TEST_DB,
    API_KEY=API_KEY,
    ADMIN_USER=ADMIN_USER,
    ADMIN_PASSWORD=ADMIN_PASSWORD,
)

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app, get_settings  # noqa: E402


@pytest.fixture(scope="session")
def settings(tmp_path_factory):
    os.environ["MEDIA_ROOT"] = str(tmp_path_factory.mktemp("media"))
    os.environ["SQLITE_PATH"] = str(tmp_path_factory.mktemp("sqlite") / "test.db")
    get_settings.cache_clear()
    s = get_settings()
    try:
        tables = db.install_schema(s)
    except db.DatabaseError as exc:
        pytest.skip(f"테스트 DB 에 연결할 수 없습니다: {exc}")
    assert {"kakao_messages", "kakao_media"} <= set(tables)
    yield s
    if s.db_engine == "sqlite":
        return  # 임시 디렉터리째 정리됨
    conn = db.connect(s, with_schema=False)
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE `{TEST_DB}`")
    conn.close()


@pytest.fixture
def client(settings):
    """쿠키를 공유하지 않도록 테스트마다 새 클라이언트"""
    return TestClient(app)
