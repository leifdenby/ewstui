"""Ctrl+O in the compose view: your calendar beside the email, working days
as columns and weeks as rows, each day listing its booked time slots."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from textual.widgets import TextArea

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import EventSummary
from ewstui.screens import ComposeScreen
from ewstui.widgets.week_calendar import WeekCalendar, by_day, monday_of, slot_text


def ev(subject, start, end, all_day=False):
    return EventSummary(subject, "c", subject, start, end, "", "x@corp.example", all_day)


MON = date(2026, 9, 28)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)


def test_monday_of():
    assert monday_of(date(2026, 10, 1)) == MON and monday_of(MON) == MON


def test_starts_this_week_or_next_at_the_weekend():
    assert WeekCalendar(today=date(2026, 10, 1)).first == MON  # a Thursday
    assert WeekCalendar(today=date(2026, 10, 3)).first == MON + timedelta(weeks=1)  # a Saturday


def test_by_day_lists_all_day_first_and_spreads_multi_day_events():
    events = [
        ev("Lunch", at(MON, 12), at(MON, 13)),
        ev("Standup", at(MON, 9), at(MON, 9, 15)),
        ev("Conference", at(MON, 0), at(MON + timedelta(days=3), 0), all_day=True),  # Mon–Wed
    ]
    days = by_day(events, MON, MON + timedelta(days=4))
    assert [e.subject for e in days[MON]] == ["Conference", "Standup", "Lunch"]
    assert [e.subject for e in days[MON + timedelta(days=2)]] == ["Conference"]
    assert MON + timedelta(days=3) not in days


def test_slot_text():
    assert slot_text(ev("Standup", at(MON, 9), at(MON, 9, 15)), 30) == "09:00–09:15 Standup"
    assert slot_text(ev("A very long meeting title", at(MON, 9), at(MON, 10)), 16) == "09:00–10:00 A v…"
    assert slot_text(ev("Holiday", at(MON, 0), at(MON, 0) + timedelta(days=1), all_day=True), 20) == "all day Holiday"


def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_ctrl_o_shows_the_calendar_beside_the_email(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(180, 45)) as pilot:
        await pilot.pause()
        await pilot.press("r")
        await settle(app, pilot)
        screen = app.screen
        assert isinstance(screen, ComposeScreen) and not screen.calendar_shown
        await pilot.press("ctrl+o")
        await settle(app, pilot)
        await settle(app, pilot)
        calendar = screen.query_one(WeekCalendar)
        text = str(calendar.render())
        today = date.today()
        first = calendar.first
        assert f"{first:%a %d %b}" in text  # from a Monday
        if today.weekday() < 5:  # the demo's events are today
            assert "09:00–09:15 Standup" in text and "14:00–14:30 1:1 with manager" in text
        assert calendar.region.x >= screen.query_one("#compose-body", TextArea).region.right  # beside it
        await pilot.press("ctrl+f")
        await settle(app, pilot)
        assert calendar.first == first + timedelta(weeks=3)
        await pilot.press("ctrl+b")
        await settle(app, pilot)
        assert calendar.first == first
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert not screen.calendar_shown


async def test_typing_still_works_with_the_calendar_open(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(180, 45)) as pilot:
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause()
        await pilot.press("ctrl+o")
        await settle(app, pilot)
        app.screen.query_one("#compose-body", TextArea).focus()
        await pilot.press(*"free on tue?")
        await pilot.pause()
        assert app.screen.query_one("#compose-body", TextArea).text.startswith("free on tue?")
