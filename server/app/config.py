"""환경설정. server/.env 를 읽고, 이미 설정된 환경변수가 우선한다."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

SERVER_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(SERVER_ROOT / ".env", override=False)


def _require(key: str) -> str:
    value = os.getenv(key, "")
    if not value:
        raise RuntimeError(f"환경변수 {key} 가 설정되지 않았습니다 (server/.env 확인).")
    return value


@dataclass(frozen=True)
class Settings:
    db_engine: str  # "mysql" | "sqlite"
    sqlite_path: Path
    db_host: str
    db_port: int
    db_user: str
    db_pass: str
    db_name: str
    api_key: str
    timezone: ZoneInfo
    media_root: Path
    max_image_bytes: int
    admin_user: str
    admin_password: str
    session_secret: bytes
    session_hours: int
    gemini_api_key: str

    @classmethod
    def load(cls) -> "Settings":
        db_name = os.getenv("DB_NAME", "kakao_collector")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", db_name):
            raise RuntimeError("DB_NAME 은 영문/숫자/밑줄만 사용할 수 있습니다.")
        media_root = Path(os.getenv("MEDIA_ROOT", str(SERVER_ROOT / "storage" / "media")))
        engine = os.getenv("DB_ENGINE", "mysql").strip().lower()
        if engine not in ("mysql", "sqlite"):
            raise RuntimeError("DB_ENGINE 은 mysql 또는 sqlite 여야 합니다.")
        mysql = engine == "mysql"
        return cls(
            db_engine=engine,
            sqlite_path=Path(os.getenv("SQLITE_PATH", str(SERVER_ROOT / "storage" / "kakao_collector.db"))).resolve(),
            db_host=_require("DB_HOST") if mysql else "",
            db_port=int(os.getenv("DB_PORT", "3306")),
            db_user=_require("DB_USER") if mysql else "",
            db_pass=os.getenv("DB_PASS", ""),
            db_name=db_name,
            api_key=_require("API_KEY"),
            timezone=ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Seoul")),
            media_root=media_root.resolve(),
            max_image_bytes=int(os.getenv("MAX_IMAGE_MB", "10")) * 1024 * 1024,
            admin_user=os.getenv("ADMIN_USER", "admin"),
            admin_password=os.getenv("ADMIN_PASSWORD", ""),
            # 미지정 시 API_KEY·ADMIN_PASSWORD 로부터 유도 → 둘 중 하나를 바꾸면 기존 로그인 세션이 모두 무효화된다
            session_secret=hashlib.sha256(
                (os.getenv("ADMIN_SESSION_SECRET") or f"{os.getenv('API_KEY', '')}|{os.getenv('ADMIN_PASSWORD', '')}").encode()
            ).digest(),
            session_hours=int(os.getenv("ADMIN_SESSION_HOURS", "12")),
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        )
