package com.mahal.kakaocollector

import android.app.Activity
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.BaseAdapter
import android.widget.ListView
import android.widget.RadioGroup
import android.widget.Spinner
import android.widget.TextView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 방별 입장·퇴장·대화명 변경 이력과 참여자(발언 기준) 목록 */
class MembersActivity : Activity() {

    private val scope = MainScope()
    private var rooms: List<String> = emptyList()
    private var room: String? = null
    private var type: MemberEvents.Type? = null
    private var showMembers = false
    private var lines: List<Pair<String, String>> = emptyList()

    private val roomFilter by lazy { findViewById<Spinner>(R.id.roomFilter) }
    private val typeFilter by lazy { findViewById<Spinner>(R.id.typeFilter) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_members)
        title = getString(R.string.members_title)
        room = intent.getStringExtra(EXTRA_ROOM)

        val types = listOf<MemberEvents.Type?>(null) + MemberEvents.Type.entries
        typeFilter.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, types.map { it?.label ?: "전체 이력" })
        typeFilter.onItemSelectedListener = selected { type = types[it]; reload() }

        findViewById<RadioGroup>(R.id.viewMode).setOnCheckedChangeListener { _, id ->
            showMembers = id == R.id.modeMembers
            typeFilter.visibility = if (showMembers) View.GONE else View.VISIBLE
            reload()
        }
        findViewById<ListView>(R.id.membersList).adapter = adapter

        scope.launch {
            rooms = withContext(Dispatchers.IO) { MessageStore.get(this@MembersActivity).memberRooms() }
            room?.let { if (it !in rooms) rooms = listOf(it) + rooms }
            val options = listOf("전체 방") + rooms
            roomFilter.adapter = ArrayAdapter(this@MembersActivity, android.R.layout.simple_spinner_dropdown_item, options)
            roomFilter.setSelection(room?.let { rooms.indexOf(it) + 1 } ?: 0)
            roomFilter.onItemSelectedListener = selected { room = if (it == 0) null else rooms[it - 1]; reload() }
            reload()
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun selected(block: (Int) -> Unit) = object : AdapterView.OnItemSelectedListener {
        override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) = block(position)
        override fun onNothingSelected(parent: AdapterView<*>?) = Unit
    }

    private fun reload() {
        scope.launch {
            val store = MessageStore.get(this@MembersActivity)
            val summary = findViewById<TextView>(R.id.membersSummary)
            if (showMembers) {
                val r = room
                if (r == null) {
                    lines = emptyList()
                    summary.text = "참여자 목록은 방을 하나 선택하십시오."
                } else {
                    val members = withContext(Dispatchers.IO) { store.members(r) }
                    lines = members.map { m ->
                        m.name to "첫 발언 ${Display.dateTime(m.firstSeen)} · 최근 ${Display.dateTime(m.lastSeen)} · ${m.messageCount}건" +
                            if (m.keyed) "" else " · 대화명 변경 추적 불가"
                    }
                    summary.text = "발언한 참여자 ${members.size}명 (카톡 알림으로 확인된 사람만)"
                }
            } else {
                val events = withContext(Dispatchers.IO) { store.memberEvents(room, type) }
                lines = events.map { e ->
                    val title = when (e.type) {
                        MemberEvents.Type.RENAME -> "✏️ ${e.oldName} → ${e.member}"
                        MemberEvents.Type.JOIN -> "➕ ${e.member} 입장"
                        MemberEvents.Type.LEAVE -> "➖ ${e.member} 퇴장"
                        MemberEvents.Type.KICK -> "⛔ ${e.member} 내보냄" + (e.actor?.let { " (by $it)" } ?: "")
                        MemberEvents.Type.INVITE -> "📨 ${e.member} 초대" + (e.actor?.let { " (by $it)" } ?: "")
                        MemberEvents.Type.FIRST_SEEN -> "💬 ${e.member} 첫 발언"
                    }
                    title to (if (room == null) "${e.room} · " else "") + Display.dateTime(e.at)
                }
                summary.text = "이력 ${events.size}건"
            }
            val empty = findViewById<TextView>(R.id.membersEmpty)
            empty.visibility = if (lines.isEmpty()) View.VISIBLE else View.GONE
            findViewById<ListView>(R.id.membersList).visibility = if (lines.isEmpty()) View.GONE else View.VISIBLE
            adapter.notifyDataSetChanged()
        }
    }

    private val adapter = object : BaseAdapter() {
        override fun getCount() = lines.size
        override fun getItem(position: Int) = lines[position]
        override fun getItemId(position: Int) = position.toLong()
        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val v = convertView ?: layoutInflater.inflate(android.R.layout.simple_list_item_2, parent, false)
            v.findViewById<TextView>(android.R.id.text1).text = lines[position].first
            v.findViewById<TextView>(android.R.id.text2).text = lines[position].second
            return v
        }
    }

    companion object {
        const val EXTRA_ROOM = "room"
    }
}
