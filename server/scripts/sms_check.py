"""뿌리오 문자 연동 점검 · 테스트 발송 (서버에서 직접 실행).

    cd /opt/kakao-collector/server
    .venv/bin/python -m scripts.sms_check                              # 연동 정보·토큰 발급·서버 IP 확인만
    .venv/bin/python -m scripts.sms_check 01012345678                  # + 테스트 문자 (발신번호: 웹 '문자 발송' 설정값)
    .venv/bin/python -m scripts.sms_check 01012345678 --from 0212345678
    .venv/bin/python -m scripts.sms_check 01012345678 --from 0212345678 --text "단문 테스트"   # 직접 문구(90byte 이하면 SMS)

인증키는 화면에 출력하지 않는다. 결과는 웹 '문자 발송' 이력에도 남긴다.
"""

from __future__ import annotations

import argparse
import sys

from app import db, sms, store
from app.config import Settings

HINTS = {
    "3001": "PPURIO_ACCOUNT(뿌리오 아이디)와 PPURIO_API_KEY(연동 인증키)를 다시 확인하십시오.",
    "3003": "뿌리오 > 연동 > 연동관리에서 위 '서버 IP' 를 등록한 뒤 다시 실행하십시오.",
    "3004": "PPURIO_ACCOUNT 가 뿌리오 아이디와 다릅니다 (네이버 로그인 계정은 뿌리오 아이디를 확인).",
    "4004": "뿌리오에서 문자연동(API) 사용 승인이 아직이거나 비활성 상태입니다.",
    "4006": "인증키가 틀렸습니다. 연동관리에서 인증키를 다시 확인·재발급하십시오.",
    "4007": "아직 인증키를 발급받지 않았습니다. 연동관리에서 인증키를 발급하십시오.",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="뿌리오 문자 연동 점검")
    parser.add_argument("to", nargs="?", help="테스트 문자를 받을 휴대폰 번호")
    parser.add_argument("--from", dest="sender", help="발신번호 (기본: 웹 '문자 발송' 설정값)")
    parser.add_argument("--text", help="보낼 문구 (기본: 템플릿 예시). 짧으면 단문(SMS)으로 발송")
    args = parser.parse_args()

    settings = Settings.load()
    account, key = settings.ppurio_account, settings.ppurio_api_key
    print(f"PPURIO_ACCOUNT : {account or '(없음)'}")
    print(f"PPURIO_API_KEY : {'설정됨 (' + str(len(key)) + '자)' if key else '(없음)'}")
    if not (account and key):
        print("\n→ server/.env 에 PPURIO_ACCOUNT=뿌리오아이디, PPURIO_API_KEY=연동인증키 를 넣고 다시 실행하십시오.")
        return 1

    client = sms.PpurioClient(account, key)
    try:
        client.token(force=True)
        print(f"토큰 발급     : 성공 · 서버 IP {client.user_ip}")
    except sms.PpurioError as exc:
        print(f"토큰 발급     : 실패 — {exc}")
        if client.user_ip:
            print(f"서버 IP       : {client.user_ip}  (뿌리오 연동관리에 등록할 IP)")
        if exc.code in HINTS:
            print(f"\n→ {HINTS[exc.code]}")
        return 1

    if not args.to:
        print("\n연동 정상입니다. 테스트 문자를 보내려면 받을 번호를 붙여 다시 실행하십시오.")
        return 0

    with db.transaction(settings) as conn, conn.cursor() as cur:
        conf = store.get_settings_map(cur)
    sender = sms.normalize_phone(args.sender or conf["sms_from"])
    to = sms.normalize_phone(args.to)
    if not sender:
        print("\n→ 발신번호가 없습니다. --from 01012345678 처럼 뿌리오에 등록한 발신번호를 붙이십시오.")
        return 1
    if not sms.PHONE.match(to):
        print(f"\n→ 받을 번호가 올바르지 않습니다: {args.to}")
        return 1

    now = store.now_local(settings)
    text = args.text.strip() if args.text and args.text.strip() else "[테스트] " + sms.render(
        conf["sms_template"], vendor="테스트업체", room="OO아파트 입주민방", category="누수·하자", summary="욕실 천장 누수",
        content="욕실 천장에서 물이 떨어집니다. 확인 부탁드립니다", sender="1203호", at=now,
    )
    print(f"발신 → 수신    : {sender} → {to} ({sms.message_type(text)}, {sms.byte_len(text)}byte)")
    try:
        result = client.send(sender=sender, to=to, text=text, ref_key=sms.new_ref_key("cli"))
        status, note, mkey, mtype = "sent", None, result.message_key, result.message_type
        print(f"발송 요청     : 성공 (messageKey {result.message_key})")
        print("\n휴대폰 수신을 확인하십시오. 최종 결과는 뿌리오 '발송결과' / '연동 사용내역'에서도 볼 수 있습니다.")
        code = 0
    except sms.PpurioError as exc:
        status, note, mkey, mtype = "failed", str(exc)[:250], None, None
        print(f"발송 요청     : 실패 — {exc}")
        if exc.code in HINTS:
            print(f"\n→ {HINTS[exc.code]}")
        elif exc.code == "2000":
            print("\n→ 요청 값 오류: 발신번호가 뿌리오에 등록·인증된 번호인지 확인하십시오.")
        code = 1
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO sms_queue (message_id, vendor_name, phone, room_name, category, text, status, attempts, note,
                                      message_key, msg_type, send_after, created_at, done_at)
               VALUES (NULL, '테스트 발송(서버)', %s, 'OO아파트 입주민방', '누수·하자', %s, %s, 1, %s, %s, %s, %s, %s, %s)""",
            (to, text, status, note, mkey, mtype, now, now, now),
        )
    return code


if __name__ == "__main__":
    sys.exit(main())
