"""Live-shaped calendar data: EWS returns timed events as UTC EWSDateTime
and all-day events as EWSDate, mixed in one view (which crashed sorting
in the calendar tab: "can't compare EWSDateTime to EWSDate").
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from exchangelib import UTC, CalendarItem, EWSDate, EWSDateTime, EWSTimeZone, Mailbox

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoMailClient
from ewstui.ews_client import CalendarClient
from ewstui.widgets.calendar_view import CalendarView

COPENHAGEN = EWSTimeZone("Europe/Copenhagen")  # UTC+2 in late September


class FakeView:
    def __init__(self, items):
        self.items = items

    def only(self, *fields):
        return self.items


def item(item_id, subject, start, end, all_day=False) -> CalendarItem:
    ci = CalendarItem(
        subject=subject, start=start, end=end, is_all_day=all_day,
        location="", organizer=Mailbox(email_address="lcd@dmi.dk"),
    )
    ci._id = SimpleNamespace(id=item_id, changekey="ck")
    return ci


@pytest.fixture
def client() -> CalendarClient:
    items = [
        item("timed", "Catch-up", EWSDateTime(2026, 9, 25, 9, 30, tzinfo=UTC), EWSDateTime(2026, 9, 25, 10, 15, tzinfo=UTC)),
        item("allday", "Attending EWGLAM", EWSDate(2026, 9, 28), EWSDate(2026, 9, 30), all_day=True),
        item("early", "Meetup", EWSDateTime(2026, 9, 25, 7, 0, tzinfo=UTC), EWSDateTime(2026, 9, 25, 8, 0, tzinfo=UTC)),
    ]
    c = CalendarClient(SimpleNamespace(calendar=SimpleNamespace(view=lambda start, end: FakeView(items))))
    c.tz = COPENHAGEN
    return c


def test_list_events_returns_naive_local_datetimes(client):
    events = {e.id: e for e in client.list_events(datetime(2026, 9, 25), datetime(2026, 10, 2))}
    # 07:00 UTC is 09:00 in Copenhagen (CEST)
    assert events["early"].start == datetime(2026, 9, 25, 9, 0)
    assert events["timed"].end == datetime(2026, 9, 25, 12, 15)
    # all-day: dates become midnight
    assert events["allday"].start == datetime(2026, 9, 28)
    assert events["allday"].end == datetime(2026, 9, 30)
    for e in events.values():
        assert type(e.start) is datetime and e.start.tzinfo is None


async def test_calendar_tab_renders_mixed_timed_and_all_day_events(tmp_path, client):
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt")])
    app = EwstuiApp(DemoMailClient(), client, cfg)
    app.calendar_range_start = datetime(2026, 9, 25)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        view = app.query_one("#calendar", CalendarView)
        rows = [view.get_row_at(i) for i in range(view.row_count)]
    # Grouped by day, the date on a day's first row, a blank row between
    # days, and a day with nothing on still listed.
    assert [tuple(str(c) for c in r[:3]) for r in rows] == [
        ("Fri 2026-09-25", "09:00-10:00", "Meetup"),
        ("", "11:30-12:15", "Catch-up"),
        ("", "", ""),
        ("Sat 2026-09-26", "", "nothing on"),
        ("", "", ""),
        ("Sun 2026-09-27", "", "nothing on"),
        ("", "", ""),
        ("Mon 2026-09-28", "all day", "Attending EWGLAM"),
        ("", "", ""),
        ("Tue 2026-09-29", "", "nothing on"),
    ]
