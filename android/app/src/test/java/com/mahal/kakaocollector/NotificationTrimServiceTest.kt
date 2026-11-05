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

/** 실제 알림 수신 서비스 코드로 알림 정리 확인: 카톡 알림 20개만 남기고 오래된 것부터 지움, 업체 톡방·다른 앱 알림은 그대로 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class NotificationTrimServiceTest {
    private lateinit var context: Context

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        MessageStore.resetForTest()
        context.deleteDatabase("queue.db")
        AppSettings(context).apply { captureEnabled = true; limitKakaoNotifications = true }
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
    fun keepsTwentyKakaoNotificationsAndLogsResult() {
        val service = Robolectric.buildService(KakaoNotificationListener::class.java).create().get()
        val shadow = shadowOf(service)
        (1..23).forEach { shadow.addActiveNotification(sbn(KakaoParser.KAKAO_PACKAGE, it, "★방$it", it * 1000L)) }
        shadow.addActiveNotification(sbn(KakaoParser.KAKAO_PACKAGE, 100, "●성도엘리베이터", 1L))   // 가장 오래된 업체 톡방
        shadow.addActiveNotification(sbn("com.other.app", 200, "다른 앱", 0L))

        service.trimKakaoNotifications()

        val left = service.activeNotifications
        val kakao = left.filter { it.packageName == KakaoParser.KAKAO_PACKAGE }
        assertEquals(21, kakao.size)                                                   // 20개 + 업체 톡방
        assertTrue(kakao.any { it.id == 100 })                                         // 업체 톡방 남김
        assertTrue(kakao.none { it.id in 1..3 })                                       // 가장 오래된 3개 지움
        assertTrue(left.any { it.packageName == "com.other.app" })                     // 다른 앱 알림은 건드리지 않음
        val settings = AppSettings(context)
        assertEquals(3, settings.lastTrimCount)
        assertEquals(null, settings.lastTrimError)
        assertTrue(MessageStore.get(context).notificationLogs().first().second.contains("[알림 정리]"))

        // 20개 이하면 아무것도 지우지 않음
        service.trimKakaoNotifications(force = true)
        assertEquals(21, service.activeNotifications.count { it.packageName == KakaoParser.KAKAO_PACKAGE })
        assertEquals(0, AppSettings(context).lastTrimCount)
    }

    @Test
    fun doesNothingWhenSwitchedOffOrPaused() {
        val service = Robolectric.buildService(KakaoNotificationListener::class.java).create().get()
        val shadow = shadowOf(service)
        (1..25).forEach { shadow.addActiveNotification(sbn(KakaoParser.KAKAO_PACKAGE, it, "★방$it", it * 1000L)) }
        AppSettings(context).limitKakaoNotifications = false
        service.trimKakaoNotifications()
        assertEquals(25, service.activeNotifications.size)
        AppSettings(context).apply { limitKakaoNotifications = true; captureEnabled = false }
        service.trimKakaoNotifications()
        assertEquals(25, service.activeNotifications.size)                          // 수집 중지 중에는 지우지 않음
    }
}
