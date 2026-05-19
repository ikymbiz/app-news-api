package com.lifecoach.core.pattern

import com.lifecoach.core.model.ModeChoiceLog
import java.time.DayOfWeek

data class WeekdayPattern(
    val dayOfWeek: DayOfWeek,
    val mostFrequentModeId: String?,
    val confidence: Double,
    val sampleCount: Int,
    val distribution: Map<String, Int>,
) {
    fun isReliable(minSamples: Int = 3, minConfidence: Double = 0.75): Boolean =
        mostFrequentModeId != null && sampleCount >= minSamples && confidence >= minConfidence
}

object PatternEngine {

    fun detect(choices: List<ModeChoiceLog>): Map<DayOfWeek, WeekdayPattern> {
        val grouped = choices.groupBy { it.date.dayOfWeek }
        return DayOfWeek.values().associateWith { dow ->
            val logs = grouped[dow].orEmpty()
            buildPattern(dow, logs)
        }
    }

    fun detectReliable(
        choices: List<ModeChoiceLog>,
        minSamples: Int = 3,
        minConfidence: Double = 0.75,
    ): Map<DayOfWeek, WeekdayPattern> =
        detect(choices).filterValues { it.isReliable(minSamples, minConfidence) }

    private fun buildPattern(dow: DayOfWeek, logs: List<ModeChoiceLog>): WeekdayPattern {
        if (logs.isEmpty()) {
            return WeekdayPattern(
                dayOfWeek = dow,
                mostFrequentModeId = null,
                confidence = 0.0,
                sampleCount = 0,
                distribution = emptyMap(),
            )
        }
        val dist = logs.groupingBy { it.modeId }.eachCount()
        val top = dist.maxBy { it.value }
        return WeekdayPattern(
            dayOfWeek = dow,
            mostFrequentModeId = top.key,
            confidence = top.value.toDouble() / logs.size.toDouble(),
            sampleCount = logs.size,
            distribution = dist,
        )
    }
}
