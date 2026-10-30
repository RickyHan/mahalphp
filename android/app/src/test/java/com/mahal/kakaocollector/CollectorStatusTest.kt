package com.mahal.kakaocollector

import com.mahal.kakaocollector.CollectorStatus.Level
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CollectorStatusTest {
    private val now = 1_761_800_000_000L
    private fun input(
        permission: Boolean = true, configured: Boolean = true, capture: Boolean = true,
        pending: Int = 0, failed: Int = 0, error: String? = null,
    ) = CollectorStatus.Input(permission, configured, capture, pending, failed, now - 120_000, now - 30_000, error, now)

    @Test
    fun normalWhenEverythingOk() {
        val s = CollectorStatus.evaluate(input())
        assertEquals(Level.GOOD, s.level)
        assertEquals("정상 수집 중", s.title)
        assertEquals("마지막 수집 2분 전 · 마지막 전송 방금 · 대기 0건", s.detail)
    }

    @Test
    fun uploadingBacklogIsStillGood() {
        val s = CollectorStatus.evaluate(input(pending = 120))
        assertEquals(Level.GOOD, s.level)
        assertTrue(s.title.contains("전송 중"))
    }

    @Test
    fun warnsOnUploadErrorOrCaptureOff() {
        assertEquals(Level.WARN, CollectorStatus.evaluate(input(pending = 3, error = "HTTP 503")).level)
        assertEquals(Level.WARN, CollectorStatus.evaluate(input(capture = false)).level)
    }

    @Test
    fun badWhenPermissionOrServerMissing() {
        assertEquals(Level.BAD, CollectorStatus.evaluate(input(permission = false)).level)
        assertEquals(Level.BAD, CollectorStatus.evaluate(input(configured = false)).level)
    }

    @Test
    fun agoFormatting() {
        assertEquals("없음", CollectorStatus.ago(0, now))
        assertEquals("3시간 전", CollectorStatus.ago(now - 3 * 3_600_000, now))
        assertEquals("2일 전", CollectorStatus.ago(now - 2 * 86_400_000L, now))
    }
}
