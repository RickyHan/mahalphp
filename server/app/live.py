"""실시간 갱신 신호: 새 메시지·민원 분류·업체 변경이 생기면 버전을 올리고,
관리자 웹은 /api/admin/stream(Server-Sent Events)으로 이를 받아 즉시 화면을 새로 그린다.

서버 프로세스 하나(uvicorn 기본) 기준의 메모리 신호다. 여러 프로세스로 띄우더라도
웹 화면은 주기적 새로고침으로 계속 따라오므로 데이터가 누락되지는 않는다.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_version = 0
_topics: dict[str, int] = {}


def bump(topic: str) -> None:
    """topic: message | complaint | vendor"""
    global _version
    with _lock:
        _version += 1
        _topics[topic] = _version


def snapshot() -> tuple[int, dict[str, int]]:
    with _lock:
        return _version, dict(_topics)
