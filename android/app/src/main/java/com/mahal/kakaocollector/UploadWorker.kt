package com.mahal.kakaocollector

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** 휴대폰에 저장해 둔 메시지를 서버로 보낸다. 30초마다(알림 서비스가 확인), 관리자 웹 '지금 동기화', 앱의 '지금 전송' 때 실행. */
class UploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val settings = AppSettings(applicationContext)
        if (!settings.isConfigured) return@withContext Result.success() // 서버 설정을 저장하면 다시 예약됨
        val store = MessageStore.get(applicationContext)
        val api = ApiClient(settings)
        settings.lastUploadAttemptAt = System.currentTimeMillis()

        // id 순서로 한 번 훑는다. 서버가 한 메시지만 거절(500)하면 그 메시지는 남겨 두고 뒤 메시지를 계속 보낸다.
        // 서버에 닿지 않으면(네트워크·재시작 중) 바로 멈추고 다시 시도한다.
        var afterId = 0L
        var skipped: String? = null
        while (true) {
            val batch = store.pendingAfter(afterId, BATCH)
            if (batch.isEmpty()) break
            for (row in batch) {
                afterId = row.id
                when (val result = api.upload(row)) {
                    ApiClient.Result.Ok -> {
                        store.markSent(row)
                        settings.lastUploadAt = System.currentTimeMillis()
                        settings.lastUploadError = null
                    }
                    is ApiClient.Result.Rejected -> store.markFailed(row, result.reason)
                    is ApiClient.Result.Retry -> {
                        store.markRetry(row, result.reason)
                        settings.lastUploadError = result.reason
                        if (!result.thisMessageOnly) {
                            StatusNotifier.update(applicationContext)
                            return@withContext Result.retry()
                        }
                        skipped = result.reason
                    }
                }
            }
        }
        store.pruneSent()
        StatusNotifier.update(applicationContext)
        // 서버가 방금 올린 메시지를 분류해 '접수 안내'를 정하면 가져가 전송한다
        ReplyWorker.enqueue(applicationContext)
        if (skipped != null) Result.retry() else Result.success()
    }

    companion object {
        private const val UNIQUE_NAME = "kakao-upload"
        private const val BATCH = 50

        /**
         * 전송을 지금 시작한다. 이미 보내는 중이면 그대로 두고(보내는 중에 들어온 메시지도 이어서 보냄),
         * 실패 후 '다시 시도'를 기다리는 중이면 기다리지 않고 바로 다시 시작한다.
         * (예전에는 새 메시지가 대기 중인 재시도 뒤에 줄을 서서, 서버 재시작 뒤 한참 동안 전송이 멈췄다)
         */
        fun enqueue(context: Context) {
            val app = context.applicationContext
            val request = OneTimeWorkRequestBuilder<UploadWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.LINEAR, 30, TimeUnit.SECONDS)
                .build()
            scheduler.execute { // 작업 상태 조회는 화면(메인) 스레드를 막지 않도록 따로
                runCatching {
                    val wm = WorkManager.getInstance(app)
                    val running = runCatching {
                        wm.getWorkInfosForUniqueWork(UNIQUE_NAME).get(3, TimeUnit.SECONDS).any { it.state == WorkInfo.State.RUNNING }
                    }.getOrDefault(false)
                    wm.enqueueUniqueWork(UNIQUE_NAME, if (running) ExistingWorkPolicy.KEEP else ExistingWorkPolicy.REPLACE, request)
                }
            }
        }

        private val scheduler = Executors.newSingleThreadExecutor()

        /** 휴대폰에 쌓아 두었다가 이 간격마다 서버로 보낸다 (알림 서비스가 30초마다 확인) */
        const val INTERVAL_MS = 30 * 1000L
        /** 30초 확인 주기와 어긋나 한 번 건너뛰지 않도록 둔 여유 */
        private const val SLACK_MS = 5 * 1000L

        /**
         * 알림 수신 서비스가 30초마다 호출: 보낼 메시지가 있고 마지막 전송 후 30초가 지났으면 전송한다.
         * (새 메시지가 올 때마다 바로 보내지 않고 휴대폰에 먼저 저장 → 서버가 잠시 멈춰도 빠지지 않음)
         */
        fun kick(context: Context, now: Long = System.currentTimeMillis()) {
            val settings = AppSettings(context)
            if (!settings.isConfigured || !MessageStore.get(context).hasPending()) return
            if (isDue(settings.lastUploadAttemptAt, now)) enqueue(context)
        }

        fun isDue(lastAttempt: Long, now: Long): Boolean = now - lastAttempt >= INTERVAL_MS - SLACK_MS || now < lastAttempt
    }
}
