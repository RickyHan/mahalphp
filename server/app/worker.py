"""백그라운드 민원 분류 워커. 새 메시지가 들어오면 깨어나 Gemini 로 분류하고, 일시 오류는 점점 길게 쉬었다 재시도한다."""

from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime

from . import db, live, store
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
            live.bump("complaint")
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
    # 새 버전에서 추가된 테이블(예: vendors)을 시작 시 자동 생성 (이미 있으면 그대로)
    try:
        db.install_schema(get_settings())
    except Exception:  # 권한 부족 등 — 서비스는 계속 띄운다
        log.exception("스키마 자동 갱신 실패: python -m scripts.install_db 를 직접 실행하십시오")
    if os.getenv("BUILDING_SEED", "1") == "1":  # 처음 받은 건물 목록·FAQ 한 번 등록
        try:
            from . import apartments

            seeded = apartments.seed_buildings(get_settings())
            if seeded:
                log.info("건물 목록 등록: 새로 %(created)s · 갱신 %(updated)s · 자동 연결 %(mapped)s", seeded)
        except Exception:
            log.exception("건물 목록 자동 등록 실패")
        try:
            from . import apartments

            done = apartments.seed_building_vendors(get_settings())
            if done:
                log.info("건물별 담당 업체 등록: 새 업체 %(vendors_created)s · 지정 %(assigned)s · 보내지 않음 %(none)s · 없는 건물 %(missing)s", done)
        except Exception:
            log.exception("건물별 담당 업체 자동 등록 실패")
        try:
            from . import faq

            added = faq.seed_faq(get_settings())
            if added:
                log.info("FAQ 문항 등록: %s개", added)
        except Exception:
            log.exception("FAQ 자동 등록 실패")
    try:  # 업체 담당자 번호표(처음 받은 것) 한 번 반영
        from . import vendor_contacts

        done = vendor_contacts.seed_contacts(get_settings())
        if done:
            log.info("업체 담당자 연락처 등록: 업체 %(vendors)s곳(새로 %(created)s) · 번호 %(contacts)s개", done)
    except Exception:
        log.exception("업체 담당자 연락처 자동 등록 실패")
    try:  # 민원 판정 기본 사례 등록 + 이미 잘못 잡힌 같은 내용 바로잡기 (한 번)
        from . import examples

        fixed = examples.seed_examples(get_settings())
        if fixed is not None:
            log.info("민원 판정 기본 사례 등록 · 바로잡은 민원 %s건", fixed)
    except Exception:
        log.exception("민원 판정 기본 사례 등록 실패")
    try:  # 예전 단일 업체 담당 분류 → 여러 분류 표(vendor_categories)로 옮김
        from . import db as _db, vendors as _vendors

        with _db.transaction(get_settings()) as conn, conn.cursor() as cur:
            moved = _vendors.backfill_vendor_categories(cur)
        if moved:
            log.info("업체 담당 분류 옮김: %s건", moved)
    except Exception:
        log.exception("업체 담당 분류 옮기기 실패")
    try:  # 이미 쌓인 '(알 수 없음)' 보낸 사람 → 같은 사람(고유 키)의 실제 이름
        from . import db as _db, store as _store

        with _db.transaction(get_settings()) as conn, conn.cursor() as cur:
            n = _store.fill_all_unknown(cur)
        if n:
            log.info("'(알 수 없음)' 보낸 사람 이름 채움: %s건", n)
    except Exception:
        log.exception("'(알 수 없음)' 이름 채우기 실패")
    thread = None
    if os.getenv("WORKER_ENABLED", "1") == "1":
        _stop.clear()
        thread = threading.Thread(target=_loop, name="complaint-worker", daemon=True)
        thread.start()
        from . import schedules  # 예약 메시지 확인 (20초마다)

        schedules.start()
    try:
        yield
    finally:
        from . import schedules

        schedules.stop()
        _stop.set()
        _wake.set()
        if thread:
            thread.join(timeout=5)
