package com.mahal.kakaolocal

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CollectorStatusTest {
    private val now = 10_000_000L

    private fun input(permission: Boolean = true, capture: Boolean = true) =
        CollectorStatus.Input(permission, capture, stored = 12, photos = 3, lastCapturedAt = now - 120_000, now = now)

    @Test
    fun normalWhenEverythingOk() {
        val s = CollectorStatus.evaluate(input())
        assertEquals(CollectorStatus.Level.GOOD, s.level)
        assertTrue(s.detail.contains("저장 12건") && s.detail.contains("사진 3장") && s.detail.contains("2분 전"))
    }

    @Test
    fun warnWhenPausedBadWithoutPermission() {
        assertEquals(CollectorStatus.Level.WARN, CollectorStatus.evaluate(input(capture = false)).level)
        assertEquals(CollectorStatus.Level.BAD, CollectorStatus.evaluate(input(permission = false)).level)
    }

    @Test
    fun agoFormatting() {
        assertEquals("없음", CollectorStatus.ago(0, now))
        assertEquals("방금", CollectorStatus.ago(now - 5_000, now))
        assertEquals("1시간 전", CollectorStatus.ago(now - 3_600_000, now))
    }
}
