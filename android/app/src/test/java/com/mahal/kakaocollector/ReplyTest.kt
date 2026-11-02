package com.mahal.kakaocollector

import android.app.Application
import android.app.Notification
import android.app.PendingIntent
import android.app.RemoteInput
import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/** 서버가 정한 민원 접수 안내를 카톡 '답장' 액션으로 전송 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class ReplyTest {
    private val app = ApplicationProvider.getApplicationContext<Application>()
    @Before
    fun setUp() {
        ReplyRegistry.clearForTest()
    }

    private fun kakaoNotification(intentAction: String = "com.kakao.talk.REPLY_TEST"): Notification {
        val pi = PendingIntent.getBroadcast(app, 0, Intent(intentAction), PendingIntent.FLAG_MUTABLE)
        val input = RemoteInput.Builder("reply_text").setLabel("답장").build()
        val action = Notification.Action.Builder(null, "답장", pi).addRemoteInput(input).build()
        return Notification.Builder(app, "chat").setContentTitle("301호").setContentText("소음").addAction(action).build()
    }

    @Test
    fun sendsTextThroughKakaoReplyAction() {
        ReplyRegistry.remember("OO아파트 입주민방", kakaoNotification())
        val text = "[소음] 민원이 접수되었습니다."   // 문구는 서버가 정한다

        assertEquals(ReplyRegistry.Result.Sent, ReplyRegistry.send(app, "OO아파트 입주민방", text))
        val sent = shadowOf(app).broadcastIntents.last { it.action == "com.kakao.talk.REPLY_TEST" }
        assertEquals(text, RemoteInput.getResultsFromIntent(sent)?.getCharSequence("reply_text")?.toString())
    }

    @Test
    fun noReplyActionForUnknownRoom() {
        assertEquals(ReplyRegistry.Result.NoAction, ReplyRegistry.send(app, "모르는 방", "x"))
        val plain = Notification.Builder(app, "chat").setContentTitle("a").build()
        ReplyRegistry.remember("버튼없는방", plain)
        assertFalse(ReplyRegistry.canReply("버튼없는방"))
    }

    @Test
    fun neverRepliesIntoCommentThread() {
        val room = "엘레벨레 업무방"
        // 일반 채팅 알림 2번 → 그 뒤 댓글 알림 1번 (가장 최근이 댓글)
        ReplyRegistry.remember(room, kakaoNotification("CHAT"), identity = "11|", thread = false, now = 1)
        ReplyRegistry.remember(room, kakaoNotification("CHAT"), identity = "11|", thread = false, now = 2)
        ReplyRegistry.remember(room, kakaoNotification("THREAD"), identity = "11|", thread = true, now = 3)
        assertEquals(ReplyRegistry.Result.Sent, ReplyRegistry.send(app, room, "처리 요청"))
        assertEquals("CHAT", shadowOf(app).broadcastIntents.last().action)
    }

    @Test
    fun prefersMostFrequentNotificationWhenThreadUndetected() {
        val room = "업체방"
        // 댓글 표시를 못 찾아도: 그 방에서 가장 자주 온 알림(일반 채팅)의 버튼을 쓴다
        repeat(3) { ReplyRegistry.remember(room, kakaoNotification("CHAT"), identity = "11|", now = it.toLong()) }
        ReplyRegistry.remember(room, kakaoNotification("THREAD"), identity = "99|", now = 10)
        assertEquals(ReplyRegistry.Result.Sent, ReplyRegistry.send(app, room, "x"))
        assertEquals("CHAT", shadowOf(app).broadcastIntents.last().action)
    }

    @Test
    fun threadOnlyRoomIsReportedNotSent() {
        ReplyRegistry.remember("댓글만온방", kakaoNotification("THREAD"), identity = "5|", thread = true)
        assertEquals(ReplyRegistry.Result.ThreadOnly, ReplyRegistry.send(app, "댓글만온방", "x"))
        assertFalse(ReplyRegistry.canReply("댓글만온방"))
    }

    @Test
    fun threadDetectorSignals() {
        assertTrue(ThreadDetector.isThread(listOf("홍길동", "댓글"), emptyList(), emptyList()))
        assertTrue(ThreadDetector.isThread(listOf("홍길동"), listOf("com.kakao.talk.threadId"), emptyList()))
        assertTrue(ThreadDetector.isThread(listOf("홍길동"), emptyList(), listOf("chat_comment")))
        assertFalse(ThreadDetector.isThread(listOf("홍길동", "OO아파트 입주민방", "답장"), listOf("android.title", "android.text", "android.messages"), listOf("12345", "chat")))
    }
}
