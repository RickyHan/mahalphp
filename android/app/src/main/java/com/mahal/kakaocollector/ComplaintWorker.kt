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
                    if (settings.autoReplyEnabled) replyToNewIssues(store, settings, result.verdicts, batch)
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

    /** 새 문제의 첫 민원에만 해당 채팅방으로 '[분류] 민원이 접수되었습니다.' 안내를 보낸다 */
    private fun replyToNewIssues(store: MessageStore, settings: AppSettings, verdicts: List<Complaints.Verdict>, batch: List<Complaints.Item>) {
        val byId = batch.associateBy { it.id }
        val now = System.currentTimeMillis()
        for (v in verdicts.filter { it.isComplaint }.sortedBy { it.id }) {
            val item = byId[v.id] ?: continue
            val postedAt = store.complaintsByIds(longArrayOf(v.id)).firstOrNull()?.row?.postedAt ?: continue
            when {
                now - postedAt > REPLY_MAX_AGE_MS -> store.setReplyStatus(v.id, 3, "30분 지난 메시지라 안내 생략")
                !store.isNewIssue(v.id) -> store.setReplyStatus(v.id, 2, "같은 문제로 이미 안내")
                else -> {
                    val text = ReplyRegistry.format(settings.replyTemplate, item.sender, v.category, v.summary, item.room)
                    when (val r = ReplyRegistry.send(applicationContext, item.room, text)) {
                        ReplyRegistry.Result.Sent -> store.setReplyStatus(v.id, 1, text)
                        ReplyRegistry.Result.NoAction -> store.setReplyStatus(v.id, -1, "답장 가능한 카톡 알림이 없음 (앱 재시작 등)")
                        is ReplyRegistry.Result.Failed -> store.setReplyStatus(v.id, -1, r.reason)
                    }
                }
            }
        }
    }

    companion object {
        private const val REPLY_MAX_AGE_MS = 30 * 60 * 1000L
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
