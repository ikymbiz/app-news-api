package com.lifecoach.core.pomodoro

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.DisplayName
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows

class PomodoroEngineTest {

    @Test
    @DisplayName("標準25/5/4サイクルのスケジュール")
    fun standardSchedule() {
        val phases = PomodoroEngine.schedule(PomodoroConfig())
        // 4 cycles * (FocusStart + FocusEnd) = 8
        // 3 inter-cycle breaks * (BreakStart + BreakEnd) = 6
        // + AllDone = 15 events
        assertEquals(15, phases.size)
        assertTrue(phases.first().event is PomodoroEvent.FocusStart)
        assertTrue(phases.last().event is PomodoroEvent.AllDone)
        // total = 25*4 + 5*3 = 115
        assertEquals(115, phases.last().atMinuteFromStart)
    }

    @Test
    @DisplayName("最終サイクル後に休憩は無い")
    fun noBreakAfterLastCycle() {
        val phases = PomodoroEngine.schedule(PomodoroConfig(focusMinutes = 25, breakMinutes = 5, cycles = 2))
        val breakStarts = phases.count { it.event is PomodoroEvent.BreakStart }
        val breakEnds = phases.count { it.event is PomodoroEvent.BreakEnd }
        assertEquals(1, breakStarts)
        assertEquals(1, breakEnds)
    }

    @Test
    @DisplayName("1サイクルだけなら集中だけで終わる")
    fun singleCycleHasNoBreak() {
        val phases = PomodoroEngine.schedule(PomodoroConfig(focusMinutes = 10, breakMinutes = 5, cycles = 1))
        assertEquals(3, phases.size)
        assertTrue(phases[0].event is PomodoroEvent.FocusStart)
        assertTrue(phases[1].event is PomodoroEvent.FocusEnd)
        assertTrue(phases[2].event is PomodoroEvent.AllDone)
        assertEquals(10, phases[2].atMinuteFromStart)
    }

    @Test
    @DisplayName("短縮動作確認モード")
    fun shortDurationMode() {
        val phases = PomodoroEngine.schedule(PomodoroConfig(focusMinutes = 1, breakMinutes = 1, cycles = 3))
        // 3 focuses (1m each) + 2 breaks (1m each) = 5m
        assertEquals(5, phases.last().atMinuteFromStart)
        assertEquals(5, PomodoroEngine.totalDurationMinutes(PomodoroConfig(1, 1, 3)))
    }

    @Test
    @DisplayName("休憩0でも動く")
    fun zeroBreakWorks() {
        val phases = PomodoroEngine.schedule(PomodoroConfig(focusMinutes = 25, breakMinutes = 0, cycles = 3))
        assertEquals(0, phases.count { it.event is PomodoroEvent.BreakStart })
        assertEquals(75, phases.last().atMinuteFromStart)
    }

    @Test
    @DisplayName("サイクルインデックスが順番に振られる")
    fun cycleIndicesIncrement() {
        val phases = PomodoroEngine.schedule(PomodoroConfig(focusMinutes = 1, breakMinutes = 1, cycles = 3))
        val focusStarts = phases.mapNotNull { (it.event as? PomodoroEvent.FocusStart)?.cycleIndex }
        assertEquals(listOf(0, 1, 2), focusStarts)
    }

    @Test
    @DisplayName("不正パラメータは例外")
    fun invalidParamsThrow() {
        assertThrows<IllegalArgumentException> { PomodoroConfig(focusMinutes = 0) }
        assertThrows<IllegalArgumentException> { PomodoroConfig(breakMinutes = -1) }
        assertThrows<IllegalArgumentException> { PomodoroConfig(cycles = 0) }
    }
}
