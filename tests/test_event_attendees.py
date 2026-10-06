"""New event: attendees added one at a time with Enter, each shown as free
or busy for the meeting's time (EWS free/busy, like the room finder)."""
from __future__ import annotations

import threading
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from exchangelib import EWSDateTime, EWSTimeZone
from exchangelib.errors import ErrorMailRecipientNotFound
from exchangelib.properties import CalendarEvent, CalendarEventDetails, FreeBusyView

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import BusySlot, CalendarClient, Room, RoomAvailability
from ewstui.screens import CHECKING, NewEventScreen, attendee_line, split_addresses

TZ = EWSTimeZone.localzone()
DAY = date(2026, 9, 25)


def at(hour, minute=0, day=DAY) -> datetime:
    return datetime.combine(day, time(hour, minute))


def ews(dt: datetime) -> EWSDateTime:
    return EWSDateTime.from_datetime(dt).astimezone(TZ)


# -- the EWS lookup ------------------------------------------------------------


def test_people_free_busy_asks_for_the_whole_day_and_keeps_what_overlaps():
    protocol = MagicMock()
    protocol.get_free_busy_info.return_value = iter([
        FreeBusyView(calendar_events=[
            CalendarEvent(start=ews(at(9)), end=ews(at(10)), busy_type="Busy"),  # before the meeting
            CalendarEvent(start=ews(at(10, 30)), end=ews(at(11, 30)), busy_type="Tentative",
                          details=CalendarEventDetails(subject="Review", is_private=False)),
        ]),
        FreeBusyView(calendar_events=[CalendarEvent(start=ews(at(10)), end=ews(at(11)), busy_type="Free")]),
        ErrorMailRecipientNotFound("unknown mailbox"),
    ])
    client = CalendarClient(SimpleNamespace(protocol=protocol, primary_smtp_address="me@corp.example"))

    busy, free, unknown = client.people_free_busy(["a@corp.example", "b@corp.example", "typo@corp.example"],
                                                  at(10), at(11))

    kwargs = protocol.get_free_busy_info.call_args.kwargs
    assert kwargs["accounts"] == [(e, "Required", False) for e in ("a@corp.example", "b@corp.example",
                                                                    "typo@corp.example")]
    assert kwargs["start"] == ews(at(0)) and kwargs["end"] == ews(at(0, day=DAY + timedelta(days=1)))
    assert not busy.free and busy.busy == [BusySlot(at(10, 30), at(11, 30), "Tentative", "Review")]
    assert busy.busy[0].start.tzinfo is None  # local, naive, like the rest of the UI
    assert free.free and free.busy == []
    assert unknown.error and not unknown.free


def test_people_free_busy_with_nobody_asks_nothing():
    protocol = MagicMock()
    client = CalendarClient(SimpleNamespace(protocol=protocol, primary_smtp_address="me@corp.example"))
    assert client.people_free_busy([], at(10), at(11)) == []
    protocol.get_free_busy_info.assert_not_called()


# -- how an attendee is shown ---------------------------------------------------


def test_split_addresses():
    assert split_addresses(" a@x, b@x;c@x ,, ") == ["a@x", "b@x", "c@x"]


def availability(email, busy=(), error=None) -> RoomAvailability:
    return RoomAvailability(Room(email, email), free=not busy and not error, busy=list(busy), error=error)


def test_attendee_lines():
    assert attendee_line("a@x", None).plain == "  · a@x"
    assert attendee_line("a@x", CHECKING).plain == "  … a@x  checking…"
    assert attendee_line("a@x", availability("a@x")).plain == "  ✓ a@x  free"
    assert "couldn't check" in attendee_line("a@x", availability("a@x", error="nope")).plain
    busy = availability("a@x", [BusySlot(at(10, 30), at(11), "Busy", "Standup"),
                                BusySlot(at(10), at(10, 15), "OOF")])
    assert attendee_line("a@x", busy, at(10)).plain == (
        "  ✗ a@x  out of office 10:00–10:15, busy 10:30–11:00 (Standup)")


def test_busy_on_another_day_names_the_day():
    busy = availability("a@x", [BusySlot(at(9, day=DAY + timedelta(days=1)), at(10, day=DAY + timedelta(days=1)),
                                         "Busy")])
    assert attendee_line("a@x", busy, at(10)).plain == "  ✗ a@x  busy Sat 09:00–10:00"


# -- the form ------------------------------------------------------------------------


def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    calendar = DemoCalendarClient()
    return EwstuiApp(DemoMailClient(calendar=calendar), calendar, cfg)


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def open_form(app, pilot) -> NewEventScreen:
    await settle(app, pilot)
    await pilot.press("3", "n")
    await settle(app, pilot)
    screen = app.screen
    assert isinstance(screen, NewEventScreen)
    today = date.today()
    screen.query_one("#event-start").value = f"{today} 14:00"  # the demo boss is in a 1:1 14:00-14:30
    screen.query_one("#event-end").value = f"{today} 15:00"
    return screen


async def add(app, pilot, screen, text):
    field = screen.query_one("#event-attendees")
    field.focus()
    field.value = text
    await pilot.press("enter")
    await settle(app, pilot)


def shown(screen) -> str:
    return str(screen.query_one("#event-attendee-list").render())


async def test_enter_adds_each_attendee_and_shows_if_they_are_free(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        screen = await open_form(app, pilot)
        await add(app, pilot, screen, "boss@corp.example")
        await add(app, pilot, screen, "a@corp.example; b@corp.example")
        await add(app, pilot, screen, "A@corp.example")  # already there
        assert screen.attendees == ["boss@corp.example", "a@corp.example", "b@corp.example"]
        assert screen.query_one("#event-attendees").value == ""
        assert "✗ boss@corp.example  busy 14:00–14:30 (1:1 with manager)" in shown(screen)
        assert "✓ a@corp.example  free" in shown(screen)

        await pilot.press("backspace")  # empty field: takes b off again
        await settle(app, pilot)
        assert screen.attendees == ["boss@corp.example", "a@corp.example"]
        assert "b@corp.example" not in shown(screen)

        screen.query_one("#event-attendees").value = "c@corp.example"  # typed, not Enter'd: still invited
        await pilot.press("ctrl+s")
        await settle(app, pilot)
    assert app.calendar_client.last_created_attendees == ["boss@corp.example", "a@corp.example", "c@corp.example"]


async def test_typing_two_attendees_one_after_the_other(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        screen = await open_form(app, pilot)
        screen.query_one("#event-attendees").focus()
        await pilot.press(*"a@corp.example", "enter")
        await settle(app, pilot)
        await pilot.press(*"b@corp.example", "enter")
        await settle(app, pilot)
        assert screen.attendees == ["a@corp.example", "b@corp.example"]
        assert "✓ b@corp.example  free" in shown(screen)


async def test_on_a_short_terminal_the_attendee_list_stays_on_screen(tmp_path):
    """The form is taller than 24 rows: it scrolls, and keeps the list in view."""
    app = make_app(tmp_path)
    async with app.run_test(size=(120, 24)) as pilot:
        screen = await open_form(app, pilot)
        screen.query_one("#event-attendees").focus()
        for who in ("a@corp.example", "b@corp.example", "c@corp.example"):
            await pilot.press(*who, "enter")
            await settle(app, pilot)
        listed = screen.query_one("#event-attendee-list").region
        field = screen.query_one("#event-attendees").region
        assert listed.height == 3 and listed.bottom <= 24 and field.y >= 0


async def test_a_second_attendee_while_the_first_is_still_being_checked(tmp_path):
    app = make_app(tmp_path)
    gate = threading.Event()
    lookups = []

    def slow(emails, start, end):
        lookups.append(list(emails))
        gate.wait(5)  # a slow Exchange server
        return [availability(e) for e in emails]

    app.calendar_client.people_free_busy = slow
    async with app.run_test(size=(140, 40)) as pilot:
        screen = await open_form(app, pilot)
        screen.query_one("#event-attendees").focus()
        await pilot.press(*"a@corp.example", "enter")
        await pilot.pause()
        await pilot.press(*"b@corp.example", "enter")
        await pilot.pause()
        assert screen.attendees == ["a@corp.example", "b@corp.example"]
        assert "… b@corp.example  checking…" in shown(screen)
        gate.set()
        await settle(app, pilot)
        assert lookups == [["a@corp.example"], ["b@corp.example"]]
        assert "✓ a@corp.example  free" in shown(screen) and "✓ b@corp.example  free" in shown(screen)


async def test_a_new_time_checks_everyone_again(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        screen = await open_form(app, pilot)
        await add(app, pilot, screen, "boss@corp.example")
        assert "✗ boss@corp.example" in shown(screen)
        screen.query_one("#event-start").value = f"{date.today()} 15:00"
        screen.query_one("#event-end").value = f"{date.today()} 16:00"
        await settle(app, pilot)
        assert "✓ boss@corp.example  free" in shown(screen)


async def test_a_failed_lookup_says_so(tmp_path):
    app = make_app(tmp_path)

    def broken(emails, start, end):
        raise ConnectionError("offline")

    app.calendar_client.people_free_busy = broken
    async with app.run_test(size=(140, 40)) as pilot:
        screen = await open_form(app, pilot)
        await add(app, pilot, screen, "a@corp.example")
        assert "? a@corp.example  couldn't check" in shown(screen)
        assert screen.attendees == ["a@corp.example"]
