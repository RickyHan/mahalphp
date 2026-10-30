package com.mahal.kakaocollector

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/** 수집 서버(server/app/main.py) 호출. */
class ApiClient(private val settings: AppSettings) {

    sealed interface Result {
        data object Ok : Result
        /** 네트워크 오류·서버 일시 오류: 나중에 다시 시도 */
        data class Retry(val reason: String) : Result
        /** 요청 자체가 잘못됨: 다시 보내도 실패 */
        data class Rejected(val reason: String) : Result
    }

    fun upload(row: MessageStore.Row): Result {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("device_id", settings.deviceId)
            .addFormDataPart("room_name", row.room)
            .addFormDataPart("sender_name", row.sender)
            .addFormDataPart("message_text", row.text)
            .addFormDataPart("message_type", row.type)
            .addFormDataPart("is_group_chat", row.isGroup.toString())
            .addFormDataPart("posted_at", row.postedAt.toString())
            .addFormDataPart("notification_key", row.notificationKey.take(255))
            .apply { row.senderKey?.let { addFormDataPart("sender_key", it.take(255)) } }
            .apply {
                row.imagePath?.let(::File)?.takeIf { it.exists() }?.let { file ->
                    addFormDataPart("image", file.name, file.asRequestBody("application/octet-stream".toMediaType()))
                }
            }
            .build()
        val request = Request.Builder()
            .url("${settings.serverUrl}/api/messages")
            .header("X-Api-Key", settings.apiKey)
            .post(body)
            .build()
        return execute(request)
    }

    data class Reply(val id: Long, val room: String, val text: String)

    /** 서버가 정한 '민원 접수 안내' 중 이 휴대폰이 카톡 답장으로 보내야 할 것 (실패 시 null) */
    fun pendingReplies(): List<Reply>? {
        val url = "${settings.serverUrl}/api/replies/pending?device_id=" + java.net.URLEncoder.encode(settings.deviceId, "UTF-8")
        val request = Request.Builder().url(url).header("X-Api-Key", settings.apiKey).get().build()
        return try {
            client.newCall(request).execute().use { res ->
                if (!res.isSuccessful) return null
                val items = org.json.JSONObject(res.body?.string().orEmpty()).getJSONArray("items")
                (0 until items.length()).map { i ->
                    val o = items.getJSONObject(i)
                    Reply(o.getLong("id"), o.getString("room_name"), o.getString("text"))
                }
            }
        } catch (e: Exception) {
            null
        }
    }

    /** 안내 전송 결과 보고 */
    fun reportReply(id: Long, sent: Boolean, note: String?): Boolean {
        val body = okhttp3.FormBody.Builder()
            .add("device_id", settings.deviceId)
            .add("sent", sent.toString())
            .apply { note?.let { add("note", it.take(250)) } }
            .build()
        val request = Request.Builder().url("${settings.serverUrl}/api/replies/$id/result").header("X-Api-Key", settings.apiKey).post(body).build()
        return try {
            client.newCall(request).execute().use { it.isSuccessful || it.code == 404 }
        } catch (e: Exception) {
            false
        }
    }

    /** 연결 테스트: 성공 시 null, 실패 시 사유 */
    fun health(): String? {
        val request = Request.Builder().url("${settings.serverUrl}/api/health").get().build()
        return try {
            client.newCall(request).execute().use { res ->
                if (res.isSuccessful) null else "HTTP ${res.code}: ${res.body?.string()?.take(200)}"
            }
        } catch (e: Exception) {
            e.message ?: e.javaClass.simpleName
        }
    }

    private fun execute(request: Request): Result = try {
        client.newCall(request).execute().use { res ->
            val code = res.code
            when {
                res.isSuccessful -> Result.Ok
                code == 401 || code == 408 || code == 429 || code >= 500 ->
                    Result.Retry("HTTP $code ${res.body?.string()?.take(200).orEmpty()}")
                else -> Result.Rejected("HTTP $code ${res.body?.string()?.take(200).orEmpty()}")
            }
        }
    } catch (e: IOException) {
        Result.Retry(e.message ?: e.javaClass.simpleName)
    } catch (e: IllegalArgumentException) {
        Result.Retry("서버 주소 오류: ${e.message}")
    }

    private companion object {
        val client: OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .build()
    }
}
