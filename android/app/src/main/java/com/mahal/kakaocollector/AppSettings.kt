package com.mahal.kakaocollector

import android.content.Context
import java.util.UUID

/** 수집 앱 설정: 서버 주소·API 키(입력값이 없으면 개인 빌드의 기본값)·수집 옵션·수집할 방 */
class AppSettings(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("settings", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, null)?.takeIf { it.isNotBlank() } ?: BuildConfig.DEFAULT_SERVER_URL
        set(value) = prefs.edit().putString(KEY_SERVER_URL, value.trim().trimEnd('/')).apply()

    var apiKey: String
        get() = prefs.getString(KEY_API_KEY, null)?.takeIf { it.isNotBlank() } ?: BuildConfig.DEFAULT_API_KEY
        set(value) = prefs.edit().putString(KEY_API_KEY, value.trim()).apply()

    var captureEnabled: Boolean
        get() = prefs.getBoolean(KEY_CAPTURE, true)
        set(value) = prefs.edit().putBoolean(KEY_CAPTURE, value).apply()

    var captureImages: Boolean
        get() = prefs.getBoolean(KEY_IMAGES, true)
        set(value) = prefs.edit().putBoolean(KEY_IMAGES, value).apply()

    var roomFilterMode: RoomFilterMode
        get() = runCatching { RoomFilterMode.valueOf(prefs.getString(KEY_ROOM_MODE, null) ?: "") }.getOrDefault(RoomFilterMode.ALL)
        set(value) = prefs.edit().putString(KEY_ROOM_MODE, value.name).apply()

    var selectedRooms: Set<String>
        get() = prefs.getStringSet(KEY_ROOMS, emptySet())?.toSet() ?: emptySet()
        set(value) = prefs.edit().putStringSet(KEY_ROOMS, value).apply()

    /** 알림줄 상태 알림 표시 */
    var statusNotification: Boolean
        get() = prefs.getBoolean(KEY_STATUS_NOTI, true)
        set(value) = prefs.edit().putBoolean(KEY_STATUS_NOTI, value).apply()

    // ---- 동작 신호 (상태 카드·상태 알림) ----
    var lastCapturedAt: Long
        get() = prefs.getLong(KEY_LAST_CAPTURED, 0)
        set(value) = prefs.edit().putLong(KEY_LAST_CAPTURED, value).apply()

    var lastUploadAt: Long
        get() = prefs.getLong(KEY_LAST_UPLOAD, 0)
        set(value) = prefs.edit().putLong(KEY_LAST_UPLOAD, value).apply()

    /** 마지막 전송 실패 사유 (성공하면 null) */
    var lastUploadError: String?
        get() = prefs.getString(KEY_LAST_UPLOAD_ERROR, null)
        set(value) = prefs.edit().putString(KEY_LAST_UPLOAD_ERROR, value).apply()

    fun allowsRoom(room: String): Boolean = RoomFilter.allows(room, roomFilterMode, selectedRooms)

    /** 설치마다 1회 생성되는 단말 식별자 (서버가 안내 메시지를 보낼 휴대폰을 구분) */
    val deviceId: String
        get() = prefs.getString(KEY_DEVICE_ID, null) ?: UUID.randomUUID().toString().also {
            prefs.edit().putString(KEY_DEVICE_ID, it).apply()
        }

    val isConfigured: Boolean
        get() = serverUrl.startsWith("http") && apiKey.isNotEmpty()

    private companion object {
        const val KEY_SERVER_URL = "server_url"
        const val KEY_API_KEY = "api_key"
        const val KEY_CAPTURE = "capture_enabled"
        const val KEY_IMAGES = "capture_images"
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_ROOM_MODE = "room_filter_mode"
        const val KEY_ROOMS = "selected_rooms"
        const val KEY_STATUS_NOTI = "status_notification"
        const val KEY_LAST_CAPTURED = "last_captured_at"
        const val KEY_LAST_UPLOAD = "last_upload_at"
        const val KEY_LAST_UPLOAD_ERROR = "last_upload_error"
    }
}
