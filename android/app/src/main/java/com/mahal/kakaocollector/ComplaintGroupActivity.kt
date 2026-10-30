package com.mahal.kakaocollector

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.TextView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 하나로 묶인 민원(같은 사람·방·분류)의 관련 메시지 전체. 메시지를 누르면 원래 대화 위치로 이동. */
class ComplaintGroupActivity : Activity() {

    private val scope = MainScope()
    private var items: List<MessageStore.Complaint> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val ids = intent.getLongArrayExtra(EXTRA_IDS) ?: return finish()
        val pad = (16 * resources.displayMetrics.density).toInt()
        val header = TextView(this).apply { setPadding(pad, pad, pad, pad / 2); textSize = 13f }
        val list = ListView(this)
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(header)
            addView(list, LinearLayout.LayoutParams(-1, 0, 1f))
        })
        list.adapter = adapter
        list.setOnItemClickListener { _, _, position, _ ->
            val r = items[position].row
            startActivity(Intent(this, ChatActivity::class.java).putExtra(ChatActivity.EXTRA_ROOM, r.room).putExtra(ChatActivity.EXTRA_FOCUS_ID, r.id))
        }

        scope.launch {
            items = withContext(Dispatchers.IO) { MessageStore.get(this@ComplaintGroupActivity).complaintsByIds(ids) }
            val g = items.takeIf { it.isNotEmpty() }?.let { ComplaintGrouping.Group(it) } ?: return@launch finish()
            title = "${g.category} · ${g.count}건"
            header.text = "${g.room} · ${g.sender}\n${Display.dateTime(g.startAt)} ~ ${Display.dateTime(g.endAt)}\n대표 요약: ${g.summary}"
            adapter.notifyDataSetChanged()
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private val adapter = object : BaseAdapter() {
        override fun getCount() = items.size
        override fun getItem(position: Int) = items[position]
        override fun getItemId(position: Int) = items[position].row.id
        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val v = convertView ?: layoutInflater.inflate(R.layout.item_complaint, parent, false)
            val c = items[position]
            v.findViewById<TextView>(R.id.category).text = Display.time(c.row.postedAt)
            v.findViewById<TextView>(R.id.summary).text = c.summary.ifEmpty { "-" }
            v.findViewById<TextView>(R.id.count).visibility = View.GONE
            v.findViewById<TextView>(R.id.original).apply { maxLines = Int.MAX_VALUE; text = c.row.text }
            v.findViewById<TextView>(R.id.meta).text = "${position + 1}번째 · ${Display.dateTime(c.row.postedAt)} · 눌러서 대화에서 보기"
            return v
        }
    }

    companion object {
        const val EXTRA_IDS = "ids"
    }
}
