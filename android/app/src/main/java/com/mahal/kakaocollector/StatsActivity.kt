package com.mahal.kakaocollector

import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 민원 집계 화면. 단위는 '문제'(같은 사람·방·분류의 연속 민원 묶음).
 * 핵심 수치 카드 + 분류·방·제기자별 순위 막대 + 일별·시간대별 막대. 막대 색은 한 가지, 글자는 기본 글자색.
 */
class StatsActivity : Activity() {

    private val scope = MainScope()
    private var period = ComplaintStats.Period.WEEK
    private lateinit var content: LinearLayout

    private val dp get() = resources.displayMetrics.density
    private fun px(v: Int) = (v * dp).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.stats_title)

        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setBackgroundColor(SURFACE) }
        // 기간 선택: 차트 위 한 줄
        val periods = RadioGroup(this).apply {
            orientation = RadioGroup.HORIZONTAL
            setPadding(px(12), px(8), px(12), 0)
            ComplaintStats.Period.entries.forEachIndexed { i, p ->
                addView(RadioButton(this@StatsActivity).apply {
                    id = 1000 + i; text = p.label; setTextColor(TEXT_PRIMARY); isChecked = p == period
                }, RadioGroup.LayoutParams(0, -2, 1f))
            }
            setOnCheckedChangeListener { _, checked -> period = ComplaintStats.Period.entries[checked - 1000]; reload() }
        }
        root.addView(periods)
        content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(px(16), px(8), px(16), px(24)) }
        root.addView(ScrollView(this).apply { addView(content) }, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
        reload()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun reload() {
        scope.launch {
            val stats = withContext(Dispatchers.IO) {
                ComplaintStats.compute(ComplaintGrouping.group(MessageStore.get(this@StatsActivity).complaints()), period, System.currentTimeMillis())
            }
            render(stats)
        }
    }

    private fun render(s: ComplaintStats.Stats) {
        content.removeAllViews()
        if (s.issues == 0 && s.daily.all { it.second == 0 }) {
            content.addView(text("${period.label} 기간에 접수된 민원이 없습니다.", 15f, TEXT_SECONDARY).apply { setPadding(0, px(32), 0, 0); gravity = Gravity.CENTER })
            return
        }

        // 핵심 수치: 문제 수를 가장 크게
        content.addView(text("${period.label} 접수된 문제", 13f, TEXT_SECONDARY))
        content.addView(text("${s.issues}건", 44f, TEXT_PRIMARY, bold = true))
        content.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            addView(tile("민원 메시지", "${s.messages}"), LinearLayout.LayoutParams(0, -2, 1f))
            addView(tile("반복 민원", "${s.repeatedIssues}"), LinearLayout.LayoutParams(0, -2, 1f))
            addView(tile("안내 보냄", "${s.replySent}"), LinearLayout.LayoutParams(0, -2, 1f))
            addView(tile("안내 실패", "${s.replyFailed}"), LinearLayout.LayoutParams(0, -2, 1f))
        })

        section("분류별 문제")
        rankedBars(s.byCategory, s.issues)

        section(if (period == ComplaintStats.Period.ALL) "일별 추이 (최근 30일)" else "일별 추이")
        columns(s.daily.map { it.first to it.second }, unit = "건")

        section("시간대별 (문제 시작 시각)")
        columns((0 until 24).map { "${it}시" to s.hourly[it] }, unit = "건", labelEvery = 6)

        section("방별 문제 TOP 10")
        rankedBars(s.byRoom, s.issues)

        section("민원 제기자 TOP 10")
        rankedBars(s.bySender, s.issues)

        if (s.replySkipped > 0) {
            content.addView(text("※ 수신 30분이 지나 안내를 생략한 문제 ${s.replySkipped}건", 12f, TEXT_SECONDARY).apply { setPadding(0, px(16), 0, 0) })
        }
    }

    // ---- 구성 요소 -------------------------------------------------------------------------

    private fun text(value: String, size: Float, color: Int, bold: Boolean = false) = TextView(this).apply {
        text = value; textSize = size; setTextColor(color)
        if (bold) typeface = Typeface.DEFAULT_BOLD
    }

    private fun section(title: String) {
        content.addView(text(title, 15f, TEXT_PRIMARY, bold = true).apply { setPadding(0, px(24), 0, px(8)) })
    }

    private fun tile(label: String, value: String) = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(0, px(8), px(8), px(4))
        addView(text(value, 22f, TEXT_PRIMARY, bold = true))
        addView(text(label, 12f, TEXT_SECONDARY))
    }

    private fun barDrawable(radiusSide: String) = GradientDrawable().apply {
        setColor(BAR)
        val r = 4 * dp
        // 값 쪽 끝만 둥글게 (기준선 쪽은 각지게)
        cornerRadii = if (radiusSide == "end") floatArrayOf(0f, 0f, r, r, r, r, 0f, 0f) else floatArrayOf(r, r, r, r, 0f, 0f, 0f, 0f)
    }

    /** 순위 가로 막대: 이름 · 막대 · 건수(비율) */
    private fun rankedBars(rows: List<Pair<String, Int>>, total: Int) {
        if (rows.isEmpty()) {
            content.addView(text("데이터 없음", 13f, TEXT_SECONDARY)); return
        }
        val max = rows.maxOf { it.second }.coerceAtLeast(1)
        rows.forEach { (name, v) ->
            content.addView(LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(0, px(3), 0, px(3))
                addView(text(name, 13f, TEXT_PRIMARY).apply {
                    maxLines = 1; ellipsize = TextUtils.TruncateAt.END
                }, LinearLayout.LayoutParams(px(120), -2))
                addView(LinearLayout(this@StatsActivity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    addView(View(this@StatsActivity).apply { background = barDrawable("end") }, LinearLayout.LayoutParams(0, px(14), v.toFloat()))
                    if (max - v > 0) addView(View(this@StatsActivity), LinearLayout.LayoutParams(0, px(14), (max - v).toFloat()))
                }, LinearLayout.LayoutParams(0, -2, 1f))
                val pct = if (total > 0) " (${v * 100 / total}%)" else ""
                addView(text("${v}건$pct", 12f, TEXT_SECONDARY).apply { gravity = Gravity.END }, LinearLayout.LayoutParams(px(76), -2))
            })
        }
    }

    /** 세로 막대(시간 축). 막대를 누르면 정확한 값 표시. 축 라벨은 일부만 */
    private fun columns(points: List<Pair<String, Int>>, unit: String, labelEvery: Int = (points.size / 5).coerceAtLeast(1)) {
        val max = points.maxOfOrNull { it.second }?.coerceAtLeast(1) ?: 1
        val height = px(110)
        content.addView(text("최대 ${max}$unit", 11f, TEXT_SECONDARY))
        content.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.BOTTOM
            points.forEach { (label, v) ->
                addView(LinearLayout(this@StatsActivity).apply {
                    orientation = LinearLayout.VERTICAL
                    gravity = Gravity.BOTTOM
                    setPadding(px(1), 0, px(1), 0)                      // 막대 사이 2dp 간격
                    if (v > 0) addView(View(this@StatsActivity).apply { background = barDrawable("top") }, LinearLayout.LayoutParams(-1, (height * v / max).coerceAtLeast(px(2))))
                    setOnClickListener { Toast.makeText(this@StatsActivity, "$label: $v$unit", Toast.LENGTH_SHORT).show() }
                }, LinearLayout.LayoutParams(0, height, 1f))
            }
        })
        content.addView(View(this).apply { setBackgroundColor(GRID) }, LinearLayout.LayoutParams(-1, px(1)))
        // 축 라벨: labelEvery 칸마다 하나, 라벨이 그 칸들의 너비를 차지
        content.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            for (i in points.indices step labelEvery) {
                val span = minOf(labelEvery, points.size - i)
                addView(text(points[i].first, 10f, TEXT_SECONDARY).apply { maxLines = 1 }, LinearLayout.LayoutParams(0, -2, span.toFloat()))
            }
        })
    }

    private companion object {
        // dataviz 기본 팔레트(라이트): 단일 계열 = 1번 파랑, 글자는 텍스트 토큰
        val SURFACE = Color.parseColor("#FCFCFB")
        val TEXT_PRIMARY = Color.parseColor("#0B0B0B")
        val TEXT_SECONDARY = Color.parseColor("#52514E")
        val BAR = Color.parseColor("#2A78D6")
        val GRID = Color.parseColor("#E4E3DF")
    }
}
