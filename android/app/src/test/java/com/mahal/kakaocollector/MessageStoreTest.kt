package com.mahal.kakaocollector

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/** 휴대폰 내 SQLite 저장소의 실제 쿼리를 Robolectric(Android SQLite)으로 검증 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class MessageStoreTest {

    private lateinit var context: Context
    private lateinit var store: MessageStore

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        MessageStore.resetForTest()
        context.deleteDatabase("queue.db")
        store = MessageStore.get(context)
    }

    @After
    fun tearDown() = MessageStore.resetForTest()

    private fun msg(room: String, sender: String, text: String, t: Long, type: MessageType = MessageType.TEXT, group: Boolean = true) =
        KakaoMessage(room, sender, text, type, group, t, "key")

    private fun image(name: String): String =
        File(context.filesDir, "media").apply { mkdirs() }.resolve(name).apply { writeBytes(byteArrayOf(1, 2, 3)) }.absolutePath

    @Test
    fun storesLocallyAndDeduplicates() {
        assertTrue(store.insert(msg("가족방", "엄마", "밥 먹었니?", 1_000), null, upload = false))
        assertFalse(store.insert(msg("가족방", "엄마", "밥 먹었니?", 1_000), null, upload = false))
        assertTrue(store.contains(msg("가족방", "엄마", "밥 먹었니?", 1_000)))
        val c = store.counts()
        assertEquals(1, c.total)
        assertEquals(1, c.local)
        assertEquals(0, c.pending)
    }

    @Test
    fun roomSummariesOrderedByLatestWithPhotoCount() {
        store.insert(msg("개발팀", "홍길동", "첫 메시지", 1_000), null, upload = false)
        store.insert(msg("이영희", "이영희", "안녕", 2_000, group = false), null, upload = false)
        store.insert(msg("개발팀", "김철수", "사진을 보냈습니다.", 3_000, MessageType.PHOTO), image("a.jpg"), upload = false)

        val rooms = store.roomSummaries()
        assertEquals(listOf("개발팀", "이영희"), rooms.map { it.room })
        assertEquals(2, rooms[0].messageCount)
        assertEquals(1, rooms[0].photoCount)
        assertEquals("김철수", rooms[0].lastSender)
        assertEquals("photo", rooms[0].lastType)
        assertTrue(rooms[0].isGroup)
        assertFalse(rooms[1].isGroup)
    }

    @Test
    fun roomMessagesPaging() {
        (1..5).forEach { store.insert(msg("개발팀", "홍길동", "메시지 $it", it * 1_000L), null, upload = false) }
        val all = store.roomMessages("개발팀")
        assertEquals((1..5).map { "메시지 $it" }, all.map { it.text }) // 오래된 → 최신

        val latest2 = store.roomMessages("개발팀", limit = 2)
        assertEquals(listOf("메시지 4", "메시지 5"), latest2.map { it.text })

        val older = store.roomMessages("개발팀", beforeId = latest2.first().id, limit = 2)
        assertEquals(listOf("메시지 2", "메시지 3"), older.map { it.text })

        val newer = store.roomMessages("개발팀", afterId = all[1].id, limit = 2)
        assertEquals(listOf("메시지 3", "메시지 4"), newer.map { it.text })
    }

    @Test
    fun searchMatchesTextAndSenderAndEscapesWildcards() {
        store.insert(msg("마케팅", "정다은", "할인율 100% 쿠폰", 1_000), null, upload = false)
        store.insert(msg("마케팅", "최유진", "100 단위로 수정", 2_000), null, upload = false)
        store.insert(msg("가족방", "엄마", "밥", 3_000), null, upload = false)

        assertEquals(listOf("할인율 100% 쿠폰"), store.search("100%").map { it.text })
        assertEquals(listOf("밥"), store.search("엄마").map { it.text })
        assertEquals(2, store.search("100").size)
        assertTrue(store.search("_").isEmpty())
    }

    @Test
    fun uploadQueueTransitions() {
        store.insert(msg("방", "a", "1", 1_000), null, upload = false)
        store.insert(msg("방", "a", "2", 2_000), null, upload = true)
        assertEquals(1, store.pending(10).size)

        assertEquals(1, store.queueLocalForUpload())
        val pending = store.pending(10)
        assertEquals(2, pending.size)

        store.markSent(pending[0])
        store.markFailed(pending[1], "HTTP 422")
        val c = store.counts()
        assertEquals(1, c.sent)
        assertEquals(1, c.failed)
        assertEquals(0, c.pending)
    }

    @Test
    fun imagesKeptAfterUploadAndRemovedOnDeleteAll() {
        val path = image("b.jpg")
        store.insert(msg("방", "a", "사진을 보냈습니다.", 1_000, MessageType.PHOTO), path, upload = true)
        store.markSent(store.pending(1).single())
        assertTrue("전송 후에도 사진은 휴대폰에 보관", File(path).exists())

        store.recordSeenRoom("방", true, 1_000)
        store.deleteAll()
        assertFalse(File(path).exists())
        assertEquals(0, store.counts().total)
        assertEquals(listOf("방"), store.seenRooms().map { it.room }) // 방 선택 목록은 유지
    }

    @Test
    fun memberTrackingFirstSeenAndRenameWithKey() {
        store.recordSender("골프방", "user-1", "버디왕", 1_000)
        store.recordSender("골프방", "user-1", "버디왕", 2_000)
        store.recordSender("골프방", "user-1", "⛳버디킹⛳", 3_000)   // 같은 고유 키, 다른 대화명 → 변경
        store.recordSender("골프방", "user-2", "이글", 4_000)

        val events = store.memberEvents("골프방")
        assertEquals(
            listOf(MemberEvents.Type.FIRST_SEEN, MemberEvents.Type.RENAME, MemberEvents.Type.FIRST_SEEN),
            events.map { it.type }
        )
        val rename = events.single { it.type == MemberEvents.Type.RENAME }
        assertEquals("버디왕", rename.oldName)
        assertEquals("⛳버디킹⛳", rename.member)

        val members = store.members("골프방")
        assertEquals(listOf("이글", "⛳버디킹⛳"), members.map { it.name })
        assertEquals(3, members.single { it.name == "⛳버디킹⛳" }.messageCount)
        assertTrue(members.all { it.keyed })
    }

    @Test
    fun withoutKeyNewNameIsNewMemberNotRename() {
        store.recordSender("방", null, "홍길동", 1_000)
        store.recordSender("방", null, "길동이", 2_000)
        assertEquals(listOf(MemberEvents.Type.FIRST_SEEN, MemberEvents.Type.FIRST_SEEN), store.memberEvents("방").map { it.type })
        assertFalse(store.members("방").any { it.keyed })
    }

    @Test
    fun systemEventsFilterAndDeleteAll() {
        store.addEvent(MemberEvents.Type.JOIN, "골프방", "신입", null, null, 5_000, "신입님이 들어왔습니다.")
        store.addEvent(MemberEvents.Type.LEAVE, "골프방", "탈퇴자", null, null, 6_000, "탈퇴자님이 나갔습니다.")
        store.addEvent(MemberEvents.Type.JOIN, "다른방", "누군가", null, null, 7_000, null)
        assertEquals(listOf("탈퇴자", "신입"), store.memberEvents("골프방").map { it.member })
        assertEquals(listOf("누군가", "신입"), store.memberEvents(type = MemberEvents.Type.JOIN).map { it.member })
        assertEquals(listOf("다른방", "골프방"), store.memberRooms())
        store.deleteAll()
        assertTrue(store.memberEvents().isEmpty())
    }
}
