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

/** 전송 대기열(SQLite) — 실제 Android SQLite 로 검증 */
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

    private fun msg(text: String, t: Long, key: String? = null) =
        KakaoMessage("가족방", "엄마", text, MessageType.TEXT, true, t, "k", senderKey = key)

    @Test
    fun queuesOnceAndKeepsSenderKey() {
        assertTrue(store.insert(msg("밥 먹었니?", 1_000, key = "u1"), null))
        assertFalse(store.insert(msg("밥 먹었니?", 1_000, key = "u1"), null))
        val row = store.pending(10).single()
        assertEquals("u1", row.senderKey)
        assertEquals(1, store.counts().pending)
    }

    @Test
    fun sentRowsDropPhotoAndArePruned() {
        val photo = File(context.filesDir, "media").apply { mkdirs() }.resolve("p.jpg").apply { writeBytes(byteArrayOf(1)) }
        store.insert(KakaoMessage("방", "a", "사진을 보냈습니다.", MessageType.PHOTO, true, 1, "k"), photo.absolutePath)
        store.markSent(store.pending(1).single())
        assertFalse("서버 저장 후 휴대폰 사진은 삭제", photo.exists())

        (2..6).forEach { store.insert(msg("m$it", it.toLong()), null) }
        store.pending(10).forEach(store::markSent)
        store.pruneSent(keep = 3)
        assertEquals(3, store.counts().total)
        assertEquals(3, store.counts().sent)
    }

    @Test
    fun failedAndRetry() {
        store.insert(msg("a", 1), null)
        store.insert(msg("b", 2), null)
        val (a, b) = store.pending(10)
        store.markRetry(a, "timeout")
        store.markFailed(b, "HTTP 422")
        assertEquals(listOf("timeout"), store.pending(10).map { it.lastError })
        assertEquals(1, store.counts().failed)
    }

    @Test
    fun seenRoomsKeepExactName() {
        store.recordSeenRoom("우리  회사 개발팀 🚀", true, 1)
        store.recordSeenRoom("우리  회사 개발팀 🚀", true, 5)
        val room = store.seenRooms().single()
        assertEquals("우리  회사 개발팀 🚀", room.room)
        assertEquals(5L, room.lastSeen)
    }

    @Test
    fun upgradeFromV7QueuesPhoneOnlyMessagesAndDropsOldTables() {
        MessageStore.resetForTest()
        context.deleteDatabase("queue.db")
        // v7 스키마 흉내: local 상태 메시지 + 앱 내 분류 테이블
        val path = context.getDatabasePath("queue.db").apply { parentFile?.mkdirs() }
        android.database.sqlite.SQLiteDatabase.openOrCreateDatabase(path, null).use { db ->
            db.execSQL(
                "CREATE TABLE queue (id INTEGER PRIMARY KEY AUTOINCREMENT, dedup TEXT NOT NULL UNIQUE, room TEXT NOT NULL, sender TEXT NOT NULL, " +
                    "text TEXT NOT NULL, type TEXT NOT NULL, is_group INTEGER NOT NULL, posted_at INTEGER NOT NULL, notification_key TEXT NOT NULL, " +
                    "image_path TEXT, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, created_at INTEGER NOT NULL)"
            )
            db.execSQL("CREATE TABLE seen_rooms (room TEXT PRIMARY KEY, is_group INTEGER NOT NULL, last_seen INTEGER NOT NULL, notifications INTEGER NOT NULL DEFAULT 0)")
            db.execSQL("CREATE TABLE notification_log (id INTEGER PRIMARY KEY AUTOINCREMENT, time INTEGER NOT NULL, dump TEXT NOT NULL)")
            db.execSQL("CREATE TABLE complaint_check (message_id INTEGER PRIMARY KEY, verdict INTEGER NOT NULL)")
            db.execSQL("CREATE TABLE member_events (id INTEGER PRIMARY KEY)")
            db.execSQL("INSERT INTO queue (dedup, room, sender, text, type, is_group, posted_at, notification_key, status, created_at) VALUES ('d1','방','a','휴대폰에만 있던 메시지','text',1,1,'k','local',1)")
            db.version = 7
        }
        store = MessageStore.get(context)
        assertEquals(listOf("휴대폰에만 있던 메시지"), store.pending(10).map { it.text })
        store.readableDatabase.rawQuery("SELECT name FROM sqlite_master WHERE type='table'", null).use { c ->
            val tables = buildList { while (c.moveToNext()) add(c.getString(0)) }
            assertFalse(tables.contains("complaint_check"))
            assertFalse(tables.contains("member_events"))
        }
    }
}
