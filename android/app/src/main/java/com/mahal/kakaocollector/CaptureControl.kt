package com.mahal.kakaocollector

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** 수집 시작·중지. 앱 화면의 버튼과 알림줄 상태 알림의 버튼이 함께 쓴다. */
object CaptureControl {
    fun isRunning(context: Context): Boolean = AppSettings(context).captureEnabled

    fun set(context: Context, running: Boolean) {
        val settings = AppSettings(context)
        settings.captureEnabled = running
        // 다시 시작하면 중지 동안 쌓인 전송 대기분부터 보낸다
        if (running && settings.isConfigured) UploadWorker.enqueue(context)
        StatusNotifier.update(context)
    }

    fun toggle(context: Context): Boolean = (!isRunning(context)).also { set(context, it) }
}

/** 상태 알림의 '수집 중지 / 수집 시작' 버튼 (앱 내부 전용, exported=false) */
class CaptureToggleReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == ACTION) CaptureControl.toggle(context)
    }

    companion object {
        const val ACTION = "com.mahal.kakaocollector.TOGGLE_CAPTURE"
    }
}
