package com.mahal.kakaocollector

import androidx.test.core.app.ApplicationProvider
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** 민원 분류: 대상 판정, Gemini 요청·응답 처리, 저장소 쿼리 (org.json 사용을 위해 Robolectric) */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class ComplaintsTest {

    private lateinit var store: MessageStore

    @Before
    fun setUp() {
        val ctx = ApplicationProvider.getApplicationContext<android.content.Context>()
        MessageStore.resetForTest()
        ctx.deleteDatabase("queue.db")
        store = MessageStore.get(ctx)
    }

    @After
    fun tearDown() = MessageStore.resetForTest()

    private fun geminiResponse(resultsJson: String): String =
        JSONObject().put(
            "candidates",
            org.json.JSONArray().put(
                JSONObject().put("content", JSONObject().put("parts", org.json.JSONArray().put(JSONObject().put("text", resultsJson))))
            )
        ).toString()

    @Test
    fun eligibilityCountsNonWhitespaceCharacters() {
        assertTrue(Complaints.isEligible("text", "주차 문제요"))      // 공백 제외 5자
        assertFalse(Complaints.isEligible("text", "넵 알겠어"))       // 4자
        assertFalse(Complaints.isEligible("text", "ㅋ ㅋ ㅋ ㅋ"))
        assertTrue(Complaints.isEligible("text", "😡😡😡😡😡"))       // 이모지도 글자 1개씩
        assertFalse(Complaints.isEligible("photo", "사진을 보냈습니다."))
    }

    @Test
    fun sqlEligibilityMatchesKotlinRule() {
        val samples = listOf("주차 문제요", "넵 알겠어", "ㅋ ㅋ ㅋ ㅋ", "엘리베이터 고장났어요", "a\nb\nc\nd", "abcde")
        samples.forEachIndexed { i, text ->
            store.insert(KakaoMessage("방", "a", text, MessageType.TEXT, true, i.toLong() + 1, "k"), null, upload = false)
        }
        store.insert(KakaoMessage("방", "a", "사진을 보냈습니다.", MessageType.PHOTO, true, 100, "k"), null, upload = false)
        val viaSql = store.unclassified(100).map { it.text }
        assertEquals(samples.filter { Complaints.isEligible("text", it) }, viaSql)
    }

    @Test
    fun requestBodyIsGeminiJsonMode() {
        val body = JSONObject(Complaints.requestBody(listOf(Complaints.Item(7, "입주민방", "301호", "윗집 소음이 심해요"))))
        assertTrue(body.getJSONObject("systemInstruction").toString().contains("민원"))
        assertEquals("application/json", body.getJSONObject("generationConfig").getString("responseMimeType"))
        assertEquals(0, body.getJSONObject("generationConfig").getInt("temperature"))
        val userText = body.getJSONArray("contents").getJSONObject(0).getJSONArray("parts").getJSONObject(0).getString("text")
        assertTrue(userText.contains("\"id\":7") && userText.contains("윗집 소음이 심해요"))
    }

    @Test
    fun parsesVerdictsAndIgnoresUnknownIds() {
        val body = geminiResponse(
            """{"results":[
                {"id":1,"is_complaint":true,"category":"소음","summary":"층간소음","confidence":0.9},
                {"id":2,"is_complaint":false,"category":"기타","summary":"","confidence":0.8},
                {"id":99,"is_complaint":true,"category":"소음","summary":"요청에 없던 id","confidence":1},
                {"id":3,"is_complaint":true,"category":"없는분류","summary":"x","confidence":7}
            ]}"""
        )
        val verdicts = Complaints.parseResponse(body, setOf(1, 2, 3))
        assertEquals(listOf(1L, 2L, 3L), verdicts.map { it.id })
        assertTrue(verdicts[0].isComplaint)
        assertEquals("층간소음", verdicts[0].summary)
        assertFalse(verdicts[1].isComplaint)
        assertEquals("기타", verdicts[2].category)      // 목록 밖 분류 → 기타
        assertEquals(1.0, verdicts[2].confidence, 0.0)  // 범위 보정
    }

    @Test(expected = IllegalArgumentException::class)
    fun malformedResponseThrows() {
        Complaints.parseResponse(geminiResponse("not json"), setOf(1))
    }

    @Test
    fun storesVerdictsAndListsComplaintsOnly() {
        listOf("윗집 소음이 너무 심합니다", "다들 좋은 아침입니다", "지하주차장 이중주차 차량 빼주세요").forEachIndexed { i, t ->
            store.insert(KakaoMessage("입주민방", "${i + 1}01호", t, MessageType.TEXT, true, i.toLong() + 1, "k"), null, upload = false)
        }
        val items = store.unclassified(10)
        assertEquals(3, items.size)
        store.saveVerdicts(
            listOf(
                Complaints.Verdict(items[0].id, true, "소음", "층간소음", 0.9),
                Complaints.Verdict(items[1].id, false, "기타", "", 0.9),
            ),
            "test-model"
        )
        store.markClassifyFailed(listOf(items[2].id), "응답에 결과 없음")

        assertEquals(listOf("윗집 소음이 너무 심합니다"), store.complaints().map { it.row.text })
        assertEquals("소음", store.complaints().single().category)
        assertTrue(store.complaints("주차").isEmpty())
        assertTrue(store.unclassified(10).isEmpty())

        var c = store.complaintCounts()
        assertEquals(3, c.eligible); assertEquals(3, c.checked); assertEquals(1, c.complaints); assertEquals(1, c.failed)

        assertEquals(1, store.resetFailedClassifications())
        assertEquals(listOf(items[2].id), store.unclassified(10).map { it.id })

        store.deleteAll()
        c = store.complaintCounts()
        assertEquals(0, c.eligible); assertEquals(0, c.checked)
    }
}
