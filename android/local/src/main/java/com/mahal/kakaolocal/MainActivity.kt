package com.mahal.kakaolocal

import android.Manifest
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.View
import android.widget.GridLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 홈: 저장 상태 + 수집 시작/중지 + 바둑판 메뉴 (대화·사진 보기, 수집할 방 등). 비밀번호를 푼 뒤에만 열린다. */
class MainActivity : AppCompatActivity() {

    private val scope = MainScope()
    private var ticker: Job? = null
    private lateinit var settings: AppSettings
    private val tiles = mutableMapOf<Tile, View>()

    /** 바둑판 메뉴 항목: 아이콘 · 제목 · 그라데이션 색 */
    enum class Tile(val icon: String, val title: String, val colorA: Int, val colorB: Int) {
        VIEW("💬", "대화 · 사진 보기", R.color.tile_blue_a, R.color.tile_blue_b),
        ROOMS("✅", "수집할 방", R.color.tile_green_a, R.color.tile_green_b),
        OPTIONS("⚙️", "수집 옵션", R.color.tile_teal_a, R.color.tile_teal_b),
        PERMISSION("🛡️", "권한 설정", R.color.tile_amber_a, R.color.tile_amber_b),
        DIAGNOSTICS("🩺", "알림 진단", R.color.tile_slate_a, R.color.tile_slate_b),
        LOCK("🔒", "지금 잠그기", R.color.tile_purple_a, R.color.tile_purple_b),
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        settings = AppSettings(this)

        findViewById<TextView>(R.id.versionText).text = "v${BuildConfig.VERSION_NAME} · 카톡 대화·사진을 이 휴대폰에만 저장"
        findViewById<TextView>(R.id.deviceId).text = "서버로 보내지 않습니다 · 다른 앱에 다녀오면 다시 잠깁니다"
        findViewById<MaterialButton>(R.id.captureToggle).setOnClickListener { onToggleCapture() }
        buildGrid()

        if (settings.statusNotification) requestNotificationPermission()
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

    // ---- 수집 시작 / 중지 ---------------------------------------------------------------

    private fun onToggleCapture() {
        val running = CaptureControl.toggle(this)
        Toast.makeText(this, if (running) "수집을 시작했습니다." else "수집을 중지했습니다. 다시 시작할 때까지 카톡을 저장하지 않습니다.", Toast.LENGTH_SHORT).show()
        scope.launch { refresh() }
    }

    private fun renderToggle(running: Boolean) {
        findViewById<MaterialButton>(R.id.captureToggle).apply {
            text = getString(if (running) R.string.capture_stop else R.string.capture_start)
            setIconResource(if (running) R.drawable.ic_stop else R.drawable.ic_play)
            backgroundTintList = ColorStateList.valueOf(ContextCompat.getColor(context, if (running) R.color.action_stop else R.color.action_start))
            setTextColor(ContextCompat.getColor(context, android.R.color.white))
            iconTint = ColorStateList.valueOf(ContextCompat.getColor(context, android.R.color.white))
        }
    }

    // ---- 바둑판 메뉴 ---------------------------------------------------------------------

    private fun buildGrid() {
        val grid = findViewById<GridLayout>(R.id.menuGrid)
        val gap = dp(6)
        for (tile in Tile.entries) {
            val view = layoutInflater.inflate(R.layout.view_tile, grid, false)
            view.findViewById<TextView>(R.id.tileIcon).text = tile.icon
            view.findViewById<TextView>(R.id.tileTitle).text = tile.title
            view.contentDescription = tile.title
            paint(view, tile.colorA, tile.colorB)
            view.setOnClickListener { onTile(tile) }
            grid.addView(view, GridLayout.LayoutParams(
                GridLayout.spec(GridLayout.UNDEFINED), GridLayout.spec(GridLayout.UNDEFINED, 1f),
            ).apply {
                width = 0
                setMargins(gap, gap, gap, gap)
            })
            tiles[tile] = view
        }
    }

    private fun paint(view: View, colorA: Int, colorB: Int) {
        view.background = GradientDrawable(
            GradientDrawable.Orientation.TL_BR,
            intArrayOf(ContextCompat.getColor(this, colorA), ContextCompat.getColor(this, colorB)),
        ).apply { cornerRadius = dp(22).toFloat() }
        view.clipToOutline = true
        view.elevation = dp(2).toFloat()
    }

    private fun onTile(tile: Tile) {
        when (tile) {
            Tile.VIEW -> startActivity(Intent(this, ChatRoomsActivity::class.java))
            Tile.ROOMS -> startActivity(Intent(this, RoomFilterActivity::class.java))
            Tile.OPTIONS, Tile.PERMISSION -> {
                // 알림 접근이 꺼져 있으면 바로 시스템 설정으로, 아니면 권한·옵션 화면으로
                if (tile == Tile.PERMISSION && !listenerGranted()) {
                    startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                } else {
                    startActivity(Intent(this, OptionsActivity::class.java))
                }
            }
            Tile.DIAGNOSTICS -> startActivity(Intent(this, DiagnosticsActivity::class.java))
            Tile.LOCK -> {
                AppLock.lock()
                startActivity(Intent(this, LockActivity::class.java))
                finish()
            }
        }
    }

    private fun setTile(tile: Tile, sub: String, badge: String? = null, alert: Boolean = false) {
        val view = tiles[tile] ?: return
        view.findViewById<TextView>(R.id.tileSub).text = sub
        view.findViewById<TextView>(R.id.tileBadge).apply {
            text = badge.orEmpty()
            visibility = if (badge.isNullOrEmpty()) View.GONE else View.VISIBLE
        }
        if (alert) paint(view, R.color.tile_red_a, R.color.tile_red_b) else paint(view, tile.colorA, tile.colorB)
    }

    // ---- 상태 갱신 ----------------------------------------------------------------------

    private suspend fun refresh() {
        val (status, counts) = withContext(Dispatchers.IO) {
            StatusNotifier.evaluate(this@MainActivity) to MessageStore.get(this@MainActivity).counts()
        }
        renderStatus(status)
        renderToggle(settings.captureEnabled)

        val granted = listenerGranted()
        val batteryOk = getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)
        setTile(Tile.VIEW, "저장 ${counts.total}건 · 사진 ${counts.photos}장")
        setTile(Tile.ROOMS, RoomFilterActivity.summary(settings.roomFilterMode, settings.selectedRooms.size))
        setTile(
            Tile.OPTIONS,
            listOf(if (settings.captureImages) "사진 켬" else "사진 끔", if (settings.statusNotification) "알림줄 켬" else "알림줄 끔").joinToString(" · "),
        )
        setTile(
            Tile.PERMISSION,
            "알림 ${if (granted) "✓" else "✗"} · 배터리 ${if (batteryOk) "✓" else "✗"}",
            badge = if (!granted) "필요" else null,
            alert = !granted,
        )
        setTile(Tile.DIAGNOSTICS, "방 이름이 이상할 때")
        setTile(Tile.LOCK, "비밀번호로 다시 열기")
        findViewById<View>(R.id.restrictedHint).visibility = if (granted) View.GONE else View.VISIBLE
        StatusNotifier.update(this)
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
    }

    // ---- 기타 ---------------------------------------------------------------------------

    private fun listenerGranted() = NotificationManagerCompat.getEnabledListenerPackages(this).contains(packageName)

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
        if (!listenerGranted()) return
        runCatching {
            android.service.notification.NotificationListenerService.requestRebind(
                ComponentName(this, KakaoNotificationListener::class.java)
            )
        }
    }
}
