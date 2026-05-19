package com.lifecoach.core.timeline

import com.lifecoach.core.model.Step
import java.time.LocalTime

data class TimelineEntry(
    val step: Step,
    val plannedStart: LocalTime,
    val plannedEnd: LocalTime,
) {
    val durationMinutes: Int get() = step.durationMinutes
}

object TimelineEngine {

    fun computeBackward(
        departureTime: LocalTime,
        steps: List<Step>,
    ): List<TimelineEntry> {
        require(steps.isNotEmpty()) { "steps must not be empty" }
        require(steps.all { it.durationMinutes >= 0 }) { "durationMinutes must be >= 0" }

        val ordered = steps.sortedBy { it.order }
        val result = ArrayDeque<TimelineEntry>(ordered.size)
        var cursor = departureTime

        for (step in ordered.asReversed()) {
            val end = cursor
            val start = cursor.minusMinutes(step.durationMinutes.toLong())
            result.addFirst(TimelineEntry(step = step, plannedStart = start, plannedEnd = end))
            cursor = start
        }
        return result.toList()
    }

    fun computeForward(
        startTime: LocalTime,
        steps: List<Step>,
    ): List<TimelineEntry> {
        require(steps.isNotEmpty()) { "steps must not be empty" }
        require(steps.all { it.durationMinutes >= 0 }) { "durationMinutes must be >= 0" }

        val ordered = steps.sortedBy { it.order }
        val result = ArrayList<TimelineEntry>(ordered.size)
        var cursor = startTime
        for (step in ordered) {
            val start = cursor
            val end = cursor.plusMinutes(step.durationMinutes.toLong())
            result += TimelineEntry(step = step, plannedStart = start, plannedEnd = end)
            cursor = end
        }
        return result
    }

    fun compressToFit(
        steps: List<Step>,
        availableMinutes: Int,
        minStepMinutes: Int = 5,
    ): List<Step> {
        require(availableMinutes >= 0)
        val total = steps.sumOf { it.durationMinutes }
        if (total == 0 || total <= availableMinutes) return steps

        val protectedTotal = steps.sumOf { minOf(it.durationMinutes, minStepMinutes) }
        if (availableMinutes <= protectedTotal) {
            return steps.map { it.copy(durationMinutes = minOf(it.durationMinutes, minStepMinutes)) }
        }

        val flexibleTotal = total - protectedTotal
        val flexibleAvailable = availableMinutes - protectedTotal
        val ratio = flexibleAvailable.toDouble() / flexibleTotal.toDouble()

        return steps.map { step ->
            val protectedPart = minOf(step.durationMinutes, minStepMinutes)
            val flexiblePart = step.durationMinutes - protectedPart
            val newFlexible = (flexiblePart * ratio).toInt()
            step.copy(durationMinutes = protectedPart + newFlexible)
        }
    }
}
