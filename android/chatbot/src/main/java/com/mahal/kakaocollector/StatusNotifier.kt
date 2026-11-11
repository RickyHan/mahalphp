package com.mahal.kakaocollector

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/** 알림줄에 조용히 떠 있는 상태 알림: "🟢 정상 수집 중 · 마지막 전송 3분 전". 앱을 열지 않아도 동작 여부를 확인 */
object StatusNotifier {
    private const val CHANNEL = "collector_status"
    private const val ID = 1001

    fun update(context: Context) {
        val settings = AppSettings(context)
        val nm = NotificationManagerCompat.from(context)
        if (!settings.statusNotification) {
            nm.cancel(ID)
            return
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        ensureChannel(context)
        val status = evaluate(context)
        val dot = when (status.level) {
            CollectorStatus.Level.GOOD -> "🟢"
            CollectorStatus.Level.WARN -> "🟡"
            CollectorStatus.Level.BAD -> "🔴"
        }
        val open = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val toggle = PendingIntent.getBroadcast(
            context, 1,
            Intent(context, CaptureToggleReceiver::class.java).setAction(CaptureToggleReceiver.ACTION),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val toggleLabel = if (settings.captureEnabled) "⏸ 수집 중지" else "▶ 수집 시작"
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_collector)
            .setContentTitle("$dot ${status.title}")
            .setContentText(status.detail.lines().last())
            .setColor(ContextCompat.getColor(context, colorOf(status.level)))
            .setStyle(NotificationCompat.BigTextStyle().bigText(status.detail))
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(open)
            .addAction(0, toggleLabel, toggle)
            .build()
        runCatching { nm.notify(ID, n) }
    }

    fun colorOf(level: CollectorStatus.Level): Int = when (level) {
        CollectorStatus.Level.GOOD -> R.color.good
        CollectorStatus.Level.WARN -> R.color.warn
        CollectorStatus.Level.BAD -> R.color.bad
    }

    fun evaluate(context: Context): CollectorStatus.Status {
        val settings = AppSettings(context)
        val counts = MessageStore.get(context).counts()
        return CollectorStatus.evaluate(
            CollectorStatus.Input(
                permissionGranted = NotificationManagerCompat.getEnabledListenerPackages(context).contains(context.packageName),
                serverConfigured = settings.isConfigured,
                captureEnabled = settings.captureEnabled,
                pending = counts.pending,
                failed = counts.failed,
                lastCapturedAt = settings.lastCapturedAt,
                lastUploadAt = settings.lastUploadAt,
                lastUploadError = settings.lastUploadError,
                now = System.currentTimeMillis(),
            )
        )
    }

    private fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = context.getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "수집 상태", NotificationManager.IMPORTANCE_LOW).apply {
                    description = "카톡 수집·서버 전송이 정상인지 알림줄에 표시합니다"
                    setShowBadge(false)
                }
            )
        }
    }
}
