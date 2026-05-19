package com.lifecoach.core.mode

import com.lifecoach.core.model.Mode
import com.lifecoach.core.model.ModeChoiceLog
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.DisplayName
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.LocalTime

class ModeStoreTest {

    private fun mode(id: String) = Mode(
        id = id, name = id,
        defaultWakeTime = LocalTime.of(7, 30),
        defaultDepartureTime = LocalTime.of(8, 30),
        builtIn = true,
    )

    @Test
    @DisplayName("モードのCRUD")
    fun crud() {
        val store = ModeStore()
        store.upsert(mode("commute"))
        store.upsert(mode("home"))
        assertEquals(2, store.all().size)

        store.upsert(mode("commute").copy(name = "出社"))
        assertEquals("出社", store.get("commute")!!.name)

        store.delete("home")
        assertNull(store.get("home"))
        assertEquals(1, store.all().size)
    }

    @Test
    @DisplayName("曜日デフォルト設定と取得")
    fun weekdayDefaults() {
        val store = ModeStore()
        store.upsert(mode("commute"))
        store.upsert(mode("home"))

        store.setWeekdayDefault(DayOfWeek.MONDAY, "commute")
        store.setWeekdayDefault(DayOfWeek.TUESDAY, "home")
        assertEquals("commute", store.getWeekdayDefault(DayOfWeek.MONDAY))
        assertEquals("home", store.getWeekdayDefault(DayOfWeek.TUESDAY))
        assertNull(store.getWeekdayDefault(DayOfWeek.WEDNESDAY))
    }

    @Test
    @DisplayName("未登録モードを曜日デフォルトにできない")
    fun unknownModeRejected() {
        val store = ModeStore()
        assertThrows<IllegalArgumentException> {
            store.setWeekdayDefault(DayOfWeek.MONDAY, "ghost")
        }
    }

    @Test
    @DisplayName("モード削除で関連する曜日デフォルトもクリアされる")
    fun cascadeOnDelete() {
        val store = ModeStore()
        store.upsert(mode("commute"))
        store.setWeekdayDefault(DayOfWeek.MONDAY, "commute")
        store.delete("commute")
        assertNull(store.getWeekdayDefault(DayOfWeek.MONDAY))
    }

    @Test
    @DisplayName("明示選択が最優先")
    fun explicitWinsOver() {
        val store = ModeStore()
        store.upsert(mode("commute"))
        store.upsert(mode("home"))
        store.upsert(mode("off"))
        store.setWeekdayDefault(DayOfWeek.MONDAY, "commute")

        val monday = LocalDate.of(2026, 5, 18)
        val d = store.decideTodayMode(
            date = monday,
            explicitChoice = "off",
            calendarSuggested = "home",
            recentChoice = "commute",
        )
        assertEquals("off", d.modeId)
        assertEquals(TodayModeDecision.Source.EXPLICIT, d.source)
    }

    @Test
    @DisplayName("次にカレンダー優先")
    fun calendarBeatsWeekday() {
        val store = ModeStore()
        store.upsert(mode("commute"))
        store.upsert(mode("home"))
        store.setWeekdayDefault(DayOfWeek.MONDAY, "commute")

        val monday = LocalDate.of(2026, 5, 18)
        val d = store.decideTodayMode(date = monday, calendarSuggested = "home", recentChoice = "commute")
        assertEquals("home", d.modeId)
        assertEquals(TodayModeDecision.Source.CALENDAR, d.source)
    }

    @Test
    @DisplayName("次に曜日パターン")
    fun weekdayBeatsRecent() {
        val store = ModeStore()
        store.upsert(mode("commute"))
        store.setWeekdayDefault(DayOfWeek.MONDAY, "commute")

        val monday = LocalDate.of(2026, 5, 18)
        val d = store.decideTodayMode(date = monday, recentChoice = "home")
        // recent "home" is not registered → falls back to weekday default
        assertEquals("commute", d.modeId)
        assertEquals(TodayModeDecision.Source.WEEKDAY_DEFAULT, d.source)
    }

    @Test
    @DisplayName("最終フォールバックは直近選択")
    fun fallbackToRecent() {
        val store = ModeStore()
        store.upsert(mode("home"))
        val monday = LocalDate.of(2026, 5, 18)
        val d = store.decideTodayMode(date = monday, recentChoice = "home")
        assertEquals("home", d.modeId)
        assertEquals(TodayModeDecision.Source.RECENT, d.source)
    }

    @Test
    @DisplayName("何もなければNONE")
    fun noneWhenNothing() {
        val store = ModeStore()
        store.upsert(mode("home"))
        val d = store.decideTodayMode(date = LocalDate.of(2026, 5, 18))
        assertNull(d.modeId)
        assertEquals(TodayModeDecision.Source.NONE, d.source)
    }

    @Test
    @DisplayName("直近選択は指定日より前から最新を取る")
    fun mostRecentBefore() {
        val store = ModeStore()
        store.upsert(mode("commute"))
        store.upsert(mode("home"))

        val history = listOf(
            ModeChoiceLog(LocalDate.of(2026, 5, 10), "home"),
            ModeChoiceLog(LocalDate.of(2026, 5, 15), "commute"),
            ModeChoiceLog(LocalDate.of(2026, 5, 18), "home"),
        )
        // before = 5/18 → most recent before that day is 5/15 commute
        assertEquals("commute", store.mostRecentChoice(history, LocalDate.of(2026, 5, 18)))
    }
}
