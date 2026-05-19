package com.lifecoach.core.timeline

import com.lifecoach.core.model.Step
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.DisplayName
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.time.LocalTime

class TimelineEngineTest {

    private val morningSteps = listOf(
        Step(id = "wake", name = "起床", durationMinutes = 0, order = 1),
        Step(id = "breakfast", name = "朝食", durationMinutes = 15, order = 2),
        Step(id = "dress", name = "着替え", durationMinutes = 10, order = 3),
        Step(id = "teeth", name = "歯磨き", durationMinutes = 5, order = 4),
        Step(id = "depart", name = "出発", durationMinutes = 0, order = 5),
    )

    @Test
    @DisplayName("逆算で出発8:30から各ステップの計画時刻が決まる")
    fun backwardSchedulesFromDeparture() {
        val timeline = TimelineEngine.computeBackward(
            departureTime = LocalTime.of(8, 30),
            steps = morningSteps,
        )
        val byId = timeline.associateBy { it.step.id }

        assertEquals(LocalTime.of(8, 0), byId["wake"]!!.plannedStart)
        assertEquals(LocalTime.of(8, 0), byId["wake"]!!.plannedEnd)
        assertEquals(LocalTime.of(8, 0), byId["breakfast"]!!.plannedStart)
        assertEquals(LocalTime.of(8, 15), byId["breakfast"]!!.plannedEnd)
        assertEquals(LocalTime.of(8, 15), byId["dress"]!!.plannedStart)
        assertEquals(LocalTime.of(8, 25), byId["dress"]!!.plannedEnd)
        assertEquals(LocalTime.of(8, 25), byId["teeth"]!!.plannedStart)
        assertEquals(LocalTime.of(8, 30), byId["teeth"]!!.plannedEnd)
        assertEquals(LocalTime.of(8, 30), byId["depart"]!!.plannedStart)
        assertEquals(LocalTime.of(8, 30), byId["depart"]!!.plannedEnd)
    }

    @Test
    @DisplayName("順方向計算は開始時刻から積み上げる")
    fun forwardSchedulesFromStart() {
        val timeline = TimelineEngine.computeForward(
            startTime = LocalTime.of(7, 30),
            steps = listOf(
                Step(id = "a", name = "A", durationMinutes = 10, order = 1),
                Step(id = "b", name = "B", durationMinutes = 20, order = 2),
            ),
        )
        assertEquals(LocalTime.of(7, 30), timeline[0].plannedStart)
        assertEquals(LocalTime.of(7, 40), timeline[0].plannedEnd)
        assertEquals(LocalTime.of(7, 40), timeline[1].plannedStart)
        assertEquals(LocalTime.of(8, 0), timeline[1].plannedEnd)
    }

    @Test
    @DisplayName("orderフィールドに従って並び替えられる")
    fun sortsByOrder() {
        val unordered = listOf(
            Step(id = "c", name = "C", durationMinutes = 5, order = 3),
            Step(id = "a", name = "A", durationMinutes = 5, order = 1),
            Step(id = "b", name = "B", durationMinutes = 5, order = 2),
        )
        val timeline = TimelineEngine.computeForward(LocalTime.of(9, 0), unordered)
        assertEquals(listOf("a", "b", "c"), timeline.map { it.step.id })
    }

    @Test
    @DisplayName("空ステップ列は例外")
    fun emptyStepsThrows() {
        assertThrows<IllegalArgumentException> {
            TimelineEngine.computeBackward(LocalTime.NOON, emptyList())
        }
    }

    @Test
    @DisplayName("負の所要時間は例外")
    fun negativeDurationThrows() {
        assertThrows<IllegalArgumentException> {
            TimelineEngine.computeForward(
                LocalTime.NOON,
                listOf(Step("x", "X", -1, 1)),
            )
        }
    }

    @Test
    @DisplayName("余裕がある場合は圧縮しない")
    fun compressNoOpWhenEnough() {
        val compressed = TimelineEngine.compressToFit(morningSteps, availableMinutes = 60)
        assertEquals(morningSteps.map { it.durationMinutes }, compressed.map { it.durationMinutes })
    }

    @Test
    @DisplayName("比例圧縮するが最小分は確保する")
    fun compressProportionallyButKeepMinimum() {
        val steps = listOf(
            Step("a", "A", 30, 1),
            Step("b", "B", 20, 2),
            Step("c", "C", 10, 3),
        )
        val compressed = TimelineEngine.compressToFit(steps, availableMinutes = 30, minStepMinutes = 5)
        assertEquals(3, compressed.size)
        assertTrue(compressed.all { it.durationMinutes >= 5 })
        assertTrue(compressed.sumOf { it.durationMinutes } <= 30)
    }
}
