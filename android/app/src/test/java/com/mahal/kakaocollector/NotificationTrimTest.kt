package com.mahal.kakaocollector

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationTrimTest {
    private fun item(i: Int, room: String = "★방$i", summary: Boolean = false, clearable: Boolean = true) =
        NotificationTrim.Item("k$i", i * 1000L, room, summary, clearable)

    @Test
    fun keepsThirtyNewestAndCancelsOldestFirst() {
        val items = (1..33).map { item(it) }.shuffled()
        assertEquals(listOf("k1", "k2", "k3"), NotificationTrim.toCancel(items))
        assertEquals(emptyList<String>(), NotificationTrim.toCancel((1..30).map { item(it) }))
    }

    @Test
    fun summariesAndUnclearableAreNotCountedOrCancelled() {
        val items = (1..30).map { item(it) } + item(0, summary = true) + item(-1, clearable = false)
        assertEquals(emptyList<String>(), NotificationTrim.toCancel(items))
    }

    @Test
    fun ordinaryRoomsGoFirstButNeverMoreThanThirty() {
        // 일반 방 30 + 업체 톡방 2 (가장 오래됨) → 일반 방 오래된 2개를 지우고 업체 톡방은 남김
        val items = (1..30).map { item(it) } + item(0, room = "●성도엘리베이터") + item(-1, room = "🔴 청소업체")
        assertEquals(listOf("k1", "k2"), NotificationTrim.toCancel(items))
        // 업체 톡방만 33개 → 업체 톡방도 오래된 3개 지움 (전체 30개 상한)
        val vendors = (1..33).map { item(it, room = "●업체$it") }
        assertEquals(listOf("k1", "k2", "k3"), NotificationTrim.toCancel(vendors))
        // 일반 방 1 + 업체 톡방 31 → 일반 방 1개 + 업체 톡방 가장 오래된 1개
        val mixed = listOf(item(100)) + (1..31).map { item(it, room = "●업체$it") }
        assertEquals(listOf("k100", "k1"), NotificationTrim.toCancel(mixed))
        assertTrue(NotificationTrim.isVendorRoom("●대명"))
        assertTrue(NotificationTrim.isVendorRoom(" 🟢업체"))
        assertFalse(NotificationTrim.isVendorRoom("★다솜빌라"))
        assertFalse(NotificationTrim.isVendorRoom(null))
    }
}
