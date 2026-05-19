package com.lifecoach.core.mode

import com.lifecoach.core.model.Mode
import com.lifecoach.core.model.ModeChoiceLog
import java.time.DayOfWeek
import java.time.LocalDate

data class TodayModeDecision(
    val modeId: String?,
    val source: Source,
) {
    enum class Source { EXPLICIT, CALENDAR, WEEKDAY_DEFAULT, RECENT, NONE }
}

class ModeStore {

    private val modes = LinkedHashMap<String, Mode>()
    private val weekdayDefaults = HashMap<DayOfWeek, String>()

    fun upsert(mode: Mode) {
        modes[mode.id] = mode
    }

    fun delete(id: String) {
        modes.remove(id)
        weekdayDefaults.entries.removeIf { it.value == id }
    }

    fun get(id: String): Mode? = modes[id]

    fun all(): List<Mode> = modes.values.toList()

    fun setWeekdayDefault(dayOfWeek: DayOfWeek, modeId: String) {
        require(modes.containsKey(modeId)) { "mode $modeId not registered" }
        weekdayDefaults[dayOfWeek] = modeId
    }

    fun clearWeekdayDefault(dayOfWeek: DayOfWeek) {
        weekdayDefaults.remove(dayOfWeek)
    }

    fun getWeekdayDefault(dayOfWeek: DayOfWeek): String? = weekdayDefaults[dayOfWeek]

    fun weekdayDefaults(): Map<DayOfWeek, String> = weekdayDefaults.toMap()

    fun decideTodayMode(
        date: LocalDate,
        explicitChoice: String? = null,
        calendarSuggested: String? = null,
        recentChoice: String? = null,
    ): TodayModeDecision {
        if (explicitChoice != null && modes.containsKey(explicitChoice)) {
            return TodayModeDecision(explicitChoice, TodayModeDecision.Source.EXPLICIT)
        }
        if (calendarSuggested != null && modes.containsKey(calendarSuggested)) {
            return TodayModeDecision(calendarSuggested, TodayModeDecision.Source.CALENDAR)
        }
        weekdayDefaults[date.dayOfWeek]?.let { id ->
            if (modes.containsKey(id)) {
                return TodayModeDecision(id, TodayModeDecision.Source.WEEKDAY_DEFAULT)
            }
        }
        if (recentChoice != null && modes.containsKey(recentChoice)) {
            return TodayModeDecision(recentChoice, TodayModeDecision.Source.RECENT)
        }
        return TodayModeDecision(null, TodayModeDecision.Source.NONE)
    }

    fun mostRecentChoice(history: List<ModeChoiceLog>, before: LocalDate): String? {
        return history
            .filter { it.date.isBefore(before) && modes.containsKey(it.modeId) }
            .maxByOrNull { it.date }
            ?.modeId
    }
}
