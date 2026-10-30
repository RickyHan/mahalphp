"""인증: 단말용 API 키(X-Api-Key)와 관리자 웹용 서명 세션 쿠키."""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from .config import Settings

SESSION_COOKIE = "kc_admin"
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 600


@lru_cache
def get_settings() -> Settings:
    return Settings.load()


SettingsDep = Annotated[Settings, Depends(get_settings)]


def _has_valid_api_key(settings: Settings, x_api_key: str | None) -> bool:
    return bool(x_api_key) and hmac.compare_digest(x_api_key, settings.api_key)


def require_api_key(settings: SettingsDep, x_api_key: Annotated[str | None, Header()] = None) -> None:
    if not _has_valid_api_key(settings, x_api_key):
        raise HTTPException(401, "API 키가 올바르지 않습니다.")


# ---- 관리자 세션 -----------------------------------------------------------------

def _sign(settings: Settings, payload: str) -> str:
    return hmac.new(settings.session_secret, payload.encode(), hashlib.sha256).hexdigest()


def issue_session(settings: Settings, now: float | None = None) -> str:
    """'<사용자>.<만료 epoch>.<서명>' 형식의 쿠키 값"""
    expires = int((now or time.time()) + settings.session_hours * 3600)
    payload = f"{settings.admin_user}.{expires}"
    return f"{payload}.{_sign(settings, payload)}"


def session_user(settings: Settings, cookie: str | None, now: float | None = None) -> str | None:
    if not cookie or not settings.admin_password:
        return None
    try:
        user, expires, signature = cookie.rsplit(".", 2)
        expired = int(expires) < (now or time.time())
    except ValueError:
        return None
    if expired or user != settings.admin_user:
        return None
    return user if hmac.compare_digest(signature, _sign(settings, f"{user}.{expires}")) else None


def require_admin(request: Request, settings: SettingsDep) -> str:
    user = session_user(settings, request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(401, "로그인이 필요합니다.")
    return user


def require_reader(request: Request, settings: SettingsDep, x_api_key: Annotated[str | None, Header()] = None) -> None:
    """조회 API: 단말 API 키 또는 관리자 세션 중 하나면 허용"""
    if _has_valid_api_key(settings, x_api_key):
        return
    if session_user(settings, request.cookies.get(SESSION_COOKIE)) is None:
        raise HTTPException(401, "인증이 필요합니다.")


# ---- 로그인 시도 제한 (프로세스 메모리 기준, IP 별) ----------------------------------

class LoginThrottle:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
        self._failures[key] = recent
        return recent

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key, time.time())) >= LOGIN_MAX_FAILURES

    def fail(self, key: str) -> None:
        with self._lock:
            self._recent(key, time.time()).append(time.time())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


login_throttle = LoginThrottle()


def verify_credentials(settings: Settings, username: str, password: str) -> bool:
    if not settings.admin_password:
        return False
    user_ok = hmac.compare_digest(username.encode(), settings.admin_user.encode())
    pass_ok = hmac.compare_digest(password.encode(), settings.admin_password.encode())
    return user_ok and pass_ok
