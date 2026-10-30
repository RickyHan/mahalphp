"""대화형 초기 설정: DB 접속 정보를 묻고 server/.env 를 만든 뒤 스키마·테이블을 생성한다.

    cd server && python -m scripts.setup_env

API_KEY 와 관리자 비밀번호는 무작위로 생성해 화면에 한 번 보여 준다.
"""

from __future__ import annotations

import getpass
import secrets
import socket
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = SERVER_ROOT / ".env"
PORT = 8000


def ask(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip() or default
        if value:
            return value
        print("  값을 입력하십시오.")


def quote(value: str) -> str:
    """python-dotenv 큰따옴표 값으로 변환 (특수문자 안전)"""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def lan_ip() -> str:
    """같은 네트워크의 휴대폰에서 접속할 주소 (UDP 소켓 경로 조회만 하며 패킷은 보내지 않음)"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> int:
    print("=== 카톡 수집기 서버 초기 설정 ===\n")
    if ENV_PATH.exists() and ask("server/.env 가 이미 있습니다. 새로 만들까요? (y/n)", "n").lower() != "y":
        print("기존 설정을 유지합니다.")
        return 0

    db_host = ask("MySQL 호스트", "127.0.0.1")
    db_port = ask("MySQL 포트", "3306")
    db_user = ask("MySQL 계정")
    db_pass = getpass.getpass("MySQL 비밀번호 (입력 내용은 표시되지 않음): ")
    db_name = ask("스키마 이름", "kakao_collector")
    admin_user = ask("관리자 웹 아이디", "admin")

    api_key = secrets.token_urlsafe(32)
    admin_password = secrets.token_urlsafe(12)
    values = {
        "DB_HOST": db_host,
        "DB_PORT": db_port,
        "DB_USER": db_user,
        "DB_PASS": db_pass,
        "DB_NAME": db_name,
        "API_KEY": api_key,
        "ADMIN_USER": admin_user,
        "ADMIN_PASSWORD": admin_password,
        "APP_TIMEZONE": "Asia/Seoul",
    }
    ENV_PATH.write_text("".join(f"{k}={quote(v)}\n" for k, v in values.items()), encoding="utf-8")
    try:
        ENV_PATH.chmod(0o600)
    except OSError:
        pass
    print(f"\n설정 파일 저장: {ENV_PATH}")

    # .env 를 쓴 뒤에 불러와야 새 값이 반영된다
    import pymysql

    from app.config import Settings
    from app.db import install_schema

    print(f"DB 접속 중: {db_user}@{db_host}:{db_port} ...")
    try:
        tables = install_schema(Settings.load())
    except pymysql.MySQLError as exc:
        code = exc.args[0] if exc.args else "?"
        print(f"\n[실패] DB 접속/스키마 생성 오류 (MySQL 오류 코드 {code})\n  {exc}")
        print("  · 2003: 호스트·포트가 틀렸거나 방화벽이 막고 있음")
        print("  · 1045: 계정 또는 비밀번호가 틀림")
        print("  · 1044/1142: 스키마 생성 권한이 없음")
        print("설정을 고친 뒤 다시 실행하십시오: python -m scripts.setup_env")
        return 1

    ip = lan_ip()
    print(f"[완료] 스키마 `{db_name}` 테이블: {', '.join(tables)}\n")
    print("=" * 60)
    print("아래 정보를 안전한 곳에 보관하십시오. (server/.env 에도 저장됨)")
    print(f"  관리자 웹   : http://{ip}:{PORT}/admin")
    print(f"  관리자 계정 : {admin_user} / {admin_password}")
    print(f"  앱 서버 주소: http://{ip}:{PORT}")
    print(f"  앱 API 키   : {api_key}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\n취소되었습니다.")
        sys.exit(130)
