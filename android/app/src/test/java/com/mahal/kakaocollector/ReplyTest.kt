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

    private fun kakaoNotification(): Notification {
        val pi = PendingIntent.getBroadcast(app, 0, Intent("com.kakao.talk.REPLY_TEST"), PendingIntent.FLAG_MUTABLE)
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
}
