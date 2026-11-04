package com.mahal.kakaocollector

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationTrimTest {
    private fun item(i: Int, room: String = "★방$i", summary: Boolean = false, clearable: Boolean = true) =
        NotificationTrim.Item("k$i", i * 1000L, room, summary, clearable)

    @Test
    fun keepsTwentyNewestAndCancelsOldestFirst() {
        val items = (1..23).map { item(it) }.shuffled()
        assertEquals(listOf("k1", "k2", "k3"), NotificationTrim.toCancel(items))
        assertEquals(emptyList<String>(), NotificationTrim.toCancel((1..20).map { item(it) }))
    }

    @Test
    fun summariesAndUnclearableAreNotCountedOrCancelled() {
        val items = (1..20).map { item(it) } + item(0, summary = true) + item(-1, clearable = false)
        assertEquals(emptyList<String>(), NotificationTrim.toCancel(items))
    }

    @Test
    fun vendorRoomsAreKept() {
        val items = (1..22).map { item(it) } + item(0, room = "●성도엘리베이터") + item(-1, room = "🔴 청소업체")
        assertEquals(listOf("k1", "k2"), NotificationTrim.toCancel(items))
        assertTrue(NotificationTrim.isVendorRoom("●대명"))
        assertTrue(NotificationTrim.isVendorRoom(" 🟢업체"))
        assertFalse(NotificationTrim.isVendorRoom("★다솜빌라"))
        assertFalse(NotificationTrim.isVendorRoom(null))
    }
}
