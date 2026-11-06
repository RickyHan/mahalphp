package com.mahal.kakaocollector

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/** 수집 서버(server/app/main.py) 호출. */
class ApiClient(private val settings: AppSettings) {

    sealed interface Result {
        data object Ok : Result
        /**
         * 네트워크 오류·서버 일시 오류: 나중에 다시 시도.
         * thisMessageOnly = 서버는 응답했지만 이 메시지 처리만 실패(HTTP 500) → 다음 메시지는 계속 보낸다.
         */
        data class Retry(val reason: String, val thisMessageOnly: Boolean = false) : Result
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
                row.roomKey?.let {
                    addFormDataPart("room_key", it.take(64))
                    addFormDataPart("room_trusted", row.roomTrusted.toString())
                }
            }
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

    /** 보낼 답장 목록 + 관리자 웹 '지금 동기화' 요청 시각(epoch ms, 없으면 0) */
    data class Pending(val replies: List<Reply>, val syncRequestedAt: Long)

    /** 서버가 정한 '민원 접수 안내' 중 이 휴대폰이 카톡 답장으로 보내야 할 것 (실패 시 null) */
    fun pendingReplies(): Pending? {
        val url = "${settings.serverUrl}/api/replies/pending?device_id=" + java.net.URLEncoder.encode(settings.deviceId, "UTF-8")
        val request = Request.Builder().url(url).header("X-Api-Key", settings.apiKey).get().build()
        return try {
            client.newCall(request).execute().use { res ->
                if (!res.isSuccessful) return null
                val json = org.json.JSONObject(res.body?.string().orEmpty())
                val items = json.getJSONArray("items")
                Pending(
                    (0 until items.length()).map { i ->
                        val o = items.getJSONObject(i)
                        Reply(o.getLong("id"), o.getString("room_name"), o.getString("text"))
                    },
                    json.optLong("sync_requested_at", 0),
                )
            }
        } catch (e: Exception) {
            null
        }
    }

    /** 지금 카톡 답장을 보낼 수 있는 방 목록 보고 (성공 여부) */
    fun reportReadyRooms(rooms: Collection<String>): Boolean {
        val json = org.json.JSONObject()
            .put("device_id", settings.deviceId)
            .put("rooms", org.json.JSONArray(rooms.take(2000)))
        val body = json.toString().toRequestBody("application/json; charset=utf-8".toMediaType())
        val request = Request.Builder().url("${settings.serverUrl}/api/replies/ready").header("X-Api-Key", settings.apiKey).post(body).build()
        return try {
            client.newCall(request).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            false
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
                code == 500 -> Result.Retry("HTTP $code ${res.body?.string()?.take(200).orEmpty()}", thisMessageOnly = true)
                code == 401 || code == 408 || code == 429 || code > 500 ->
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
