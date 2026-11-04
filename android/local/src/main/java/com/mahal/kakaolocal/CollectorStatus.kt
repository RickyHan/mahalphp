package com.mahal.kakaolocal

/**
 * 보관함이 정상 동작 중인지 한 줄로 판정한다 (상태 카드·상태 알림 공용). Android 의존성 없음.
 * 🟢 정상: 권한 OK, 수집 중
 * 🟡 주의: 수집 일시 중지(사용자가 중지)
 * 🔴 조치 필요: 알림 접근 권한 꺼짐
 */
object CollectorStatus {
    enum class Level { GOOD, WARN, BAD }

    data class Input(
        val permissionGranted: Boolean,
        val captureEnabled: Boolean,
        val stored: Int,
        val photos: Int,
        val lastCapturedAt: Long,
        val now: Long,
    )

    data class Status(val level: Level, val title: String, val detail: String)

    fun evaluate(i: Input): Status {
        val detail = "마지막 수집 ${ago(i.lastCapturedAt, i.now)} · 저장 ${i.stored}건 · 사진 ${i.photos}장"
        return when {
            !i.permissionGranted -> Status(Level.BAD, "알림 접근 권한이 꺼져 있습니다", "'권한 설정'에서 알림 접근을 켜야 카톡을 저장합니다")
            !i.captureEnabled -> Status(Level.WARN, "수집 일시 중지됨", "수집 시작을 누르면 다시 저장합니다 · 저장 ${i.stored}건")
            else -> Status(Level.GOOD, "정상 수집 중 · 휴대폰에 저장", detail)
        }
    }

    fun ago(at: Long, now: Long): String {
        if (at <= 0) return "없음"
        val sec = (now - at).coerceAtLeast(0) / 1000
        return when {
            sec < 60 -> "방금"
            sec < 3600 -> "${sec / 60}분 전"
            sec < 86_400 -> "${sec / 3600}시간 전"
            else -> "${sec / 86_400}일 전"
        }
    }
}
