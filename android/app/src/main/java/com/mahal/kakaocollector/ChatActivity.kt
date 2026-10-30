package com.mahal.kakaocollector

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.Gravity
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.TextView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/** 한 대화방의 메시지를 카카오톡처럼 보여 준다. 새 메시지는 몇 초마다 자동으로 추가된다. */
class ChatActivity : Activity() {

    private val scope = MainScope()
    private lateinit var room: String
    private var focusId: Long? = null
    private var messages: List<MessageStore.Row> = emptyList()
    private var hasOlder = false

    private val list by lazy { findViewById<ListView>(R.id.messages) }
    private val olderButton by lazy {
        Button(this).apply {
            text = getString(R.string.load_older)
            setOnClickListener { scope.launch { loadOlder() } }
        }
    }
    private val header by lazy { LinearLayout(this).apply { gravity = Gravity.CENTER; addView(olderButton) } }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_chat)
        room = intent.getStringExtra(EXTRA_ROOM) ?: return finish()
        focusId = intent.getLongExtra(EXTRA_FOCUS_ID, -1).takeIf { it > 0 }
        title = room
        list.addHeaderView(header, null, false)
        list.adapter = adapter
        scope.launch {
            loadInitial()
            while (isActive) {
                delay(POLL_MS)
                loadNewer()
            }
        }
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, MENU_MEMBERS, 0, getString(R.string.members_title)).setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        if (item.itemId != MENU_MEMBERS) return super.onOptionsItemSelected(item)
        startActivity(Intent(this, MembersActivity::class.java).putExtra(MembersActivity.EXTRA_ROOM, room))
        return true
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private suspend fun loadInitial() {
        val store = MessageStore.get(this)
        val focus = focusId
        if (focus != null) {
            val (older, newer) = withContext(Dispatchers.IO) {
                store.roomMessages(room, beforeId = focus + 1, limit = PAGE) to store.roomMessages(room, afterId = focus, limit = PAGE)
            }
            messages = older + newer
            hasOlder = older.size == PAGE
        } else {
            messages = withContext(Dispatchers.IO) { store.roomMessages(room, limit = PAGE) }
            hasOlder = messages.size == PAGE
        }
        render()
        val target = focus?.let { id -> messages.indexOfFirst { it.id == id } }?.takeIf { it >= 0 } ?: messages.lastIndex
        list.setSelection(target + list.headerViewsCount)
    }

    private suspend fun loadOlder() {
        val first = messages.firstOrNull() ?: return
        val older = withContext(Dispatchers.IO) { MessageStore.get(this@ChatActivity).roomMessages(room, beforeId = first.id, limit = PAGE) }
        val top = list.getChildAt(1)?.top ?: 0
        val firstVisible = list.firstVisiblePosition
        messages = older + messages
        hasOlder = older.size == PAGE
        render()
        list.setSelectionFromTop(firstVisible + older.size, top) // 읽던 위치 유지
    }

    private suspend fun loadNewer() {
        val lastId = messages.lastOrNull()?.id ?: 0L
        val newer = withContext(Dispatchers.IO) { MessageStore.get(this@ChatActivity).roomMessages(room, afterId = lastId, limit = PAGE) }
        if (newer.isEmpty()) return
        val atBottom = list.lastVisiblePosition >= list.count - 2
        messages = messages + newer
        render()
        if (atBottom) list.setSelection(list.count - 1)
    }

    private fun render() {
        olderButton.visibility = if (hasOlder) View.VISIBLE else View.GONE
        adapter.notifyDataSetChanged()
    }

    private fun openPhoto(m: MessageStore.Row) {
        val path = m.imagePath ?: return
        startActivity(
            Intent(this, PhotoActivity::class.java)
                .putExtra(PhotoActivity.EXTRA_PATH, path)
                .putExtra(PhotoActivity.EXTRA_CAPTION, "${m.sender} · ${Display.dateTime(m.postedAt)}")
        )
    }

    private val adapter = object : BaseAdapter() {
        override fun getCount() = messages.size
        override fun getItem(position: Int) = messages[position]
        override fun getItemId(position: Int) = messages[position].id

        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val v = convertView ?: layoutInflater.inflate(R.layout.item_message, parent, false)
            val m = messages[position]
            val prev = messages.getOrNull(position - 1)
            val next = messages.getOrNull(position + 1)
            val self = m.sender == KakaoParser.SELF_SENDER
            val sameMinute = { a: MessageStore.Row?, b: MessageStore.Row? ->
                a != null && b != null && a.sender == b.sender && a.postedAt / 60_000 == b.postedAt / 60_000
            }
            val newDay = prev == null || Display.dayKey(prev.postedAt) != Display.dayKey(m.postedAt)

            v.findViewById<TextView>(R.id.daySep).apply {
                visibility = if (newDay) View.VISIBLE else View.GONE
                text = Display.day(m.postedAt)
            }
            v.findViewById<LinearLayout>(R.id.row).gravity = Gravity.BOTTOM or if (self) Gravity.END else Gravity.START
            v.findViewById<LinearLayout>(R.id.column).gravity = if (self) Gravity.END else Gravity.START

            v.findViewById<TextView>(R.id.sender).apply {
                visibility = if (!self && m.isGroup && (newDay || !sameMinute(prev, m))) View.VISIBLE else View.GONE
                text = m.sender
            }

            val showTime = !sameMinute(m, next)
            v.findViewById<TextView>(R.id.timeLeft).apply {
                visibility = if (self && showTime) View.VISIBLE else View.GONE
                text = Display.time(m.postedAt)
            }
            v.findViewById<TextView>(R.id.timeRight).apply {
                visibility = if (!self && showTime) View.VISIBLE else View.GONE
                text = Display.time(m.postedAt)
            }

            val hasImage = m.imagePath != null && File(m.imagePath).exists()
            val bubble = v.findViewById<TextView>(R.id.bubble)
            val bubbleText = when {
                hasImage && Display.isPlaceholder(m.type, m.text) -> null
                m.type == "photo" && Display.isPlaceholder(m.type, m.text) -> "📷 사진 (미리보기 없음)"
                Display.isPlaceholder(m.type, m.text) -> Display.label(m.type) ?: m.text
                else -> Display.label(m.type)?.takeIf { m.type == "file" }?.let { "$it ${m.text}" } ?: m.text
            }
            bubble.visibility = if (bubbleText == null) View.GONE else View.VISIBLE
            bubble.text = bubbleText
            bubble.setBackgroundResource(
                when {
                    m.id == focusId -> R.drawable.bubble_highlight
                    self -> R.drawable.bubble_self
                    else -> R.drawable.bubble_other
                }
            )

            val photo = v.findViewById<ImageView>(R.id.photo)
            if (hasImage) {
                photo.visibility = View.VISIBLE
                ImageLoader.load(photo, m.imagePath!!, THUMB_PX)
                photo.setOnClickListener { openPhoto(m) }
            } else {
                photo.visibility = View.GONE
                photo.setOnClickListener(null)
            }
            return v
        }
    }

    companion object {
        const val EXTRA_ROOM = "room"
        const val EXTRA_FOCUS_ID = "focus_id"
        private const val PAGE = 200
        private const val POLL_MS = 4000L
        private const val THUMB_PX = 600
        private const val MENU_MEMBERS = 1
    }
}
