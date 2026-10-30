"""백그라운드 민원 분류 워커. 새 메시지가 들어오면 깨어나 Gemini 로 분류하고, 일시 오류는 점점 길게 쉬었다 재시도한다."""

from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime

from . import store
from .auth import get_settings

log = logging.getLogger("kakao.worker")

_wake = threading.Event()
_stop = threading.Event()
status: dict[str, str] = {"state": "대기", "last": ""}


def wake() -> None:
    _wake.set()


def _loop() -> None:
    backoff = 0
    while not _stop.is_set():
        try:
            result = store.classify_once(get_settings())
        except Exception as exc:  # DB 일시 오류 등 — 워커는 계속 살아 있어야 한다
            log.exception("분류 실패")
            result = {"done": 0, "retry": True, "reason": f"{type(exc).__name__}: {exc}"}
        stamp = datetime.now().strftime("%m-%d %H:%M")
        if result.get("done"):
            backoff = 0
            status.update(state="분류 중", last=f"{stamp} {result['done']}건 분류 · 민원 {result.get('complaints', 0)}건")
            continue  # 남은 것이 있을 수 있으니 바로 이어서
        if result.get("retry"):
            backoff = min(backoff * 2 or 30, 600)
            status.update(state="대기(재시도)", last=f"{stamp} {result['reason']} — {backoff}초 후 재시도")
        elif result.get("stop"):
            backoff = 600
            status.update(state="중단", last=f"{stamp} {result['reason']} — 설정 확인 필요")
        else:
            backoff = 0
            status["state"] = "대기" if result.get("reason") != "disabled" else "꺼짐"
        _wake.wait(timeout=backoff or 60)
        _wake.clear()


@asynccontextmanager
async def lifespan(_app):
    thread = None
    if os.getenv("WORKER_ENABLED", "1") == "1":
        _stop.clear()
        thread = threading.Thread(target=_loop, name="complaint-worker", daemon=True)
        thread.start()
    try:
        yield
    finally:
        _stop.set()
        _wake.set()
        if thread:
            thread.join(timeout=5)
