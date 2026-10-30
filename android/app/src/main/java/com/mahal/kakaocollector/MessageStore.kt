package com.mahal.kakaocollector

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import java.io.File
import java.security.MessageDigest

/**
 * 수집한 메시지·사진의 휴대폰 내 저장소(SQLite). 앱 단독으로 조회·검색·내보내기가 가능하다.
 * 서버 전송을 켠 경우에는 전송 대기열 역할도 겸한다(status: local → pending → sent/failed).
 */
class MessageStore private constructor(context: Context) :
    SQLiteOpenHelper(context.applicationContext, "queue.db", null, 6) {

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

    /** 민원으로 분류된 메시지 */
    data class Complaint(val row: Row, val category: String, val summary: String, val confidence: Double, val checkedAt: Long)

    data class ComplaintCounts(val eligible: Int, val checked: Int, val complaints: Int, val failed: Int) {
        val waiting get() = eligible - checked
    }

    /** 방별 참여자 (발언 기준). keyed=true 면 카카오톡 고유 키로 식별되어 대화명 변경 추적 가능 */
    data class Member(val room: String, val name: String, val keyed: Boolean, val firstSeen: Long, val lastSeen: Long, val messageCount: Int)

    data class MemberEvent(
        val id: Long,
        val room: String,
        val type: MemberEvents.Type,
        val member: String,
        val oldName: String?,
        val actor: String?,
        val at: Long,
        val text: String?,
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
        createComplaintCheck(db)
        createMembers(db)
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
        if (oldVersion < 5) {
            createComplaintCheck(db)
        }
        if (oldVersion < 6) {
            createMembers(db)
        }
    }

    /** 참여자(room_members)와 입장·퇴장·대화명 변경 이력(member_events) */
    private fun createMembers(db: SQLiteDatabase) {
        db.execSQL(
            """CREATE TABLE IF NOT EXISTS room_members (
                room TEXT NOT NULL,
                member_key TEXT NOT NULL,
                display_name TEXT NOT NULL,
                keyed INTEGER NOT NULL,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (room, member_key)
            )"""
        )
        db.execSQL(
            """CREATE TABLE IF NOT EXISTS member_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room TEXT NOT NULL,
                type TEXT NOT NULL,
                member TEXT NOT NULL,
                old_name TEXT,
                actor TEXT,
                at INTEGER NOT NULL,
                text TEXT
            )"""
        )
        db.execSQL("CREATE INDEX IF NOT EXISTS idx_member_events_room ON member_events(room, at)")
    }

    // ---- 참여자·이력 ------------------------------------------------------------------------

    /**
     * 발언자를 기록한다. 처음 보는 사람이면 '첫 발언', 같은 고유 키인데 이름이 다르면 '대화명 변경' 이력을 남긴다.
     * 고유 키가 없으면 이름으로 식별하므로 대화명 변경은 감지할 수 없다.
     */
    fun recordSender(room: String, senderKey: String?, name: String, at: Long) {
        val keyed = senderKey != null
        val key = if (keyed) "k:$senderKey" else "n:$name"
        val db = writableDatabase
        db.beginTransaction()
        try {
            val previous = db.rawQuery(
                "SELECT display_name FROM room_members WHERE room = ? AND member_key = ?", arrayOf(room, key)
            ).use { c -> if (c.moveToFirst()) c.getString(0) else null }
            when {
                previous == null -> {
                    db.execSQL(
                        "INSERT INTO room_members (room, member_key, display_name, keyed, first_seen, last_seen, message_count) VALUES (?, ?, ?, ?, ?, ?, 1)",
                        arrayOf(room, key, name, if (keyed) 1 else 0, at, at)
                    )
                    addEvent(MemberEvents.Type.FIRST_SEEN, room, name, null, null, at, null)
                }
                else -> {
                    if (previous != name) addEvent(MemberEvents.Type.RENAME, room, name, previous, null, at, null)
                    db.execSQL(
                        "UPDATE room_members SET display_name = ?, last_seen = MAX(last_seen, ?), first_seen = MIN(first_seen, ?), " +
                            "message_count = message_count + 1 WHERE room = ? AND member_key = ?",
                        arrayOf(name, at, at, room, key)
                    )
                }
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun addEvent(type: MemberEvents.Type, room: String, member: String, oldName: String?, actor: String?, at: Long, text: String?) {
        writableDatabase.execSQL(
            "INSERT INTO member_events (room, type, member, old_name, actor, at, text) VALUES (?, ?, ?, ?, ?, ?, ?)",
            arrayOf(room, type.wire, member, oldName, actor, at, text)
        )
    }

    /** 이력 (최신순). room·type 이 null 이면 전체 */
    fun memberEvents(room: String? = null, type: MemberEvents.Type? = null, limit: Int = 500): List<MemberEvent> {
        val where = mutableListOf<String>()
        val args = mutableListOf<String>()
        room?.let { where += "room = ?"; args += it }
        type?.let { where += "type = ?"; args += it.wire }
        return readableDatabase.rawQuery(
            "SELECT id, room, type, member, old_name, actor, at, text FROM member_events" +
                (if (where.isEmpty()) "" else " WHERE " + where.joinToString(" AND ")) + " ORDER BY at DESC, id DESC LIMIT $limit",
            args.toTypedArray()
        ).use { c ->
            buildList {
                while (c.moveToNext()) {
                    val t = MemberEvents.Type.of(c.getString(2)) ?: continue
                    add(MemberEvent(c.getLong(0), c.getString(1), t, c.getString(3), c.getString(4), c.getString(5), c.getLong(6), c.getString(7)))
                }
            }
        }
    }

    /** 방 참여자 (최근 발언순) */
    fun members(room: String): List<Member> = readableDatabase.rawQuery(
        "SELECT room, display_name, keyed, first_seen, last_seen, message_count FROM room_members WHERE room = ? ORDER BY last_seen DESC",
        arrayOf(room)
    ).use { c -> buildList { while (c.moveToNext()) add(Member(c.getString(0), c.getString(1), c.getInt(2) == 1, c.getLong(3), c.getLong(4), c.getInt(5))) } }

    /** 참여자 기록이 있는 방 목록 */
    fun memberRooms(): List<String> = readableDatabase.rawQuery(
        "SELECT room FROM (SELECT room, MAX(at) AS t FROM member_events GROUP BY room) ORDER BY t DESC", null
    ).use { c -> buildList { while (c.moveToNext()) add(c.getString(0)) } }

    /** Gemini 민원 분류 결과 (메시지 1건당 1행). verdict: 1=민원, 0=민원 아님, -1=분류 실패(재시도 대상) */
    private fun createComplaintCheck(db: SQLiteDatabase) {
        db.execSQL(
            """CREATE TABLE IF NOT EXISTS complaint_check (
                message_id INTEGER PRIMARY KEY REFERENCES queue(id) ON DELETE CASCADE,
                verdict INTEGER NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                model TEXT NOT NULL DEFAULT '',
                checked_at INTEGER NOT NULL
            )"""
        )
        db.execSQL("CREATE INDEX IF NOT EXISTS idx_complaint_verdict ON complaint_check(verdict, message_id)")
    }

    // ---- 민원 분류 ---------------------------------------------------------------------------

    /** 아직 분류하지 않은 대상 메시지 (오래된 순) */
    fun unclassified(limit: Int): List<Complaints.Item> = readableDatabase.rawQuery(
        "SELECT q.id, q.room, q.sender, q.text FROM queue q LEFT JOIN complaint_check c ON c.message_id = q.id " +
            "WHERE c.message_id IS NULL AND ${Complaints.eligibleSql("q.")} " +
            "ORDER BY q.id LIMIT $limit",
        null
    ).use { c -> buildList { while (c.moveToNext()) add(Complaints.Item(c.getLong(0), c.getString(1), c.getString(2), c.getString(3))) } }

    fun saveVerdicts(verdicts: List<Complaints.Verdict>, model: String, now: Long = System.currentTimeMillis()) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            for (v in verdicts) {
                db.execSQL(
                    "INSERT OR REPLACE INTO complaint_check (message_id, verdict, category, summary, confidence, model, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    arrayOf(v.id, if (v.isComplaint) 1 else 0, v.category, v.summary, v.confidence, model, now)
                )
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    /** 응답에 결과가 빠진 메시지: 실패로 기록 (재시도 버튼으로 다시 분류) */
    fun markClassifyFailed(ids: Collection<Long>, reason: String, now: Long = System.currentTimeMillis()) {
        val db = writableDatabase
        ids.forEach { id ->
            db.execSQL(
                "INSERT OR REPLACE INTO complaint_check (message_id, verdict, summary, checked_at) VALUES (?, -1, ?, ?)",
                arrayOf(id, reason.take(200), now)
            )
        }
    }

    fun resetFailedClassifications(): Int =
        writableDatabase.compileStatement("DELETE FROM complaint_check WHERE verdict = -1").executeUpdateDelete()

    fun complaintCounts(): ComplaintCounts = readableDatabase.rawQuery(
        "SELECT (SELECT COUNT(*) FROM queue WHERE ${Complaints.eligibleSql()}), " +
            "(SELECT COUNT(*) FROM complaint_check), " +
            "(SELECT COUNT(*) FROM complaint_check WHERE verdict = 1), " +
            "(SELECT COUNT(*) FROM complaint_check WHERE verdict = -1)",
        null
    ).use { c ->
        c.moveToFirst()
        ComplaintCounts(c.getInt(0), c.getInt(1), c.getInt(2), c.getInt(3))
    }

    /** 지정한 메시지들의 민원 정보 (묶음 상세 화면용) */
    fun complaintsByIds(ids: LongArray): List<Complaint> =
        if (ids.isEmpty()) emptyList()
        else complaints(idIn = ids, limit = ids.size).sortedBy { it.row.postedAt }

    /** 민원 목록 (최신순). category 가 있으면 해당 분류만 */
    fun complaints(category: String? = null, limit: Int = 2000, idIn: LongArray? = null): List<Complaint> {
        val args = mutableListOf<String>()
        val where = StringBuilder("c.verdict = 1")
        category?.let { where.append(" AND c.category = ?"); args += it }
        idIn?.let { ids -> where.append(" AND q.id IN (${ids.joinToString(",")})") }
        return readableDatabase.rawQuery(
            "SELECT q.*, c.category AS c_category, c.summary AS c_summary, c.confidence AS c_confidence, c.checked_at AS c_checked " +
                "FROM complaint_check c JOIN queue q ON q.id = c.message_id WHERE $where ORDER BY q.id DESC LIMIT $limit",
            args.toTypedArray()
        ).use { c ->
            buildList {
                while (c.moveToNext()) {
                    add(
                        Complaint(
                            row = rowFrom(c),
                            category = c.getString(c.getColumnIndexOrThrow("c_category")),
                            summary = c.getString(c.getColumnIndexOrThrow("c_summary")),
                            confidence = c.getDouble(c.getColumnIndexOrThrow("c_confidence")),
                            checkedAt = c.getLong(c.getColumnIndexOrThrow("c_checked")),
                        )
                    )
                }
            }
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
        writableDatabase.execSQL("DELETE FROM complaint_check")
        writableDatabase.execSQL("DELETE FROM member_events")
        writableDatabase.execSQL("DELETE FROM room_members")
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
    )

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
