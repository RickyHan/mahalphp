package com.mahal.kakaocollector

/**
 * 알림창의 카톡 알림을 최대 [MAX]개로 유지: 넘치면 가장 오래된 알림부터 지운다. Android 의존성 없음(단위 테스트용).
 * - 묶음 요약 알림(그룹 요약)·지울 수 없는 알림은 세지도 지우지도 않는다.
 * - 업체 톡방(● 로 시작)의 알림은 지우지 않는다: 업체 처리 요청을 그 알림의 '답장'으로 보내므로(최대 12시간 대기).
 * 지우는 알림은 이미 수집해 휴대폰에 저장한 뒤라 수집에서 빠지지 않는다.
 */
object NotificationTrim {
    const val MAX = 20
    private const val CIRCLES = "●⚫⬤⏺🔴🟠🟡🟢🔵🟣🟤"

    data class Item(val key: String, val postTime: Long, val room: String?, val isSummary: Boolean, val clearable: Boolean)

    /** 방 이름이 꽉 찬 동그라미(이모지 포함)로 시작하는지 — 첫 글자(코드 포인트)로 판단 */
    fun isVendorRoom(room: String?): Boolean {
        val r = room?.trimStart().orEmpty()
        if (r.isEmpty()) return false
        return CIRCLES.contains(String(Character.toChars(r.codePointAt(0))))
    }

    /** 지울 알림 key (오래된 순) */
    fun toCancel(items: List<Item>, max: Int = MAX): List<String> {
        val counted = items.filter { !it.isSummary && it.clearable }
        if (counted.size <= max) return emptyList()
        return counted.sortedByDescending { it.postTime }
            .drop(max)
            .filter { !isVendorRoom(it.room) }
            .sortedBy { it.postTime }
            .map { it.key }
    }
}
