package com.mahal.kakaocollector

import android.app.Notification
import android.app.PendingIntent
import android.app.RemoteInput
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.service.notification.StatusBarNotification
import java.util.concurrent.ConcurrentHashMap

/**
 * 카카오톡 알림의 '답장' 액션(RemoteInput)을 방별로 기억했다가, 그 방에 메시지를 보낸다.
 * PendingIntent 는 저장할 수 없으므로 메모리에만 보관한다 → 앱 프로세스가 살아 있는 동안 받은 알림만 답장 가능.
 *
 * 카카오톡 '댓글' 알림의 답장 버튼은 채팅방이 아니라 그 댓글 스레드로 보낸다.
 * 그래서 방마다 알림 종류(알림 ID·태그)별로 답장 수단을 따로 기억하고, 보낼 때는
 *   1) 댓글로 판단된 알림의 버튼은 쓰지 않고
 *   2) 남은 것 중 그 방에서 가장 자주 온 알림(= 일반 채팅방 알림)의 버튼을 쓴다.
 */
object ReplyRegistry {

    sealed interface Result {
        data object Sent : Result
        data object NoAction : Result
        /** 이 방은 댓글 알림만 받아서 채팅방으로 보낼 수단이 없음 */
        data object ThreadOnly : Result
        data class Failed(val reason: String) : Result
    }

    private class Entry(val action: Notification.Action, var at: Long, var count: Int, val thread: Boolean)

    /** 방 → (알림 식별자 → 답장 수단) */
    private val actions = ConcurrentHashMap<String, ConcurrentHashMap<String, Entry>>()

    /** 알림에 답장 액션이 있으면 그 방·그 알림 종류의 최신 답장 수단으로 기억 */
    fun remember(room: String, notification: Notification, identity: String = "default", thread: Boolean = false, now: Long = System.currentTimeMillis()) {
        val action = replyAction(notification) ?: return
        val perRoom = actions.getOrPut(RoomFilter.normalize(room)) { ConcurrentHashMap() }
        val key = (if (thread) "thread:" else "chat:") + identity
        val count = (perRoom[key]?.count ?: 0) + 1
        perRoom[key] = Entry(action, now, count, thread)
    }

    /** 리스너에서 쓰는 형태: 알림 식별자·댓글 여부를 알림에서 직접 판단 */
    fun remember(room: String, sbn: StatusBarNotification) =
        remember(room, sbn.notification, identityOf(sbn), ThreadDetector.isThread(sbn))

    fun canReply(room: String) = pick(room) != null

    fun replyAction(notification: Notification): Notification.Action? =
        notification.actions?.firstOrNull { a -> a.remoteInputs?.any { it.allowFreeFormInput } == true }

    /** 채팅방으로 보낼 답장 수단: 댓글 제외, 가장 자주 온 알림 → 같으면 최근 것 */
    private fun pick(room: String): Entry? =
        actions[RoomFilter.normalize(room)]?.values?.filter { !it.thread }?.maxWithOrNull(compareBy<Entry>({ it.count }, { it.at }))

    fun send(context: Context, room: String, text: String): Result {
        val perRoom = actions[RoomFilter.normalize(room)] ?: return Result.NoAction
        val entry = pick(room) ?: return if (perRoom.isNotEmpty()) Result.ThreadOnly else Result.NoAction
        val inputs = entry.action.remoteInputs ?: return Result.NoAction
        return try {
            val results = Bundle().apply {
                inputs.filter { it.allowFreeFormInput }.forEach { putCharSequence(it.resultKey, text) }
            }
            val fillIn = Intent()
            RemoteInput.addResultsToIntent(inputs, fillIn, results)
            entry.action.actionIntent.send(context, 0, fillIn)
            Result.Sent
        } catch (e: PendingIntent.CanceledException) {
            perRoom.values.remove(entry)
            Result.Failed("카톡 답장 기능이 만료됨 (알림이 사라졌거나 카톡이 재시작됨)")
        } catch (e: Exception) {
            Result.Failed(e.javaClass.simpleName + ": " + (e.message ?: ""))
        }
    }

    /** 같은 방이라도 일반 채팅·댓글 알림은 알림 ID/태그가 다를 수 있어 이것으로 구분해 센다 */
    fun identityOf(sbn: StatusBarNotification): String = "${sbn.id}|${sbn.tag ?: ""}"

    internal fun clearForTest() = actions.clear()
}

/**
 * 카카오톡 '댓글' 알림 판단. 버전마다 표시가 달라 여러 신호를 함께 본다.
 * (진단 기록에 판단 결과를 남겨 실제 알림 구조로 보정할 수 있게 한다)
 */
object ThreadDetector {
    private val MARKER = Regex("""댓글|답글|스레드|thread|comment""", RegexOption.IGNORE_CASE)
    private val KEY_MARKER = Regex("""thread|comment""", RegexOption.IGNORE_CASE)

    /** JVM 테스트용 순수 함수 */
    fun isThread(texts: List<String?>, extraKeys: Collection<String>, ids: List<String?>): Boolean =
        texts.any { it != null && MARKER.containsMatchIn(it) && !it.startsWith("[민원 처리 요청]") } ||
            extraKeys.any { KEY_MARKER.containsMatchIn(it) } ||
            ids.any { it != null && KEY_MARKER.containsMatchIn(it) }

    fun isThread(sbn: StatusBarNotification): Boolean {
        val n = sbn.notification
        val e = n.extras
        // 본문(사람이 쓴 내용)은 '댓글'이라는 단어가 들어갈 수 있어 제목·부제목류만 본다
        val texts = listOf(
            e.getCharSequence(Notification.EXTRA_TITLE)?.toString(),
            e.getCharSequence(Notification.EXTRA_SUB_TEXT)?.toString(),
            e.getCharSequence(Notification.EXTRA_SUMMARY_TEXT)?.toString(),
            e.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)?.toString(),
            replyActionTitle(n),
        )
        val ids = listOf(sbn.tag, n.group, if (android.os.Build.VERSION.SDK_INT >= 26) n.shortcutId else null, if (android.os.Build.VERSION.SDK_INT >= 26) n.channelId else null)
        return isThread(texts, e.keySet(), ids)
    }

    private fun replyActionTitle(n: Notification): String? = ReplyRegistry.replyAction(n)?.title?.toString()
}
