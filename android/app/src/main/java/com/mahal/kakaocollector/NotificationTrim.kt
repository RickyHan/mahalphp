package com.mahal.kakaocollector

/**
 * 알림창의 카톡 알림을 최대 [MAX]개로 유지: 넘치면 오래된 알림부터 지운다. Android 의존성 없음(단위 테스트용).
 * - 묶음 요약 알림(그룹 요약)·지울 수 없는 알림은 세지도 지우지도 않는다.
 * - 업체 톡방(● 로 시작)의 알림은 업체 처리 요청 '답장'에 쓰므로 일반 방 알림을 먼저 지운다.
 *   그래도 넘치면 업체 톡방 알림도 오래된 것부터 지워, 전체가 [MAX]개를 넘지 않게 한다.
 * 지우는 알림은 이미 수집해 휴대폰에 저장한 뒤라 수집에서 빠지지 않는다.
 */
object NotificationTrim {
    const val MAX = 30
    private const val CIRCLES = "●⚫⬤⏺🔴🟠🟡🟢🔵🟣🟤"

    data class Item(val key: String, val postTime: Long, val room: String?, val isSummary: Boolean, val clearable: Boolean)

    /** 방 이름이 꽉 찬 동그라미(이모지 포함)로 시작하는지 — 첫 글자(코드 포인트)로 판단 */
    fun isVendorRoom(room: String?): Boolean {
        val r = room?.trimStart().orEmpty()
        if (r.isEmpty()) return false
        return CIRCLES.contains(String(Character.toChars(r.codePointAt(0))))
    }

    /** 지울 알림 key (지울 순서대로) */
    fun toCancel(items: List<Item>, max: Int = MAX): List<String> {
        val counted = items.filter { !it.isSummary && it.clearable }
        val over = counted.size - max
        if (over <= 0) return emptyList()
        // 일반 방의 오래된 것 → 업체 톡방의 오래된 것 순으로 넘친 만큼
        val order = counted.filter { !isVendorRoom(it.room) }.sortedBy { it.postTime } +
            counted.filter { isVendorRoom(it.room) }.sortedBy { it.postTime }
        return order.take(over).map { it.key }
    }
}
