package com.mahal.kakaocollector

import android.app.Notification
import android.content.Context
import android.os.Process
import android.service.notification.StatusBarNotification
import androidx.test.core.app.ApplicationProvider
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/** 챗봇 앱: 실제 알림 수신 서비스 코드로 확인 — 카톡 알림·다른 앱 알림을 하나도 지우지 않음 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class NotificationTrimServiceTest {
    private lateinit var context: Context

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        MessageStore.resetForTest()
        context.deleteDatabase("queue.db")
        AppSettings(context).apply { captureEnabled = true; otherRemoved = 0 }
    }

    @After
    fun tearDown() = MessageStore.resetForTest()

    private fun sbn(pkg: String, id: Int, room: String, postTime: Long): StatusBarNotification {
        val n = Notification.Builder(context, "c")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("보낸사람$id")
            .setContentText("메시지 $id")
            .setSubText(room)
            .build()
        @Suppress("DEPRECATION")
        return StatusBarNotification(pkg, pkg, id, null, 0, 0, 0, n, Process.myUserHandle(), postTime)
    }

    @Test
    fun neverRemovesAnyNotification() {
        val service = Robolectric.buildService(KakaoNotificationListener::class.java).create().get()
        val shadow = shadowOf(service)
        (1..40).forEach { shadow.addActiveNotification(sbn(KakaoParser.KAKAO_PACKAGE, it, "★방$it", it * 1000L)) }
        shadow.addActiveNotification(sbn("com.other.app", 200, "다른 앱", 0L))
        val settings = AppSettings(context)
        settings.limitKakaoNotifications = true   // 켜려 해도
        settings.removeOtherNotifications = true
        assertEquals(false, settings.limitKakaoNotifications)
        assertEquals(false, settings.removeOtherNotifications)

        service.trimKakaoNotifications(force = true)
        assertEquals(41, service.activeNotifications.size)            // 카톡 40개 + 다른 앱 1개 모두 그대로
        service.trimKakaoNotifications()                                // 수집 중 주기 정리도 지우지 않음
        assertEquals(41, service.activeNotifications.size)
        assertTrue(service.activeNotifications.any { it.packageName == "com.other.app" })
        assertEquals(0, AppSettings(context).otherRemoved)
    }
}
