package com.mahal.kakaocollector

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 화면 표시용 문구. Android 의존성 없음. */
object Display {
    private val LABEL = mapOf("photo" to "📷 사진", "video" to "🎬 동영상", "emoticon" to "😊 이모티콘", "file" to "📎 파일", "other" to "기타")

    /** 카카오톡이 내용 대신 넣는 안내 문구("사진을 보냈습니다." 등) */
    private val PLACEHOLDER = Regex("""(보냈습니다\.?|^(사진|동영상|이모티콘|photos?|video|emoticon))$""", RegexOption.IGNORE_CASE)

    fun label(type: String): String? = LABEL[type]

    fun isPlaceholder(type: String, text: String): Boolean =
        type != "text" && type != "file" && (text.isBlank() || PLACEHOLDER.containsMatchIn(text.trim()))

    fun preview(type: String, text: String): String {
        val label = LABEL[type] ?: return text
        return if (isPlaceholder(type, text)) label else "$label $text"
    }

    fun dateTime(time: Long): String = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.KOREA).format(Date(time))

    fun time(time: Long): String = SimpleDateFormat("a h:mm", Locale.KOREA).format(Date(time))

    fun day(time: Long): String = SimpleDateFormat("yyyy년 M월 d일 EEEE", Locale.KOREA).format(Date(time))

    fun dayKey(time: Long): String = SimpleDateFormat("yyyyMMdd", Locale.KOREA).format(Date(time))
}
