"""server/.env 의 접속 정보로 스키마와 테이블을 생성한다.

    cd server && python -m scripts.install_db
"""

from __future__ import annotations

import sys

from app.config import Settings
from app.db import install_schema


def main() -> int:
    settings = Settings.load()
    print(f"접속: {settings.db_user}@{settings.db_host}:{settings.db_port} / 스키마: {settings.db_name}")
    tables = install_schema(settings)
    print("테이블:", ", ".join(tables) or "(없음)")
    return 0 if {"kakao_messages", "kakao_media"} <= set(tables) else 1


if __name__ == "__main__":
    sys.exit(main())
