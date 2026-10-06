"""The calendar pane: one working week (Monday-Friday) at a time, a blank
row between days, which the cursor steps over."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import EventSummary
from ewstui.widgets.calendar_view import GAP, CalendarView, working_week_start

MONDAY = date(2026, 9, 28)


@pytest.mark.parametrize("offset, expected", [(0, MONDAY), (2, MONDAY), (4, MONDAY),
                                              (5, MONDAY + timedelta(weeks=1)), (6, MONDAY + timedelta(weeks=1))])
def test_working_week_start(offset, expected):
    assert working_week_start(MONDAY + timedelta(days=offset)) == expected


class WeekCalendar(DemoCalendarClient):
    def __init__(self):
        super().__init__()
        at = lambda d, h: datetime.combine(MONDAY + timedelta(days=d), datetime.min.time()).replace(hour=h)  # noqa: E731
        self._events = [
            EventSummary("m1", "c", "Standup", at(0, 9), at(0, 10), "", "", False),
            EventSummary("m2", "c", "Lunch", at(0, 12), at(0, 13), "", "", False),
            EventSummary("w1", "c", "Review", at(2, 9), at(2, 10), "", "", False),
            EventSummary("s1", "c", "Saturday hike", at(5, 9), at(5, 12), "", "", False),
        ]
        self.ranges = []

    def list_events(self, start, end):
        self.ranges.append((start, end))
        return super().list_events(start, end)


def make_app(tmp_path):
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    calendar = WeekCalendar()
    app = EwstuiApp(DemoMailClient(calendar=calendar), calendar, cfg)
    app.calendar_range_start = datetime.combine(MONDAY, datetime.min.time())
    return app, calendar


def keys(view) -> list[str]:
    return [view.coordinate_to_cell_key((i, 0)).row_key.value for i in range(view.row_count)]


async def test_monday_to_friday_with_a_gap_between_days(tmp_path):
    app, calendar = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        view = app.query_one("#calendar", CalendarView)
        start, end = calendar.ranges[-1]
        assert (start.date(), end.date()) == (MONDAY, MONDAY + timedelta(days=5))  # up to Saturday 00:00
        assert [str(view.get_row_at(i)[0]) for i in range(view.row_count)] == [
            "Mon 2026-09-28", "", "", "Tue 2026-09-29", "", "Wed 2026-09-30", "", "Thu 2026-10-01", "",
            "Fri 2026-10-02",
        ]
        assert "s1" not in keys(view)  # the weekend isn't shown

        await pilot.press("]")
        await pilot.pause()
        start, _ = calendar.ranges[-1]
        assert start.date() == MONDAY + timedelta(weeks=1)
        await pilot.press("[", "[")
        await pilot.pause()
        assert calendar.ranges[-1][0].date() == MONDAY - timedelta(weeks=1)


async def test_the_cursor_steps_over_the_gaps(tmp_path):
    app, _ = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        view = app.query_one("#calendar", CalendarView)
        view.focus()
        visited = []
        for key in ["j"] * 4 + ["k"] * 4:
            await pilot.press(key)
            await pilot.pause()
            visited.append(keys(view)[view.cursor_row])
        assert not any(k.startswith(GAP) for k in visited)
        assert visited[:4] == ["m2", "empty:2026-09-29", "w1", "empty:2026-10-01"]
        assert visited[4:] == ["w1", "empty:2026-09-29", "m2", "m1"]
