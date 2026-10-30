package com.mahal.kakaocollector

import android.os.Build
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import java.io.File
import java.util.concurrent.Executors

/** 카카오톡 알림을 받아 로컬 큐에 저장하고 업로드를 예약한다. */
class KakaoNotificationListener : NotificationListenerService() {

    private val worker = Executors.newSingleThreadExecutor()

    override fun onListenerConnected() {
        // 권한을 막 켰거나 서비스가 재시작된 경우, 이미 떠 있는 알림도 수집한다(중복은 큐에서 걸러짐)
        runCatching { activeNotifications }.getOrNull()?.forEach(::onNotificationPosted)
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName != KakaoParser.KAKAO_PACKAGE) return
        worker.execute { handle(sbn) }
    }

    override fun onDestroy() {
        worker.shutdown()
        super.onDestroy()
    }

    private fun handle(sbn: StatusBarNotification) {
        val settings = AppSettings(this)
        if (!settings.captureEnabled) return
        try {
            val store = MessageStore.get(this)
            val shortcut = conversationShortcut(sbn)
            val messages = KakaoParser.parse(NotificationExtractor.extract(sbn, shortcut?.first))
            val parsed = messages.joinToString("\n") { "  → 방=[${it.room}] 보낸사람=[${it.sender}] 단톡=${it.isGroupChat} 종류=${it.type.wire} 내용=[${it.text.take(40)}]" }
            // 수집 제외 방의 알림은 진단 기록에도 남기지 않는다 (해석 실패 알림은 원인 확인을 위해 기록)
            if (messages.isEmpty() || messages.any { settings.allowsRoom(it.room) }) {
                store.addNotificationLog(System.currentTimeMillis(), NotificationExtractor.dump(sbn, shortcut?.let { "short=[${it.first}] long=[${it.second}]" }) + "해석 결과:\n" + parsed.ifEmpty { "  (대화 알림 아님 — 건너뜀)" })
            }
            var queued = 0
            messages.forEachIndexed { index, message ->
                if (store.contains(message)) return@forEachIndexed
                store.recordSeenRoom(message.room, message.isGroupChat, message.postedAt)
                if (!settings.allowsRoom(message.room)) return@forEachIndexed
                val image = if (settings.captureImages && message.type == MessageType.PHOTO) {
                    NotificationExtractor.saveImage(this, sbn, message, isLatest = index == messages.lastIndex)
                } else null
                if (store.insert(message, image, upload = settings.shouldUpload)) queued++ else image?.let { File(it).delete() }
            }
            if (queued > 0 && settings.shouldUpload) UploadWorker.enqueue(this)
            if (queued > 0 && settings.shouldClassify) ComplaintWorker.enqueue(this)
        } catch (e: Exception) {
            Log.w(TAG, "알림 처리 실패", e)
        }
    }

    /**
     * Android 12+ 대화 바로가기의 (짧은 이름, 긴 이름). 카카오톡은 여기에 채팅방 이름을 등록한다.
     * 알림 수신 앱만 Ranking 을 통해 읽을 수 있다.
     */
    private fun conversationShortcut(sbn: StatusBarNotification): Pair<String?, String?>? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return null
        return runCatching {
            val ranking = Ranking()
            if (!currentRanking.getRanking(sbn.key, ranking)) return null
            val info = ranking.conversationShortcutInfo ?: return null
            val short = info.shortLabel?.toString()
            val long = info.longLabel?.toString()
            (short ?: long) to long
        }.getOrNull()
    }

    private companion object {
        const val TAG = "KakaoListener"
    }
}
