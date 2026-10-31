#!/usr/bin/env bash
# =============================================================================
# nginx 서버(예: 192.168.0.26)에서 실행: 외부 → nginx → 민원 자동처리 시스템 웹 서버(192.168.0.68:8000) 연결
#
#   curl -fsSL https://raw.githubusercontent.com/rickyhan/mahalphp/claude/new-creation-dz2guv/deploy/setup-nginx.sh | sudo bash
#
# 방식 (환경변수로 선택)
#   기본  : 전용 포트 LISTEN_PORT(28000) 로 받음 → 공유기에서 외부 28000 → 이 서버:28000 포워딩
#   도메인: SERVER_NAME=kakao.example.com 이면 80 포트 이름 기반 가상호스트 (DNS 가 이 공인 IP 를 가리켜야 함)
#   BACKEND=192.168.0.68:8000 (웹 서버 주소)
# 기존 nginx 사이트 설정은 건드리지 않고 kakao-collector 설정 파일 하나만 추가한다.
# =============================================================================
set -euo pipefail

BACKEND=${BACKEND:-192.168.0.68:8000}
LISTEN_PORT=${LISTEN_PORT:-28000}
SERVER_NAME=${SERVER_NAME:-}
NAME=kakao-collector

say() { printf '\n\033[1;34m[%s]\033[0m %s\n' "$1" "$2"; }
die() { printf '\n\033[1;31m[오류]\033[0m %s\n' "$1" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "sudo 로 실행하십시오."
command -v nginx >/dev/null || die "nginx 가 설치되어 있지 않습니다."
[[ "$BACKEND" =~ ^[A-Za-z0-9.:-]+$ ]] || die "BACKEND 형식 오류: $BACKEND"
[[ -z "$SERVER_NAME" || "$SERVER_NAME" =~ ^[A-Za-z0-9.-]+$ ]] || die "SERVER_NAME 형식 오류: $SERVER_NAME"

say 1/4 "웹 서버 연결 확인: http://$BACKEND/api/health"
if curl -fsS -m 5 "http://$BACKEND/api/health"; then echo; else
  echo "⚠️  지금은 $BACKEND 에 연결되지 않습니다. 웹 서버(68번) 설치·실행 후 다시 확인하십시오. 설정은 계속 진행합니다."
fi

if [[ -n "$SERVER_NAME" ]]; then LISTEN="80"; HOST="$SERVER_NAME"; else LISTEN="$LISTEN_PORT"; HOST="_"; fi

# Debian/Ubuntu 형식(sites-available) 우선, 없으면 conf.d
if [[ -d /etc/nginx/sites-available ]]; then
  CONF=/etc/nginx/sites-available/$NAME
  LINK=/etc/nginx/sites-enabled/$NAME
else
  CONF=/etc/nginx/conf.d/$NAME.conf
  LINK=""
fi

say 2/4 "nginx 설정 작성: $CONF (listen $LISTEN, server_name $HOST → $BACKEND)"
[[ -f "$CONF" ]] && cp "$CONF" "$CONF.bak.$(date +%Y%m%d%H%M%S)"
cat > "$CONF" <<NGINX
# 민원 자동처리 시스템 웹·API 프록시 (deploy/setup-nginx.sh 가 생성)
server {
    listen $LISTEN;
    server_name $HOST;

    # 앱이 사진(최대 10MB)을 올린다
    client_max_body_size 20m;

    access_log /var/log/nginx/$NAME.access.log;
    error_log  /var/log/nginx/$NAME.error.log;

    location / {
        proxy_pass http://$BACKEND;
        proxy_http_version 1.1;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$remote_addr;   # 덮어쓰기: 외부에서 IP 위조 방지
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout    120s;
    }
}
NGINX
[[ -n "$LINK" ]] && ln -sf "$CONF" "$LINK"

say 3/4 "설정 검사 후 적용"
nginx -t
if command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]; then systemctl reload nginx; else nginx -s reload 2>/dev/null || nginx; fi
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow "$LISTEN/tcp" >/dev/null && echo "방화벽(ufw): $LISTEN/tcp 허용"
fi

say 4/4 "확인"
curl -fsS -m 5 "http://127.0.0.1:$LISTEN/api/health" -H "Host: ${SERVER_NAME:-localhost}" && echo || echo "⚠️  nginx 를 거친 확인 실패 — 웹 서버(68번)가 실행 중인지 확인하십시오."

PUBLIC_IP=$(curl -fsS -m 5 https://ifconfig.me 2>/dev/null || true)
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [[ -n "$SERVER_NAME" ]]; then
  URL="http://$SERVER_NAME"
  ROUTER="공유기: 외부 80 → $LAN_IP:80 (이미 이 nginx 로 포워딩돼 있으면 추가 작업 없음), DNS: $SERVER_NAME → ${PUBLIC_IP:-공인IP}"
else
  URL="http://${PUBLIC_IP:-공인IP}:$LISTEN"
  ROUTER="공유기 포트포워딩 추가: 외부 TCP $LISTEN → $LAN_IP:$LISTEN"
fi
cat <<INFO

================================================================
 nginx 설정 완료
----------------------------------------------------------------
 $ROUTER
 외부 접속 주소 : $URL/admin   (관리자 웹)
 앱 서버 주소   : $URL         (휴대폰 앱 '서버 주소'에 입력)
 사내망 주소    : http://$LAN_IP:$LISTEN/admin
----------------------------------------------------------------
 · 68번 웹 서버가 실제 접속자 IP 를 알도록 68번에서 한 번 실행:
   curl -fsSL https://raw.githubusercontent.com/rickyhan/mahalphp/claude/new-creation-dz2guv/deploy/install.sh | sudo env TRUSTED_PROXY=$LAN_IP bash
 · 외부 공개 시 HTTPS 권장: 도메인이 있으면 SERVER_NAME=도메인 으로 다시 실행 후 certbot --nginx
================================================================
INFO
