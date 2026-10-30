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
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * 아직 분류하지 않은 메시지(5글자 이상 텍스트)를 Gemini 로 민원 여부 판정한다.
 * 일시 오류는 WorkManager 가 지수 백오프로 재시도하고, 키·모델 오류는 멈추고 화면에 사유를 표시한다.
 */
class ComplaintWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val settings = AppSettings(applicationContext)
        if (!settings.shouldClassify) return@withContext Result.success()
        val store = MessageStore.get(applicationContext)
        val client = GeminiClient(settings.geminiKey, settings.geminiModel)
        val stamp = SimpleDateFormat("MM-dd HH:mm", Locale.KOREA).format(Date())
        var done = 0
        var complaints = 0

        while (done < MAX_PER_RUN) {
            val batch = store.unclassified(Complaints.BATCH)
            if (batch.isEmpty()) break
            when (val result = client.classify(batch)) {
                is GeminiClient.Result.Ok -> {
                    store.saveVerdicts(result.verdicts, settings.geminiModel)
                    val missing = batch.map { it.id }.toSet() - result.verdicts.map { it.id }.toSet()
                    if (missing.isNotEmpty()) store.markClassifyFailed(missing, "응답에 결과 없음")
                    done += batch.size
                    complaints += result.verdicts.count { it.isComplaint }
                }
                is GeminiClient.Result.BadResponse -> {
                    store.markClassifyFailed(batch.map { it.id }, result.reason)
                    done += batch.size
                }
                is GeminiClient.Result.Retry -> {
                    settings.complaintStatus = "$stamp 대기: ${result.reason}"
                    return@withContext Result.retry()
                }
                is GeminiClient.Result.ConfigError -> {
                    settings.complaintStatus = "$stamp 중단: ${result.reason}"
                    return@withContext Result.failure()
                }
            }
        }
        settings.complaintStatus = "$stamp 완료: ${done}건 분류 · 민원 ${complaints}건"
        // 한 번에 처리량을 제한했으므로 남은 것이 있으면 이어서 예약
        if (store.unclassified(1).isNotEmpty()) enqueue(applicationContext)
        Result.success()
    }

    companion object {
        private const val UNIQUE_NAME = "complaint-classify"
        private const val MAX_PER_RUN = 200

        fun enqueue(context: Context) {
            val request = OneTimeWorkRequestBuilder<ComplaintWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(UNIQUE_NAME, ExistingWorkPolicy.APPEND_OR_REPLACE, request)
        }
    }
}
