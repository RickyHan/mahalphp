package com.mahal.kakaolocal

import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.TextWatcher
import android.util.LruCache
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.AbsListView
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.BaseAdapter
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.GridView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/** 보관함: 대화가 있는 방 목록 (최근 순, 검색). 방을 누르면 대화, 길게 누르면 그 방 기록 삭제. */
class ChatRoomsActivity : AppCompatActivity() {

    private val io = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private var all: List<MessageStore.RoomSummary> = emptyList()
    private var shown: List<MessageStore.RoomSummary> = emptyList()
    private lateinit var adapter: RoomAdapter
    private lateinit var empty: TextView
    private lateinit var search: EditText

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.viewer_title)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(ContextCompat.getColor(context, R.color.surface))
        }
        search = EditText(this).apply {
            hint = "방 이름 검색"
            setSingleLine()
            setPadding(dp(16), dp(12), dp(16), dp(12))
            addTextChangedListener(object : TextWatcher {
                override fun afterTextChanged(s: Editable?) = filter()
                override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) = Unit
                override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) = Unit
            })
        }
        root.addView(search, LinearLayout.LayoutParams(-1, -2).apply { setMargins(dp(12), dp(8), dp(12), 0) })
        empty = TextView(this).apply {
            text = "아직 저장된 대화가 없습니다."
            setPadding(dp(24), dp(40), dp(24), dp(24))
            gravity = Gravity.CENTER
            setTextColor(ContextCompat.getColor(context, R.color.text_secondary))
            visibility = View.GONE
        }
        root.addView(empty)
        adapter = RoomAdapter()
        val list = ListView(this).apply {
            divider = null
            this.adapter = this@ChatRoomsActivity.adapter
            setOnItemClickListener { _, _, pos, _ ->
                startActivity(Intent(this@ChatRoomsActivity, ChatActivity::class.java).putExtra(ChatActivity.EXTRA_ROOM, shown[pos].room))
            }
            setOnItemLongClickListener { _, _, pos, _ ->
                confirmDelete(shown[pos])
                true
            }
        }
        root.addView(list, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
    }

    override fun onResume() {
        super.onResume()
        io.execute {
            val rooms = MessageStore.get(this).rooms()
            main.post {
                all = rooms
                filter()
            }
        }
    }

    override fun onDestroy() {
        io.shutdownNow()
        super.onDestroy()
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    private fun filter() {
        val q = search.text.toString().trim().lowercase().replace(" ", "")
        shown = if (q.isEmpty()) all else all.filter { it.room.lowercase().replace(" ", "").contains(q) }
        empty.visibility = if (shown.isEmpty()) View.VISIBLE else View.GONE
        empty.text = if (all.isEmpty()) "아직 저장된 대화가 없습니다." else "검색 결과가 없습니다."
        adapter.notifyDataSetChanged()
    }

    private fun confirmDelete(r: MessageStore.RoomSummary) {
        AlertDialog.Builder(this)
            .setTitle("'${r.room}' 기록 삭제")
            .setMessage("이 방의 메시지 ${r.count}건과 사진 ${r.photos}장을 휴대폰에서 지웁니다. 되돌릴 수 없습니다.")
            .setNegativeButton("취소", null)
            .setPositiveButton("삭제") { _, _ ->
                io.execute {
                    MessageStore.get(this).deleteRoom(r.room)
                    main.post { onResume() }
                }
            }
            .show()
    }

    private inner class RoomAdapter : BaseAdapter() {
        override fun getCount() = shown.size
        override fun getItem(position: Int) = shown[position]
        override fun getItemId(position: Int) = position.toLong()

        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val r = shown[position]
            val box = (convertView as? LinearLayout) ?: LinearLayout(parent.context).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(18), dp(14), dp(18), dp(14))
                addView(LinearLayout(context).apply {
                    orientation = LinearLayout.HORIZONTAL
                    addView(TextView(context).apply {
                        tag = "title"
                        textSize = 16f
                        setTypeface(typeface, Typeface.BOLD)
                        setTextColor(ContextCompat.getColor(context, R.color.text_primary))
                        maxLines = 1
                    }, LinearLayout.LayoutParams(0, -2, 1f))
                    addView(TextView(context).apply {
                        tag = "time"
                        textSize = 12f
                        setTextColor(ContextCompat.getColor(context, R.color.text_secondary))
                    })
                })
                addView(TextView(context).apply {
                    tag = "last"
                    textSize = 13f
                    maxLines = 1
                    setTextColor(ContextCompat.getColor(context, R.color.text_secondary))
                    setPadding(0, dp(4), 0, 0)
                })
            }
            box.findViewWithTag<TextView>("title").text = "${r.room}  (${r.count})"
            box.findViewWithTag<TextView>("time").text = shortTime(r.lastAt)
            box.findViewWithTag<TextView>("last").text = "${r.lastSender}: ${if (r.lastType == "photo") "📷 사진" else r.lastText}"
            return box
        }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
}

/** 한 방의 대화: 오래된 순, 위로 '이전 대화 더 보기'. 사진은 작게 보이고 누르면 크게. */
class ChatActivity : AppCompatActivity() {

    private val io = Executors.newFixedThreadPool(2)
    private val main = Handler(Looper.getMainLooper())
    private val rows = mutableListOf<MessageStore.Row>()
    private lateinit var room: String
    private lateinit var list: ListView
    private lateinit var adapter: MessageAdapter
    private lateinit var more: TextView
    private var hasMore = true

    private val thumbs = object : LruCache<String, Bitmap>(16 * 1024 * 1024) {
        override fun sizeOf(key: String, value: Bitmap) = value.byteCount
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        room = intent.getStringExtra(EXTRA_ROOM) ?: return finish()
        title = room
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        adapter = MessageAdapter()
        more = TextView(this).apply {
            text = "▲ 이전 대화 더 보기"
            gravity = Gravity.CENTER
            setPadding(0, dp(14), 0, dp(14))
            setTextColor(ContextCompat.getColor(context, R.color.brand))
            setOnClickListener { load(older = true) }
        }
        list = ListView(this).apply {
            divider = null
            setBackgroundColor(ContextCompat.getColor(context, R.color.chat_bg))
            addHeaderView(more, null, false)
            this.adapter = this@ChatActivity.adapter
            transcriptMode = ListView.TRANSCRIPT_MODE_NORMAL
        }
        setContentView(list)
        load(older = false)
    }

    override fun onCreateOptionsMenu(menu: android.view.Menu): Boolean {
        menu.add(0, MENU_PHOTOS, 0, "이 방 사진만").setShowAsAction(android.view.MenuItem.SHOW_AS_ACTION_ALWAYS)
        return true
    }

    override fun onOptionsItemSelected(item: android.view.MenuItem): Boolean {
        if (item.itemId == MENU_PHOTOS) {
            startActivity(Intent(this, PhotoGridActivity::class.java).putExtra(EXTRA_ROOM, room))
            return true
        }
        if (item.itemId == android.R.id.home) {
            finish()
            return true
        }
        return super.onOptionsItemSelected(item)
    }

    override fun onDestroy() {
        io.shutdownNow()
        super.onDestroy()
    }

    private fun load(older: Boolean) {
        val before = if (older) rows.firstOrNull()?.id ?: Long.MAX_VALUE else Long.MAX_VALUE
        io.execute {
            val page = MessageStore.get(this).messages(room, before, PAGE)
            main.post {
                if (!older) rows.clear()
                rows.addAll(0, page)
                hasMore = page.size == PAGE
                more.visibility = if (hasMore) View.VISIBLE else View.GONE
                more.text = if (hasMore) "▲ 이전 대화 더 보기" else ""
                adapter.notifyDataSetChanged()
                if (older) list.setSelection(page.size) else list.setSelection(adapter.count)
            }
        }
    }

    private inner class MessageAdapter : BaseAdapter() {
        override fun getCount() = rows.size
        override fun getItem(position: Int) = rows[position]
        override fun getItemId(position: Int) = rows[position].id

        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val r = rows[position]
            val mine = r.sender == KakaoParser.SELF_SENDER
            val box = (convertView as? LinearLayout) ?: LinearLayout(parent.context).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(12), dp(6), dp(12), dp(6))
                addView(TextView(context).apply {
                    tag = "head"
                    textSize = 12f
                    setTextColor(ContextCompat.getColor(context, R.color.text_secondary))
                })
                addView(TextView(context).apply {
                    tag = "text"
                    textSize = 15f
                    setTextColor(ContextCompat.getColor(context, R.color.text_primary))
                    setTextIsSelectable(true)
                    setPadding(dp(12), dp(8), dp(12), dp(8))
                }, LinearLayout.LayoutParams(-2, -2).apply { topMargin = dp(3) })
                addView(ImageView(context).apply {
                    tag = "photo"
                    adjustViewBounds = true
                    scaleType = ImageView.ScaleType.FIT_START
                    maxWidth = dp(220)
                    maxHeight = dp(260)
                }, LinearLayout.LayoutParams(-2, -2).apply { topMargin = dp(4) })
            }
            box.gravity = if (mine) Gravity.END else Gravity.START
            box.findViewWithTag<TextView>("head").text = if (mine) shortTime(r.postedAt) else "${r.sender} · ${shortTime(r.postedAt)}"
            val text = box.findViewWithTag<TextView>("text")
            val photo = box.findViewWithTag<ImageView>("photo")
            val path = r.imagePath?.takeIf { File(it).exists() }
            val showText = path == null || (r.text.isNotBlank() && r.text != "사진을 보냈습니다.")
            text.visibility = if (showText) View.VISIBLE else View.GONE
            text.text = if (path == null && r.type == "photo") "📷 ${r.text} (사진 미리보기 없음)" else r.text
            text.background = GradientDrawable().apply {
                cornerRadius = dp(14).toFloat()
                setColor(ContextCompat.getColor(this@ChatActivity, if (mine) R.color.brand_yellow else R.color.card))
            }
            photo.tag = "photo"
            photo.setImageDrawable(null)
            photo.visibility = if (path != null) View.VISIBLE else View.GONE
            photo.setOnClickListener(null)
            if (path != null) {
                photo.setTag(R.id.photoPath, path)
                thumbs.get(path)?.let { photo.setImageBitmap(it) } ?: io.execute {
                    val bmp = decodeSampled(path, dp(220))
                    if (bmp != null) thumbs.put(path, bmp)
                    main.post { if (photo.getTag(R.id.photoPath) == path) photo.setImageBitmap(bmp) }
                }
                photo.setOnClickListener { PhotoActivity.open(this@ChatActivity, r) }
            }
            return box
        }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    companion object {
        const val EXTRA_ROOM = "room"
        private const val PAGE = 200
        private const val MENU_PHOTOS = 1
    }
}

/** 사진 크게 보기 (누르면 닫힘). 아래에 방 · 보낸 사람 · 시각 */
class PhotoActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val path = intent.getStringExtra(EXTRA_PATH) ?: return finish()
        val root = FrameLayout(this).apply {
            setBackgroundColor(0xFF000000.toInt())
            setOnClickListener { finish() }
        }
        val view = ImageView(this).apply {
            scaleType = ImageView.ScaleType.FIT_CENTER
            contentDescription = "사진"
        }
        root.addView(view, FrameLayout.LayoutParams(-1, -1))
        intent.getStringExtra(EXTRA_CAPTION)?.let { caption ->
            root.addView(TextView(this).apply {
                text = caption
                textSize = 13f
                setTextColor(0xFFFFFFFF.toInt())
                setBackgroundColor(0x99000000.toInt())
                val p = (12 * resources.displayMetrics.density).toInt()
                setPadding(p, p, p, p)
            }, FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM))
        }
        setContentView(root)
        val max = maxOf(resources.displayMetrics.widthPixels, resources.displayMetrics.heightPixels)
        Thread { decodeSampled(path, max)?.let { b -> runOnUiThread { view.setImageBitmap(b) } } }.start()
    }

    companion object {
        const val EXTRA_PATH = "path"
        const val EXTRA_CAPTION = "caption"

        fun open(context: android.content.Context, row: MessageStore.Row) {
            context.startActivity(
                Intent(context, PhotoActivity::class.java)
                    .putExtra(EXTRA_PATH, row.imagePath)
                    .putExtra(EXTRA_CAPTION, "${row.room}\n${row.sender} · ${shortTime(row.postedAt)}"),
            )
        }
    }
}

/**
 * 사진 모아보기: 저장된 사진을 최신 순 격자로. 위에서 방을 고르면 그 방 사진만.
 * 아래로 내리면 더 불러오고, 누르면 크게 본다.
 */
class PhotoGridActivity : AppCompatActivity() {

    private val io = Executors.newFixedThreadPool(3)
    private val main = Handler(Looper.getMainLooper())
    private val rows = mutableListOf<MessageStore.Row>()
    private var room: String? = null
    private var loading = false
    private var hasMore = true
    private lateinit var adapter: GridAdapter
    private lateinit var empty: TextView
    private var cell = 0

    private val thumbs = object : LruCache<String, Bitmap>(24 * 1024 * 1024) {
        override fun sizeOf(key: String, value: Bitmap) = value.byteCount
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.photos_title)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        room = intent.getStringExtra(ChatActivity.EXTRA_ROOM)
        cell = resources.displayMetrics.widthPixels / COLUMNS

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(ContextCompat.getColor(context, R.color.surface))
        }
        val spinner = Spinner(this)
        root.addView(spinner, LinearLayout.LayoutParams(-1, -2).apply { setMargins(dp(12), dp(8), dp(12), dp(4)) })
        empty = TextView(this).apply {
            text = "저장된 사진이 없습니다.\n'수집 옵션'에서 사진 미리보기 수집이 켜져 있는지 확인하십시오."
            gravity = Gravity.CENTER
            setPadding(dp(24), dp(40), dp(24), dp(24))
            setTextColor(ContextCompat.getColor(context, R.color.text_secondary))
            visibility = View.GONE
        }
        root.addView(empty)
        adapter = GridAdapter()
        val grid = GridView(this).apply {
            numColumns = COLUMNS
            stretchMode = GridView.STRETCH_COLUMN_WIDTH
            horizontalSpacing = dp(2)
            verticalSpacing = dp(2)
            this.adapter = this@PhotoGridActivity.adapter
            setOnItemClickListener { _, _, pos, _ -> PhotoActivity.open(this@PhotoGridActivity, rows[pos]) }
            setOnScrollListener(object : AbsListView.OnScrollListener {
                override fun onScrollStateChanged(view: AbsListView?, state: Int) = Unit
                override fun onScroll(view: AbsListView?, first: Int, visible: Int, total: Int) {
                    if (hasMore && !loading && total > 0 && first + visible >= total - COLUMNS * 2) load(more = true)
                }
            })
        }
        root.addView(grid, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)

        io.execute {
            val rooms = MessageStore.get(this).photoRooms()
            main.post {
                val labels = listOf("전체 방 (${rooms.sumOf { it.second }}장)") + rooms.map { "${it.first} (${it.second}장)" }
                spinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, labels)
                spinner.setSelection(room?.let { r -> rooms.indexOfFirst { it.first == r } + 1 }?.coerceAtLeast(0) ?: 0, false)
                spinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                    override fun onItemSelected(parent: AdapterView<*>?, view: View?, pos: Int, id: Long) {
                        val next = if (pos == 0) null else rooms[pos - 1].first
                        if (next != room) {
                            room = next
                            load(more = false)
                        }
                    }
                    override fun onNothingSelected(parent: AdapterView<*>?) = Unit
                }
            }
        }
        load(more = false)
    }

    override fun onDestroy() {
        io.shutdownNow()
        super.onDestroy()
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    private fun load(more: Boolean) {
        loading = true
        val target = room
        val before = if (more) rows.lastOrNull()?.id ?: Long.MAX_VALUE else Long.MAX_VALUE
        io.execute {
            val page = MessageStore.get(this).photos(target, before, PAGE).filter { r -> r.imagePath?.let { File(it).exists() } == true }
            main.post {
                if (target != room) return@post // 그 사이 방을 바꿈
                if (!more) rows.clear()
                rows.addAll(page)
                hasMore = page.size >= PAGE / 2
                loading = false
                empty.visibility = if (rows.isEmpty()) View.VISIBLE else View.GONE
                adapter.notifyDataSetChanged()
            }
        }
    }

    private inner class GridAdapter : BaseAdapter() {
        override fun getCount() = rows.size
        override fun getItem(position: Int) = rows[position]
        override fun getItemId(position: Int) = rows[position].id

        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val r = rows[position]
            val path = r.imagePath.orEmpty()
            val img = (convertView as? ImageView) ?: ImageView(parent.context).apply {
                scaleType = ImageView.ScaleType.CENTER_CROP
                layoutParams = AbsListView.LayoutParams(-1, cell)
                setBackgroundColor(ContextCompat.getColor(context, R.color.outline))
            }
            img.contentDescription = "${r.room} ${r.sender} 사진"
            img.setTag(R.id.photoPath, path)
            val cached = thumbs.get(path)
            img.setImageBitmap(cached)
            if (cached == null) io.execute {
                val bmp = decodeSampled(path, cell)
                if (bmp != null) thumbs.put(path, bmp)
                main.post { if (img.getTag(R.id.photoPath) == path) img.setImageBitmap(bmp) }
            }
            return img
        }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    companion object {
        private const val COLUMNS = 3
        private const val PAGE = 90
    }
}

/** 큰 사진을 화면 크기에 맞게 줄여 읽는다(메모리 절약) */
fun decodeSampled(path: String, target: Int): Bitmap? = runCatching {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(path, bounds)
    var sample = 1
    while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= target) sample *= 2
    BitmapFactory.decodeFile(path, BitmapFactory.Options().apply { inSampleSize = sample })
}.getOrNull()

fun shortTime(at: Long): String {
    if (at <= 0) return ""
    val now = System.currentTimeMillis()
    val today = SimpleDateFormat("yyyyMMdd", Locale.KOREA)
    val sameDay = today.format(Date(at)) == today.format(Date(now))
    return SimpleDateFormat(if (sameDay) "a h:mm" else "M/d a h:mm", Locale.KOREA).format(Date(at))
}
