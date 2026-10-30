package com.mahal.kakaocollector

import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.Calendar
import java.util.TimeZone

class ComplaintStatsTest {
    private val kst = TimeZone.getTimeZone("Asia/Seoul")
    private var id = 1L

    /** 2025-10-30 14:00 KST 를 '지금'으로 */
    private val now = Calendar.getInstance(kst).apply { set(2025, Calendar.OCTOBER, 30, 14, 0, 0); set(Calendar.MILLISECOND, 0) }.timeInMillis
    private fun at(daysAgo: Int, hour: Int) = Calendar.getInstance(kst).apply {
        timeInMillis = now; add(Calendar.DAY_OF_MONTH, -daysAgo); set(Calendar.HOUR_OF_DAY, hour); set(Calendar.MINUTE, 5)
    }.timeInMillis

    private fun c(room: String, sender: String, category: String, time: Long, reply: Int = 0) = MessageStore.Complaint(
        MessageStore.Row(id++, room, sender, "t", "text", true, time, "k", null, "local", null), category, "", 0.9, 0, reply,
    )

    private val data = listOf(
        c("A동", "301호", "소음", at(0, 9), reply = 1), c("A동", "301호", "소음", at(0, 9) + 600_000), // 같은 문제 2건
        c("A동", "502호", "주차", at(0, 13), reply = -1),
        c("B동", "805호", "누수·하자", at(3, 22), reply = 1),
        c("B동", "805호", "소음", at(10, 23)),
        c("A동", "301호", "소음", at(40, 1)),
    )
    private val groups = ComplaintGrouping.group(data)

    @Test
    fun todayCountsIssuesNotMessages() {
        val s = ComplaintStats.compute(groups, ComplaintStats.Period.TODAY, now, kst)
        assertEquals(2, s.issues)
        assertEquals(3, s.messages)
        assertEquals(1, s.repeatedIssues)
        assertEquals(1, s.replySent)
        assertEquals(1, s.replyFailed)
        assertEquals(listOf("소음" to 1, "주차" to 1), s.byCategory)
        assertEquals(1, s.hourly[9]); assertEquals(1, s.hourly[13])
        assertEquals(listOf("10/30" to 2), s.daily)
    }

    @Test
    fun weekAndMonthAndAll() {
        assertEquals(3, ComplaintStats.compute(groups, ComplaintStats.Period.WEEK, now, kst).issues)
        val month = ComplaintStats.compute(groups, ComplaintStats.Period.MONTH, now, kst)
        assertEquals(4, month.issues)
        assertEquals(30, month.daily.size)
        assertEquals("10/01" to 0, month.daily.first())
        assertEquals(listOf("소음" to 2, "누수·하자" to 1, "주차" to 1), month.byCategory)
        assertEquals(listOf("A동" to 2, "B동" to 2), month.byRoom)

        val all = ComplaintStats.compute(groups, ComplaintStats.Period.ALL, now, kst)
        assertEquals(5, all.issues)
        assertEquals("301호 · A동" to 2, all.bySender.first())
        assertEquals(1, all.hourly[1])
    }
}
