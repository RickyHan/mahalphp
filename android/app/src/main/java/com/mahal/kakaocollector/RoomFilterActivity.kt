package com.mahal.kakaocollector

import android.app.Activity
import android.os.Bundle
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.ListView
import android.widget.RadioGroup
import android.widget.TextView
import android.widget.Toast
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 수집할 대화방 선택. 목록은 알림으로 들어온 적 있는 방 + 직접 추가한 방. */
class RoomFilterActivity : Activity() {

    private lateinit var settings: AppSettings
    private val rooms = mutableListOf<String>()
    private val labels = mutableListOf<String>()
    private lateinit var adapter: ArrayAdapter<String>

    private val modeGroup by lazy { findViewById<RadioGroup>(R.id.modeGroup) }
    private val roomList by lazy { findViewById<ListView>(R.id.roomList) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_room_filter)
        title = getString(R.string.room_filter_title)
        settings = AppSettings(this)

        adapter = ArrayAdapter(this, android.R.layout.simple_list_item_multiple_choice, labels)
        roomList.adapter = adapter
        loadRooms(settings.selectedRooms)

        modeGroup.check(
            when (settings.roomFilterMode) {
                RoomFilterMode.ALL -> R.id.modeAll
                RoomFilterMode.ONLY_SELECTED -> R.id.modeOnly
                RoomFilterMode.EXCEPT_SELECTED -> R.id.modeExcept
            }
        )
        modeGroup.setOnCheckedChangeListener { _, _ -> updateListEnabled() }
        updateListEnabled()

        findViewById<Button>(R.id.addRoom).setOnClickListener { addManualRoom() }
        findViewById<Button>(R.id.saveRooms).setOnClickListener { save() }
    }

    private fun loadRooms(checked: Set<String>) {
        val fmt = SimpleDateFormat("MM/dd HH:mm", Locale.KOREA)
        val seen = MessageStore.get(this).seenRooms()
        rooms.clear()
        labels.clear()
        for (r in seen) {
            rooms += r.room
            labels += "${r.room}\n  ${if (r.isGroup) "단톡방" else "1:1"} · 최근 알림 ${fmt.format(Date(r.lastSeen))}"
        }
        // 아직 알림이 오지 않았지만 직접 추가해 둔 방
        for (name in checked.sorted()) {
            if (rooms.none { RoomFilter.normalize(it) == name }) {
                rooms += name
                labels += "$name\n  직접 추가 · 아직 알림 없음"
            }
        }
        adapter.notifyDataSetChanged()
        rooms.forEachIndexed { i, room -> roomList.setItemChecked(i, RoomFilter.normalize(room) in checked) }
        findViewById<TextView>(R.id.roomListTitle).text = getString(R.string.room_list_title, rooms.size)
        findViewById<TextView>(R.id.emptyRooms).visibility = if (rooms.isEmpty()) View.VISIBLE else View.GONE
    }

    private fun currentChecked(): Set<String> =
        rooms.filterIndexed { i, _ -> roomList.isItemChecked(i) }.map(RoomFilter::normalize).toSet()

    private fun addManualRoom() {
        val input = findViewById<EditText>(R.id.manualRoom)
        val name = RoomFilter.normalize(input.text.toString())
        if (name.isEmpty()) return
        loadRooms(currentChecked() + name)
        input.text.clear()
    }

    private fun selectedMode() = when (modeGroup.checkedRadioButtonId) {
        R.id.modeOnly -> RoomFilterMode.ONLY_SELECTED
        R.id.modeExcept -> RoomFilterMode.EXCEPT_SELECTED
        else -> RoomFilterMode.ALL
    }

    private fun updateListEnabled() {
        val enabled = selectedMode() != RoomFilterMode.ALL
        roomList.isEnabled = enabled
        roomList.alpha = if (enabled) 1f else 0.4f
    }

    private fun save() {
        val mode = selectedMode()
        val checked = currentChecked()
        settings.roomFilterMode = mode
        settings.selectedRooms = checked
        val message = if (mode == RoomFilterMode.ONLY_SELECTED && checked.isEmpty()) {
            "선택한 방이 없어 아무 대화도 수집되지 않습니다."
        } else {
            "저장했습니다: ${summary(mode, checked.size)}"
        }
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
        finish()
    }

    companion object {
        fun summary(mode: RoomFilterMode, count: Int) = when (mode) {
            RoomFilterMode.ALL -> "모든 방 수집"
            RoomFilterMode.ONLY_SELECTED -> "선택한 ${count}개 방만 수집"
            RoomFilterMode.EXCEPT_SELECTED -> "선택한 ${count}개 방 제외하고 수집"
        }
    }
}
