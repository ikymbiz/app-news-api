package com.lifecoach.core.diff

import com.lifecoach.core.model.ActionLog
import com.lifecoach.core.model.Step
import com.lifecoach.core.timeline.TimelineEngine
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.DisplayName
import org.junit.jupiter.api.Test
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime

class DiffEngineTest {

    private val date = LocalDate.of(2026, 5, 19)
    private fun at(t: LocalTime) = LocalDateTime.of(date, t)

    private val steps = listOf(
        Step("breakfast", "朝食", 15, 1),
        Step("dress", "着替え", 10, 2),
        Step("teeth", "歯磨き", 5, 3),
    )
    private val plan = TimelineEngine.computeForward(LocalTime.of(8, 0), steps)
    // plan: breakfast 8:00-8:15, dress 8:15-8:25, teeth 8:25-8:30

    @Test
    @DisplayName("予定通りなら差分0")
    fun onTime() {
        val actual = listOf(
            ActionLog("breakfast", at(LocalTime.of(8, 0)), at(LocalTime.of(8, 0)), at(LocalTime.of(8, 15))),
            ActionLog("dress", at(LocalTime.of(8, 15)), at(LocalTime.of(8, 15)), at(LocalTime.of(8, 25))),
            ActionLog("teeth", at(LocalTime.of(8, 25)), at(LocalTime.of(8, 25)), at(LocalTime.of(8, 30))),
        )
        val r = DiffEngine.compare(plan, actual)
        assertEquals(0, r.totalDeltaMinutes)
        assertTrue(r.isOnTime)
        assertEquals(listOf(0, 0, 0), r.stepDiffs.map { it.deltaMinutes })
    }

    @Test
    @DisplayName("着替えが5分超過")
    fun dressOverrun() {
        val actual = listOf(
            ActionLog("breakfast", at(LocalTime.of(8, 0)), at(LocalTime.of(8, 0)), at(LocalTime.of(8, 15))),
            ActionLog("dress", at(LocalTime.of(8, 15)), at(LocalTime.of(8, 15)), at(LocalTime.of(8, 30))),
            ActionLog("teeth", at(LocalTime.of(8, 25)), at(LocalTime.of(8, 30)), at(LocalTime.of(8, 35))),
        )
        val r = DiffEngine.compare(plan, actual)
        val dress = r.stepDiffs.first { it.stepId == "dress" }
        assertEquals(15, dress.actualDurationMinutes)
        assertEquals(5, dress.deltaMinutes)
        assertEquals(5, r.totalDeltaMinutes)
        assertTrue(r.isBehind)
    }

    @Test
    @DisplayName("早く終わると負の差分")
    fun aheadOfSchedule() {
        val actual = listOf(
            ActionLog("breakfast", at(LocalTime.of(8, 0)), at(LocalTime.of(8, 0)), at(LocalTime.of(8, 10))),
            ActionLog("dress", at(LocalTime.of(8, 15)), at(LocalTime.of(8, 10)), at(LocalTime.of(8, 20))),
            ActionLog("teeth", at(LocalTime.of(8, 25)), at(LocalTime.of(8, 20)), at(LocalTime.of(8, 25))),
        )
        val r = DiffEngine.compare(plan, actual)
        assertEquals(-5, r.totalDeltaMinutes)
        assertTrue(r.isAhead)
    }

    @Test
    @DisplayName("実績が無いステップはnull差分")
    fun missingActualBecomesNullDelta() {
        val actual = listOf(
            ActionLog("breakfast", at(LocalTime.of(8, 0)), at(LocalTime.of(8, 0)), at(LocalTime.of(8, 15))),
        )
        val r = DiffEngine.compare(plan, actual)
        assertNull(r.stepDiffs.first { it.stepId == "dress" }.deltaMinutes)
        // total uses planned for missing → delta 0
        assertEquals(0, r.totalDeltaMinutes)
    }

    @Test
    @DisplayName("傾向分析で平均超過がわかる")
    fun trendDetectsConsistentOverrun() {
        val history = listOf(
            ActionLog("dress", at(LocalTime.of(8, 15)), at(LocalTime.of(8, 15)), at(LocalTime.of(8, 30))),
            ActionLog("dress", at(LocalTime.of(8, 15)), at(LocalTime.of(8, 15)), at(LocalTime.of(8, 26))),
            ActionLog("dress", at(LocalTime.of(8, 15)), at(LocalTime.of(8, 15)), at(LocalTime.of(8, 28))),
        ).map { listOf(it) }.map { DiffEngine.compare(plan, it) }

        val trend = DiffEngine.trend(history)
        val dressTrend = trend.first { it.stepId == "dress" }
        // deltas: +5, +1, +3 → avg 3.0
        assertEquals(3.0, dressTrend.averageDeltaMinutes, 0.01)
        assertEquals(3, dressTrend.sampleCount)
        assertTrue(dressTrend.isConsistentlyOver(thresholdMinutes = 3.0))
    }

    @Test
    @DisplayName("開始の遅延も検出する")
    fun detectsStartDelay() {
        val actual = listOf(
            ActionLog("breakfast", at(LocalTime.of(8, 0)), at(LocalTime.of(8, 7)), at(LocalTime.of(8, 22))),
        )
        val r = DiffEngine.compare(plan, actual)
        assertEquals(7, r.stepDiffs.first { it.stepId == "breakfast" }.startDeltaMinutes)
    }
}
