package com.mahal.kakaocollector

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** 서버 연결 확인 결과를 화면들이 함께 쓴다 (메인 타일 · 서버 연결 화면). */
object ServerCheck {
    /** null = 아직 확인 전, true = 정상, false = 실패 */
    @Volatile var lastOk: Boolean? = null
        private set
    @Volatile var lastMessage: String = ""
        private set

    suspend fun run(context: Context): Boolean {
        val settings = AppSettings(context)
        if (!settings.isConfigured) {
            lastOk = false
            lastMessage = "서버 주소(http:// 또는 https://)와 API 키를 입력하십시오."
            return false
        }
        val error = withContext(Dispatchers.IO) { ApiClient(settings).health() }
        lastOk = error == null
        lastMessage = if (error == null) "✅ 서버 연결 정상" else "⚠️ 연결 실패: $error"
        return error == null
    }

    fun reset() {
        lastOk = null
        lastMessage = ""
    }
}
