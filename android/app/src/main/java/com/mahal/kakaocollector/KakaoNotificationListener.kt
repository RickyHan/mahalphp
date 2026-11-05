package com.mahal.kakaocollector

import android.app.Notification
import android.os.Build
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

/** 카카오톡 알림을 받아 휴대폰(전송 대기열)에 먼저 저장하고, 30초마다 모아서 서버로 보낸다. 조회·분류·이력은 서버가 처리. */
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
            // 쌓아 둔 메시지: 30초마다 모아서 서버로 보낸다
            runCatching { UploadWorker.kick(this) }.onFailure { Log.w(TAG, "upload kick", it) }
            // 새 알림이 없어도 30개 넘게 남은 오래된 카톡 알림·다른 앱 알림을 정리
            worker.execute { trimKakaoNotifications() }
        }, 5, RELAY_SECONDS, TimeUnit.SECONDS)
        // 권한을 막 켰거나 서비스가 재시작된 경우, 이미 떠 있는 알림도 수집한다(중복은 큐에서 걸러짐)
        runCatching { activeNotifications }.getOrNull()?.forEach(::onNotificationPosted)
        worker.execute { trimKakaoNotifications() } // 수집이 끝난 뒤 30개 넘는 오래된 알림·다른 앱 알림 정리
    }

    override fun onCreate() {
        super.onCreate()
        running = this
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName != KakaoParser.KAKAO_PACKAGE) {
            removeOther(sbn) // 카카오톡이 아닌 알림은 바로 지움 (옵션)
            return
        }
        worker.execute { handle(sbn) }
    }

    /** 카카오톡·이 앱이 아닌 알림 중 지울 수 있는 것 → 지움 (통화 중·충전 등 지울 수 없는 알림은 그대로) */
    private fun removeOther(sbn: StatusBarNotification): Boolean {
        if (!AppSettings(this).removeOtherNotifications) return false
        if (sbn.packageName == KakaoParser.KAKAO_PACKAGE || sbn.packageName == packageName || !sbn.isClearable) return false
        return runCatching { cancelNotification(sbn.key) }.onFailure { Log.w(TAG, "다른 앱 알림 지우기 실패", it) }.isSuccess
    }

    override fun onListenerDisconnected() {
        relayTask?.cancel(false)
        StatusNotifier.update(this)
    }

    override fun onDestroy() {
        if (running === this) running = null
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
                // 바로 보내지 않고 휴대폰에 저장만 한다. 30초마다(또는 '지금 동기화' 요청 때) 묶어서 서버로 보낸다.
                StatusNotifier.update(this)
            }
        } catch (e: Exception) {
            Log.w(TAG, "알림 처리 실패", e)
        }
        trimKakaoNotifications()
    }

    /**
     * 알림창의 카톡 알림을 최대 30개로 유지: 넘치면 오래된 것부터 지운다(이미 저장한 뒤라 수집에서 빠지지 않음).
     * 업체 톡방(●) 알림은 업체 처리 요청 '답장'에 쓰므로 일반 방 알림을 먼저 지운다.
     */
    internal fun trimKakaoNotifications(force: Boolean = false) {
        val settings = AppSettings(this)
        if (!settings.limitKakaoNotifications && !settings.removeOtherNotifications) return
        val active = runCatching { activeNotifications }.getOrElse {
            settings.recordTrim(0, 0, "알림 목록 읽기 실패: ${it.javaClass.simpleName}")
            return
        } ?: return
        // 이미 떠 있는 다른 앱 알림 정리 (카톡 30개 유지와 별개로 켜고 끔)
        val others = active.count { removeOther(it) }
        if (others > 0) settings.otherRemoved += others
        if (!settings.limitKakaoNotifications || (!force && !settings.captureEnabled)) return
        val kakao = active.filter { it.packageName == KakaoParser.KAKAO_PACKAGE }
        val counted = kakao.count { it.notification.flags and Notification.FLAG_GROUP_SUMMARY == 0 && it.isClearable }
        settings.kakaoNotificationCount = counted
        if (counted <= NotificationTrim.MAX) {
            if (force) settings.recordTrim(counted, 0, null)
            return
        }
        val items = kakao.map { sbn ->
            val summary = sbn.notification.flags and Notification.FLAG_GROUP_SUMMARY != 0
            val room = if (summary) null else runCatching {
                KakaoParser.parse(NotificationExtractor.extract(sbn, conversationShortcut(sbn)?.first)).firstOrNull()?.room
            }.getOrNull()
            NotificationTrim.Item(sbn.key, sbn.postTime, room, summary, sbn.isClearable)
        }
        val rooms = items.associate { it.key to (it.room ?: "?") }
        var cancelled = 0
        var error: String? = null
        val names = mutableListOf<String>()
        for (key in NotificationTrim.toCancel(items)) {
            runCatching { cancelNotification(key) }
                .onSuccess { cancelled++; names += rooms[key].orEmpty() }
                .onFailure { error = "${it.javaClass.simpleName}: ${it.message.orEmpty().take(80)}"; Log.w(TAG, "알림 정리 실패", it) }
        }
        // 실제로 지워졌는지 다시 세어 기록 (Android 버전·제조사에 따라 막히는 경우 확인용)
        val after = runCatching { activeNotifications }.getOrNull()
            ?.count { it.packageName == KakaoParser.KAKAO_PACKAGE && it.notification.flags and Notification.FLAG_GROUP_SUMMARY == 0 && it.isClearable }
            ?: -1
        settings.kakaoNotificationCount = after.coerceAtLeast(0)
        if (cancelled > 0 && after >= counted) error = error ?: "지우기 요청은 했지만 알림이 줄지 않음 (휴대폰이 막고 있을 수 있음)"
        settings.recordTrim(after, cancelled, error)
        runCatching {
            MessageStore.get(this).addNotificationLog(
                System.currentTimeMillis(),
                "[알림 정리] 카톡 알림 $counted 개 → $after 개, ${cancelled}개 지움" +
                    (if (names.isNotEmpty()) " (오래된 순: ${names.joinToString(", ").take(300)})" else "") +
                    (error?.let { "\n  실패: $it" } ?: "") + "\n",
            )
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

    companion object {
        private const val TAG = "KakaoListener"
        const val RELAY_SECONDS = 30L

        /** 실행 중인 알림 수신 서비스 (수집 옵션의 '지금 정리' 버튼용) */
        @Volatile private var running: KakaoNotificationListener? = null

        /** 지금 바로 알림 정리 (서비스가 꺼져 있으면 false) */
        fun trimNow(): Boolean {
            val service = running ?: return false
            service.worker.execute { service.trimKakaoNotifications(force = true) }
            return true
        }
    }
}
