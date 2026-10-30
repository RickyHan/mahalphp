package com.mahal.kakaocollector

import java.util.Calendar
import java.util.TimeZone

/**
 * 민원 집계. '문제'(ComplaintGrouping 묶음) 단위로 센다 — 같은 사람이 같은 문제로 여러 번 말해도 1건.
 * 기간에 속하는 기준은 문제의 시작 시각. Android 의존성 없음.
 */
object ComplaintStats {

    enum class Period(val label: String, val days: Int?) {
        TODAY("오늘", 1), WEEK("7일", 7), MONTH("30일", 30), ALL("전체", null)
    }

    data class Stats(
        val issues: Int,
        val messages: Int,
        val repeatedIssues: Int,
        val replySent: Int,
        val replyFailed: Int,
        val replySkipped: Int,
        val byCategory: List<Pair<String, Int>>,
        val byRoom: List<Pair<String, Int>>,
        val bySender: List<Pair<String, Int>>,
        /** (MM/dd, 문제 수) — 기간의 날짜별, 전체는 최근 30일 */
        val daily: List<Pair<String, Int>>,
        /** 0~23시별 문제 수 */
        val hourly: IntArray,
    )

    /** 기간 시작 시각 (오늘 0시 기준으로 N일). 전체는 null */
    fun periodStart(period: Period, now: Long, zone: TimeZone = TimeZone.getDefault()): Long? {
        val days = period.days ?: return null
        return startOfDay(now, zone).apply { add(Calendar.DAY_OF_MONTH, -(days - 1)) }.timeInMillis
    }

    fun compute(
        groups: List<ComplaintGrouping.Group>,
        period: Period,
        now: Long,
        zone: TimeZone = TimeZone.getDefault(),
        top: Int = 10,
    ): Stats {
        val from = periodStart(period, now, zone)
        val inRange = groups.filter { from == null || it.startAt >= from }
        val cal = Calendar.getInstance(zone)

        fun ranked(keyOf: (ComplaintGrouping.Group) -> String) =
            inRange.groupingBy(keyOf).eachCount().entries
                .sortedWith(compareByDescending<Map.Entry<String, Int>> { it.value }.thenBy { it.key })
                .take(top).map { it.key to it.value }

        val dayCount = (period.days ?: 30)
        val dayStart = startOfDay(now, zone).apply { add(Calendar.DAY_OF_MONTH, -(dayCount - 1)) }
        val daily = (0 until dayCount).map { offset ->
            val start = (dayStart.clone() as Calendar).apply { add(Calendar.DAY_OF_MONTH, offset) }
            val end = (start.clone() as Calendar).apply { add(Calendar.DAY_OF_MONTH, 1) }
            val label = "%02d/%02d".format(start.get(Calendar.MONTH) + 1, start.get(Calendar.DAY_OF_MONTH))
            label to groups.count { it.startAt >= start.timeInMillis && it.startAt < end.timeInMillis }
        }

        val hourly = IntArray(24)
        inRange.forEach { g -> cal.timeInMillis = g.startAt; hourly[cal.get(Calendar.HOUR_OF_DAY)]++ }

        val firsts = inRange.map { it.first }
        return Stats(
            issues = inRange.size,
            messages = inRange.sumOf { it.count },
            repeatedIssues = inRange.count { it.count > 1 },
            replySent = firsts.count { it.replyStatus == 1 },
            replyFailed = firsts.count { it.replyStatus == -1 },
            replySkipped = firsts.count { it.replyStatus == 3 },
            byCategory = ranked { it.category },
            byRoom = ranked { it.room },
            bySender = ranked { "${it.sender} · ${it.room}" },
            daily = daily,
            hourly = hourly,
        )
    }

    private fun startOfDay(now: Long, zone: TimeZone): Calendar = Calendar.getInstance(zone).apply {
        timeInMillis = now
        set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
    }
}
