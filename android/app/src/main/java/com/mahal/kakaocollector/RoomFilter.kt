package com.mahal.kakaocollector

/** 수집할 대화방 선택 규칙. Android 의존성이 없어 JVM 단위 테스트가 가능하다. */
enum class RoomFilterMode { ALL, ONLY_SELECTED, EXCEPT_SELECTED }

object RoomFilter {
    fun allows(room: String, mode: RoomFilterMode, selected: Set<String>): Boolean {
        val key = normalize(room)
        return when (mode) {
            RoomFilterMode.ALL -> true
            RoomFilterMode.ONLY_SELECTED -> selected.any { normalize(it) == key }
            RoomFilterMode.EXCEPT_SELECTED -> selected.none { normalize(it) == key }
        }
    }

    /** 앞뒤 공백·연속 공백 차이로 같은 방이 다르게 취급되지 않도록 한다 */
    fun normalize(room: String): String = room.trim().replace(Regex("\\s+"), " ")
}
