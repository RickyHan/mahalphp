package com.mahal.kakaocollector

import android.content.Context

/**
 * 서버가 정한 안내·업체 처리 요청을 가져와 카톡 '답장'으로 보내고 결과를 보고한다.
 * 업로드 직후 작업(ReplyWorker)과 알림 서비스의 30초 주기 확인이 함께 쓰며, 같은 메시지를 두 번 보내지 않도록 한 번에 하나만 실행한다.
 */
object ReplyRelay {
    private val lock = Any()

    /** 한 번 확인·전송. 반환값: 보낸 건수 (서버 연결 실패면 -1) */
    fun relayOnce(context: Context): Int = synchronized(lock) {
        val settings = AppSettings(context)
        if (!settings.isConfigured) return 0
        val api = ApiClient(settings)
        val replies = api.pendingReplies() ?: return -1
        var sent = 0
        for (r in replies) {
            when (val result = ReplyRegistry.send(context, r.room, r.text)) {
                ReplyRegistry.Result.Sent -> {
                    api.reportReply(r.id, true, null)
                    sent++
                }
                // 이 방의 답장 수단이 아직 없음(앱 재시작 직후 등)·만료됨 → 실패로 보고하지 않고 대기열에 둔다.
                // 그 방에 새 글이 와서 알림을 받으면 다음 확인 때 보낸다. (서버가 접수 안내 30분·업체 요청 12시간 뒤 만료)
                ReplyRegistry.Result.NoAction, ReplyRegistry.Result.ThreadOnly -> Unit
                is ReplyRegistry.Result.Expired -> log(context, "답장 기능 만료: 방=[${r.room}] — 그 방의 새 알림을 기다립니다")
                is ReplyRegistry.Result.Failed -> api.reportReply(r.id, false, result.reason)
            }
        }
        sent
    }

    private fun log(context: Context, text: String) {
        runCatching { MessageStore.get(context).addNotificationLog(System.currentTimeMillis(), "[전송] $text\n") }
    }
}
