package com.mahal.kakaocollector

import android.os.Build
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

/** 카카오톡 알림을 받아 전송 대기열에 넣고 서버 업로드를 예약한다. 조회·분류·이력은 서버가 처리. */
class KakaoNotificationListener : NotificationListenerService() {

    private val worker = Executors.newSingleThreadExecutor()
    // 서버가 정한 안내·업체 처리 요청을 30초마다 확인해 보낸다 (분류가 늦어도·방 알림을 기다리는 요청도 놓치지 않게)
    private val relayTimer = Executors.newSingleThreadScheduledExecutor()
    private var relayTask: ScheduledFuture<*>? = null

    override fun onListenerConnected() {
        StatusNotifier.update(this)
        relayTask?.cancel(false)
        relayTask = relayTimer.scheduleWithFixedDelay({
            runCatching { if (AppSettings(this).captureEnabled) ReplyRelay.relayOnce(this) }
                .onFailure { Log.w(TAG, "relay", it) }
        }, 5, RELAY_SECONDS, TimeUnit.SECONDS)
        // 권한을 막 켰거나 서비스가 재시작된 경우, 이미 떠 있는 알림도 수집한다(중복은 큐에서 걸러짐)
        runCatching { activeNotifications }.getOrNull()?.forEach(::onNotificationPosted)
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName != KakaoParser.KAKAO_PACKAGE) return
        worker.execute { handle(sbn) }
    }

    override fun onListenerDisconnected() {
        relayTask?.cancel(false)
        StatusNotifier.update(this)
    }

    override fun onDestroy() {
        relayTask?.cancel(false)
        relayTimer.shutdownNow()
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
            // 이 방에 보낼 '답장' 수단을 기억 (내가 보낸 글 제외). 수집 제외 방·이미 받은 글이어도 기억해 둔다.
            // (댓글 알림의 답장 버튼은 댓글 스레드로 보내므로 ReplyRegistry 가 구분한다)
            messages.lastOrNull { it.sender != KakaoParser.SELF_SENDER }?.let { ReplyRegistry.remember(it.room, sbn) }
            messages.forEachIndexed { index, message ->
                if (store.contains(message)) return@forEachIndexed
                store.recordSeenRoom(message.room, message.isGroupChat, message.postedAt)
                if (!settings.allowsRoom(message.room)) return@forEachIndexed
                val image = if (settings.captureImages && message.type == MessageType.PHOTO) {
                    NotificationExtractor.saveImage(this, sbn, message, isLatest = index == messages.lastIndex)
                } else null
                if (store.insert(message, image)) queued++ else image?.let { File(it).delete() }
            }
            if (queued > 0) {
                settings.lastCapturedAt = System.currentTimeMillis()
                if (settings.isConfigured) UploadWorker.enqueue(this)
                StatusNotifier.update(this)
            }
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
        const val RELAY_SECONDS = 30L
    }
}
