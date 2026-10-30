package com.mahal.kakaocollector

import android.app.Activity
import android.content.Intent
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.Editable
import android.text.SpannableString
import android.text.Spanned
import android.text.TextWatcher
import android.text.style.BackgroundColorSpan
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.EditText
import android.widget.ListView
import android.widget.TextView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/** 수집한 대화방 목록. 검색어를 입력하면 전체 메시지 검색 결과로 바뀐다. */
class RoomsActivity : Activity() {

    private val scope = MainScope()
    private var searchJob: Job? = null
    private var rooms: List<MessageStore.RoomSummary> = emptyList()
    private var results: List<MessageStore.Row> = emptyList()
    private var query = ""

    private val list by lazy { findViewById<ListView>(R.id.list) }
    private val empty by lazy { findViewById<TextView>(R.id.empty) }
    private val listTitle by lazy { findViewById<TextView>(R.id.listTitle) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_rooms)
        title = getString(R.string.rooms_title)
        list.adapter = adapter
        list.setOnItemClickListener { _, _, position, _ ->
            val intent = Intent(this, ChatActivity::class.java)
            if (query.isEmpty()) {
                intent.putExtra(ChatActivity.EXTRA_ROOM, rooms[position].room)
            } else {
                intent.putExtra(ChatActivity.EXTRA_ROOM, results[position].room)
                intent.putExtra(ChatActivity.EXTRA_FOCUS_ID, results[position].id)
            }
            startActivity(intent)
        }
        findViewById<EditText>(R.id.searchInput).addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
            override fun afterTextChanged(s: Editable?) {
                searchJob?.cancel()
                searchJob = scope.launch {
                    delay(250)
                    query = s?.toString()?.trim().orEmpty()
                    reload()
                }
            }
        })
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
        val q = query
        if (q.isEmpty()) {
            rooms = withContext(Dispatchers.IO) { store.roomSummaries() }
            listTitle.text = getString(R.string.rooms_count, rooms.size)
        } else {
            results = withContext(Dispatchers.IO) { store.search(q) }
            listTitle.text = getString(R.string.search_count, results.size)
        }
        val isEmpty = if (q.isEmpty()) rooms.isEmpty() else results.isEmpty()
        empty.text = getString(if (q.isEmpty()) R.string.rooms_empty else R.string.search_empty)
        empty.visibility = if (isEmpty) View.VISIBLE else View.GONE
        list.visibility = if (isEmpty) View.GONE else View.VISIBLE
        adapter.notifyDataSetChanged()
    }

    private val adapter = object : BaseAdapter() {
        override fun getCount() = if (query.isEmpty()) rooms.size else results.size
        override fun getItem(position: Int): Any = if (query.isEmpty()) rooms[position] else results[position]
        override fun getItemId(position: Int) = position.toLong()
        override fun getViewTypeCount() = 2
        override fun getItemViewType(position: Int) = if (query.isEmpty()) 0 else 1

        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View =
            if (query.isEmpty()) roomView(rooms[position], convertView, parent) else resultView(results[position], convertView, parent)
    }

    private fun roomView(r: MessageStore.RoomSummary, convertView: View?, parent: ViewGroup): View {
        val v = convertView ?: layoutInflater.inflate(R.layout.item_room, parent, false)
        val avatar = v.findViewById<TextView>(R.id.avatar)
        avatar.text = if (r.isGroup) "👥" else r.room.take(1)
        (avatar.background.mutate() as GradientDrawable).setColor(avatarColor(r.room))
        v.findViewById<TextView>(R.id.roomName).text = "${r.room}  ${r.messageCount}"
        v.findViewById<TextView>(R.id.roomTime).text = shortTime(r.lastPostedAt)
        val prefix = if (r.isGroup) "${r.lastSender}: " else ""
        v.findViewById<TextView>(R.id.roomLast).text = prefix + Display.preview(r.lastType, r.lastText)
        return v
    }

    private fun resultView(m: MessageStore.Row, convertView: View?, parent: ViewGroup): View {
        val v = convertView ?: layoutInflater.inflate(R.layout.item_search, parent, false)
        v.findViewById<TextView>(R.id.resultMeta).text = "${m.room} · ${m.sender} · ${Display.dateTime(m.postedAt)}"
        v.findViewById<TextView>(R.id.resultText).text = highlight(Display.preview(m.type, m.text), query)
        return v
    }

    private fun highlight(text: String, q: String): CharSequence {
        val span = SpannableString(text)
        var i = text.indexOf(q, ignoreCase = true)
        while (q.isNotEmpty() && i >= 0) {
            span.setSpan(BackgroundColorSpan(0x66FFC107), i, i + q.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            i = text.indexOf(q, i + q.length, ignoreCase = true)
        }
        return span
    }

    private fun shortTime(time: Long): String {
        val now = Calendar.getInstance()
        val then = Calendar.getInstance().apply { timeInMillis = time }
        val sameDay = now.get(Calendar.YEAR) == then.get(Calendar.YEAR) && now.get(Calendar.DAY_OF_YEAR) == then.get(Calendar.DAY_OF_YEAR)
        return SimpleDateFormat(if (sameDay) "HH:mm" else "MM/dd", Locale.KOREA).format(Date(time))
    }

    private fun avatarColor(name: String): Int {
        val hue = (name.hashCode().toLong() and 0xffffffffL) % 360
        return android.graphics.Color.HSVToColor(floatArrayOf(hue.toFloat(), 0.45f, 0.72f))
    }
}
