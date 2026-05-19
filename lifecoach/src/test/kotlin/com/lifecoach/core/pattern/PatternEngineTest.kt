package com.lifecoach.core.pattern

import com.lifecoach.core.model.ModeChoiceLog
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.DisplayName
import org.junit.jupiter.api.Test
import java.time.DayOfWeek
import java.time.LocalDate

class PatternEngineTest {

    private fun monday(week: Int) = LocalDate.of(2026, 5, 4).plusWeeks(week.toLong())

    @Test
    @DisplayName("4週間連続出社の月曜は信頼度100%")
    fun fullConfidence() {
        val logs = (0..3).map { ModeChoiceLog(monday(it), "commute") }
        val patterns = PatternEngine.detect(logs)
        val mon = patterns[DayOfWeek.MONDAY]!!
        assertEquals("commute", mon.mostFrequentModeId)
        assertEquals(1.0, mon.confidence, 0.001)
        assertEquals(4, mon.sampleCount)
        assertTrue(mon.isReliable())
    }

    @Test
    @DisplayName("混在する場合は最頻が選ばれる")
    fun pickMostFrequent() {
        val logs = listOf(
            ModeChoiceLog(monday(0), "commute"),
            ModeChoiceLog(monday(1), "commute"),
            ModeChoiceLog(monday(2), "home"),
            ModeChoiceLog(monday(3), "commute"),
        )
        val mon = PatternEngine.detect(logs)[DayOfWeek.MONDAY]!!
        assertEquals("commute", mon.mostFrequentModeId)
        assertEquals(0.75, mon.confidence, 0.001)
        assertEquals(mapOf("commute" to 3, "home" to 1), mon.distribution)
    }

    @Test
    @DisplayName("データのない曜日は空パターン")
    fun emptyWeekday() {
        val logs = listOf(ModeChoiceLog(monday(0), "commute"))
        val tue = PatternEngine.detect(logs)[DayOfWeek.TUESDAY]!!
        assertNull(tue.mostFrequentModeId)
        assertEquals(0, tue.sampleCount)
        assertEquals(0.0, tue.confidence, 0.001)
        assertTrue(!tue.isReliable())
    }

    @Test
    @DisplayName("信頼パターンだけ取り出せる")
    fun extractOnlyReliable() {
        val logs = listOf(
            // monday: 4 commute → 100%
            ModeChoiceLog(monday(0), "commute"),
            ModeChoiceLog(monday(1), "commute"),
            ModeChoiceLog(monday(2), "commute"),
            ModeChoiceLog(monday(3), "commute"),
            // tuesday: only 2 samples → too few
            ModeChoiceLog(monday(0).plusDays(1), "home"),
            ModeChoiceLog(monday(1).plusDays(1), "home"),
            // wednesday: 2/2 split → low confidence
            ModeChoiceLog(monday(0).plusDays(2), "home"),
            ModeChoiceLog(monday(1).plusDays(2), "commute"),
            ModeChoiceLog(monday(2).plusDays(2), "home"),
            ModeChoiceLog(monday(3).plusDays(2), "commute"),
        )
        val reliable = PatternEngine.detectReliable(logs)
        assertEquals(setOf(DayOfWeek.MONDAY), reliable.keys)
    }

    @Test
    @DisplayName("閾値を緩めれば多くの曜日が信頼判定される")
    fun lowerThresholds() {
        val logs = listOf(
            ModeChoiceLog(monday(0), "commute"),
            ModeChoiceLog(monday(1), "commute"),
            ModeChoiceLog(monday(0).plusDays(1), "home"),
            ModeChoiceLog(monday(1).plusDays(1), "home"),
        )
        val reliable = PatternEngine.detectReliable(logs, minSamples = 2, minConfidence = 0.5)
        assertEquals(setOf(DayOfWeek.MONDAY, DayOfWeek.TUESDAY), reliable.keys)
    }
}
