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

/** 실제 알림 수신 서비스 코드로 알림 정리 확인: 카톡 알림 30개만 남기고 오래된 것부터 지움, 카톡 외 알림은 지움 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class NotificationTrimServiceTest {
    private lateinit var context: Context

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        MessageStore.resetForTest()
        context.deleteDatabase("queue.db")
        AppSettings(context).apply { captureEnabled = true; limitKakaoNotifications = true; removeOtherNotifications = true; otherRemoved = 0 }
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
    fun keepsThirtyKakaoAndRemovesOtherApps() {
        val service = Robolectric.buildService(KakaoNotificationListener::class.java).create().get()
        val shadow = shadowOf(service)
        (1..33).forEach { shadow.addActiveNotification(sbn(KakaoParser.KAKAO_PACKAGE, it, "★방$it", it * 1000L)) }
        shadow.addActiveNotification(sbn(KakaoParser.KAKAO_PACKAGE, 100, "●성도엘리베이터", 1L))   // 가장 오래된 업체 톡방
        shadow.addActiveNotification(sbn("com.other.app", 200, "다른 앱", 0L))
        shadow.addActiveNotification(sbn("com.samsung.android.messaging", 201, "문자", 0L))

        service.trimKakaoNotifications()

        val left = service.activeNotifications
        val kakao = left.filter { it.packageName == KakaoParser.KAKAO_PACKAGE }
        assertEquals(30, kakao.size)                                                   // 상한 30개
        assertTrue(kakao.any { it.id == 100 })                                         // 업체 톡방은 일반 방보다 나중에
        assertTrue(kakao.none { it.id in 1..4 })                                       // 일반 방 가장 오래된 4개 지움
        assertTrue(left.none { it.packageName != KakaoParser.KAKAO_PACKAGE })          // 카톡 외 알림 모두 지움
        val settings = AppSettings(context)
        assertEquals(4, settings.lastTrimCount)
        assertEquals(2, settings.otherRemoved)
        assertEquals(null, settings.lastTrimError)
        assertTrue(MessageStore.get(context).notificationLogs().first().second.contains("[알림 정리]"))

        // 30개 이하면 카톡 알림은 지우지 않음
        service.trimKakaoNotifications(force = true)
        assertEquals(30, service.activeNotifications.count { it.packageName == KakaoParser.KAKAO_PACKAGE })
        assertEquals(0, AppSettings(context).lastTrimCount)
    }

    @Test
    fun otherAppsKeptWhenSwitchedOff() {
        val service = Robolectric.buildService(KakaoNotificationListener::class.java).create().get()
        val shadow = shadowOf(service)
        AppSettings(context).removeOtherNotifications = false
        shadow.addActiveNotification(sbn("com.other.app", 200, "다른 앱", 0L))
        service.trimKakaoNotifications()
        assertEquals(1, service.activeNotifications.size)
        AppSettings(context).removeOtherNotifications = true
        service.trimKakaoNotifications()
        assertEquals(0, service.activeNotifications.size)
    }

    @Test
    fun doesNothingWhenSwitchedOffOrPaused() {
        val service = Robolectric.buildService(KakaoNotificationListener::class.java).create().get()
        val shadow = shadowOf(service)
        (1..35).forEach { shadow.addActiveNotification(sbn(KakaoParser.KAKAO_PACKAGE, it, "★방$it", it * 1000L)) }
        AppSettings(context).limitKakaoNotifications = false
        service.trimKakaoNotifications()
        assertEquals(35, service.activeNotifications.size)
        AppSettings(context).apply { limitKakaoNotifications = true; captureEnabled = false }
        service.trimKakaoNotifications()
        assertEquals(35, service.activeNotifications.size)                          // 수집 중지 중에는 지우지 않음
    }
}
