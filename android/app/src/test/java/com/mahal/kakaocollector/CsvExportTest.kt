package com.mahal.kakaocollector

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CsvExportTest {
    @Test
    fun escapesSpecialCharacters() {
        assertEquals("plain", CsvExport.escape("plain"))
        assertEquals("\"a,b\"", CsvExport.escape("a,b"))
        assertEquals("\"say \"\"hi\"\"\"", CsvExport.escape("say \"hi\""))
        assertEquals("\"line1\nline2\"", CsvExport.escape("line1\nline2"))
    }

    @Test
    fun buildsRowsWithBomAndHeader() {
        val csv = CsvExport.build(
            listOf(CsvExport.Item(7, 1_761_800_000_000, "개발팀, 본부", "홍길동", "photo", true, "사진을 보냈습니다.", true, "local"))
        )
        assertTrue(csv.startsWith("\uFEFFid,수신시각,방,보낸사람,종류,단톡방,내용,사진,서버전송\r\n"))
        val row = csv.lines()[1]
        assertTrue(row.startsWith("7,"))
        assertTrue(row.contains(",\"개발팀, 본부\",홍길동,photo,Y,사진을 보냈습니다.,Y,local"))
    }
}
