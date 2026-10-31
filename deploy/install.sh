#!/usr/bin/env bash
# =============================================================================
# 민원 자동처리 시스템 서버 설치 (Ubuntu 22.04 / 24.04)
#
#   curl -fsSL https://raw.githubusercontent.com/rickyhan/mahalphp/claude/new-creation-dz2guv/deploy/install.sh | sudo bash
#
# 하는 일: MySQL·Python 설치 → 소스 받기 → MySQL 계정(OS 계정과 같은 이름) 생성 → server/.env 작성
#          → 테이블 생성 → systemd 서비스 등록(재부팅 시 자동 시작) → 동작 확인 → 접속 정보 출력
# 다시 실행하면 소스만 최신으로 바꾸고 기존 설정(.env)은 유지한다.
#
# 환경변수로 바꿀 수 있는 값: APP_USER(기본: sudo 실행한 계정) PORT(8000) APP_DIR(/opt/kakao-collector)
#   BRANCH, DB_NAME(kakao_collector), DB_PASS·GEMINI_API_KEY·ADMIN_PASSWORD(주면 묻지 않음)
#   TRUSTED_PROXY: 앞단 nginx 의 IP(예: 192.168.0.26). 주면 실제 접속자 IP·https 여부를 그 프록시 헤더로 인식
#                  (한 번 주면 서비스 설정에 남아 재실행 시 유지, 해제는 TRUSTED_PROXY=none)
# =============================================================================
set -euo pipefail

REPO_URL=${REPO_URL:-https://github.com/rickyhan/mahalphp.git}
BRANCH=${BRANCH:-claude/new-creation-dz2guv}
APP_DIR=${APP_DIR:-/opt/kakao-collector}
APP_USER=${APP_USER:-${SUDO_USER:-kakaochat}}
DB_NAME=${DB_NAME:-kakao_collector}
DB_USER=${DB_USER:-$APP_USER}
PORT=${PORT:-8000}
SERVICE=kakao-collector
MYSQL_ROOT_CMD=${MYSQL_ROOT_CMD:-mysql}   # Ubuntu 기본: root 는 소켓 인증이라 sudo 로 접속
SKIP_APT=${SKIP_APT:-0}                   # 테스트용
SOURCE_DIR=${SOURCE_DIR:-}                # 테스트용: git 대신 이 폴더를 복사

say()  { printf '\n\033[1;34m[%s]\033[0m %s\n' "$1" "$2"; }
die()  { printf '\n\033[1;31m[오류]\033[0m %s\n' "$1" >&2; exit 1; }
ask_secret() { # $1=안내문 → 표준출력으로 값 (curl | bash 에서도 키보드 입력을 받도록 /dev/tty 사용)
  local v
  read -r -s -p "$1: " v < /dev/tty; echo >&2
  printf '%s' "$v"
}

[[ $EUID -eq 0 ]] || die "sudo 로 실행하십시오:  sudo bash install.sh"
id "$APP_USER" >/dev/null 2>&1 || die "OS 계정 '$APP_USER' 이 없습니다. APP_USER=계정명 으로 지정하십시오."
[[ "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]] || die "DB_NAME 은 영문/숫자/밑줄만 가능합니다."
[[ "$DB_USER" =~ ^[A-Za-z0-9_.-]+$ ]] || die "DB 계정 이름에 쓸 수 없는 문자가 있습니다: $DB_USER"

# ---- 1. 패키지 ----------------------------------------------------------------
if [[ "$SKIP_APT" != 1 ]]; then
  say 1/7 "패키지 설치 (MySQL, Python, git)"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q mysql-server python3-venv python3-pip git curl
  if command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]; then
    systemctl enable --now mysql
  else
    service mysql start || true
  fi
fi

# ---- 2. 소스 ------------------------------------------------------------------
say 2/7 "소스 받기 → $APP_DIR"
if [[ -n "$SOURCE_DIR" ]]; then
  mkdir -p "$APP_DIR"
  (cd "$SOURCE_DIR" && tar --exclude=.git --exclude=android --exclude='server/.venv' --exclude='server/.env' --exclude='__pycache__' -cf - .) | (cd "$APP_DIR" && tar -xf -)
elif [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch -q --depth 1 origin "$BRANCH"
  git -C "$APP_DIR" reset -q --hard FETCH_HEAD
else
  git clone -q --depth 1 -b "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
chown -R "$APP_USER": "$APP_DIR"
SERVER_DIR="$APP_DIR/server"
ENV_FILE="$SERVER_DIR/.env"

# ---- 3. 설정 값 ----------------------------------------------------------------
FRESH_ENV=1
[[ -f "$ENV_FILE" ]] && FRESH_ENV=0
if [[ $FRESH_ENV == 1 ]]; then
  say 3/7 "설정 입력"
  if [[ -z "${DB_PASS:-}" ]]; then
    echo "MySQL 계정 '$DB_USER' 을 만듭니다. 비밀번호는 OS 계정 '$APP_USER' 과 같게 입력하십시오."
    DB_PASS=$(ask_secret "MySQL 비밀번호")
    DB_PASS2=$(ask_secret "한 번 더 입력")
    [[ "$DB_PASS" == "$DB_PASS2" ]] || die "두 비밀번호가 다릅니다."
    [[ -n "$DB_PASS" ]] || die "비밀번호가 비어 있습니다."
  fi
  if [[ -z "${GEMINI_API_KEY+x}" ]]; then
    GEMINI_API_KEY=$(ask_secret "Gemini API 키 (민원 분류용, 없으면 Enter — 나중에 .env 에 추가 가능)")
  fi
else
  say 3/7 "기존 설정 유지: $ENV_FILE"
  # 기존 .env 에서 DB 비밀번호를 읽어 계정을 맞춘다
  DB_PASS=$(sed -n 's/^DB_PASS="\(.*\)"$/\1/p' "$ENV_FILE" | sed 's/\\"/"/g; s/\\\\/\\/g')
  [[ -n "$DB_PASS" ]] || die "$ENV_FILE 에서 DB_PASS 를 읽지 못했습니다."
fi

# ---- 4. MySQL 계정·스키마 -------------------------------------------------------
say 4/7 "MySQL: 스키마 '$DB_NAME', 계정 '$DB_USER'@localhost"
PW_SQL=${DB_PASS//\\/\\\\}; PW_SQL=${PW_SQL//\'/\'\'}
$MYSQL_ROOT_CMD <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$PW_SQL';
CREATE USER IF NOT EXISTS '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$PW_SQL';
ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$PW_SQL';
ALTER USER '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$PW_SQL';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

# ---- 5. Python 환경·.env·테이블 --------------------------------------------------
say 5/7 "Python 가상환경과 패키지"
runuser -u "$APP_USER" -- python3 -m venv "$SERVER_DIR/.venv"
runuser -u "$APP_USER" -- "$SERVER_DIR/.venv/bin/python" -m pip install -q --disable-pip-version-check -r "$SERVER_DIR/requirements.txt"

if [[ $FRESH_ENV == 1 ]]; then
  ADMIN_PASSWORD=${ADMIN_PASSWORD:-$("$SERVER_DIR/.venv/bin/python" -c 'import secrets;print(secrets.token_urlsafe(12))')}
  API_KEY=$("$SERVER_DIR/.venv/bin/python" -c 'import secrets;print(secrets.token_urlsafe(32))')
  # 특수문자 안전하게: 큰따옴표 값, \ 와 " 는 이스케이프
  DB_USER="$DB_USER" DB_PASS="$DB_PASS" DB_NAME="$DB_NAME" API_KEY="$API_KEY" ADMIN_PASSWORD="$ADMIN_PASSWORD" \
  GEMINI_API_KEY="${GEMINI_API_KEY:-}" "$SERVER_DIR/.venv/bin/python" - "$ENV_FILE" <<'PY'
import os, sys
q = lambda v: '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
values = {
    "DB_ENGINE": "mysql", "DB_HOST": "127.0.0.1", "DB_PORT": "3306",
    "DB_USER": os.environ["DB_USER"], "DB_PASS": os.environ["DB_PASS"], "DB_NAME": os.environ["DB_NAME"],
    "API_KEY": os.environ["API_KEY"], "ADMIN_USER": "admin", "ADMIN_PASSWORD": os.environ["ADMIN_PASSWORD"],
    "GEMINI_API_KEY": os.environ["GEMINI_API_KEY"], "APP_TIMEZONE": "Asia/Seoul",
}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    f.write("".join(f"{k}={q(v)}\n" for k, v in values.items()))
PY
  chown "$APP_USER": "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

say 5/7 "테이블 생성"
(cd "$SERVER_DIR" && runuser -u "$APP_USER" -- .venv/bin/python -m scripts.install_db) || die "DB 접속 또는 테이블 생성 실패 (위 메시지 확인)"

# ---- 6. 서비스 ---------------------------------------------------------------
say 6/7 "서비스 등록: $SERVICE (포트 $PORT)"
UNIT_FILE=/etc/systemd/system/$SERVICE.service
if [[ -z "${TRUSTED_PROXY:-}" && -f "$UNIT_FILE" ]]; then   # 재실행 시 기존 값 유지
  TRUSTED_PROXY=$(sed -n 's/.*--forwarded-allow-ips \([^ ]*\).*/\1/p' "$UNIT_FILE")
fi
[[ "${TRUSTED_PROXY:-}" == none ]] && TRUSTED_PROXY=""
[[ -z "${TRUSTED_PROXY:-}" || "$TRUSTED_PROXY" =~ ^[0-9A-Fa-f.:,/]+$ ]] || die "TRUSTED_PROXY 형식 오류: $TRUSTED_PROXY"
RUN_CMD="$SERVER_DIR/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $PORT"
[[ -n "${TRUSTED_PROXY:-}" ]] && RUN_CMD+=" --proxy-headers --forwarded-allow-ips $TRUSTED_PROXY" && echo "프록시 신뢰: $TRUSTED_PROXY"
if command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]; then
  cat > "$UNIT_FILE" <<UNIT
[Unit]
Description=Kakao notification collector (web + API + complaint worker)
After=network-online.target mysql.service
Wants=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$SERVER_DIR
ExecStart=$RUN_CMD
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable -q $SERVICE
  systemctl restart $SERVICE
  if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    ufw allow "$PORT/tcp" >/dev/null && echo "방화벽(ufw): $PORT/tcp 허용"
  fi
  STARTED=1
else
  echo "systemd 가 없어 서비스 등록을 건너뜁니다. 직접 실행: cd $SERVER_DIR && sudo -u $APP_USER $RUN_CMD"
  STARTED=0
fi

# ---- 7. 확인·안내 -------------------------------------------------------------
say 7/7 "동작 확인"
if [[ $STARTED == 1 ]]; then
  for _ in $(seq 1 20); do curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break; sleep 1; done
  curl -fsS "http://127.0.0.1:$PORT/api/health" && echo || die "웹 서버가 응답하지 않습니다: journalctl -u $SERVICE -n 50"
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
get_env() { sed -n "s/^$1=\"\(.*\)\"$/\1/p" "$ENV_FILE" | sed 's/\\"/"/g; s/\\\\/\\/g'; }
cat <<INFO

================================================================
 설치 완료
----------------------------------------------------------------
 관리자 웹   : http://${IP:-서버IP}:$PORT/admin
 관리자 계정 : admin / $(get_env ADMIN_PASSWORD)
 앱 서버 주소: http://${IP:-서버IP}:$PORT
 앱 API 키   : $(get_env API_KEY)
 Gemini 키   : $( [[ -n "$(get_env GEMINI_API_KEY)" ]] && echo 설정됨 || echo "없음 → $ENV_FILE 에 GEMINI_API_KEY 추가 후 sudo systemctl restart $SERVICE")
 MySQL       : $DB_USER@localhost / 스키마 $DB_NAME (비밀번호: OS 계정과 동일)
----------------------------------------------------------------
 · 휴대폰이 같은 Wi-Fi 가 아니면 공유기에서 외부 포트 → $IP:$PORT 포워딩 후
   앱에 http://<공인IP>:<외부포트> 를 입력하십시오.
 · 로그: journalctl -u $SERVICE -f      재시작: sudo systemctl restart $SERVICE
 · 업데이트: 이 설치 명령을 다시 실행 (설정은 유지)
================================================================
INFO
