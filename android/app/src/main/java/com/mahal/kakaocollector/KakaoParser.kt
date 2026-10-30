package com.mahal.kakaocollector

/** 알림 객체에서 꺼낸 원시 값. Android 의존성이 없어 JVM 단위 테스트가 가능하다. */
data class RawNotification(
    val key: String,
    val postTime: Long,
    val whenTime: Long,
    val title: String?,
    val text: String?,
    val subText: String?,
    val conversationTitle: String?,
    val isGroupConversation: Boolean,
    val messages: List<RawMessage>,
    val hasPicture: Boolean,
    val isGroupSummary: Boolean,
    /** EXTRA_SUMMARY_TEXT. 일부 카카오톡 버전은 단톡방 이름을 여기에 넣는다 */
    val summaryText: String? = null,
    /**
     * Android 12+ 대화 바로가기(ShortcutInfo) 이름. 최신 카카오톡은 알림 본문에 방 이름을 넣지 않고
     * 이 바로가기에만 방 이름(오픈채팅 포함)을 등록하는 경우가 있다. 1:1 대화면 상대방 이름.
     */
    val shortcutLabel: String? = null,
)

/** MessagingStyle 의 메시지 1개. sender 가 null 이면 내가 보낸 메시지다. */
data class RawMessage(
    val text: String?,
    val sender: String?,
    val timestamp: Long,
    val dataMimeType: String? = null,
    val dataUri: String? = null,
)

enum class MessageType(val wire: String) { TEXT("text"), PHOTO("photo"), VIDEO("video"), EMOTICON("emoticon"), FILE("file"), OTHER("other") }

data class KakaoMessage(
    val room: String,
    val sender: String,
    val text: String,
    val type: MessageType,
    val isGroupChat: Boolean,
    val postedAt: Long,
    val notificationKey: String,
    /** MessagingStyle 메시지에 첨부된 이미지 URI (있을 때만) */
    val imageUri: String? = null,
)

object KakaoParser {
    const val KAKAO_PACKAGE = "com.kakao.talk"
    const val SELF_SENDER = "나"

    private val PHOTO = Regex("""^(사진(\s*\d+\s*장)?(을)?\s*보냈습니다\.?|사진|Photo|sent (a )?photos?\.?)$""", RegexOption.IGNORE_CASE)
    private val VIDEO = Regex("""^(동영상(을)?\s*보냈습니다\.?|동영상|Video|sent a video\.?)$""", RegexOption.IGNORE_CASE)
    private val EMOTICON = Regex("""^(\(?이모티콘\)?(을)?\s*(보냈습니다\.?)?|Emoticon|sent an emoticon\.?)$""", RegexOption.IGNORE_CASE)
    private val FILE = Regex("""^(파일\s*:|File\s*:)""", RegexOption.IGNORE_CASE)

    /** 카카오톡 알림 1건을 메시지 목록으로 변환한다. 대화 알림이 아니면 빈 목록. */
    fun parse(raw: RawNotification): List<KakaoMessage> {
        if (raw.isGroupSummary) return emptyList()
        return if (raw.messages.isNotEmpty()) parseMessagingStyle(raw) else listOfNotNull(parseLegacy(raw))
    }

    /**
     * 단톡방 이름 후보. 카카오톡 버전에 따라 대화 제목(conversationTitle) 또는
     * subText/summaryText(알림 상단 작은 글씨)에 들어온다. 없으면 1:1 대화.
     */
    private fun groupRoomName(raw: RawNotification): String? =
        raw.conversationTitle.clean()
            ?: raw.shortcutLabel.clean()?.takeIf { it != raw.title.clean() } // 1:1 이면 바로가기 이름 = 상대 이름
            ?: raw.subText.clean()
            ?: raw.summaryText.clean()

    private fun parseMessagingStyle(raw: RawNotification): List<KakaoMessage> {
        val groupRoom = groupRoomName(raw)
        // 1:1 대화는 방 이름 = 상대방 이름(알림 제목)
        val room = groupRoom ?: raw.title.clean()
        val isGroup = raw.isGroupConversation || groupRoom != null
        val lastIndex = raw.messages.lastIndex
        return raw.messages.mapIndexedNotNull { index, m ->
            val text = m.text.clean() ?: ""
            val hasImageData = m.dataMimeType?.startsWith("image/") == true && m.dataUri != null
            if (text.isEmpty() && !hasImageData) return@mapIndexedNotNull null
            val sender = m.sender.clean() ?: SELF_SENDER
            // BigPicture(EXTRA_PICTURE) 는 알림의 최신 메시지에만 해당한다
            val pictureHint = hasImageData || (raw.hasPicture && index == lastIndex)
            KakaoMessage(
                room = room ?: sender,
                sender = sender,
                text = text,
                type = classify(text, pictureHint),
                isGroupChat = isGroup,
                postedAt = m.timestamp.takeIf { it > 0 } ?: raw.fallbackTime(),
                notificationKey = raw.key,
                imageUri = if (hasImageData) m.dataUri else null,
            )
        }
    }

    /** 구형 카카오톡: title=보낸 사람, text=내용, subText=단톡방 이름(1:1 이면 없음) */
    private fun parseLegacy(raw: RawNotification): KakaoMessage? {
        val sender = raw.title.clean() ?: return null
        val text = raw.text.clean() ?: return null
        val room = groupRoomName(raw)
        return KakaoMessage(
            room = room ?: sender,
            sender = sender,
            text = text,
            type = classify(text, raw.hasPicture),
            isGroupChat = room != null,
            postedAt = raw.fallbackTime(),
            notificationKey = raw.key,
        )
    }

    fun classify(text: String, hasPicture: Boolean): MessageType {
        val t = text.trim()
        return when {
            hasPicture || PHOTO.matches(t) -> MessageType.PHOTO
            VIDEO.matches(t) -> MessageType.VIDEO
            EMOTICON.matches(t) -> MessageType.EMOTICON
            FILE.containsMatchIn(t) -> MessageType.FILE
            else -> MessageType.TEXT
        }
    }

    private fun RawNotification.fallbackTime() = whenTime.takeIf { it > 0 } ?: postTime

    private fun String?.clean(): String? = this?.trim()?.takeIf { it.isNotEmpty() }
}
