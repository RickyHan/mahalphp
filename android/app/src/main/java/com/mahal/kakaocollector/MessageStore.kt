package com.mahal.kakaocollector

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import java.io.File
import java.security.MessageDigest

/**
 * 수집한 메시지·사진의 휴대폰 내 저장소(SQLite). 앱 단독으로 조회·검색·내보내기가 가능하다.
 * 서버 전송을 켠 경우에는 전송 대기열 역할도 겸한다(status: local → pending → sent/failed).
 */
class MessageStore private constructor(context: Context) :
    SQLiteOpenHelper(context.applicationContext, "queue.db", null, 4) {

    data class Row(
        val id: Long,
        val room: String,
        val sender: String,
        val text: String,
        val type: String,
        val isGroup: Boolean,
        val postedAt: Long,
        val notificationKey: String,
        val imagePath: String?,
        val status: String,
        val lastError: String?,
    )

    data class Counts(val total: Int, val photos: Int, val local: Int, val pending: Int, val sent: Int, val failed: Int)

    data class RoomSummary(
        val room: String,
        val isGroup: Boolean,
        val messageCount: Int,
        val photoCount: Int,
        val lastSender: String,
        val lastText: String,
        val lastType: String,
        val lastPostedAt: Long,
    )

    data class SeenRoom(val room: String, val isGroup: Boolean, val lastSeen: Long, val notifications: Int)

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """CREATE TABLE queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedup TEXT NOT NULL UNIQUE,
                room TEXT NOT NULL,
                sender TEXT NOT NULL,
                text TEXT NOT NULL,
                type TEXT NOT NULL,
                is_group INTEGER NOT NULL,
                posted_at INTEGER NOT NULL,
                notification_key TEXT NOT NULL,
                image_path TEXT,
                status TEXT NOT NULL DEFAULT '$PENDING',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at INTEGER NOT NULL
            )"""
        )
        db.execSQL("CREATE INDEX idx_queue_status ON queue(status, id)")
        db.execSQL("CREATE INDEX IF NOT EXISTS idx_queue_room ON queue(room, id)")
        createSeenRooms(db)
        createNotificationLog(db)
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        if (oldVersion < 2) {
            createSeenRooms(db)
            db.execSQL(
                "INSERT OR IGNORE INTO seen_rooms (room, is_group, last_seen, notifications) " +
                    "SELECT room, MAX(is_group), MAX(posted_at), COUNT(*) FROM queue GROUP BY room"
            )
        }
        if (oldVersion < 3) {
            db.execSQL("CREATE INDEX IF NOT EXISTS idx_queue_room ON queue(room, id)")
        }
        if (oldVersion < 4) {
            createNotificationLog(db)
        }
    }

    /** 진단용: 최근 카카오톡 알림 원본 구조 (최근 [LOG_KEEP]건만 유지) */
    private fun createNotificationLog(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE IF NOT EXISTS notification_log (id INTEGER PRIMARY KEY AUTOINCREMENT, time INTEGER NOT NULL, dump TEXT NOT NULL)")
    }

    fun addNotificationLog(time: Long, dump: String) {
        writableDatabase.run {
            execSQL("INSERT INTO notification_log (time, dump) VALUES (?, ?)", arrayOf(time, dump))
            execSQL("DELETE FROM notification_log WHERE id NOT IN (SELECT id FROM notification_log ORDER BY id DESC LIMIT $LOG_KEEP)")
        }
    }

    fun notificationLogs(): List<Pair<Long, String>> =
        readableDatabase.rawQuery("SELECT time, dump FROM notification_log ORDER BY id DESC", null).use { c ->
            buildList { while (c.moveToNext()) add(c.getLong(0) to c.getString(1)) }
        }

    /** 알림으로 들어온 적 있는 방 목록 (수집 제외된 방 포함 — 방 선택 화면용, 내용은 저장하지 않음) */
    private fun createSeenRooms(db: SQLiteDatabase) {
        db.execSQL(
            """CREATE TABLE IF NOT EXISTS seen_rooms (
                room TEXT PRIMARY KEY,
                is_group INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                notifications INTEGER NOT NULL DEFAULT 0
            )"""
        )
    }

    fun recordSeenRoom(room: String, isGroup: Boolean, time: Long) {
        // UPSERT(ON CONFLICT DO UPDATE)는 SQLite 3.24+(Android 11+) 전용이라 두 문장으로 처리
        // 방 이름은 카톡에 설정된 그대로 저장 (공백 정리는 비교할 때만)
        val key = room
        val group = if (isGroup) 1 else 0
        writableDatabase.run {
            execSQL("INSERT OR IGNORE INTO seen_rooms (room, is_group, last_seen, notifications) VALUES (?, ?, ?, 0)", arrayOf(key, group, time))
            execSQL(
                "UPDATE seen_rooms SET is_group = MAX(is_group, ?), last_seen = MAX(last_seen, ?), notifications = notifications + 1 WHERE room = ?",
                arrayOf(group, time, key)
            )
        }
    }

    fun seenRooms(): List<SeenRoom> =
        readableDatabase.rawQuery("SELECT room, is_group, last_seen, notifications FROM seen_rooms ORDER BY last_seen DESC", null)
            .use { c ->
                buildList {
                    while (c.moveToNext()) add(SeenRoom(c.getString(0), c.getInt(1) == 1, c.getLong(2), c.getInt(3)))
                }
            }

    /** 이미 받은 메시지(같은 방·보낸 사람·내용·시각)면 false. [upload]=false 면 휴대폰에만 저장(local). */
    fun insert(message: KakaoMessage, imagePath: String?, upload: Boolean = true): Boolean {
        val values = ContentValues().apply {
            put("dedup", dedupKey(message))
            put("room", message.room)
            put("sender", message.sender)
            put("text", message.text)
            put("type", message.type.wire)
            put("is_group", if (message.isGroupChat) 1 else 0)
            put("posted_at", message.postedAt)
            put("notification_key", message.notificationKey)
            put("image_path", imagePath)
            put("status", if (upload) PENDING else LOCAL)
            put("created_at", System.currentTimeMillis())
        }
        return writableDatabase.insertWithOnConflict("queue", null, values, SQLiteDatabase.CONFLICT_IGNORE) != -1L
    }

    fun contains(message: KakaoMessage): Boolean =
        readableDatabase.rawQuery("SELECT 1 FROM queue WHERE dedup = ?", arrayOf(dedupKey(message))).use { it.moveToFirst() }

    fun pending(limit: Int): List<Row> = query("status = ?", arrayOf(PENDING), "id ASC", limit)

    fun recent(limit: Int): List<Row> = query(null, null, "id DESC", limit)

    // 사진 파일은 전송 후에도 휴대폰에 보관한다(앱 안에서 조회)
    fun markSent(row: Row) = update(row.id, SENT, null)

    fun markRetry(row: Row, error: String) {
        writableDatabase.execSQL("UPDATE queue SET attempts = attempts + 1, last_error = ? WHERE id = ?", arrayOf(error, row.id))
    }

    fun markFailed(row: Row, error: String) = update(row.id, FAILED, error)

    /** 서버 전송을 켰을 때: 휴대폰에만 있던 메시지도 전송 대기열에 넣는다 */
    fun queueLocalForUpload(): Int = writableDatabase.compileStatement(
        "UPDATE queue SET status = '$PENDING' WHERE status = '$LOCAL'"
    ).executeUpdateDelete()

    fun counts(): Counts = readableDatabase.rawQuery(
        "SELECT COUNT(*), SUM(image_path IS NOT NULL), SUM(status = '$LOCAL'), SUM(status = '$PENDING'), " +
            "SUM(status = '$SENT'), SUM(status = '$FAILED') FROM queue",
        null
    ).use { c ->
        c.moveToFirst()
        Counts(c.getInt(0), c.getInt(1), c.getInt(2), c.getInt(3), c.getInt(4), c.getInt(5))
    }

    // ---- 조회 (앱 내 대화 보기) ------------------------------------------------------------

    /** 대화방 목록: 최근 대화순 */
    fun roomSummaries(): List<RoomSummary> = readableDatabase.rawQuery(
        """SELECT r.room, m.is_group, r.cnt, r.photos, m.sender, m.text, m.type, m.posted_at
           FROM (SELECT room, COUNT(*) AS cnt, SUM(image_path IS NOT NULL) AS photos, MAX(id) AS last_id
                 FROM queue GROUP BY room) r
           JOIN queue m ON m.id = r.last_id
           ORDER BY m.id DESC""",
        null
    ).use { c ->
        buildList {
            while (c.moveToNext()) {
                add(RoomSummary(c.getString(0), c.getInt(1) == 1, c.getInt(2), c.getInt(3), c.getString(4), c.getString(5), c.getString(6), c.getLong(7)))
            }
        }
    }

    /** 방의 메시지(오래된 → 최신). beforeId 가 있으면 그보다 이전 것, afterId 가 있으면 그보다 이후 것 */
    fun roomMessages(room: String, beforeId: Long? = null, afterId: Long? = null, limit: Int = 200): List<Row> {
        val where = StringBuilder("room = ?")
        val args = mutableListOf(room)
        beforeId?.let { where.append(" AND id < ?"); args += it.toString() }
        afterId?.let { where.append(" AND id > ?"); args += it.toString() }
        val rows = query(where.toString(), args.toTypedArray(), if (afterId != null) "id ASC" else "id DESC", limit)
        return if (afterId != null) rows else rows.reversed()
    }

    /** 내용·보낸 사람 검색 (최신순) */
    fun search(term: String, limit: Int = 200): List<Row> {
        val like = "%" + term.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        return query("text LIKE ? ESCAPE '!' OR sender LIKE ? ESCAPE '!'", arrayOf(like, like), "id DESC", limit)
    }

    fun allMessages(): List<Row> = query(null, null, "id ASC", Int.MAX_VALUE)

    /** 수집 데이터 전체 삭제 (사진 파일 포함). 방 선택 목록(seen_rooms)은 유지 */
    fun deleteAll() {
        query("image_path IS NOT NULL", null, "id ASC", Int.MAX_VALUE).forEach { r -> r.imagePath?.let { File(it).delete() } }
        writableDatabase.execSQL("DELETE FROM queue")
    }

    private fun update(id: Long, status: String, error: String?) {
        writableDatabase.update("queue", ContentValues().apply {
            put("status", status)
            put("last_error", error)
        }, "id = ?", arrayOf(id.toString()))
    }

    private fun query(where: String?, args: Array<String>?, order: String, limit: Int): List<Row> =
        readableDatabase.query("queue", null, where, args, null, null, order, limit.toString()).use { c ->
            buildList {
                while (c.moveToNext()) {
                    add(
                        Row(
                            id = c.getLong(c.getColumnIndexOrThrow("id")),
                            room = c.getString(c.getColumnIndexOrThrow("room")),
                            sender = c.getString(c.getColumnIndexOrThrow("sender")),
                            text = c.getString(c.getColumnIndexOrThrow("text")),
                            type = c.getString(c.getColumnIndexOrThrow("type")),
                            isGroup = c.getInt(c.getColumnIndexOrThrow("is_group")) == 1,
                            postedAt = c.getLong(c.getColumnIndexOrThrow("posted_at")),
                            notificationKey = c.getString(c.getColumnIndexOrThrow("notification_key")),
                            imagePath = c.getString(c.getColumnIndexOrThrow("image_path")),
                            status = c.getString(c.getColumnIndexOrThrow("status")),
                            lastError = c.getString(c.getColumnIndexOrThrow("last_error")),
                        )
                    )
                }
            }
        }

    companion object {
        const val PENDING = "pending"
        const val SENT = "sent"
        const val FAILED = "failed"
        /** 서버 전송 꺼짐: 휴대폰에만 저장 */
        const val LOCAL = "local"
        const val LOG_KEEP = 30

        @Volatile private var instance: MessageStore? = null

        fun get(context: Context): MessageStore =
            instance ?: synchronized(this) { instance ?: MessageStore(context).also { instance = it } }

        /** 테스트 전용: 싱글턴을 닫고 비운다 */
        internal fun resetForTest() = synchronized(this) {
            instance?.close()
            instance = null
        }

        fun dedupKey(m: KakaoMessage): String {
            val digest = MessageDigest.getInstance("SHA-256").digest("${m.room}|${m.sender}|${m.text}|${m.postedAt}".toByteArray())
            return digest.joinToString("") { "%02x".format(it) }
        }
    }
}
