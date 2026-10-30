package com.mahal.kakaocollector

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

/**
 * 서버가 결정한 '민원 접수 안내'를 가져와 해당 채팅방에 카톡 답장으로 보내고 결과를 보고한다.
 * 서버 분류는 업로드 후 몇 초 뒤에 끝나므로, 업로드 직후 약 1분간 몇 차례 확인한다.
 */
class ReplyWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val settings = AppSettings(applicationContext)
        if (!settings.isConfigured) return@withContext Result.success()
        val api = ApiClient(settings)
        repeat(POLLS) { attempt ->
            if (attempt > 0) delay(POLL_INTERVAL_MS)
            val replies = api.pendingReplies() ?: return@repeat
            for (r in replies) {
                when (val result = ReplyRegistry.send(applicationContext, r.room, r.text)) {
                    ReplyRegistry.Result.Sent -> api.reportReply(r.id, true, null)
                    ReplyRegistry.Result.NoAction -> api.reportReply(r.id, false, "휴대폰에 이 방의 답장 가능한 카톡 알림이 없음 (앱 재시작 등)")
                    is ReplyRegistry.Result.Failed -> api.reportReply(r.id, false, result.reason)
                }
            }
        }
        Result.success()
    }

    companion object {
        private const val UNIQUE_NAME = "reply-relay"
        private const val POLLS = 6
        private const val POLL_INTERVAL_MS = 10_000L

        fun enqueue(context: Context) {
            val request = OneTimeWorkRequestBuilder<ReplyWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            // 진행 중이면 새로 시작해 확인 시간을 연장
            WorkManager.getInstance(context).enqueueUniqueWork(UNIQUE_NAME, ExistingWorkPolicy.REPLACE, request)
        }
    }
}
