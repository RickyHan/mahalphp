#!/usr/bin/env bash
# 카톡 수집기 서버: 최초 실행 시 가상환경·패키지 설치와 DB 설정을 하고, 서버를 시작한다.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "[1/3] Python 가상환경 생성"
  python3 -m venv .venv
fi
echo "[2/3] 패키지 설치"
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt

if [ ! -f .env ]; then
  echo "[3/3] 초기 설정"
  .venv/bin/python -m scripts.setup_env
fi

echo "서버 시작: http://0.0.0.0:8000  (관리자 웹: /admin, 종료: Ctrl+C)"
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
