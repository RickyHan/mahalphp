package com.mahal.kakaocollector

/**
 * 같은 사람이 같은 방에서 짧은 시간에 같은 문제로 여러 번 보낸 민원을 하나의 '문제'로 묶는다.
 * 기준: 같은 방 + 같은 보낸 사람 + 같은 분류, 그리고 직전 메시지와의 간격이 [WINDOW_MS] 이내(연쇄 허용).
 * Android 의존성 없음.
 */
object ComplaintGrouping {
    const val WINDOW_MS = 30 * 60 * 1000L

    data class Group(val items: List<MessageStore.Complaint>) {
        val first get() = items.first()
        val latest get() = items.last()
        val room get() = first.row.room
        val sender get() = first.row.sender
        val category get() = first.category
        val count get() = items.size
        val startAt get() = first.row.postedAt
        val endAt get() = latest.row.postedAt
        /** 대표 요약: 가장 확신도가 높은 메시지의 요약 */
        val summary get() = items.maxByOrNull { it.confidence }?.summary?.takeIf { it.isNotBlank() } ?: first.row.text.take(30)
        val ids get() = items.map { it.row.id }
    }

    /** 결과는 최근 문제 먼저 (각 묶음 안의 메시지는 시간순) */
    fun group(complaints: List<MessageStore.Complaint>, windowMs: Long = WINDOW_MS): List<Group> {
        val open = HashMap<Triple<String, String, String>, MutableList<MessageStore.Complaint>>()
        val groups = mutableListOf<MutableList<MessageStore.Complaint>>()
        for (c in complaints.sortedWith(compareBy({ it.row.postedAt }, { it.row.id }))) {
            val key = Triple(c.row.room, c.row.sender, c.category)
            val current = open[key]
            if (current != null && c.row.postedAt - current.last().row.postedAt <= windowMs) {
                current += c
            } else {
                val fresh = mutableListOf(c)
                groups += fresh
                open[key] = fresh
            }
        }
        return groups.map { Group(it) }.sortedByDescending { it.endAt }
    }
}
