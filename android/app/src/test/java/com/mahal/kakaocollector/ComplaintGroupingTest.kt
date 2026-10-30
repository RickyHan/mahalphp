package com.mahal.kakaocollector

import org.junit.Assert.assertEquals
import org.junit.Test

class ComplaintGroupingTest {
    private var nextId = 1L
    private val min = 60_000L

    private fun c(room: String, sender: String, category: String, atMin: Long, summary: String = "", confidence: Double = 0.5) =
        MessageStore.Complaint(
            row = MessageStore.Row(nextId++, room, sender, "text$nextId", "text", true, atMin * min, "k", null, "local", null),
            category = category, summary = summary, confidence = confidence, checkedAt = 0,
        )

    @Test
    fun sameSenderSameIssueWithinWindowIsOneGroup() {
        val groups = ComplaintGrouping.group(
            listOf(
                c("A동방", "301호", "소음", 0, "층간소음"),
                c("A동방", "301호", "소음", 10, "또 쿵쿵거림", 0.9),
                c("A동방", "301호", "소음", 35),   // 직전과 25분 → 이어짐(연쇄)
            )
        )
        assertEquals(1, groups.size)
        assertEquals(3, groups[0].count)
        assertEquals("또 쿵쿵거림", groups[0].summary)   // 확신도 최고 요약
        assertEquals(0L, groups[0].startAt)
        assertEquals(35 * min, groups[0].endAt)
    }

    @Test
    fun splitsByGapSenderRoomAndCategory() {
        val groups = ComplaintGrouping.group(
            listOf(
                c("A동방", "301호", "소음", 0),
                c("A동방", "301호", "소음", 31),     // 31분 간격 → 새 문제
                c("A동방", "502호", "소음", 1),      // 다른 사람
                c("B동방", "301호", "소음", 2),      // 다른 방
                c("A동방", "301호", "주차", 3),      // 다른 분류
            )
        )
        assertEquals(5, groups.size)
        assertEquals(listOf(31 * min, 3 * min, 2 * min, 1 * min, 0L), groups.map { it.endAt }) // 최근 문제 먼저
    }

    @Test
    fun interleavedSendersStillGroupPerSender() {
        val groups = ComplaintGrouping.group(
            listOf(
                c("A동방", "301호", "주차", 0),
                c("A동방", "502호", "주차", 1),
                c("A동방", "301호", "주차", 2),
                c("A동방", "502호", "주차", 3),
            )
        )
        assertEquals(2, groups.size)
        assertEquals(setOf(2), groups.map { it.count }.toSet())
    }
}
