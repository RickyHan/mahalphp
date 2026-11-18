package com.mahal.kakaocollector

/**
 * 챗봇 앱(com.mahal.kakaochatbot) 전용 알림 정리 규칙:
 * - 주택방(★ 로 시작) 카톡 알림은 최근 [NotificationTrim.HOUSE_MAX]개만 남기고 오래된 것부터 지운다(이미 저장한 뒤라 수집에서 빠지지 않음).
 * - 그 밖의 카톡 알림(업체 ●·접수방 ◆·1:1 등)과 다른 앱 알림은 지우지 않는다.
 * 수집·저장·서버 전송·카톡 답장은 기존 수집 앱과 같다.
 */
const val KEEP_NOTIFICATIONS = true
