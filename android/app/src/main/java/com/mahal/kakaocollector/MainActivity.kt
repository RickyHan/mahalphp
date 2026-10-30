package com.mahal.kakaocollector

import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.NotificationManagerCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 수집 전용 앱의 설정·상태 화면. 조회·분석은 웹 관리 화면에서 한다. */
class MainActivity : Activity() {

    private val scope = MainScope()
    private lateinit var settings: AppSettings

    private val permissionStatus by lazy { findViewById<TextView>(R.id.permissionStatus) }
    private val serverUrl by lazy { findViewById<EditText>(R.id.serverUrl) }
    private val apiKey by lazy { findViewById<EditText>(R.id.apiKey) }
    private val connectionStatus by lazy { findViewById<TextView>(R.id.connectionStatus) }
    private val queueStatus by lazy { findViewById<TextView>(R.id.queueStatus) }
    private val recentMessages by lazy { findViewById<TextView>(R.id.recentMessages) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        settings = AppSettings(this)

        serverUrl.setText(settings.serverUrl)
        apiKey.setText(settings.apiKey)
        findViewById<Switch>(R.id.captureEnabled).apply {
            isChecked = settings.captureEnabled
            setOnCheckedChangeListener { _, checked -> settings.captureEnabled = checked }
        }
        findViewById<Switch>(R.id.captureImages).apply {
            isChecked = settings.captureImages
            setOnCheckedChangeListener { _, checked -> settings.captureImages = checked }
        }

        findViewById<Button>(R.id.openWeb).setOnClickListener { openWeb() }
        findViewById<Button>(R.id.openListenerSettings).setOnClickListener {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }
        findViewById<Button>(R.id.openBatterySettings).setOnClickListener {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
        findViewById<Button>(R.id.openRoomFilter).setOnClickListener { startActivity(Intent(this, RoomFilterActivity::class.java)) }
        findViewById<Button>(R.id.openDiagnostics).setOnClickListener { startActivity(Intent(this, DiagnosticsActivity::class.java)) }
        findViewById<Button>(R.id.save).setOnClickListener { saveServer() }
        findViewById<Button>(R.id.testConnection).setOnClickListener { saveServer(); testConnection() }
        findViewById<Button>(R.id.uploadNow).setOnClickListener {
            UploadWorker.enqueue(this)
            Toast.makeText(this, "전송을 예약했습니다.", Toast.LENGTH_SHORT).show()
        }
        findViewById<Button>(R.id.refresh).setOnClickListener { refresh() }
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun openWeb() {
        if (!settings.serverUrl.startsWith("http")) {
            Toast.makeText(this, "먼저 서버 주소를 저장하십시오.", Toast.LENGTH_SHORT).show()
            return
        }
        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("${settings.serverUrl}/admin")))
    }

    private fun saveServer() {
        settings.serverUrl = serverUrl.text.toString()
        settings.apiKey = apiKey.text.toString()
        if (settings.isConfigured) UploadWorker.enqueue(this)
        Toast.makeText(this, "저장했습니다.", Toast.LENGTH_SHORT).show()
    }

    private fun testConnection() {
        if (!settings.isConfigured) {
            connectionStatus.text = "서버 주소(http:// 또는 https://)와 API 키를 입력하십시오."
            return
        }
        connectionStatus.text = "확인 중…"
        scope.launch {
            val error = withContext(Dispatchers.IO) { ApiClient(settings).health() }
            connectionStatus.text = if (error == null) "✅ 서버 연결 정상" else "❌ 연결 실패: $error"
        }
    }

    private fun refresh() {
        val granted = NotificationManagerCompat.getEnabledListenerPackages(this).contains(packageName)
        permissionStatus.text = if (granted) "✅ 알림 접근 허용됨" else "❌ 알림 접근 권한이 필요합니다. 아래 버튼에서 '카톡 수집기'를 켜십시오."
        if (granted) requestRebindIfNeeded()
        findViewById<TextView>(R.id.roomFilterStatus).text =
            "현재: " + RoomFilterActivity.summary(settings.roomFilterMode, settings.selectedRooms.size)

        scope.launch {
            val (counts, rows) = withContext(Dispatchers.IO) {
                val store = MessageStore.get(this@MainActivity)
                store.counts() to store.recent(30)
            }
            queueStatus.text = (if (settings.isConfigured) "" else "⚠️ 서버가 설정되지 않아 휴대폰에만 쌓이고 있습니다.\n") +
                "전송 대기 ${counts.pending} · 전송 완료 ${counts.sent} · 실패 ${counts.failed}\n단말 ID: ${settings.deviceId}"
            val fmt = SimpleDateFormat("MM-dd HH:mm:ss", Locale.KOREA)
            recentMessages.text = if (rows.isEmpty()) "아직 수집된 메시지가 없습니다." else rows.joinToString("\n") { r ->
                val mark = when (r.status) {
                    MessageStore.SENT -> "✔"
                    MessageStore.FAILED -> "✖"
                    else -> "…"
                }
                val media = if (r.imagePath != null && r.status != MessageStore.SENT) " [사진]" else ""
                "$mark ${fmt.format(Date(r.postedAt))} [${r.room}] ${r.sender}: ${r.text.take(60)}$media" +
                    (r.lastError?.takeIf { r.status != MessageStore.SENT }?.let { "\n   ↳ $it" } ?: "")
            }
        }
    }

    /** 일부 제조사 기기에서 앱 업데이트 후 리스너가 끊기는 경우를 대비해 재연결을 요청한다. */
    private fun requestRebindIfNeeded() {
        runCatching {
            android.service.notification.NotificationListenerService.requestRebind(
                ComponentName(this, KakaoNotificationListener::class.java)
            )
        }
    }
}
