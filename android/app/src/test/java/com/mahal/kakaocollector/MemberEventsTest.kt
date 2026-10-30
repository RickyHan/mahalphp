package com.mahal.kakaocollector

import com.mahal.kakaocollector.MemberEvents.Detected
import com.mahal.kakaocollector.MemberEvents.Type
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MemberEventsTest {
    @Test
    fun joinAndLeave() {
        assertEquals(listOf(Detected(Type.JOIN, "버디왕")), MemberEvents.detect("버디왕님이 들어왔습니다."))
        assertEquals(listOf(Detected(Type.LEAVE, "⛳이글⛳")), MemberEvents.detect("⛳이글⛳님이 나갔습니다."))
        assertEquals(listOf(Detected(Type.JOIN, "홍길동")), MemberEvents.detect("홍길동님이 입장하셨습니다"))
    }

    @Test
    fun kickWithAndWithoutActor() {
        assertEquals(listOf(Detected(Type.KICK, "광고봇")), MemberEvents.detect("광고봇님을 내보냈습니다."))
        assertEquals(listOf(Detected(Type.KICK, "광고봇", "방장")), MemberEvents.detect("방장님이 광고봇님을 내보냈습니다."))
    }

    @Test
    fun inviteMultiple() {
        assertEquals(
            listOf(Detected(Type.INVITE, "김철수", "홍길동"), Detected(Type.INVITE, "이영희", "홍길동"), Detected(Type.INVITE, "박민수", "홍길동")),
            MemberEvents.detect("홍길동님이 김철수님, 이영희님과 박민수님을 초대했습니다.")
        )
    }

    @Test
    fun ordinaryChatIsNotAnEvent() {
        assertTrue(MemberEvents.detect("내일 7시에 들어왔습니다 라고 하던데요?").isEmpty())
        assertTrue(MemberEvents.detect("다들 나갔습니까").isEmpty())
        assertTrue(MemberEvents.detect("안녕하세요").isEmpty())
    }
}
