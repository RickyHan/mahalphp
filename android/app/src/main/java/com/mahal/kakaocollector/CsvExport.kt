package com.mahal.kakaocollector

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 수집 메시지를 CSV 로 변환 (엑셀에서 한글이 깨지지 않도록 UTF-8 BOM 포함). Android 의존성 없음. */
object CsvExport {
    private const val BOM = '\uFEFF'
    private val HEADER = listOf("id", "수신시각", "방", "보낸사람", "종류", "단톡방", "내용", "사진", "서버전송")

    data class Item(
        val id: Long,
        val postedAt: Long,
        val room: String,
        val sender: String,
        val type: String,
        val isGroup: Boolean,
        val text: String,
        val hasImage: Boolean,
        val status: String,
    )

    fun escape(value: String): String =
        if (value.any { it == ',' || it == '"' || it == '\n' || it == '\r' }) "\"" + value.replace("\"", "\"\"") + "\"" else value

    fun build(items: List<Item>): String {
        val fmt = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.KOREA)
        val sb = StringBuilder().append(BOM).append(HEADER.joinToString(",")).append("\r\n")
        for (i in items) {
            val cols = listOf(
                i.id.toString(), fmt.format(Date(i.postedAt)), i.room, i.sender, i.type,
                if (i.isGroup) "Y" else "N", i.text, if (i.hasImage) "Y" else "N", i.status,
            )
            sb.append(cols.joinToString(",") { escape(it) }).append("\r\n")
        }
        return sb.toString()
    }
}
