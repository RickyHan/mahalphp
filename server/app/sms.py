"""민원처리업체 문자 발송 (뿌리오 연동 API v1).

흐름
  1) 분류 워커가 새 민원을 찾으면, 그 방에 카톡 접수 안내를 먼저 대기열에 넣고(reply_queue)
  2) 민원 카테고리에 연결된 업체마다 문자를 sms_queue 에 넣는다.
     카톡 안내가 휴대폰에서 처리(성공·실패)되면 바로, 늦어도 SMS_WAIT 뒤에는 문자를 보낸다.
  3) 워커가 sms_queue 를 처리한다: 뿌리오 토큰(24시간) 발급 → /v1/message 로 발송.

뿌리오 규격 (https://www.ppurio.com/send-api/guide)
  POST https://message.ppurio.com/v1/token    Authorization: Basic base64("계정:인증키")
       → {"token","type":"Bearer","expired":"yyyyMMddHHmmss","userIp"}
  POST https://message.ppurio.com/v1/message  Authorization: Bearer {token}
       {"account","messageType":"SMS|LMS","content","from","duplicateFlag","targetCount","targets":[{"to"}],"refKey","subject"?}
       → {"code":"1000","description","refKey","messageKey"}
  SMS 90byte · LMS 2000byte (한글 2byte), 추가 필드 불허, 기본 초당 15회.
"""

from __future__ import annotations

import base64
import logging
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

log = logging.getLogger("kakao.sms")

BASE_URL = "https://message.ppurio.com"
SMS_MAX_BYTES = 90
LMS_MAX_BYTES = 2000
SMS_WAIT = timedelta(minutes=2)  # 카톡 안내 결과를 기다리는 최대 시간
MAX_ATTEMPTS = 3
CONTENT_MAX_CHARS = 300  # 문자에 넣을 민원 원문 길이

DEFAULT_SUBJECT = "민원 처리 요청"
DEFAULT_TEMPLATE = (
    "[민원 처리 요청] {업체명} 담당자님,\n"
    "'{방}' 대화방에서 [{분류}] 민원이 접수되었습니다.\n"
    "- 요약: {요약}\n"
    "- 내용: {내용}\n"
    "- 접수: {시각} ({보낸사람})\n"
    "빠른 처리 부탁드립니다."
)
PLACEHOLDERS = {
    "{업체명}": "민원처리업체 이름",
    "{방}": "민원이 발생한 카톡 대화방 이름",
    "{분류}": "민원 카테고리",
    "{요약}": "Gemini 가 만든 한 줄 요약",
    "{내용}": f"민원 메시지 원문 (최대 {CONTENT_MAX_CHARS}자)",
    "{보낸사람}": "민원을 올린 사람(카톡 대화명)",
    "{시각}": "민원 메시지 시각 (MM/DD HH:mm)",
}
PHONE = re.compile(r"^0\d{8,10}$")


# ---- 문구 ----------------------------------------------------------------------------------

def byte_len(text: str) -> int:
    """뿌리오 기준 길이: 한글 등 2byte, 영문·숫자 1byte"""
    return sum(1 if ord(ch) < 128 else 2 for ch in text)


def clip_bytes(text: str, limit: int) -> str:
    out, used = [], 0
    for ch in text:
        size = 1 if ord(ch) < 128 else 2
        if used + size > limit:
            break
        out.append(ch)
        used += size
    return "".join(out)


def render(template: str, *, vendor: str, room: str, category: str, summary: str, content: str, sender: str, at: datetime) -> str:
    values = {
        "{업체명}": vendor,
        "{방}": room,
        "{분류}": category,
        "{요약}": summary or "-",
        "{내용}": re.sub(r"\s+", " ", content or "").strip()[:CONTENT_MAX_CHARS],
        "{보낸사람}": sender,
        "{시각}": at.strftime("%m/%d %H:%M"),
    }
    text = template
    for key, value in values.items():
        text = text.replace(key, value)
    return text.strip()


def message_type(text: str) -> str:
    return "SMS" if byte_len(text) <= SMS_MAX_BYTES else "LMS"


def normalize_phone(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


# ---- 뿌리오 클라이언트 -------------------------------------------------------------------------

class PpurioError(Exception):
    def __init__(self, message: str, retry: bool, code: str = ""):
        super().__init__(message)
        self.retry = retry
        self.code = code


ERRORS = {
    "2000": "요청 값이 올바르지 않습니다",
    "3001": "계정·인증키가 올바르지 않습니다 (PPURIO_ACCOUNT / PPURIO_API_KEY 확인)",
    "3002": "토큰이 유효하지 않습니다",
    "3003": "등록되지 않은 서버 IP 입니다 (뿌리오 > 연동 > 연동신청관리에서 서버 IP 등록)",
    "3004": "뿌리오 계정이 유효하지 않습니다",
    "3005": "토큰이 유효하지 않습니다",
    "3006": "인증 헤더가 없습니다",
    "3007": "토큰 발급에 실패했습니다",
    "3008": "요청이 너무 많습니다 (초당 제한)",
    "4004": "API 사용 권한이 비활성 상태입니다",
    "4006": "인증키가 유효하지 않습니다",
    "4007": "인증키를 발급받지 않았습니다",
}
RETRY_CODES = {"3002", "3005", "3007", "3008"}


@dataclass
class SendResult:
    message_key: str
    message_type: str


class PpurioClient:
    def __init__(self, account: str, api_key: str, base_url: str = BASE_URL, timeout: float = 15.0):
        self.account, self.api_key, self.base_url, self.timeout = account, api_key, base_url, timeout
        self._token: str | None = None
        self._expires: datetime | None = None
        self.user_ip = ""
        self._lock = threading.Lock()

    def _error(self, res: httpx.Response) -> PpurioError:
        try:
            body = res.json()
        except ValueError:
            body = {}
        code = str(body.get("code", ""))
        if body.get("userIp"):
            self.user_ip = body["userIp"]  # 실패 응답에도 뿌리오가 본 요청 IP 가 온다 (IP 등록용)
        desc = ERRORS.get(code) or body.get("description") or res.text[:150]
        retry = res.status_code == 429 or res.status_code >= 500 or code in RETRY_CODES or code.startswith("9")
        return PpurioError(f"뿌리오 {code or res.status_code}: {desc}", retry=retry, code=code)

    def token(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and self._expires and datetime.now() < self._expires:
                return self._token
            basic = base64.b64encode(f"{self.account}:{self.api_key}".encode()).decode()
            try:
                res = httpx.post(f"{self.base_url}/v1/token", headers={"Authorization": f"Basic {basic}"}, timeout=self.timeout)
            except httpx.HTTPError as exc:
                raise PpurioError(f"뿌리오 연결 실패: {exc}", retry=True) from exc
            if res.status_code != 200:
                raise self._error(res)
            body = res.json()
            self._token = body["token"]
            self.user_ip = body.get("userIp", "")
            # 만료 10분 전에 새로 받는다 (만료 표기를 못 읽으면 23시간)
            try:
                self._expires = datetime.strptime(body["expired"], "%Y%m%d%H%M%S") - timedelta(minutes=10)
            except (KeyError, ValueError):
                self._expires = datetime.now() + timedelta(hours=23)
            return self._token

    def send(self, *, sender: str, to: str, text: str, ref_key: str, subject: str = DEFAULT_SUBJECT) -> SendResult:
        kind = message_type(text)
        body: dict[str, Any] = {
            "account": self.account,
            "messageType": kind,
            "content": clip_bytes(text, LMS_MAX_BYTES),
            "from": normalize_phone(sender),
            "duplicateFlag": "N",
            "targetCount": 1,
            "targets": [{"to": normalize_phone(to)}],
            "refKey": ref_key[:32],
        }
        if kind == "LMS":
            body["subject"] = clip_bytes(subject, 60)[:30]
        for attempt in (1, 2):  # 토큰 만료 시 한 번 새로 받아 재시도
            try:
                res = httpx.post(
                    f"{self.base_url}/v1/message",
                    headers={"Authorization": f"Bearer {self.token(force=attempt == 2)}"},
                    json=body,
                    timeout=self.timeout,
                )
            except httpx.HTTPError as exc:
                raise PpurioError(f"뿌리오 연결 실패: {exc}", retry=True) from exc
            if res.status_code == 200 and str(res.json().get("code")) == "1000":
                return SendResult(res.json().get("messageKey", ""), kind)
            err = self._error(res)
            if attempt == 1 and err.code in ("3002", "3005"):
                continue
            raise err
        raise PpurioError("뿌리오 발송 실패", retry=True)


_client: PpurioClient | None = None
_client_lock = threading.Lock()


def client_for(settings) -> PpurioClient | None:
    """설정된 계정의 클라이언트 (토큰 재사용을 위해 프로세스당 하나)"""
    global _client
    if not (settings.ppurio_account and settings.ppurio_api_key):
        return None
    with _client_lock:
        if _client is None or (_client.account, _client.api_key) != (settings.ppurio_account, settings.ppurio_api_key):
            _client = PpurioClient(settings.ppurio_account, settings.ppurio_api_key)
        return _client


def new_ref_key(sms_id: int | str) -> str:
    return f"kc{sms_id}-{secrets.token_hex(6)}"[:32]
