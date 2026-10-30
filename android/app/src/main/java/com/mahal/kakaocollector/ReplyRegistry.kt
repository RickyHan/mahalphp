package com.mahal.kakaocollector

import android.app.Notification
import android.app.PendingIntent
import android.app.RemoteInput
import android.content.Context
import android.content.Intent
import android.os.Bundle
import java.util.concurrent.ConcurrentHashMap

/**
 * 카카오톡 알림의 '답장' 액션(RemoteInput)을 방별로 기억했다가, 그 방에 메시지를 보낸다.
 * PendingIntent 는 저장할 수 없으므로 메모리에만 보관한다 → 앱 프로세스가 살아 있는 동안 받은 알림만 답장 가능.
 */
object ReplyRegistry {

    sealed interface Result {
        data object Sent : Result
        data object NoAction : Result
        data class Failed(val reason: String) : Result
    }

    private class Entry(val action: Notification.Action, val at: Long)

    private val actions = ConcurrentHashMap<String, Entry>()

    /** 알림에 답장 액션이 있으면 그 방의 최신 답장 수단으로 기억 */
    fun remember(room: String, notification: Notification, now: Long = System.currentTimeMillis()) {
        replyAction(notification)?.let { actions[RoomFilter.normalize(room)] = Entry(it, now) }
    }

    fun canReply(room: String) = actions.containsKey(RoomFilter.normalize(room))

    fun replyAction(notification: Notification): Notification.Action? =
        notification.actions?.firstOrNull { a -> a.remoteInputs?.any { it.allowFreeFormInput } == true }

    fun send(context: Context, room: String, text: String): Result {
        val entry = actions[RoomFilter.normalize(room)] ?: return Result.NoAction
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
            actions.remove(RoomFilter.normalize(room))
            Result.Failed("카톡 답장 기능이 만료됨 (알림이 사라졌거나 카톡이 재시작됨)")
        } catch (e: Exception) {
            Result.Failed(e.javaClass.simpleName + ": " + (e.message ?: ""))
        }
    }

    /** 안내 문구 치환: {보낸사람} {분류} {요약} {방} */
    fun format(template: String, sender: String, category: String, summary: String, room: String): String =
        template.replace("{보낸사람}", sender).replace("{분류}", category).replace("{요약}", summary).replace("{방}", room).trim()

    internal fun clearForTest() = actions.clear()
}
