package com.mahal.kakaocollector

import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class CaptureControlTest {
    private val ctx = ApplicationProvider.getApplicationContext<android.app.Application>()

    @Test fun toggleStopsAndStartsCapture() {
        CaptureControl.set(ctx, true)
        assertTrue(CaptureControl.isRunning(ctx))
        assertFalse(CaptureControl.toggle(ctx))
        assertFalse(AppSettings(ctx).captureEnabled)
        assertTrue(CaptureControl.toggle(ctx))
    }

    @Test fun receiverTogglesOnlyOnItsAction() {
        CaptureControl.set(ctx, true)
        val receiver = CaptureToggleReceiver()
        receiver.onReceive(ctx, android.content.Intent("other"))
        assertTrue(CaptureControl.isRunning(ctx))
        receiver.onReceive(ctx, android.content.Intent(CaptureToggleReceiver.ACTION))
        assertFalse(CaptureControl.isRunning(ctx))
    }

    @Test fun pausedStatusIsWarn() {
        val s = CollectorStatus.evaluate(
            CollectorStatus.Input(true, true, false, 2, 0, 0, 0, null, 1_000_000),
        )
        assertEquals(CollectorStatus.Level.WARN, s.level)
        assertEquals("수집 일시 중지됨", s.title)
    }
}
