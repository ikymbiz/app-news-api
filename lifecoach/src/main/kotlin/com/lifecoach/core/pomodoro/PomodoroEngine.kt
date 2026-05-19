package com.lifecoach.core.pomodoro

data class PomodoroConfig(
    val focusMinutes: Int = 25,
    val breakMinutes: Int = 5,
    val cycles: Int = 4,
) {
    init {
        require(focusMinutes > 0) { "focusMinutes must be > 0" }
        require(breakMinutes >= 0) { "breakMinutes must be >= 0" }
        require(cycles > 0) { "cycles must be > 0" }
    }
}

sealed class PomodoroEvent {
    data class FocusStart(val cycleIndex: Int) : PomodoroEvent()
    data class FocusEnd(val cycleIndex: Int) : PomodoroEvent()
    data class BreakStart(val cycleIndex: Int) : PomodoroEvent()
    data class BreakEnd(val cycleIndex: Int) : PomodoroEvent()
    object AllDone : PomodoroEvent()
}

data class PomodoroPhase(
    val event: PomodoroEvent,
    val atMinuteFromStart: Int,
)

object PomodoroEngine {

    fun schedule(config: PomodoroConfig): List<PomodoroPhase> {
        val phases = ArrayList<PomodoroPhase>(config.cycles * 4 + 1)
        var t = 0
        for (i in 0 until config.cycles) {
            phases += PomodoroPhase(PomodoroEvent.FocusStart(i), t)
            t += config.focusMinutes
            phases += PomodoroPhase(PomodoroEvent.FocusEnd(i), t)

            val isLast = i == config.cycles - 1
            if (!isLast && config.breakMinutes > 0) {
                phases += PomodoroPhase(PomodoroEvent.BreakStart(i), t)
                t += config.breakMinutes
                phases += PomodoroPhase(PomodoroEvent.BreakEnd(i), t)
            }
        }
        phases += PomodoroPhase(PomodoroEvent.AllDone, t)
        return phases
    }

    fun totalDurationMinutes(config: PomodoroConfig): Int {
        val focus = config.focusMinutes * config.cycles
        val br = config.breakMinutes * (config.cycles - 1).coerceAtLeast(0)
        return focus + br
    }
}
