package com.mahal.kakaocollector

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class KakaoParserTest {

    private fun raw(
        title: String? = null,
        text: String? = null,
        subText: String? = null,
        conversationTitle: String? = null,
        isGroup: Boolean = false,
        messages: List<RawMessage> = emptyList(),
        hasPicture: Boolean = false,
        isGroupSummary: Boolean = false,
        summaryText: String? = null,
        shortcutLabel: String? = null,
    ) = RawNotification(
        key = "0|com.kakao.talk|2|null|10001",
        postTime = 2_000L,
        whenTime = 1_500L,
        title = title,
        text = text,
        subText = subText,
        conversationTitle = conversationTitle,
        isGroupConversation = isGroup,
        messages = messages,
        hasPicture = hasPicture,
        isGroupSummary = isGroupSummary,
        summaryText = summaryText,
        shortcutLabel = shortcutLabel,
    )

    @Test
    fun messagingStyleGroupChat() {
        val result = KakaoParser.parse(
            raw(
                title = "홍길동",
                conversationTitle = "개발팀 단톡방",
                isGroup = true,
                messages = listOf(
                    RawMessage("첫 번째", "홍길동", 1_000L),
                    RawMessage("두 번째", "김철수", 1_100L),
                ),
            )
        )
        assertEquals(2, result.size)
        assertEquals("개발팀 단톡방", result[0].room)
        assertEquals("홍길동", result[0].sender)
        assertEquals("김철수", result[1].sender)
        assertEquals(1_100L, result[1].postedAt)
        assertTrue(result.all { it.isGroupChat && it.type == MessageType.TEXT })
    }

    @Test
    fun messagingStyleOneToOneUsesTitleAsRoom() {
        val m = KakaoParser.parse(raw(title = "이영희", messages = listOf(RawMessage("안녕", "이영희", 1_000L)))).single()
        assertEquals("이영희", m.room)
        assertEquals(false, m.isGroupChat)
    }

    @Test
    fun ownMessageGetsSelfSender() {
        val m = KakaoParser.parse(raw(title = "이영희", messages = listOf(RawMessage("내 답장", null, 1_000L)))).single()
        assertEquals(KakaoParser.SELF_SENDER, m.sender)
        assertEquals("이영희", m.room)
    }

    @Test
    fun messagingStyleImageData() {
        val m = KakaoParser.parse(
            raw(
                conversationTitle = "가족방",
                messages = listOf(RawMessage("사진", "엄마", 1_000L, "image/jpeg", "content://com.kakao.talk/x/1")),
            )
        ).single()
        assertEquals(MessageType.PHOTO, m.type)
        assertEquals("content://com.kakao.talk/x/1", m.imageUri)
    }

    @Test
    fun bigPictureAppliesOnlyToLatestMessage() {
        val result = KakaoParser.parse(
            raw(
                conversationTitle = "가족방",
                hasPicture = true,
                messages = listOf(RawMessage("밥 먹었어?", "엄마", 1L), RawMessage("사진을 보냈습니다.", "엄마", 2L)),
            )
        )
        assertEquals(listOf(MessageType.TEXT, MessageType.PHOTO), result.map { it.type })
    }

    @Test
    fun legacyGroupChat() {
        val m = KakaoParser.parse(raw(title = "홍길동", text = "회의 3시", subText = "개발팀")).single()
        assertEquals("개발팀", m.room)
        assertEquals("홍길동", m.sender)
        assertEquals("회의 3시", m.text)
        assertEquals(1_500L, m.postedAt)
        assertTrue(m.isGroupChat)
        assertNull(m.imageUri)
    }

    @Test
    fun legacyOneToOne() {
        val m = KakaoParser.parse(raw(title = "이영희", text = "ㅎㅇ")).single()
        assertEquals("이영희", m.room)
        assertEquals(false, m.isGroupChat)
    }

    @Test
    fun skipsSummaryAndEmptyNotifications() {
        assertTrue(KakaoParser.parse(raw(title = "카카오톡", text = "새 메시지 3개", isGroupSummary = true)).isEmpty())
        assertTrue(KakaoParser.parse(raw(title = "홍길동")).isEmpty())
        assertTrue(KakaoParser.parse(raw(text = "내용만")).isEmpty())
    }

    @Test
    fun classifiesKakaoPlaceholders() {
        assertEquals(MessageType.PHOTO, KakaoParser.classify("사진을 보냈습니다.", false))
        assertEquals(MessageType.PHOTO, KakaoParser.classify("사진 3장을 보냈습니다.", false))
        assertEquals(MessageType.VIDEO, KakaoParser.classify("동영상을 보냈습니다.", false))
        assertEquals(MessageType.EMOTICON, KakaoParser.classify("이모티콘을 보냈습니다.", false))
        assertEquals(MessageType.FILE, KakaoParser.classify("파일: 보고서.pdf", false))
        assertEquals(MessageType.TEXT, KakaoParser.classify("사진 잘 나왔네", false))
    }

    // 실제 카카오톡: 단톡방 이름이 conversationTitle 이 아니라 subText 에만 있는 경우.
    // 수정 전에는 보낸 사람 이름(title)이 방 이름이 되어 단톡방이 사람별로 쪼개졌다.
    @Test
    fun messagingStyleGroupRoomFromSubText() {
        val fromHong = KakaoParser.parse(
            raw(title = "홍길동", subText = "개발팀 단톡방", messages = listOf(RawMessage("배포 3시", "홍길동", 1_000L)))
        ).single()
        val fromKim = KakaoParser.parse(
            raw(title = "김철수", subText = "개발팀 단톡방", messages = listOf(RawMessage("확인", "김철수", 2_000L)))
        ).single()
        assertEquals("개발팀 단톡방", fromHong.room)
        assertEquals("개발팀 단톡방", fromKim.room)
        assertEquals("홍길동", fromHong.sender)
        assertEquals("김철수", fromKim.sender)
        assertTrue(fromHong.isGroupChat && fromKim.isGroupChat)
    }

    @Test
    fun groupRoomFromSummaryText() {
        val styled = KakaoParser.parse(
            raw(title = "박민수", summaryText = "가족방", messages = listOf(RawMessage("안녕", "박민수", 1_000L)))
        ).single()
        assertEquals("가족방", styled.room)
        assertTrue(styled.isGroupChat)

        val legacy = KakaoParser.parse(raw(title = "박민수", text = "안녕", summaryText = "가족방")).single()
        assertEquals("가족방", legacy.room)
        assertTrue(legacy.isGroupChat)
    }

    @Test
    fun conversationTitleWinsOverSubText() {
        val m = KakaoParser.parse(
            raw(title = "홍길동", conversationTitle = "오픈채팅 개발자모임", subText = "다른값", messages = listOf(RawMessage("hi", "홍길동", 1L)))
        ).single()
        assertEquals("오픈채팅 개발자모임", m.room)
    }

    // 실제 사례(오픈채팅 "⛳골프는 노터치⛳"): 알림 본문엔 방 이름이 없고 대화 바로가기에만 있음.
    // 수정 전에는 보낸 사람 대화명이 방 이름이 되었다.
    @Test
    fun openChatRoomFromConversationShortcut() {
        val room = "⛳골프는 노터치⛳"
        val a = KakaoParser.parse(raw(title = "버디왕", shortcutLabel = room, messages = listOf(RawMessage("내일 라운딩 몇 시죠", "버디왕", 1L)))).single()
        val b = KakaoParser.parse(raw(title = "이글", shortcutLabel = room, messages = listOf(RawMessage("7시요", "이글", 2L)))).single()
        assertEquals(room, a.room)
        assertEquals(room, b.room)
        assertEquals("버디왕", a.sender)
        assertTrue(a.isGroupChat && b.isGroupChat)
    }

    @Test
    fun oneToOneShortcutEqualsSenderStaysOneToOne() {
        val m = KakaoParser.parse(raw(title = "이영희", shortcutLabel = "이영희", messages = listOf(RawMessage("안녕", "이영희", 1L)))).single()
        assertEquals("이영희", m.room)
        assertEquals(false, m.isGroupChat)
    }
}
