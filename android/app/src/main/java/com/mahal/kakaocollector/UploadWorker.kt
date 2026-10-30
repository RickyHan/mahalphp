package com.mahal.kakaocollector

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.concurrent.TimeUnit

/** 로컬 큐의 대기 메시지를 서버로 전송한다. 실패 시 WorkManager 가 지수 백오프로 재시도한다. */
class UploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val settings = AppSettings(applicationContext)
        if (!settings.shouldUpload) return@withContext Result.success() // 서버 전송을 켜면 다시 예약됨
        val store = MessageStore.get(applicationContext)
        val api = ApiClient(settings)

        while (true) {
            val batch = store.pending(BATCH)
            if (batch.isEmpty()) break
            for (row in batch) {
                when (val result = api.upload(row)) {
                    ApiClient.Result.Ok -> store.markSent(row)
                    is ApiClient.Result.Rejected -> store.markFailed(row, result.reason)
                    is ApiClient.Result.Retry -> {
                        store.markRetry(row, result.reason)
                        return@withContext Result.retry()
                    }
                }
            }
        }
        Result.success()
    }

    companion object {
        private const val UNIQUE_NAME = "kakao-upload"
        private const val BATCH = 50

        fun enqueue(context: Context) {
            val request = OneTimeWorkRequestBuilder<UploadWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(UNIQUE_NAME, ExistingWorkPolicy.APPEND_OR_REPLACE, request)
        }
    }
}
