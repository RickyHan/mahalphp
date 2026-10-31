package com.mahal.kakaocollector

/**
 * 앱이 정상 동작 중인지 한 줄로 판정한다 (상태 카드·상태 알림 공용). Android 의존성 없음.
 * 🟢 정상: 권한·서버 설정 OK, 전송 대기 없음(또는 전송 진행 중)
 * 🟡 주의: 수집 일시 중지(사용자가 중지), 서버 전송 지연(오류 후 재시도 중)
 * 🔴 조치 필요: 알림 접근 권한 꺼짐, 서버 미설정
 */
object CollectorStatus {
    enum class Level { GOOD, WARN, BAD }

    data class Input(
        val permissionGranted: Boolean,
        val serverConfigured: Boolean,
        val captureEnabled: Boolean,
        val pending: Int,
        val failed: Int,
        val lastCapturedAt: Long,
        val lastUploadAt: Long,
        val lastUploadError: String?,
        val now: Long,
    )

    data class Status(val level: Level, val title: String, val detail: String)

    fun evaluate(i: Input): Status {
        val detail = buildList {
            add("마지막 수집 ${ago(i.lastCapturedAt, i.now)}")
            add("마지막 전송 ${ago(i.lastUploadAt, i.now)}")
            add("대기 ${i.pending}건")
            if (i.failed > 0) add("실패 ${i.failed}건")
        }.joinToString(" · ")
        return when {
            !i.permissionGranted -> Status(Level.BAD, "알림 접근 권한이 꺼져 있습니다", "'권한 설정'에서 알림 접근을 켜야 카톡을 수집합니다")
            !i.serverConfigured -> Status(Level.BAD, "서버 설정이 필요합니다", "'서버 연결'에서 서버 주소와 API 키를 입력하십시오")
            !i.captureEnabled -> Status(Level.WARN, "수집 일시 중지됨", "수집 시작을 누르면 다시 수집합니다 · 대기 ${i.pending}건")
            i.pending > 0 && i.lastUploadError != null -> Status(Level.WARN, "서버 전송 지연 — 자동 재시도 중", "${i.lastUploadError.take(60)}\n$detail")
            i.pending > 0 -> Status(Level.GOOD, "정상 · 서버로 전송 중", detail)
            else -> Status(Level.GOOD, "정상 수집 중", detail)
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
