"""The calendar pane starts with the cursor at today; days before today are
greyed out, so events that have already happened don't look current."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from textual.app import App, ComposeResult

from ewstui.ews_client import EventSummary
from ewstui.widgets.calendar_view import CalendarView, working_week_start

TODAY = date.today()
WEEK = [working_week_start(TODAY) + timedelta(days=i) for i in range(5)]


def event(day: date, subject: str) -> EventSummary:
    start = datetime.combine(day, datetime.min.time()).replace(hour=9)
    return EventSummary(f"ev-{day}", "c", subject, start, start + timedelta(hours=1), "Room 4B", "", False)


class CalendarApp(App):
    def compose(self) -> ComposeResult:
        yield CalendarView(id="calendar")


async def test_cursor_starts_at_today_and_past_days_are_dim():
    app = CalendarApp()
    async with app.run_test(size=(120, 30)) as pilot:
        view = app.query_one(CalendarView)
        view.set_events([event(d, f"Meeting {d:%a}") for d in WEEK], WEEK)
        await pilot.pause()
        row_keys = [view.coordinate_to_cell_key((i, 0)).row_key.value for i in range(view.row_count)]
        first_day = next((d for d in WEEK if d >= TODAY), None)
        if first_day is not None:  # at the weekend the coming week is shown, cursor at the top
            assert row_keys[view.cursor_row] == f"ev-{first_day}"
        else:
            assert view.cursor_row == 0
        for i, key in enumerate(row_keys):
            if not key.startswith("ev-"):
                continue
            subject = view.get_row_at(i)[2]
            assert subject.style == ("dim" if date.fromisoformat(key[3:]) < TODAY else "")
