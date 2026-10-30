package com.mahal.kakaocollector

import android.content.Context
import java.util.UUID

class AppSettings(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("settings", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, "") ?: ""
        set(value) = prefs.edit().putString(KEY_SERVER_URL, value.trim().trimEnd('/')).apply()

    var apiKey: String
        get() = prefs.getString(KEY_API_KEY, "") ?: ""
        set(value) = prefs.edit().putString(KEY_API_KEY, value.trim()).apply()

    var captureEnabled: Boolean
        get() = prefs.getBoolean(KEY_CAPTURE, true)
        set(value) = prefs.edit().putBoolean(KEY_CAPTURE, value).apply()

    var captureImages: Boolean
        get() = prefs.getBoolean(KEY_IMAGES, true)
        set(value) = prefs.edit().putBoolean(KEY_IMAGES, value).apply()

    /** 서버로도 전송할지 (끄면 휴대폰에만 저장) */
    var uploadEnabled: Boolean
        get() = prefs.getBoolean(KEY_UPLOAD, false)
        set(value) = prefs.edit().putBoolean(KEY_UPLOAD, value).apply()

    /** Gemini 민원 분류 사용 */
    var complaintEnabled: Boolean
        get() = prefs.getBoolean(KEY_COMPLAINT, BuildConfig.DEFAULT_GEMINI_KEY.isNotEmpty()) // 내장 키가 있으면 기본 켜짐
        set(value) = prefs.edit().putBoolean(KEY_COMPLAINT, value).apply()

    /** Gemini API 키. 직접 입력한 값이 없으면 개인 빌드에 내장된 기본 키(공개 빌드는 빈 값) */
    var geminiKey: String
        get() = prefs.getString(KEY_GEMINI_KEY, null)?.takeIf { it.isNotBlank() } ?: BuildConfig.DEFAULT_GEMINI_KEY
        set(value) = prefs.edit().putString(KEY_GEMINI_KEY, value.trim()).apply()

    var geminiModel: String
        get() = prefs.getString(KEY_GEMINI_MODEL, null)?.takeIf { it.isNotBlank() } ?: DEFAULT_MODEL
        set(value) = prefs.edit().putString(KEY_GEMINI_MODEL, value.trim().removePrefix("models/")).apply()

    /** 마지막 분류 실행 결과 (화면 표시용) */
    var complaintStatus: String
        get() = prefs.getString(KEY_COMPLAINT_STATUS, "") ?: ""
        set(value) = prefs.edit().putString(KEY_COMPLAINT_STATUS, value).apply()

    val shouldClassify: Boolean
        get() = complaintEnabled && geminiKey.isNotEmpty()

    var roomFilterMode: RoomFilterMode
        get() = runCatching { RoomFilterMode.valueOf(prefs.getString(KEY_ROOM_MODE, null) ?: "") }.getOrDefault(RoomFilterMode.ALL)
        set(value) = prefs.edit().putString(KEY_ROOM_MODE, value.name).apply()

    var selectedRooms: Set<String>
        get() = prefs.getStringSet(KEY_ROOMS, emptySet())?.toSet() ?: emptySet()
        set(value) = prefs.edit().putStringSet(KEY_ROOMS, value).apply()

    fun allowsRoom(room: String): Boolean = RoomFilter.allows(room, roomFilterMode, selectedRooms)

    /** 설치마다 1회 생성되는 단말 식별자 */
    val deviceId: String
        get() = prefs.getString(KEY_DEVICE_ID, null) ?: UUID.randomUUID().toString().also {
            prefs.edit().putString(KEY_DEVICE_ID, it).apply()
        }

    val isConfigured: Boolean
        get() = serverUrl.startsWith("http") && apiKey.isNotEmpty()

    /** 실제로 서버 전송을 할 상태인지 */
    val shouldUpload: Boolean
        get() = uploadEnabled && isConfigured

    companion object {
        const val DEFAULT_MODEL = "gemini-3.5-flash-lite"
        private const val KEY_COMPLAINT = "complaint_enabled"
        private const val KEY_GEMINI_KEY = "gemini_key"
        private const val KEY_GEMINI_MODEL = "gemini_model"
        private const val KEY_COMPLAINT_STATUS = "complaint_status"
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_API_KEY = "api_key"
        private const val KEY_CAPTURE = "capture_enabled"
        private const val KEY_IMAGES = "capture_images"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_ROOM_MODE = "room_filter_mode"
        private const val KEY_UPLOAD = "upload_enabled"
        private const val KEY_ROOMS = "selected_rooms"
    }
}
