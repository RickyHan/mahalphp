package com.mahal.kakaocollector

import android.app.Notification
import android.content.Context
import android.graphics.Bitmap
import android.graphics.drawable.Icon
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.graphics.drawable.toBitmap
import androidx.core.os.BundleCompat
import java.io.File
import java.util.UUID

/** StatusBarNotification → RawNotification 변환과 사진 추출. */
object NotificationExtractor {
    private const val TAG = "NotificationExtractor"
    private const val EXTRA_PICTURE_ICON = "android.pictureIcon" // Notification.EXTRA_PICTURE_ICON (API 31+)

    fun extract(sbn: StatusBarNotification): RawNotification {
        val n = sbn.notification
        val extras = n.extras
        val style = NotificationCompat.MessagingStyle.extractMessagingStyleFromNotification(n)
        val messages = style?.messages.orEmpty().map { m ->
            RawMessage(
                text = m.text?.toString(),
                sender = m.person?.name?.toString(),
                timestamp = m.timestamp,
                dataMimeType = m.dataMimeType,
                dataUri = m.dataUri?.toString(),
            )
        }
        return RawNotification(
            key = sbn.key,
            postTime = sbn.postTime,
            whenTime = n.`when`,
            title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString(),
            text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString(),
            subText = extras.getCharSequence(Notification.EXTRA_SUB_TEXT)?.toString(),
            conversationTitle = style?.conversationTitle?.toString()
                ?: extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)?.toString(),
            isGroupConversation = style?.isGroupConversation ?: false,
            messages = messages,
            hasPicture = extras.containsKey(Notification.EXTRA_PICTURE) || extras.containsKey(EXTRA_PICTURE_ICON),
            isGroupSummary = n.flags and Notification.FLAG_GROUP_SUMMARY != 0,
        )
    }

    /**
     * 사진 메시지의 이미지를 앱 내부 저장소에 JPEG/원본 파일로 저장하고 경로를 반환한다.
     * 1) MessagingStyle 메시지의 content:// URI (카카오톡이 권한을 열어 준 경우에만 읽힘)
     * 2) BigPictureStyle 미리보기 비트맵 (최신 메시지에만 해당)
     * 둘 다 없거나 읽을 수 없으면 null — 이 경우 텍스트("사진을 보냈습니다.")만 저장된다.
     */
    fun saveImage(context: Context, sbn: StatusBarNotification, message: KakaoMessage, isLatest: Boolean): String? {
        val dir = File(context.filesDir, "pending_media").apply { mkdirs() }
        message.imageUri?.let { uri ->
            runCatching {
                val target = File(dir, "${UUID.randomUUID()}.img")
                context.contentResolver.openInputStream(Uri.parse(uri))?.use { input ->
                    target.outputStream().use { input.copyTo(it) }
                }
                if (target.length() > 0) return target.absolutePath else target.delete()
            }.onFailure { Log.i(TAG, "이미지 URI 접근 불가: ${it.javaClass.simpleName}") }
        }
        if (!isLatest) return null
        val bitmap = pictureFrom(context, sbn.notification.extras) ?: return null
        val target = File(dir, "${UUID.randomUUID()}.jpg")
        return runCatching {
            target.outputStream().use { bitmap.compress(Bitmap.CompressFormat.JPEG, 92, it) }
            target.absolutePath
        }.getOrElse {
            target.delete()
            null
        }
    }

    private fun pictureFrom(context: Context, extras: Bundle): Bitmap? {
        BundleCompat.getParcelable(extras, Notification.EXTRA_PICTURE, Bitmap::class.java)?.let { return it }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val icon = BundleCompat.getParcelable(extras, EXTRA_PICTURE_ICON, Icon::class.java) ?: return null
            return runCatching { icon.loadDrawable(context)?.toBitmap() }.getOrNull()
        }
        return null
    }
}
