package com.mahal.kakaolocal

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

/** 보관함: 비밀번호 · 방 목록 · 방 대화 페이지 · 방 삭제 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class VaultTest {
    private lateinit var context: Context
    private lateinit var store: MessageStore

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        MessageStore.resetForTest()
        context.deleteDatabase("queue.db")
        store = MessageStore.get(context)
        AppLock.lock()
    }

    @After
    fun tearDown() = MessageStore.resetForTest()

    @Test
    fun pinUnlocksOnlyWithTheRightNumber() {
        assertFalse(AppLock.check("000000"))
        assertFalse(AppLock.unlocked)
        assertTrue(AppLock.check("146900"))
        assertTrue(AppLock.unlocked)
        AppLock.lock()
        assertFalse(AppLock.unlocked)
    }

    @Test
    fun roomsMessagesAndDelete() {
        store.insert(KakaoMessage("가족방", "엄마", "밥 먹었니?", MessageType.TEXT, true, 1_000, "k"), null)
        store.insert(KakaoMessage("회사방", "팀장", "회의 3시", MessageType.TEXT, true, 2_000, "k"), null)
        val photo = File(context.filesDir, "media").apply { mkdirs() }.resolve("a.jpg").apply { writeBytes(byteArrayOf(1)) }
        store.insert(KakaoMessage("가족방", "아빠", "사진을 보냈습니다.", MessageType.PHOTO, true, 3_000, "k"), photo.absolutePath)
        (1..5).forEach { store.insert(KakaoMessage("회사방", "팀장", "m$it", MessageType.TEXT, true, 1_500L + it, "k"), null) }

        val rooms = store.rooms()
        assertEquals(listOf("가족방", "회사방"), rooms.map { it.room })          // 최근 대화 순
        assertEquals(2, rooms[0].count)
        assertEquals(1, rooms[0].photos)
        assertEquals("아빠", rooms[0].lastSender)
        assertEquals(1, store.counts().photos)

        val page = store.messages("회사방", limit = 3)                          // 최근 받은 3건을 받은 순으로
        assertEquals(listOf("m3", "m4", "m5"), page.map { it.text })
        val older = store.messages("회사방", beforeId = page.first().id, limit = 10)
        assertEquals(listOf("회의 3시", "m1", "m2"), older.map { it.text })

        val photo2 = File(context.filesDir, "media").resolve("b.jpg").apply { writeBytes(byteArrayOf(2)) }
        store.insert(KakaoMessage("회사방", "팀장", "사진을 보냈습니다.", MessageType.PHOTO, true, 4_000, "k"), photo2.absolutePath)
        assertEquals(listOf(photo2.absolutePath, photo.absolutePath), store.photos().map { it.imagePath })   // 모든 방, 최신 순
        assertEquals(listOf(photo.absolutePath), store.photos("가족방").map { it.imagePath })              // 방별
        assertEquals(listOf(photo.absolutePath), store.photos(beforeId = store.photos().first().id).map { it.imagePath })
        assertEquals(listOf("가족방" to 1, "회사방" to 1), store.photoRooms())
        store.deleteRoom("회사방")

        assertEquals(2, store.deleteRoom("가족방"))
        assertFalse("방을 지우면 사진 파일도 지움", photo.exists())
        assertTrue(store.rooms().isEmpty() && store.photos().isEmpty())
    }
}
