package com.mahal.kakaocollector

import android.Manifest
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.materialswitch.MaterialSwitch
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 수집 전용 앱의 상태·설정 화면. 조회·분석은 웹 관리 화면에서 한다. */
class MainActivity : AppCompatActivity() {

    private val scope = MainScope()
    private var ticker: Job? = null
    private lateinit var settings: AppSettings
    private var serverOk: Boolean? = null

    private val serverUrl by lazy { findViewById<TextInputEditText>(R.id.serverUrl) }
    private val apiKey by lazy { findViewById<TextInputEditText>(R.id.apiKey) }
    private val connectionStatus by lazy { findViewById<TextView>(R.id.connectionStatus) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        settings = AppSettings(this)

        findViewById<TextView>(R.id.versionText).text = "v${BuildConfig.VERSION_NAME} · 카톡 알림 수집 → 서버 전송"
        findViewById<TextView>(R.id.deviceId).text = "단말 ID ${settings.deviceId}"
        serverUrl.setText(settings.serverUrl)
        apiKey.setText(settings.apiKey)

        bindSwitch(R.id.captureEnabled, settings.captureEnabled) { settings.captureEnabled = it }
        bindSwitch(R.id.captureImages, settings.captureImages) { settings.captureImages = it }
        bindSwitch(R.id.statusNotification, settings.statusNotification) {
            settings.statusNotification = it
            if (it) requestNotificationPermission()
            StatusNotifier.update(this)
        }

        click(R.id.openWeb) { openWeb() }
        click(R.id.openListenerSettings) { startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)) }
        click(R.id.openBatterySettings) { startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) }
        click(R.id.openRoomFilter) { startActivity(Intent(this, RoomFilterActivity::class.java)) }
        click(R.id.openDiagnostics) { startActivity(Intent(this, DiagnosticsActivity::class.java)) }
        click(R.id.save) { saveServer(); testConnection() }
        click(R.id.testConnection) { testConnection() }
        click(R.id.uploadNow) {
            UploadWorker.enqueue(this)
            Toast.makeText(this, "전송을 시작했습니다.", Toast.LENGTH_SHORT).show()
        }

        if (settings.statusNotification) requestNotificationPermission()
        if (settings.isConfigured) {
            UploadWorker.enqueue(this) // 쌓여 있던 메시지(이전 버전 포함)를 서버로
            testConnection()
        }
    }

    override fun onResume() {
        super.onResume()
        requestRebindIfNeeded()
        // 화면을 보는 동안 3초마다 상태 갱신 (수집·전송이 실시간으로 보이도록)
        ticker = scope.launch {
            while (isActive) {
                refresh()
                delay(3000)
            }
        }
    }

    override fun onPause() {
        ticker?.cancel()
        super.onPause()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun click(id: Int, block: () -> Unit) = findViewById<View>(id).setOnClickListener { block() }

    private fun bindSwitch(id: Int, value: Boolean, onChange: (Boolean) -> Unit) {
        findViewById<MaterialSwitch>(id).apply {
            isChecked = value
            setOnCheckedChangeListener { _, checked -> onChange(checked) }
        }
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
            serverOk = false
            connectionStatus.text = "서버 주소(http:// 또는 https://)와 API 키를 입력하십시오."
            return
        }
        connectionStatus.text = "연결 확인 중…"
        scope.launch {
            val error = withContext(Dispatchers.IO) { ApiClient(settings).health() }
            serverOk = error == null
            connectionStatus.text = if (error == null) "서버 연결 정상" else "연결 실패: $error"
            refresh()
        }
    }

    private suspend fun refresh() {
        val (status, counts, rows) = withContext(Dispatchers.IO) {
            val store = MessageStore.get(this@MainActivity)
            Triple(StatusNotifier.evaluate(this@MainActivity), store.counts(), store.recent(15))
        }
        renderStatus(status)

        val granted = NotificationManagerCompat.getEnabledListenerPackages(this).contains(packageName)
        val batteryOk = getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)
        check(R.id.checkPermission, R.id.openListenerSettings, granted, "알림 접근 권한")
        check(R.id.checkBattery, R.id.openBatterySettings, batteryOk, "배터리 최적화 예외")
        findViewById<TextView>(R.id.checkServer).text = when (serverOk) {
            true -> "✅  서버 연결"
            false -> "⚠️  서버 연결 안 됨"
            null -> "⏳  서버 연결 확인 전"
        }
        findViewById<TextView>(R.id.roomFilterStatus).text =
            RoomFilterActivity.summary(settings.roomFilterMode, settings.selectedRooms.size)
        renderRecent(rows, counts)
        StatusNotifier.update(this)
    }

    private fun check(textId: Int, buttonId: Int, ok: Boolean, label: String) {
        findViewById<TextView>(textId).text = if (ok) "✅  $label" else "⚠️  $label 필요"
        findViewById<MaterialButton>(buttonId).visibility = if (ok) View.GONE else View.VISIBLE
    }

    private fun renderStatus(s: CollectorStatus.Status) {
        val (fg, bg) = when (s.level) {
            CollectorStatus.Level.GOOD -> R.color.good to R.color.good_bg
            CollectorStatus.Level.WARN -> R.color.warn to R.color.warn_bg
            CollectorStatus.Level.BAD -> R.color.bad to R.color.bad_bg
        }
        findViewById<MaterialCardView>(R.id.statusCard).setCardBackgroundColor(ContextCompat.getColor(this, bg))
        (findViewById<View>(R.id.statusDot).background.mutate() as GradientDrawable).setColor(ContextCompat.getColor(this, fg))
        findViewById<TextView>(R.id.statusTitle).apply {
            text = s.title
            setTextColor(ContextCompat.getColor(this@MainActivity, fg))
        }
        findViewById<TextView>(R.id.statusDetail).text = s.detail
        findViewById<MaterialButton>(R.id.openWeb).backgroundTintList = ColorStateList.valueOf(ContextCompat.getColor(this, R.color.brand))
    }

    private fun renderRecent(rows: List<MessageStore.Row>, counts: MessageStore.Counts) {
        val list = findViewById<LinearLayout>(R.id.recentList)
        list.removeAllViews()
        val fmt = SimpleDateFormat("MM/dd HH:mm", Locale.KOREA)
        if (rows.isEmpty()) {
            list.addView(line("아직 수집된 메시지가 없습니다.", R.color.text_secondary))
            return
        }
        list.addView(line("전송 완료 ${counts.sent} · 대기 ${counts.pending} · 실패 ${counts.failed}", R.color.text_secondary, 12f))
        for (r in rows) {
            val (mark, color) = when (r.status) {
                MessageStore.SENT -> "✔" to R.color.good
                MessageStore.FAILED -> "✖" to R.color.bad
                else -> "↑" to R.color.warn
            }
            list.addView(LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                setPadding(0, dp(6), 0, dp(6))
                addView(TextView(this@MainActivity).apply {
                    text = mark
                    setTextColor(ContextCompat.getColor(this@MainActivity, color))
                    textSize = 14f
                    minWidth = dp(20)
                })
                addView(LinearLayout(this@MainActivity).apply {
                    orientation = LinearLayout.VERTICAL
                    addView(line("${r.room} · ${r.sender} · ${fmt.format(Date(r.postedAt))}", R.color.text_secondary, 12f))
                    addView(line(r.text.take(80), R.color.text_primary, 14f).apply { maxLines = 2 })
                    r.lastError?.takeIf { r.status != MessageStore.SENT }?.let { addView(line("↳ $it", R.color.bad, 12f)) }
                }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            })
        }
    }

    private fun line(value: String, color: Int, size: Float = 14f) = TextView(this).apply {
        text = value
        textSize = size
        setTextColor(ContextCompat.getColor(this@MainActivity, color))
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        StatusNotifier.update(this)
    }

    /** 일부 제조사 기기에서 앱 업데이트 후 리스너가 끊기는 경우를 대비해 재연결을 요청한다. */
    private fun requestRebindIfNeeded() {
        if (!NotificationManagerCompat.getEnabledListenerPackages(this).contains(packageName)) return
        runCatching {
            android.service.notification.NotificationListenerService.requestRebind(
                ComponentName(this, KakaoNotificationListener::class.java)
            )
        }
    }
}
