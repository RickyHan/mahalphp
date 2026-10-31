package com.mahal.kakaocollector

import android.os.Bundle
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
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

/** 휴대폰에 남아 있는 최근 수집 메시지와 전송 상태 (✔ 전송 완료 · ↑ 대기 · ✖ 실패) */
class RecentActivity : AppCompatActivity() {
    private val scope = MainScope()
    private var ticker: Job? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_recent)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        findViewById<TextView>(R.id.uploadNow).setOnClickListener {
            UploadWorker.enqueue(this)
            Toast.makeText(this, "전송을 시작했습니다.", Toast.LENGTH_SHORT).show()
        }
    }

    override fun onResume() {
        super.onResume()
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

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    private suspend fun refresh() {
        val (counts, rows) = withContext(Dispatchers.IO) {
            val store = MessageStore.get(this@RecentActivity)
            store.counts() to store.recent(30)
        }
        findViewById<TextView>(R.id.recentCounts).text = "전송 완료 ${counts.sent} · 대기 ${counts.pending} · 실패 ${counts.failed}"
        val list = findViewById<LinearLayout>(R.id.recentList)
        list.removeAllViews()
        if (rows.isEmpty()) {
            list.addView(line("아직 수집된 메시지가 없습니다.", R.color.text_secondary))
            return
        }
        val fmt = SimpleDateFormat("MM/dd HH:mm", Locale.KOREA)
        for (r in rows) {
            val (mark, color) = when (r.status) {
                MessageStore.SENT -> "✔" to R.color.good
                MessageStore.FAILED -> "✖" to R.color.bad
                else -> "↑" to R.color.warn
            }
            list.addView(LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                setPadding(0, dp(7), 0, dp(7))
                addView(line(mark, color).apply { minWidth = dp(22) })
                addView(LinearLayout(this@RecentActivity).apply {
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
        setTextColor(ContextCompat.getColor(this@RecentActivity, color))
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
}
