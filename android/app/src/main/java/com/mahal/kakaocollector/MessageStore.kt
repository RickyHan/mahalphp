package com.mahal.kakaocollector

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import java.io.File
import java.security.MessageDigest

/**
 * 서버 전송 전까지 메시지를 보관하는 로컬 큐(SQLite).
 * 네트워크가 끊겨도 수집은 계속되고, 연결되면 UploadWorker 가 순서대로 전송한다.
 */
class MessageStore private constructor(context: Context) :
    SQLiteOpenHelper(context.applicationContext, "queue.db", null, 2) {

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

    data class Counts(val pending: Int, val sent: Int, val failed: Int)

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
        createSeenRooms(db)
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        if (oldVersion < 2) {
            createSeenRooms(db)
            db.execSQL(
                "INSERT OR IGNORE INTO seen_rooms (room, is_group, last_seen, notifications) " +
                    "SELECT room, MAX(is_group), MAX(posted_at), COUNT(*) FROM queue GROUP BY room"
            )
        }
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
        val key = RoomFilter.normalize(room)
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

    /** 이미 받은 메시지(같은 방·보낸 사람·내용·시각)면 false. */
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
            put("created_at", System.currentTimeMillis())
        }
        return writableDatabase.insertWithOnConflict("queue", null, values, SQLiteDatabase.CONFLICT_IGNORE) != -1L
    }

    fun contains(message: KakaoMessage): Boolean =
        readableDatabase.rawQuery("SELECT 1 FROM queue WHERE dedup = ?", arrayOf(dedupKey(message))).use { it.moveToFirst() }

    fun pending(limit: Int): List<Row> = query("status = ?", arrayOf(PENDING), "id ASC", limit)

    fun recent(limit: Int): List<Row> = query(null, null, "id DESC", limit)

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
        "SELECT SUM(status = '$PENDING'), SUM(status = '$SENT'), SUM(status = '$FAILED') FROM queue", null
    ).use { c ->
        c.moveToFirst()
        Counts(c.getInt(0), c.getInt(1), c.getInt(2))
    }

    /** 전송 완료된 행은 최근 [keep] 건만 남긴다(중복 판별용). */
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

        @Volatile private var instance: MessageStore? = null

        fun get(context: Context): MessageStore =
            instance ?: synchronized(this) { instance ?: MessageStore(context).also { instance = it } }

        fun dedupKey(m: KakaoMessage): String {
            val digest = MessageDigest.getInstance("SHA-256").digest("${m.room}|${m.sender}|${m.text}|${m.postedAt}".toByteArray())
            return digest.joinToString("") { "%02x".format(it) }
        }
    }
}
