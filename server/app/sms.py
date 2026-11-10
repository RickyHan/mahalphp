"""민원처리업체 문자 발송 (뿌리오 연동 API v1).

흐름 (store.enqueue_vendor_messages · worker)
  1) 새 민원의 담당 업체마다: 건물×업체 담당자 번호(없으면 연락처가 한 명뿐인 업체의 그 번호)가 있으면 문자,
     번호가 없으면 예전처럼 업체 카톡방에 처리 요청(reply_queue).
     테스트 모드면 모든 문자를 테스트 번호로만 보내고, 카톡 처리 요청은 예전과 같이 보낸다.
  2) 워커가 sms_queue 를 처리한다: 뿌리오 토큰(24시간) 발급 → /v1/message 로 발송.
     끝내 보내지 못하면(설정 오류·발송 실패) 그 업체 카톡방으로 대신 보낸다.

뿌리오 규격 (https://www.ppurio.com/send-api/guide)
  POST https://message.ppurio.com/v1/token    Authorization: Basic base64("계정:인증키")
       → {"token","type":"Bearer","expired":"yyyyMMddHHmmss","userIp"}
  POST https://message.ppurio.com/v1/message  Authorization: Bearer {token}
       {"account","messageType":"SMS|LMS","content","from","duplicateFlag","targetCount","targets":[{"to"}],"refKey","subject"?}
       → {"code":1000 또는 "1000","description","refKey","messageKey"(예시 표기 "messsageKey")}
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
MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=1)
DEFAULT_TEST_NUMBER = "01075728018"

DEFAULT_SUBJECT = "민원 처리 요청"
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


def message_type(text: str) -> str:
    return "SMS" if byte_len(text) <= SMS_MAX_BYTES else "LMS"


def normalize_phone(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


# ---- 뿌리오 클라이언트 -------------------------------------------------------------------------

def _json(res: httpx.Response) -> dict[str, Any]:
    try:
        body = res.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


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
            if not (self.account.isascii() and self.api_key.isascii()) or ":" in self.account:
                raise PpurioError("PPURIO_ACCOUNT / PPURIO_API_KEY 값에 한글·공백·따옴표 등이 섞여 있습니다 (.env 확인)", retry=False)
            basic = base64.b64encode(f"{self.account}:{self.api_key}".encode()).decode()
            try:
                res = httpx.post(f"{self.base_url}/v1/token", headers={"Authorization": f"Basic {basic}"}, timeout=self.timeout)
            except httpx.HTTPError as exc:
                raise PpurioError(f"뿌리오 연결 실패: {exc}", retry=True) from exc
            if res.status_code != 200:
                raise self._error(res)
            try:
                body = res.json()
                token = str(body["token"])
            except (ValueError, KeyError, TypeError) as exc:
                raise PpurioError(f"뿌리오 토큰 응답을 해석할 수 없습니다: {res.text[:120]}", retry=True) from exc
            self._token = token
            self.user_ip = str(body.get("userIp") or "")
            # 만료 10분 전에 새로 받는다. expired 는 문자열 또는 숫자(yyyyMMddHHmmss), 못 읽으면 23시간
            try:
                self._expires = datetime.strptime(str(body["expired"]), "%Y%m%d%H%M%S") - timedelta(minutes=10)
            except (KeyError, ValueError, TypeError):
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
            if res.status_code == 200 and str(_json(res).get("code")) == "1000":
                data = _json(res)
                # 규격 예시에는 'messsageKey'(s 3개)로 표기되어 있어 둘 다 받는다
                return SendResult(str(data.get("messageKey") or data.get("messsageKey") or ""), kind)
            err = self._error(res)
            if attempt == 1 and err.code in ("3002", "3005"):
                continue
            raise err
        raise PpurioError("뿌리오 발송 실패", retry=True)


_client: PpurioClient | None = None
_client_lock = threading.Lock()


def credentials(settings, conf: dict[str, str] | None = None) -> tuple[str, str]:
    """뿌리오 계정·인증키: 서버 .env(PPURIO_ACCOUNT·PPURIO_API_KEY)가 우선, 없으면 관리자 웹 설정"""
    conf = conf or {}
    account = settings.ppurio_account or conf.get("ppurio_account", "").strip()
    key = settings.ppurio_api_key or conf.get("ppurio_api_key", "").strip()
    return account, key


def client_for(settings, conf: dict[str, str] | None = None) -> PpurioClient | None:
    """설정된 계정의 클라이언트 (토큰 재사용을 위해 프로세스당 하나)"""
    global _client
    account, key = credentials(settings, conf)
    if not (account and key):
        return None
    with _client_lock:
        if _client is None or (_client.account, _client.api_key) != (account, key):
            _client = PpurioClient(account, key)
        return _client


def new_ref_key(sms_id: int | str) -> str:
    return f"kc{sms_id}-{secrets.token_hex(6)}"[:32]
