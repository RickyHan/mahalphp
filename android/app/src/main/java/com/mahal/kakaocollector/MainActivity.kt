package com.mahal.kakaocollector

import android.app.Activity
import android.app.AlertDialog
import android.content.ComponentName
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : Activity() {

    private val scope = MainScope()
    private lateinit var settings: AppSettings

    private val permissionStatus by lazy { findViewById<TextView>(R.id.permissionStatus) }
    private val storageSummary by lazy { findViewById<TextView>(R.id.storageSummary) }
    private val serverUrl by lazy { findViewById<EditText>(R.id.serverUrl) }
    private val apiKey by lazy { findViewById<EditText>(R.id.apiKey) }
    private val captureEnabled by lazy { findViewById<Switch>(R.id.captureEnabled) }
    private val captureImages by lazy { findViewById<Switch>(R.id.captureImages) }
    private val uploadEnabled by lazy { findViewById<Switch>(R.id.uploadEnabled) }
    private val serverFields by lazy { findViewById<View>(R.id.serverFields) }
    private val connectionStatus by lazy { findViewById<TextView>(R.id.connectionStatus) }
    private val queueStatus by lazy { findViewById<TextView>(R.id.queueStatus) }
    private val recentMessages by lazy { findViewById<TextView>(R.id.recentMessages) }
    private val complaintEnabled by lazy { findViewById<Switch>(R.id.complaintEnabled) }
    private val complaintFields by lazy { findViewById<View>(R.id.complaintFields) }
    private val geminiKey by lazy { findViewById<EditText>(R.id.geminiKey) }
    private val geminiModel by lazy { findViewById<EditText>(R.id.geminiModel) }
    private val complaintStatus by lazy { findViewById<TextView>(R.id.complaintStatus) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        settings = AppSettings(this)

        serverUrl.setText(settings.serverUrl)
        apiKey.setText(settings.apiKey)
        captureEnabled.isChecked = settings.captureEnabled
        captureImages.isChecked = settings.captureImages
        uploadEnabled.isChecked = settings.uploadEnabled
        serverFields.visibility = if (settings.uploadEnabled) View.VISIBLE else View.GONE

        captureEnabled.setOnCheckedChangeListener { _, checked -> settings.captureEnabled = checked }
        captureImages.setOnCheckedChangeListener { _, checked -> settings.captureImages = checked }
        uploadEnabled.setOnCheckedChangeListener { _, checked -> onUploadToggled(checked) }

        findViewById<Button>(R.id.openRooms).setOnClickListener { startActivity(Intent(this, RoomsActivity::class.java)) }
        findViewById<Button>(R.id.openMembers).setOnClickListener { startActivity(Intent(this, MembersActivity::class.java)) }
        findViewById<Button>(R.id.openComplaints).setOnClickListener { startActivity(Intent(this, ComplaintsActivity::class.java)) }

        geminiKey.setText(settings.geminiKey)
        geminiModel.setText(settings.geminiModel)
        complaintEnabled.isChecked = settings.complaintEnabled
        complaintFields.visibility = if (settings.complaintEnabled) View.VISIBLE else View.GONE
        complaintEnabled.setOnCheckedChangeListener { _, checked ->
            settings.complaintEnabled = checked
            complaintFields.visibility = if (checked) View.VISIBLE else View.GONE
            if (checked && settings.shouldClassify) ComplaintWorker.enqueue(this)
            refresh()
        }
        findViewById<Button>(R.id.saveComplaint).setOnClickListener { saveComplaintSettings(); classifyNow() }
        findViewById<Button>(R.id.classifyNow).setOnClickListener { saveComplaintSettings(); classifyNow() }
        findViewById<Button>(R.id.retryFailed).setOnClickListener {
            scope.launch {
                val n = withContext(Dispatchers.IO) { MessageStore.get(this@MainActivity).resetFailedClassifications() }
                Toast.makeText(this@MainActivity, "실패 ${n}건을 다시 분류합니다.", Toast.LENGTH_SHORT).show()
                classifyNow()
            }
        }
        findViewById<Button>(R.id.openListenerSettings).setOnClickListener {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }
        findViewById<Button>(R.id.openBatterySettings).setOnClickListener {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
        findViewById<Button>(R.id.openRoomFilter).setOnClickListener {
            startActivity(Intent(this, RoomFilterActivity::class.java))
        }
        findViewById<Button>(R.id.save).setOnClickListener { saveServer() }
        findViewById<Button>(R.id.testConnection).setOnClickListener { saveServer(); testConnection() }
        findViewById<Button>(R.id.uploadNow).setOnClickListener {
            UploadWorker.enqueue(this)
            Toast.makeText(this, "전송을 예약했습니다.", Toast.LENGTH_SHORT).show()
        }
        findViewById<Button>(R.id.exportCsv).setOnClickListener { exportCsv() }
        findViewById<Button>(R.id.deleteAll).setOnClickListener { confirmDeleteAll() }
        findViewById<Button>(R.id.openDiagnostics).setOnClickListener { startActivity(Intent(this, DiagnosticsActivity::class.java)) }
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

    private fun onUploadToggled(enabled: Boolean) {
        settings.uploadEnabled = enabled
        serverFields.visibility = if (enabled) View.VISIBLE else View.GONE
        if (enabled && settings.isConfigured) startUpload()
        refresh()
    }

    private fun saveServer() {
        settings.serverUrl = serverUrl.text.toString()
        settings.apiKey = apiKey.text.toString()
        if (settings.shouldUpload) startUpload()
        Toast.makeText(this, "저장했습니다.", Toast.LENGTH_SHORT).show()
    }

    /** 휴대폰에만 있던 메시지까지 대기열에 넣고 전송 시작 */
    private fun startUpload() {
        scope.launch {
            withContext(Dispatchers.IO) { MessageStore.get(this@MainActivity).queueLocalForUpload() }
            UploadWorker.enqueue(this@MainActivity)
            refresh()
        }
    }

    private fun saveComplaintSettings() {
        settings.geminiKey = geminiKey.text.toString()
        settings.geminiModel = geminiModel.text.toString()
        geminiModel.setText(settings.geminiModel)
    }

    private fun classifyNow() {
        if (!settings.shouldClassify) {
            complaintStatus.text = "Gemini API 키를 입력하고 '민원 자동 분류 사용'을 켜십시오."
            return
        }
        settings.complaintStatus = ""
        ComplaintWorker.enqueue(this)
        Toast.makeText(this, "민원 분류를 시작했습니다. 잠시 후 '민원 목록'에서 확인하십시오.", Toast.LENGTH_SHORT).show()
        refresh()
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

    private fun exportCsv() {
        scope.launch {
            val file = withContext(Dispatchers.IO) {
                val rows = MessageStore.get(this@MainActivity).allMessages()
                if (rows.isEmpty()) return@withContext null
                val csv = CsvExport.build(rows.map {
                    CsvExport.Item(it.id, it.postedAt, it.room, it.sender, it.type, it.isGroup, it.text, it.imagePath != null, it.status)
                })
                val dir = File(cacheDir, "exports").apply { mkdirs(); listFiles()?.forEach(File::delete) }
                val stamp = SimpleDateFormat("yyyyMMdd_HHmm", Locale.KOREA).format(Date())
                File(dir, "kakao_messages_$stamp.csv").apply { writeText(csv, Charsets.UTF_8) }
            }
            if (file == null) {
                Toast.makeText(this@MainActivity, "내보낼 메시지가 없습니다.", Toast.LENGTH_SHORT).show()
                return@launch
            }
            val uri = FileProvider.getUriForFile(this@MainActivity, "$packageName.files", file)
            val share = Intent(Intent.ACTION_SEND)
                .setType("text/csv")
                .putExtra(Intent.EXTRA_STREAM, uri)
                .putExtra(Intent.EXTRA_SUBJECT, file.name)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            startActivity(Intent.createChooser(share, "CSV 보내기"))
        }
    }

    private fun confirmDeleteAll() {
        AlertDialog.Builder(this)
            .setTitle("전체 삭제")
            .setMessage("휴대폰에 저장된 모든 메시지와 사진을 삭제합니다. 되돌릴 수 없습니다.")
            .setPositiveButton("삭제") { _, _ ->
                scope.launch {
                    withContext(Dispatchers.IO) { MessageStore.get(this@MainActivity).deleteAll() }
                    Toast.makeText(this@MainActivity, "삭제했습니다.", Toast.LENGTH_SHORT).show()
                    refresh()
                }
            }
            .setNegativeButton("취소", null)
            .show()
    }

    private fun refresh() {
        val granted = NotificationManagerCompat.getEnabledListenerPackages(this).contains(packageName)
        permissionStatus.text = if (granted) "✅ 알림 접근 허용됨" else "❌ 알림 접근 권한이 필요합니다. 아래 버튼에서 '카톡 수집기'를 켜십시오."
        if (granted) requestRebindIfNeeded()
        findViewById<TextView>(R.id.roomFilterStatus).text =
            "현재: " + RoomFilterActivity.summary(settings.roomFilterMode, settings.selectedRooms.size)

        scope.launch {
            val (counts, rooms, rows) = withContext(Dispatchers.IO) {
                val store = MessageStore.get(this@MainActivity)
                Triple(store.counts(), store.roomSummaries().size, store.recent(30))
            }
            val cc = withContext(Dispatchers.IO) { MessageStore.get(this@MainActivity).complaintCounts() }
            complaintStatus.text = "대상 ${cc.eligible}건 · 분류 ${cc.checked - cc.failed} · 민원 ${cc.complaints} · 대기 ${cc.waiting} · 실패 ${cc.failed}" +
                (settings.complaintStatus.takeIf { it.isNotEmpty() }?.let { "\n최근 실행: $it" } ?: "")
            findViewById<Button>(R.id.openComplaints).text = getString(R.string.open_complaints) + if (cc.complaints > 0) " (${cc.complaints})" else ""
            storageSummary.text = "휴대폰 저장: 메시지 ${counts.total}건 · 대화방 ${rooms}개 · 사진 ${counts.photos}장"
            queueStatus.text = "서버 전송: 대기 ${counts.pending} · 완료 ${counts.sent} · 실패 ${counts.failed} · 미전송(휴대폰만) ${counts.local}\n단말 ID: ${settings.deviceId}"
            val fmt = SimpleDateFormat("MM-dd HH:mm:ss", Locale.KOREA)
            recentMessages.text = if (rows.isEmpty()) "아직 수집된 메시지가 없습니다." else rows.joinToString("\n") { r ->
                val mark = when (r.status) {
                    MessageStore.SENT -> "✔"
                    MessageStore.FAILED -> "✖"
                    MessageStore.PENDING -> "…"
                    else -> "•"
                }
                val media = if (r.imagePath != null) " [사진]" else ""
                "$mark ${fmt.format(Date(r.postedAt))} [${r.room}] ${r.sender}: ${Display.preview(r.type, r.text).take(60)}$media" +
                    (r.lastError?.takeIf { r.status == MessageStore.FAILED || r.status == MessageStore.PENDING }?.let { "\n   ↳ $it" } ?: "")
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
