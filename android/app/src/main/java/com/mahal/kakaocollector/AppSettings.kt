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

    private companion object {
        const val KEY_SERVER_URL = "server_url"
        const val KEY_API_KEY = "api_key"
        const val KEY_CAPTURE = "capture_enabled"
        const val KEY_IMAGES = "capture_images"
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_ROOM_MODE = "room_filter_mode"
        const val KEY_UPLOAD = "upload_enabled"
        const val KEY_ROOMS = "selected_rooms"
    }
}
