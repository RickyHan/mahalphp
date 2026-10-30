package com.mahal.kakaocollector

import org.json.JSONArray
import org.json.JSONObject

/**
 * 입주민 민원 분류 규칙과 Gemini API 요청·응답 형식.
 * - 대상: 텍스트 메시지 중 공백을 뺀 글자 수가 [MIN_CHARS] 이상인 것
 * - 결과: is_complaint, 분류(category), 요약(summary), 확신도(confidence)
 */
object Complaints {
    const val MIN_CHARS = 5
    const val BATCH = 20

    val CATEGORIES = listOf("소음", "누수·하자", "주차", "청소·쓰레기", "시설·고장", "관리비", "보안·출입", "생활·이웃", "기타")

    data class Item(val id: Long, val room: String, val sender: String, val text: String)

    data class Verdict(val id: Long, val isComplaint: Boolean, val category: String, val summary: String, val confidence: Double)

    /** 분류 대상 여부. MessageStore 의 SQL 조건(eligibleSql)과 같은 규칙 */
    fun isEligible(type: String, text: String): Boolean =
        type == "text" && text.filterNot { it == ' ' || it == '\n' || it == '\r' || it == '\t' }.let { it.codePointCount(0, it.length) } >= MIN_CHARS

    /** SQLite 조건식: 텍스트 메시지 && 공백·줄바꿈·탭 제외 글자 수 ≥ MIN_CHARS. [alias] 는 테이블 별칭(예: "q.") */
    fun eligibleSql(alias: String = ""): String =
        "${alias}type = 'text' AND length(replace(replace(replace(replace(${alias}text, ' ', ''), char(10), ''), char(13), ''), char(9), '')) >= $MIN_CHARS"

    val SYSTEM_PROMPT = """
        너는 아파트·오피스텔 등 공동주택 관리사무소의 민원 분류 담당자다.
        카카오톡 대화방에서 수집한 메시지 각각이 "건물에 사는 입주민이 제기하는 민원"인지 판정하라.

        민원으로 본다: 입주민이 건물·시설·관리·생활 불편에 대해 해결·조치·개선을 요청하거나 불만·문제를 알리는 메시지.
          예) 층간소음, 누수·결로·하자, 주차·불법주차, 청소·쓰레기·분리수거, 엘리베이터·조명·보일러 등 시설 고장,
              관리비 문의·이의, 출입·보안·택배, 흡연·냄새·반려동물 등 이웃 간 생활 불편
        민원이 아니다: 인사·잡담·감사, 불편 제기가 아닌 단순 대화, 관리사무소·관리자의 공지나 답변,
          광고·홍보, 개인 간 거래, 건물과 무관한 내용. 애매하면 민원이 아닌 것으로 판정한다.

        분류 기준: 층간소음·생활소음 등 소리 문제는 "소음", 물 새는 문제·시공 불량은 "누수·하자",
          이웃 간 흡연·냄새·반려동물 문제는 "생활·이웃", 해당 없는 민원은 "기타".

        반드시 아래 JSON 형식으로만 답하라. 입력의 모든 id 에 대해 결과를 하나씩 포함한다.
        {"results":[{"id":입력id,"is_complaint":true 또는 false,
          "category":"${CATEGORIES.joinToString("|")}",
          "summary":"민원이면 핵심 내용을 25자 이내로, 아니면 빈 문자열",
          "confidence":0.0~1.0}]}
    """.trimIndent()

    fun userContent(items: List<Item>): String =
        JSONArray().apply {
            items.forEach { put(JSONObject().put("id", it.id).put("room", it.room).put("sender", it.sender).put("text", it.text.take(1000))) }
        }.toString()

    /** Gemini generateContent 요청 본문 (JSON 응답 강제, 결정적 출력) */
    fun requestBody(items: List<Item>): String =
        JSONObject()
            .put("systemInstruction", JSONObject().put("parts", JSONArray().put(JSONObject().put("text", SYSTEM_PROMPT))))
            .put(
                "contents",
                JSONArray().put(
                    JSONObject().put("role", "user").put("parts", JSONArray().put(JSONObject().put("text", userContent(items))))
                )
            )
            .put("generationConfig", JSONObject().put("temperature", 0).put("responseMimeType", "application/json"))
            .toString()

    /**
     * Gemini 응답 → 판정 목록. 요청에 없던 id 는 버린다.
     * 형식이 잘못되면 IllegalArgumentException.
     */
    fun parseResponse(body: String, requestedIds: Set<Long>): List<Verdict> {
        val content = runCatching {
            val parts = JSONObject(body).getJSONArray("candidates").getJSONObject(0).getJSONObject("content").getJSONArray("parts")
            (0 until parts.length()).joinToString("") { parts.getJSONObject(it).optString("text") }
        }.getOrElse { throw IllegalArgumentException("응답 형식 오류: ${it.message}") }
        val results = runCatching { JSONObject(content).getJSONArray("results") }
            .getOrElse { throw IllegalArgumentException("결과 JSON 오류: ${it.message}") }
        return buildList {
            for (i in 0 until results.length()) {
                val r = results.optJSONObject(i) ?: continue
                val id = r.optLong("id", -1)
                if (id !in requestedIds) continue
                val category = r.optString("category").takeIf { it in CATEGORIES } ?: "기타"
                add(
                    Verdict(
                        id = id,
                        isComplaint = r.optBoolean("is_complaint", false),
                        category = category,
                        summary = r.optString("summary").take(100),
                        confidence = r.optDouble("confidence", 0.0).coerceIn(0.0, 1.0),
                    )
                )
            }
        }.distinctBy { it.id }
    }
}
