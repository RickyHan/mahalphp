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

/** 로컬 큐의 대기 메시지를 서버로 전송한다. 실패 시 30초 간격(늘어남)으로 재시도하고, 알림 서비스가 30초마다 남은 메시지를 다시 확인한다. */
class UploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val settings = AppSettings(applicationContext)
        if (!settings.isConfigured) return@withContext Result.success() // 서버 설정을 저장하면 다시 예약됨
        val store = MessageStore.get(applicationContext)
        val api = ApiClient(settings)

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

        /** 보낼 메시지가 남아 있으면 전송을 시작한다 (알림 수신 서비스가 30초마다 호출: 서버가 돌아오면 바로 이어서 보냄) */
        fun kick(context: Context) {
            if (AppSettings(context).isConfigured && MessageStore.get(context).hasPending()) enqueue(context)
        }
    }
}
