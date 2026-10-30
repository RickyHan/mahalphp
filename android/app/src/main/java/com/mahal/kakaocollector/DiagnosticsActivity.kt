package com.mahal.kakaocollector

import androidx.appcompat.app.AppCompatActivity
import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 최근 카카오톡 알림의 원본 구조와 해석 결과. 방 이름·보낸 사람이 잘못 나올 때 원인 확인용. */
class DiagnosticsActivity : AppCompatActivity() {
    private val scope = MainScope()
    private var report = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.diagnostics_title)
        val pad = (12 * resources.displayMetrics.density).toInt()
        val body = TextView(this).apply {
            typeface = Typeface.MONOSPACE
            textSize = 11f
            setTextIsSelectable(true)
            setPadding(pad, pad, pad, pad)
            text = "불러오는 중…"
        }
        val share = Button(this).apply {
            text = getString(R.string.diagnostics_share)
            setOnClickListener {
                startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, report), "진단 정보 보내기"))
            }
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(share)
            addView(ScrollView(this@DiagnosticsActivity).apply { addView(body) }, LinearLayout.LayoutParams(-1, 0, 1f))
        })

        scope.launch {
            val logs = withContext(Dispatchers.IO) { MessageStore.get(this@DiagnosticsActivity).notificationLogs() }
            val fmt = SimpleDateFormat("MM-dd HH:mm:ss", Locale.KOREA)
            report = if (logs.isEmpty()) {
                getString(R.string.diagnostics_empty)
            } else {
                "카톡 수집기 ${BuildConfig.VERSION_NAME} · Android ${android.os.Build.VERSION.RELEASE} · 최근 알림 ${logs.size}건\n\n" +
                    logs.joinToString("\n") { (time, dump) -> "■ ${fmt.format(Date(time))}\n$dump" }
            }
            body.text = report
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}
