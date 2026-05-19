package com.lifecoach.core.model

import java.time.DayOfWeek
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime

data class Step(
    val id: String,
    val name: String,
    val durationMinutes: Int,
    val order: Int,
    val icon: String? = null,
)

data class Routine(
    val id: String,
    val name: String,
    val steps: List<Step>,
    val modeId: String? = null,
)

data class Mode(
    val id: String,
    val name: String,
    val defaultWakeTime: LocalTime,
    val defaultDepartureTime: LocalTime,
    val builtIn: Boolean = false,
)

data class ActionLog(
    val stepId: String,
    val plannedStart: LocalDateTime,
    val actualStart: LocalDateTime?,
    val actualEnd: LocalDateTime?,
)

data class WeekdayDefaultMode(
    val dayOfWeek: DayOfWeek,
    val modeId: String,
)

data class CharacterConfig(
    val characterId: String,
    val addressTerm: String,
    val useFormal: Boolean,
    val voiceRate: Float = 1.0f,
    val voicePitch: Float = 1.0f,
)

data class ImplicitSignalConfig(
    val homeWifiSsid: String? = null,
    val officeWifiSsid: String? = null,
)

data class ModeChoiceLog(
    val date: LocalDate,
    val modeId: String,
)
