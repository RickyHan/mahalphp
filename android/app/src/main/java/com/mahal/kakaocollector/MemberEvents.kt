package com.mahal.kakaocollector

/**
 * 대화방 참여자 이력. Android 의존성 없음.
 * - 카카오톡 안내 메시지("○○님이 들어왔습니다." 등)에서 입장·퇴장·내보냄·초대를 인식
 * - 그 외 첫 발언(first_seen)·대화명 변경(rename)은 MessageStore 가 발신자 기록으로 판단
 */
object MemberEvents {

    enum class Type(val wire: String, val label: String) {
        JOIN("join", "입장"),
        LEAVE("leave", "퇴장"),
        KICK("kick", "내보냄"),
        INVITE("invite", "초대"),
        RENAME("rename", "대화명 변경"),
        FIRST_SEEN("first_seen", "첫 발언");

        companion object {
            fun of(wire: String) = entries.firstOrNull { it.wire == wire }
        }
    }

    /** 안내 메시지에서 인식한 사건. actor 는 초대·내보낸 사람(있을 때) */
    data class Detected(val type: Type, val member: String, val actor: String? = null)

    private val JOIN = Regex("""^(.+?)님이 (?:들어왔습니다|입장하셨습니다|입장했습니다)\.?$""")
    private val LEAVE = Regex("""^(.+?)님이 (?:나갔습니다|퇴장하셨습니다|퇴장했습니다)\.?$""")
    private val KICK_BY = Regex("""^(.+?)님이 (.+?)님을 내보냈습니다\.?$""")
    private val KICK = Regex("""^(.+?)님을 내보냈습니다\.?$""")
    private val INVITE = Regex("""^(.+?)님이 (.+?)님을 초대했습니다\.?$""")

    /** 안내 메시지면 사건 목록, 일반 대화면 빈 목록 */
    fun detect(text: String): List<Detected> {
        val t = text.trim()
        INVITE.matchEntire(t)?.let { m ->
            val actor = m.groupValues[1]
            return splitNames(m.groupValues[2]).map { Detected(Type.INVITE, it, actor) }
        }
        KICK_BY.matchEntire(t)?.let { m -> return listOf(Detected(Type.KICK, m.groupValues[2], m.groupValues[1])) }
        KICK.matchEntire(t)?.let { m -> return listOf(Detected(Type.KICK, m.groupValues[1])) }
        JOIN.matchEntire(t)?.let { m -> return listOf(Detected(Type.JOIN, m.groupValues[1])) }
        LEAVE.matchEntire(t)?.let { m -> return listOf(Detected(Type.LEAVE, m.groupValues[1])) }
        return emptyList()
    }

    /** "A님, B님과 C" → [A, B, C] */
    fun splitNames(raw: String): List<String> =
        raw.split(Regex("""님,\s*|님과\s+|,\s*"""))
            .map { it.trim().removeSuffix("님") }
            .filter { it.isNotEmpty() }
}
