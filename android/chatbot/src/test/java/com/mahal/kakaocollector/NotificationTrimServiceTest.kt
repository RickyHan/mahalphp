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

/** 챗봇 앱: 실제 알림 수신 서비스 코드로 확인 — 주택방(★) 알림만 최근 20개 유지, 나머지 알림은 지우지 않음 */
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
    fun keepsTwentyNewestHouseNotificationsOnly() {
        val service = Robolectric.buildService(KakaoNotificationListener::class.java).create().get()
        val shadow = shadowOf(service)
        // 주택방(★) 25개 + 업체 톡방(●) 15개 + 접수방(◆) 5개 + 다른 앱 1개
        (1..25).forEach { shadow.addActiveNotification(sbn(KakaoParser.KAKAO_PACKAGE, it, "★방$it", it * 1000L)) }
        (101..115).forEach { shadow.addActiveNotification(sbn(KakaoParser.KAKAO_PACKAGE, it, "●업체$it", 0L)) }
        (201..205).forEach { shadow.addActiveNotification(sbn(KakaoParser.KAKAO_PACKAGE, it, "◆◇동대표 AI접수방", 0L)) }
        shadow.addActiveNotification(sbn("com.other.app", 300, "다른 앱", 0L))
        val settings = AppSettings(context)
        settings.removeOtherNotifications = true   // 켜려 해도 다른 앱 알림은 지우지 않음
        assertEquals(false, settings.removeOtherNotifications)

        service.trimKakaoNotifications()
        val active = service.activeNotifications
        val house = active.filter { it.notification.extras.getCharSequence(Notification.EXTRA_SUB_TEXT)?.startsWith("★") == true }
        assertEquals(20, house.size)                                         // ★ 방은 최근 20개만
        assertEquals((6..25).toSet(), house.map { it.id }.toSet())           // 오래된 1~5 번이 지워짐
        assertEquals(15, active.count { it.id in 101..115 })                 // 업체 톡방은 그대로
        assertEquals(5, active.count { it.id in 201..205 })                  // 접수방은 그대로
        assertTrue(active.any { it.packageName == "com.other.app" })         // 다른 앱 알림도 그대로
        assertEquals(0, AppSettings(context).otherRemoved)

        service.trimKakaoNotifications()                                     // 다시 불러도 더 지우지 않음
        assertEquals(41, service.activeNotifications.size)
    }

    @Test
    fun doesNotTrimWhileCaptureStopped() {
        val service = Robolectric.buildService(KakaoNotificationListener::class.java).create().get()
        val shadow = shadowOf(service)
        (1..25).forEach { shadow.addActiveNotification(sbn(KakaoParser.KAKAO_PACKAGE, it, "★방$it", it * 1000L)) }
        AppSettings(context).captureEnabled = false
        service.trimKakaoNotifications()
        assertEquals(25, service.activeNotifications.size)                   // 수집 중지 중에는 지우지 않음
        service.trimKakaoNotifications(force = true)                         // '지금 정리'는 정리함
        assertEquals(20, service.activeNotifications.size)
    }
}
