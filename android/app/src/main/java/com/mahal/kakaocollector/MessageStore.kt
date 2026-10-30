package com.mahal.kakaocollector

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import java.io.File
import java.security.MessageDigest

/**
 * 서버 전송 대기열(SQLite). 수집한 메시지를 서버로 보낼 때까지 보관하고, 보낸 뒤에는 사진을 지운다.
 * 조회·민원 분류·통계 등은 모두 웹 서버에서 처리한다.
 */
class MessageStore private constructor(context: Context) :
    SQLiteOpenHelper(context.applicationContext, "queue.db", null, 8) {

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
        val senderKey: String? = null,
    )

    data class Counts(val total: Int, val pending: Int, val sent: Int, val failed: Int)

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
                created_at INTEGER NOT NULL,
                sender_key TEXT
            )"""
        )
        db.execSQL("CREATE INDEX idx_queue_status ON queue(status, id)")
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
        if (oldVersion < 4) createNotificationLog(db)
        if (oldVersion < 8) {
            // v2.0: 앱은 수집 전용 — 휴대폰에서 하던 분류·이력 테이블은 서버로 이전되어 삭제
            db.execSQL("DROP TABLE IF EXISTS complaint_check")
            db.execSQL("DROP TABLE IF EXISTS room_members")
            db.execSQL("DROP TABLE IF EXISTS member_events")
            db.execSQL("ALTER TABLE queue ADD COLUMN sender_key TEXT")
            // 휴대폰에만 저장돼 있던(local) 메시지와 예전에 전송 실패한 메시지도 서버로 올린다
            db.execSQL("UPDATE queue SET status = '$PENDING', last_error = NULL WHERE status IN ('local', '$FAILED')")
        }
    }

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

    /** 알림으로 들어온 적 있는 방 (수집할 방 선택 화면용) */
    fun recordSeenRoom(room: String, isGroup: Boolean, time: Long) {
        // UPSERT(ON CONFLICT DO UPDATE)는 SQLite 3.24+(Android 11+) 전용이라 두 문장으로 처리
        val group = if (isGroup) 1 else 0
        writableDatabase.run {
            execSQL("INSERT OR IGNORE INTO seen_rooms (room, is_group, last_seen, notifications) VALUES (?, ?, ?, 0)", arrayOf(room, group, time))
            execSQL(
                "UPDATE seen_rooms SET is_group = MAX(is_group, ?), last_seen = MAX(last_seen, ?), notifications = notifications + 1 WHERE room = ?",
                arrayOf(group, time, room)
            )
        }
    }

    fun seenRooms(): List<SeenRoom> =
        readableDatabase.rawQuery("SELECT room, is_group, last_seen, notifications FROM seen_rooms ORDER BY last_seen DESC", null)
            .use { c -> buildList { while (c.moveToNext()) add(SeenRoom(c.getString(0), c.getInt(1) == 1, c.getLong(2), c.getInt(3))) } }

    // ---- 전송 대기열 ---------------------------------------------------------------------

    /** 이미 받은 메시지(같은 방·보낸 사람·내용·시각)면 false */
    fun insert(message: KakaoMessage, imagePath: String?): Boolean {
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
            put("sender_key", message.senderKey)
            put("created_at", System.currentTimeMillis())
        }
        return writableDatabase.insertWithOnConflict("queue", null, values, SQLiteDatabase.CONFLICT_IGNORE) != -1L
    }

    fun contains(message: KakaoMessage): Boolean =
        readableDatabase.rawQuery("SELECT 1 FROM queue WHERE dedup = ?", arrayOf(dedupKey(message))).use { it.moveToFirst() }

    fun pending(limit: Int): List<Row> = query("status = ?", arrayOf(PENDING), "id ASC", limit)

    fun recent(limit: Int): List<Row> = query(null, null, "id DESC", limit)

    /** 서버에 저장되면 사진 파일은 지운다(휴대폰은 수집만) */
    fun markSent(row: Row) {
        update(row.id, SENT, null)
        row.imagePath?.let { File(it).delete() }
    }

    fun markRetry(row: Row, error: String) {
        writableDatabase.execSQL("UPDATE queue SET attempts = attempts + 1, last_error = ? WHERE id = ?", arrayOf(error, row.id))
    }

    fun markFailed(row: Row, error: String) {
        update(row.id, FAILED, error)
        row.imagePath?.let { File(it).delete() }
    }

    fun counts(): Counts = readableDatabase.rawQuery(
        "SELECT COUNT(*), SUM(status = '$PENDING'), SUM(status = '$SENT'), SUM(status = '$FAILED') FROM queue", null
    ).use { c ->
        c.moveToFirst()
        Counts(c.getInt(0), c.getInt(1), c.getInt(2), c.getInt(3))
    }

    /** 전송 완료된 행은 최근 [keep] 건만 남긴다(같은 알림 재수신 시 중복 판별용) */
    fun pruneSent(keep: Int = 2000) {
        writableDatabase.execSQL(
            "DELETE FROM queue WHERE status = '$SENT' AND id NOT IN (SELECT id FROM queue WHERE status = '$SENT' ORDER BY id DESC LIMIT $keep)"
        )
    }

    private fun update(id: Long, status: String, error: String?) {
        writableDatabase.update("queue", ContentValues().apply {
            put("status", status)
            put("last_error", error)
        }, "id = ?", arrayOf(id.toString()))
    }

    private fun query(where: String?, args: Array<String>?, order: String, limit: Int): List<Row> =
        readableDatabase.query("queue", null, where, args, null, null, order, limit.toString()).use { c ->
            buildList { while (c.moveToNext()) add(rowFrom(c)) }
        }

    private fun rowFrom(c: Cursor) = Row(
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
        senderKey = c.getString(c.getColumnIndexOrThrow("sender_key")),
    )

    companion object {
        const val PENDING = "pending"
        const val SENT = "sent"
        const val FAILED = "failed"
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
