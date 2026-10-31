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
