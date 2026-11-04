package com.mahal.kakaolocal

import android.content.Context

/** 보관함 설정: 수집 옵션 · 수집할 방 (서버 없음, 휴대폰에만 저장) */
class AppSettings(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("settings", Context.MODE_PRIVATE)

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

    fun allowsRoom(room: String): Boolean = RoomFilter.allows(room, roomFilterMode, selectedRooms)


    private companion object {
        const val KEY_CAPTURE = "capture_enabled"
        const val KEY_IMAGES = "capture_images"
        const val KEY_ROOM_MODE = "room_filter_mode"
        const val KEY_ROOMS = "selected_rooms"
        const val KEY_STATUS_NOTI = "status_notification"
        const val KEY_LAST_CAPTURED = "last_captured_at"
    }
}
