package com.mahal.kakaocollector

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationTrimTest {
    private fun item(i: Int, room: String? = "★방$i", summary: Boolean = false, clearable: Boolean = true) =
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

    @Test
    fun houseRoomsKeepTwentyNewest() {
        // ★ 방 23개 + 업체 ● 10개 + 접수방 ◆ 5개 + 방 모름 3개 → ★ 방 오래된 3개만 지움
        val items = (1..23).map { item(it) } + (101..110).map { item(it, room = "●업체$it") } +
            (201..205).map { item(it, room = "◆◇동대표 AI접수방") } + (301..303).map { item(it, room = null) }
        assertEquals(listOf("k1", "k2", "k3"), NotificationTrim.houseToCancel(items.shuffled()))
        // 20개 이하면 지우지 않음 · 요약·지울 수 없는 알림은 세지 않음
        assertEquals(emptyList<String>(), NotificationTrim.houseToCancel((1..20).map { item(it) } + item(0, summary = true) + item(-1, clearable = false)))
        // ★ 가 아닌 방은 아무리 많아도 지우지 않음
        assertEquals(emptyList<String>(), NotificationTrim.houseToCancel((1..50).map { item(it, room = "●업체$it") }))
        assertTrue(NotificationTrim.isHouseRoom("★미영팰리스/10세대"))
        assertTrue(NotificationTrim.isHouseRoom(" ☆하이클래스"))
        assertTrue(NotificationTrim.isHouseRoom("⭐마포더클래시"))
        assertFalse(NotificationTrim.isHouseRoom("●바른CCTV"))
        assertFalse(NotificationTrim.isHouseRoom("◆◇동대표 AI접수방"))
        assertFalse(NotificationTrim.isHouseRoom("청소(닥터크린): 봉황빌"))
        assertFalse(NotificationTrim.isHouseRoom(null))
    }
}
