package com.mahal.kakaocollector

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

/** Google Gemini API(generateContent)로 메시지 묶음을 민원 여부로 분류한다. */
class GeminiClient(private val apiKey: String, private val model: String) {

    sealed interface Result {
        data class Ok(val verdicts: List<Complaints.Verdict>) : Result
        /** 네트워크·일시적 과부하(503)·한도 초과(429): 나중에 다시 */
        data class Retry(val reason: String) : Result
        /** API 키·모델 설정 오류: 사용자가 고쳐야 함 */
        data class ConfigError(val reason: String) : Result
        /** 응답 해석 실패 */
        data class BadResponse(val reason: String) : Result
    }

    fun classify(items: List<Complaints.Item>): Result {
        val request = Request.Builder()
            .url("$BASE/${URLEncoder.encode(model, "UTF-8")}:generateContent")
            .header("x-goog-api-key", apiKey)
            .post(Complaints.requestBody(items).toRequestBody("application/json".toMediaType()))
            .build()
        return try {
            client.newCall(request).execute().use { res ->
                val body = res.body?.string().orEmpty()
                when {
                    res.isSuccessful -> try {
                        Result.Ok(Complaints.parseResponse(body, items.map { it.id }.toSet()))
                    } catch (e: IllegalArgumentException) {
                        Result.BadResponse(e.message ?: "응답 해석 실패")
                    }
                    res.code == 400 && body.contains("API_KEY_INVALID") -> Result.ConfigError("Gemini API 키가 올바르지 않습니다")
                    res.code == 401 || res.code == 403 -> Result.ConfigError("Gemini API 키 권한 오류 (HTTP ${res.code})")
                    res.code == 404 -> Result.ConfigError("모델 '$model' 을 사용할 수 없습니다. 모델 이름을 확인하십시오")
                    res.code == 429 -> Result.Retry("사용 한도 초과 (HTTP 429) — 잠시 후 자동 재시도")
                    res.code >= 500 -> Result.Retry("Gemini 일시 오류 (HTTP ${res.code}) — 잠시 후 자동 재시도")
                    else -> Result.ConfigError("HTTP ${res.code}: ${body.take(200)}")
                }
            }
        } catch (e: IOException) {
            Result.Retry(e.message ?: e.javaClass.simpleName)
        }
    }

    private companion object {
        const val BASE = "https://generativelanguage.googleapis.com/v1beta/models"
        val client: OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(90, TimeUnit.SECONDS)
            .build()
    }
}
