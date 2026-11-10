"""운영 서버에서 업체 문자(뿌리오)를 실제로 1통 보내 본다. 결과는 관리자 웹 '업체 발송 이력 > 테스트 문자'에도 남는다.

    cd /opt/kakao-collector/server && sudo -u kakaochat .venv/bin/python -m scripts.sms_test            # 테스트 번호로
    ... -m scripts.sms_test 010-1234-5678                       # 이 번호로
    ... -m scripts.sms_test --from 010-8738-7240                # 발신번호를 저장하고 보냄

뿌리오는 등록된 서버 IP 에서만 받는다. 실패 시 표시되는 '뿌리오가 본 IP'를 뿌리오 > 연동 > 연동신청관리에 등록한다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from app import db, sms, sms_dispatch, store
from app.config import Settings


def main() -> int:
    p = argparse.ArgumentParser(description="뿌리오 문자 1통 실제 발송 시험")
    p.add_argument("to", nargs="?", default="", help="받을 번호 (비우면 관리자 웹의 테스트 번호)")
    p.add_argument("--from", dest="sender", default="", help="발신번호 (뿌리오에 등록한 번호, 주면 설정에 저장)")
    args = p.parse_args()

    settings = Settings.load()
    db.install_schema(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        if args.sender:
            store.put_settings(cur, {"sms_from": sms.normalize_phone(args.sender)})
        conf = store.get_settings_map(cur)
    to = sms.normalize_phone(args.to) or sms.normalize_phone(conf["sms_test_number"])
    account, _ = sms.credentials(settings, conf)
    print(f"뿌리오 계정 : {account or '(없음 — .env PPURIO_ACCOUNT 또는 관리자 웹에서 입력)'}")
    print(f"발신번호    : {conf['sms_from'] or '(없음 — --from 으로 지정)'}")
    print(f"받는 번호   : {to or '(없음)'}")
    if not to:
        return 1

    client = sms.client_for(settings, conf)
    if client is not None:
        try:
            client.token(force=True)
            print(f"연결 확인   : 정상 (뿌리오가 본 서버 IP {client.user_ip})")
        except sms.PpurioError as exc:
            print(f"연결 확인   : 실패 — {exc}")
            if client.user_ip:
                print(f"              뿌리오가 본 서버 IP {client.user_ip} 를 연동신청관리에 등록하십시오.")
            return 1

    now = store.now_local(settings)
    text = f"[테스트] 민원 자동처리 시스템 업체 문자 실제 발송 시험입니다. ({now:%m/%d %H:%M})"
    with db.transaction(settings) as conn, conn.cursor() as cur:
        sms_id = store.enqueue_sms(cur, message_id=None, vendor=None, phone=to, room="", category="", text=text, now=now, test=True)
    sms_dispatch.send_pending(settings)
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, note, message_key, msg_type FROM sms_queue WHERE id = %s", (sms_id,))
        row = cur.fetchone()
    if row["status"] == "sent":
        print(f"결과        : 보냄 ({row['msg_type']}, 뿌리오 접수번호 {row['message_key']}) — 휴대폰을 확인하십시오. ({datetime.now():%H:%M:%S})")
        return 0
    print(f"결과        : {row['status']} — {row['note'] or ''}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
