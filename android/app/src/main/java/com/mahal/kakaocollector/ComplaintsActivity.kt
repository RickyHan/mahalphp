package com.mahal.kakaocollector

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.BaseAdapter
import android.widget.ListView
import android.widget.Spinner
import android.widget.TextView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Gemini 가 입주민 민원으로 분류한 메시지를 '문제' 단위로 모아 본다.
 * 같은 사람·같은 방·같은 분류로 짧은 시간에 여러 번 보낸 민원은 한 줄로 묶이며,
 * 누르면 관련 메시지 전체(묶음 상세), 1건이면 원래 대화 위치로 이동.
 */
class ComplaintsActivity : Activity() {

    private val scope = MainScope()
    private var items: List<ComplaintGrouping.Group> = emptyList()
    private var category: String? = null

    private val list by lazy { findViewById<ListView>(R.id.complaintList) }
    private val empty by lazy { findViewById<TextView>(R.id.complaintEmpty) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_complaints)
        title = getString(R.string.complaints_title)

        val filter = findViewById<Spinner>(R.id.categoryFilter)
        val options = listOf("전체 분류") + Complaints.CATEGORIES
        filter.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, options)
        filter.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                category = if (position == 0) null else options[position]
                scope.launch { reload() }
            }
            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
        }

        list.adapter = adapter
        list.setOnItemClickListener { _, _, position, _ ->
            val g = items[position]
            if (g.count == 1) {
                startActivity(
                    Intent(this, ChatActivity::class.java)
                        .putExtra(ChatActivity.EXTRA_ROOM, g.room)
                        .putExtra(ChatActivity.EXTRA_FOCUS_ID, g.first.row.id)
                )
            } else {
                startActivity(Intent(this, ComplaintGroupActivity::class.java).putExtra(ComplaintGroupActivity.EXTRA_IDS, g.ids.toLongArray()))
            }
        }
    }

    override fun onResume() {
        super.onResume()
        scope.launch { reload() }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private suspend fun reload() {
        val store = MessageStore.get(this)
        val (rows, counts) = withContext(Dispatchers.IO) { store.complaints(category) to store.complaintCounts() }
        items = ComplaintGrouping.group(rows)
        val settings = AppSettings(this)
        findViewById<TextView>(R.id.complaintSummary).text =
            "문제 ${items.size}개 (민원 메시지 ${rows.size}건) · 분류 완료 ${counts.checked - counts.failed}/${counts.eligible}건" +
                (if (counts.waiting > 0) " · 대기 ${counts.waiting}" else "") +
                (if (counts.failed > 0) " · 실패 ${counts.failed}" else "") +
                (settings.complaintStatus.takeIf { it.isNotEmpty() }?.let { "\n최근 실행: $it" } ?: "")
        empty.text = when {
            !settings.shouldClassify -> getString(R.string.complaints_disabled)
            category != null -> "‘$category’ 민원이 없습니다."
            else -> getString(R.string.complaints_empty)
        }
        empty.visibility = if (items.isEmpty()) View.VISIBLE else View.GONE
        list.visibility = if (items.isEmpty()) View.GONE else View.VISIBLE
        adapter.notifyDataSetChanged()
    }

    private fun replyLabel(c: MessageStore.Complaint): String = when (c.replyStatus) {
        1 -> "\n✅ 접수 안내 보냄"
        -1 -> "\n⚠️ 안내 실패: ${c.replyNote.orEmpty()}"
        3 -> "\n· 안내 생략 (오래된 메시지)"
        else -> ""
    }

    private val adapter = object : BaseAdapter() {
        override fun getCount() = items.size
        override fun getItem(position: Int) = items[position]
        override fun getItemId(position: Int) = items[position].first.row.id

        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val v = convertView ?: layoutInflater.inflate(R.layout.item_complaint, parent, false)
            val g = items[position]
            v.findViewById<TextView>(R.id.category).text = g.category
            v.findViewById<TextView>(R.id.summary).text = g.summary
            v.findViewById<TextView>(R.id.count).apply {
                visibility = if (g.count > 1) View.VISIBLE else View.GONE
                text = "${g.count}건"
            }
            // 묶음이면 가장 최근 메시지를 미리 보여 준다
            v.findViewById<TextView>(R.id.original).text = g.latest.row.text
            val period = if (g.count > 1) "${Display.dateTime(g.startAt)} ~ ${Display.time(g.endAt)}" else Display.dateTime(g.startAt)
            v.findViewById<TextView>(R.id.meta).text = "${g.room} · ${g.sender} · $period" + replyLabel(g.first)
            return v
        }
    }
}
