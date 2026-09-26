"""c: a calendar event from the email being read (a save-the-date without
an invite), in a panel next to the email."""
from __future__ import annotations

import inspect
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import exchangelib
from exchangelib import CalendarItem, EWSDate

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.event_panel import EventFromEmailPanel, event_subject, parse_time
from ewstui.ews_client import CalendarClient
from ewstui.widgets.date_picker import DatePicker, add_months
from ewstui.widgets.message_table import MessageTable
from ewstui.widgets.preview import PreviewPane


def test_event_subject_drops_save_the_date_and_reply_prefixes():
    assert event_subject("Fwd: SAVE THE DATE: Autumn workshop") == "Autumn workshop"
    assert event_subject("Save-the-date – Christmas party!") == "Christmas party"
    assert event_subject("[EXT] Re: Save the date") == "Save the date"  # nothing left: keep it
    assert event_subject("Board meeting") == "Board meeting"


def test_parse_time():
    assert parse_time("9") == time(9)
    assert parse_time("09.30") == time(9, 30)
    assert parse_time("1415") == time(14, 15)
    assert parse_time("25:00") is None and parse_time("soon") is None


def test_add_months_clamps_the_day():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert add_months(date(2026, 1, 15), -1) == date(2025, 12, 15)


def test_all_day_events_are_created_with_dates(monkeypatch):
    item_cls = MagicMock()
    monkeypatch.setattr(exchangelib, "CalendarItem", item_cls)
    start = datetime(2026, 10, 14)
    CalendarClient(SimpleNamespace(calendar="cal")).create_event("Workshop", start, start + timedelta(days=2), is_all_day=True)
    kwargs = item_cls.call_args.kwargs
    assert kwargs["start"] == EWSDate(2026, 10, 14) and kwargs["end"] == EWSDate(2026, 10, 16)
    assert kwargs["is_all_day"] is True
    inspect.signature(CalendarItem).bind(**{k: v for k, v in kwargs.items()})  # real field names
    item_cls.return_value.save.assert_called_once_with()  # no invitations


# -- in the app (demo) --------------------------------------------------------

def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    calendar = DemoCalendarClient()
    return EwstuiApp(DemoMailClient(calendar=calendar, with_invites=True), calendar, cfg)


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def open_panel(app, pilot, message_id="std1"):
    await pilot.pause()
    table = app.query_one("#messages", MessageTable)
    await pilot.press(*["j"] * table.get_row_index(message_id), "o", "c")
    await settle(app, pilot)
    return app.screen.query_one(EventFromEmailPanel)


async def test_panel_starts_from_the_save_the_date(tmp_path):
    app = make_app(tmp_path)
    workshop = date.today() + timedelta(days=18)
    async with app.run_test(size=(160, 45)) as pilot:
        panel = await open_panel(app, pilot)
        assert app.query_one("#preview", PreviewPane).region.width > 40  # the email stays readable beside it
        assert panel.query_one("#ev-subject").value == "Autumn ML workshop"
        picker = panel.query_one(DatePicker)
        assert picker.value == workshop and isinstance(app.focused, DatePicker)
        assert picker.marked == [workshop, workshop + timedelta(days=1)]
        assert panel.query_one("#ev-start").value == "10:00" and panel.query_one("#ev-end").value == "16:00"
        assert not panel.query_one("#ev-allday").value  # a time range was found
        assert "new event" in str(app.query_one("#statusbar").render())


async def test_pick_another_day_and_create(tmp_path):
    app = make_app(tmp_path)
    workshop = date.today() + timedelta(days=18)
    async with app.run_test(size=(160, 45)) as pilot:
        await open_panel(app, pilot)
        await pilot.press("n")  # the next date from the email: the dinner
        await pilot.press("l", "h", "j", "k")  # and around the calendar, ending where it started
        await pilot.press("ctrl+s")
        await settle(app, pilot)
        assert not app.screen.query(EventFromEmailPanel)  # closed
        assert isinstance(app.focused, PreviewPane)
        created = app.calendar_client._events[-1]
        assert created.subject == "Autumn ML workshop"
        assert created.start == datetime.combine(workshop + timedelta(days=1), time(10))
        assert created.end == datetime.combine(workshop + timedelta(days=1), time(16))
        assert "From the email “Save the date: Autumn ML workshop”" in app.calendar_client.last_created_body


async def test_typed_date_and_all_day(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 45)) as pilot:
        panel = await open_panel(app, pilot)
        panel.query_one("#ev-date-text").focus()
        await pilot.press(*"tomorrow", "enter")
        await pilot.pause()
        assert panel.query_one(DatePicker).value == date.today() + timedelta(days=1)
        panel.query_one("#ev-allday").value = True
        await pilot.press("v", "l")  # and the day after: two days
        await pilot.press("ctrl+s")
        await settle(app, pilot)
        created = app.calendar_client._events[-1]
        start = datetime.combine(date.today() + timedelta(days=1), time.min)
        assert created.is_all_day and (created.start, created.end) == (start, start + timedelta(days=2))


async def test_timed_event_over_selected_days(tmp_path):
    app = make_app(tmp_path)
    workshop = date.today() + timedelta(days=18)
    async with app.run_test(size=(160, 45)) as pilot:
        panel = await open_panel(app, pilot)  # 10:00–16:00 from the email
        await pilot.press("v", "l", "l")
        await pilot.pause()
        picker = panel.query_one(DatePicker)
        assert picker.days == (workshop, workshop + timedelta(days=2))
        assert "(3 days" in str(panel.query_one("#ev-day").render())
        await pilot.press("ctrl+s")
        await settle(app, pilot)
        created = app.calendar_client._events[-1]
        assert (created.start, created.end) == (datetime.combine(workshop, time(10)),
                                                datetime.combine(workshop + timedelta(days=2), time(16)))
        body = app.calendar_client.last_created_body
        assert body.endswith("A proper invite follows later.\n")  # the whole email text


async def test_selecting_backwards_and_escape_ends_the_selection_first(tmp_path):
    app = make_app(tmp_path)
    workshop = date.today() + timedelta(days=18)
    async with app.run_test(size=(160, 45)) as pilot:
        panel = await open_panel(app, pilot)
        await pilot.press("v", "k")  # a week back: the range is still first..last
        await pilot.pause()
        picker = panel.query_one(DatePicker)
        assert picker.days == (workshop - timedelta(days=7), workshop)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.query(EventFromEmailPanel)  # still open
        assert picker.anchor is None and picker.days == (workshop - timedelta(days=7),) * 2
        await pilot.press("escape")
        await pilot.pause()
        assert not app.screen.query(EventFromEmailPanel)


async def test_a_date_range_in_the_email_is_selected(tmp_path):
    mail_app = make_app(tmp_path)
    conference = date.today() + timedelta(days=40)
    end = conference + timedelta(days=2)
    detail = mail_app.mail_client.get_message("inbox", "std1")
    if end.month != conference.month:  # keep the range within a month, as written in emails
        conference, end = conference.replace(day=1), conference.replace(day=3)
    detail.body_text = f"The conference runs {conference.day}–{end.day} {conference:%B %Y}. Registration opens soon."
    async with mail_app.run_test(size=(160, 45)) as pilot:
        panel = await open_panel(mail_app, pilot)
        picker = panel.query_one(DatePicker)
        assert picker.days == (conference, end)
        assert panel.query_one("#ev-allday").value
        assert conference + timedelta(days=1) in picker.marked
        await pilot.press("ctrl+s")
        await settle(mail_app, pilot)
        created = mail_app.calendar_client._events[-1]
        assert created.is_all_day and created.start.date() == conference and created.end.date() == end + timedelta(days=1)


async def test_bad_times_keep_the_panel_open(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 45)) as pilot:
        panel = await open_panel(app, pilot)
        before = len(app.calendar_client._events)
        panel.query_one("#ev-end").value = "09:00"  # before the start
        await pilot.press("ctrl+s")
        await settle(app, pilot)
        assert app.screen.query(EventFromEmailPanel) and len(app.calendar_client._events) == before


async def test_escape_closes_and_the_day_shows_your_calendar(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 45)) as pilot:
        panel = await open_panel(app, pilot, message_id="m1")  # no dates in it: today
        assert panel.query_one(DatePicker).value == date.today()
        assert panel.query_one("#ev-allday").value  # no time range found: all day
        panel.query_one("#ev-allday").value = False
        panel.query_one("#ev-start").value = "14:00"
        panel.query_one("#ev-end").value = "15:00"
        await settle(app, pilot)
        assert "1:1 with manager" in str(panel.query_one("#ev-day").render())  # the demo calendar today
        await pilot.press("escape")
        await pilot.pause()
        assert not app.screen.query(EventFromEmailPanel)


async def test_switching_view_closes_the_panel(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 45)) as pilot:
        await open_panel(app, pilot)
        app.query_one("#preview", PreviewPane).focus()
        await pilot.press("3")
        await pilot.pause()
        assert not app.screen.query(EventFromEmailPanel)
