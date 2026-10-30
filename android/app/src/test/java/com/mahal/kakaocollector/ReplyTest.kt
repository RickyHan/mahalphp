package com.mahal.kakaocollector

import android.app.Application
import android.app.Notification
import android.app.PendingIntent
import android.app.RemoteInput
import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/** 민원 접수 안내: 카톡 '답장' 액션으로 전송, 문제당 1회 판단 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class ReplyTest {
    private val app = ApplicationProvider.getApplicationContext<Application>()
    private lateinit var store: MessageStore

    @Before
    fun setUp() {
        ReplyRegistry.clearForTest()
        MessageStore.resetForTest()
        app.deleteDatabase("queue.db")
        store = MessageStore.get(app)
    }

    @After
    fun tearDown() = MessageStore.resetForTest()

    private fun kakaoNotification(): Notification {
        val pi = PendingIntent.getBroadcast(app, 0, Intent("com.kakao.talk.REPLY_TEST"), PendingIntent.FLAG_MUTABLE)
        val input = RemoteInput.Builder("reply_text").setLabel("답장").build()
        val action = Notification.Action.Builder(null, "답장", pi).addRemoteInput(input).build()
        return Notification.Builder(app, "chat").setContentTitle("301호").setContentText("소음").addAction(action).build()
    }

    @Test
    fun sendsTextThroughKakaoReplyAction() {
        ReplyRegistry.remember("OO아파트 입주민방", kakaoNotification())
        val text = ReplyRegistry.format(AppSettings.DEFAULT_REPLY_TEMPLATE, "301호", "소음", "층간소음", "OO아파트 입주민방")
        assertEquals("[소음] 민원이 접수되었습니다.", text)

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
    fun templatePlaceholders() {
        assertEquals("301호님 [주차] 이중주차 접수 (A동)", ReplyRegistry.format("{보낸사람}님 [{분류}] {요약} 접수 ({방})", "301호", "주차", "이중주차", "A동"))
    }

    private fun complaint(sender: String, text: String, atMin: Long, category: String): Long {
        store.insert(KakaoMessage("A동", sender, text, MessageType.TEXT, true, atMin * 60_000, "k"), null, upload = false)
        val id = store.unclassified(50).last().id
        store.saveVerdicts(listOf(Complaints.Verdict(id, true, category, text, 0.9)), "m")
        return id
    }

    @Test
    fun newIssueOnlyForFirstComplaintOfGroup() {
        val first = complaint("301호", "윗집 소음 심해요", 0, "소음")
        val repeat = complaint("301호", "아직도 시끄러워요", 10, "소음")
        val other = complaint("502호", "저희도 소음 들려요", 11, "소음")
        val later = complaint("301호", "오늘도 소음이 납니다", 100, "소음")   // 90분 뒤 → 새 문제
        assertTrue(store.isNewIssue(first))
        assertFalse(store.isNewIssue(repeat))
        assertTrue(store.isNewIssue(other))
        assertTrue(store.isNewIssue(later))

        store.setReplyStatus(first, 1, "[소음] 민원이 접수되었습니다.")
        assertEquals(1, store.complaintsByIds(longArrayOf(first)).single().replyStatus)
    }

    @Test
    fun ownMessagesAreNotClassified() {
        store.insert(KakaoMessage("A동", KakaoParser.SELF_SENDER, "[소음] 민원이 접수되었습니다.", MessageType.TEXT, true, 1, "k"), null, upload = false)
        assertTrue(store.unclassified(10).isEmpty())
    }
}
