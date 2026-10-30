@echo off
rem 카톡 수집기 서버: 최초 실행 시 가상환경·패키지 설치와 DB 설정을 하고, 서버를 시작한다.
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Python 가상환경 생성
  %PY% -m venv .venv || goto :error
)
echo [2/3] 패키지 설치
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || goto :error

if not exist ".env" (
  echo [3/3] 초기 설정
  ".venv\Scripts\python.exe" -m scripts.setup_env || goto :error
)

echo 서버 시작: http://0.0.0.0:8000  (관리자 웹: /admin, 종료: Ctrl+C)
echo Windows 방화벽 허용 창이 뜨면 "허용"을 누르십시오.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
goto :eof

:error
echo.
echo 오류가 발생했습니다. 위 메시지를 확인하십시오.
echo Python 3.10 이상이 필요합니다: https://www.python.org/downloads/ (설치 시 "Add python.exe to PATH" 체크)
pause
exit /b 1
