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

    /** 설치마다 1회 생성되는 단말 식별자 */
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
    }
}
