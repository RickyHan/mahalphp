package com.mahal.kakaocollector

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RoomFilterTest {
    private val selected = setOf("개발팀 단톡방", "가족방")

    @Test
    fun allModeAcceptsEverything() {
        assertTrue(RoomFilter.allows("아무 방", RoomFilterMode.ALL, emptySet()))
    }

    @Test
    fun onlySelected() {
        assertTrue(RoomFilter.allows("가족방", RoomFilterMode.ONLY_SELECTED, selected))
        assertFalse(RoomFilter.allows("마케팅 TF", RoomFilterMode.ONLY_SELECTED, selected))
        assertFalse(RoomFilter.allows("가족방", RoomFilterMode.ONLY_SELECTED, emptySet()))
    }

    @Test
    fun exceptSelected() {
        assertFalse(RoomFilter.allows("가족방", RoomFilterMode.EXCEPT_SELECTED, selected))
        assertTrue(RoomFilter.allows("마케팅 TF", RoomFilterMode.EXCEPT_SELECTED, selected))
    }

    @Test
    fun ignoresWhitespaceDifferences() {
        assertTrue(RoomFilter.allows("  개발팀   단톡방 ", RoomFilterMode.ONLY_SELECTED, selected))
    }
}
