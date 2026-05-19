package com.lifecoach.core.diff

import com.lifecoach.core.model.ActionLog
import com.lifecoach.core.timeline.TimelineEntry
import java.time.Duration

data class StepDiff(
    val stepId: String,
    val stepName: String,
    val plannedDurationMinutes: Int,
    val actualDurationMinutes: Int?,
    val deltaMinutes: Int?,
    val startDeltaMinutes: Int?,
)

data class DiffReport(
    val stepDiffs: List<StepDiff>,
    val totalPlannedMinutes: Int,
    val totalActualMinutes: Int,
    val totalDeltaMinutes: Int,
) {
    val isOnTime: Boolean get() = totalDeltaMinutes == 0
    val isAhead: Boolean get() = totalDeltaMinutes < 0
    val isBehind: Boolean get() = totalDeltaMinutes > 0
}

data class StepTrend(
    val stepId: String,
    val averageDeltaMinutes: Double,
    val sampleCount: Int,
) {
    fun isConsistentlyOver(thresholdMinutes: Double = 3.0): Boolean =
        averageDeltaMinutes >= thresholdMinutes && sampleCount >= 3
}

object DiffEngine {

    fun compare(plan: List<TimelineEntry>, actual: List<ActionLog>): DiffReport {
        val actualByStep = actual.associateBy { it.stepId }

        val diffs = plan.map { entry ->
            val log = actualByStep[entry.step.id]
            val actualDuration = log?.let {
                if (it.actualStart != null && it.actualEnd != null) {
                    Duration.between(it.actualStart, it.actualEnd).toMinutes().toInt()
                } else null
            }
            val startDelta = log?.let {
                if (it.actualStart != null) {
                    Duration.between(it.plannedStart, it.actualStart).toMinutes().toInt()
                } else null
            }
            StepDiff(
                stepId = entry.step.id,
                stepName = entry.step.name,
                plannedDurationMinutes = entry.durationMinutes,
                actualDurationMinutes = actualDuration,
                deltaMinutes = actualDuration?.let { it - entry.durationMinutes },
                startDeltaMinutes = startDelta,
            )
        }

        val totalPlanned = diffs.sumOf { it.plannedDurationMinutes }
        val totalActual = diffs.sumOf { it.actualDurationMinutes ?: it.plannedDurationMinutes }
        return DiffReport(
            stepDiffs = diffs,
            totalPlannedMinutes = totalPlanned,
            totalActualMinutes = totalActual,
            totalDeltaMinutes = totalActual - totalPlanned,
        )
    }

    fun trend(history: List<DiffReport>): List<StepTrend> {
        val acc = HashMap<String, MutableList<Int>>()
        for (report in history) {
            for (sd in report.stepDiffs) {
                val delta = sd.deltaMinutes ?: continue
                acc.getOrPut(sd.stepId) { ArrayList() }.add(delta)
            }
        }
        return acc.map { (stepId, deltas) ->
            StepTrend(
                stepId = stepId,
                averageDeltaMinutes = deltas.average(),
                sampleCount = deltas.size,
            )
        }.sortedByDescending { it.averageDeltaMinutes }
    }
}
