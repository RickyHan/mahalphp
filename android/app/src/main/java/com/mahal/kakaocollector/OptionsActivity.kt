package com.mahal.kakaocollector

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.View
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.google.android.material.materialswitch.MaterialSwitch

/** 수집 옵션(사진 · 알림줄 상태)과 권한(알림 접근 · 배터리 최적화 예외) */
class OptionsActivity : AppCompatActivity() {
    private lateinit var settings: AppSettings

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_options)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        settings = AppSettings(this)

        findViewById<MaterialSwitch>(R.id.captureImages).apply {
            isChecked = settings.captureImages
            setOnCheckedChangeListener { _, on -> settings.captureImages = on }
        }
        findViewById<MaterialSwitch>(R.id.limitNotifications).apply {
            isChecked = settings.limitKakaoNotifications
            setOnCheckedChangeListener { _, on -> settings.limitKakaoNotifications = on }
        }
        findViewById<MaterialSwitch>(R.id.removeOthers).apply {
            isChecked = settings.removeOtherNotifications
            setOnCheckedChangeListener { _, on ->
                settings.removeOtherNotifications = on
                if (on) KakaoNotificationListener.trimNow()
            }
        }
        findViewById<View>(R.id.trimNow).setOnClickListener {
            if (!KakaoNotificationListener.trimNow()) {
                Toast.makeText(this, "알림 접근 권한이 꺼져 있거나 수집 서비스가 실행 중이 아닙니다.", Toast.LENGTH_LONG).show()
            } else {
                Toast.makeText(this, "카톡 알림을 정리합니다…", Toast.LENGTH_SHORT).show()
                it.postDelayed({ renderTrim() }, 1500)
            }
        }
        findViewById<MaterialSwitch>(R.id.statusNotification).apply {
            isChecked = settings.statusNotification
            setOnCheckedChangeListener { _, on ->
                settings.statusNotification = on
                if (on && Build.VERSION.SDK_INT >= 33 &&
                    ContextCompat.checkSelfPermission(this@OptionsActivity, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                ) {
                    ActivityCompat.requestPermissions(this@OptionsActivity, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
                }
                StatusNotifier.update(this@OptionsActivity)
            }
        }
        findViewById<View>(R.id.openListenerSettings).setOnClickListener {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }
        findViewById<View>(R.id.openBatterySettings).setOnClickListener {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
    }

    override fun onResume() {
        super.onResume()
        val granted = NotificationManagerCompat.getEnabledListenerPackages(this).contains(packageName)
        val batteryOk = getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)
        findViewById<TextView>(R.id.checkPermission).text = if (granted) "✅  알림 접근 권한 켜짐" else "⚠️  알림 접근 권한 필요"
        findViewById<TextView>(R.id.checkBattery).text = if (batteryOk) "✅  배터리 최적화 예외" else "⚠️  배터리 최적화 예외 필요"
        renderTrim()
    }

    /** 알림 정리 결과: 지금 떠 있는 카톡 알림 수 · 마지막 정리 시각·지운 수·실패 사유 */
    private fun renderTrim() {
        val count = settings.kakaoNotificationCount
        val last = settings.lastTrimAt
        val time = if (last > 0) java.text.SimpleDateFormat("M/d HH:mm:ss", java.util.Locale.KOREA).format(java.util.Date(last)) else "없음"
        findViewById<TextView>(R.id.trimStatus).text = buildString {
            append("카톡 알림 ").append(if (count >= 0) "${count}개" else "확인 전").append(" (최대 ${NotificationTrim.MAX}개)\n")
            append("마지막 정리 ").append(time)
            if (last > 0) append(" · ${settings.lastTrimCount}개 지움")
            settings.lastTrimError?.let { append("\n⚠️ ").append(it) }
            if (settings.removeOtherNotifications) append("\n다른 앱 알림 지금까지 ${settings.otherRemoved}개 지움")
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        StatusNotifier.update(this)
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }
}
