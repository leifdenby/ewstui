from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import exchangelib
import pytest
from exchangelib import CalendarItem, EWSDateTime, EWSTimeZone
from exchangelib.errors import ErrorMailRecipientNotFound
from exchangelib.items import SEND_MEETING_INVITATIONS_CHOICES, SEND_TO_ALL_AND_SAVE_COPY
from exchangelib.properties import CalendarEvent, FreeBusyView
from textual.widgets import Input, ListView

from ewstui import config_file
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DEMO_ROOMS, DemoCalendarClient, DemoMailClient
from ewstui.ews_client import CalendarClient, Room
from ewstui.screens import FindRoomScreen, NewEventScreen, _next_half_hour

TZ = EWSTimeZone.localzone()
ROOM_A = Room("Havgus (8 pers)", "havgus@corp.example")
ROOM_B = Room("Stormen", "stormen@corp.example")
ROOM_C = Room("Typo", "no-such-room@corp.example")


def at(hour, minute=0) -> datetime:
    return datetime(2026, 9, 25, hour, minute)


def ews(dt: datetime) -> EWSDateTime:
    return EWSDateTime.from_datetime(dt).astimezone(TZ)


def event(start, end, busy_type="Busy") -> CalendarEvent:
    return CalendarEvent(start=ews(start), end=ews(end), busy_type=busy_type)


def calendar_client(views) -> tuple[CalendarClient, MagicMock]:
    protocol = MagicMock()
    protocol.get_free_busy_info.return_value = iter(views)
    return CalendarClient(SimpleNamespace(protocol=protocol)), protocol


# -- config ------------------------------------------------------------------


def test_rooms_as_table_or_list(tmp_path):
    doc = config_file.tomlkit.parse(
        '[accounts.a.rooms]\n"Havgus (8 pers)" = "havgus@corp.example"\n"Stormen" = "stormen@corp.example"\n'
        '[accounts.b]\nrooms = ["x@corp.example"]\n'
    )
    assert config_file.account_rooms(doc, "a") == [ROOM_A, ROOM_B]
    assert config_file.account_rooms(doc, "b") == [Room("x@corp.example", "x@corp.example")]
    assert config_file.account_rooms(doc, "missing") == []


def test_bad_rooms_shape_is_rejected(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[accounts.a]\nemail = "me@corp.example"\nrooms = [1, 2]\n')
    with pytest.raises(SystemExit, match="rooms must be a table"):
        config_from_args(["--config", str(cfg_path), "--account", "a"])


def test_rooms_reach_config_and_survive_saves(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'default_account = "a"\n[accounts.a]\nemail = "me@corp.example"\n'
        '[accounts.a.rooms]\n"Havgus (8 pers)" = "havgus@corp.example"\n'
    )
    assert config_from_args(["--config", str(cfg_path)]).rooms == [ROOM_A]
    config_file.save_account(cfg_path, "a", {"page_size": 25})
    assert config_from_args(["--config", str(cfg_path)]).rooms == [ROOM_A]


# -- availability --------------------------------------------------------------


def test_room_availability_classifies_rooms():
    views = [
        FreeBusyView(calendar_events=[event(at(13), at(14))]),  # overlaps 13:30-14:30
        FreeBusyView(calendar_events=[event(at(12), at(13, 30)), event(at(14, 30), at(15))]),  # touches only
        ErrorMailRecipientNotFound("unknown mailbox"),
    ]
    client, protocol = calendar_client(views)
    results = client.room_availability([ROOM_A, ROOM_B, ROOM_C], at(13, 30), at(14, 30))

    (a, b, c) = results
    assert (a.free, [t for *_, t in a.busy]) == (False, ["Busy"])
    assert (b.free, b.busy) == (True, [])
    assert (c.free, c.error) == (False, "ErrorMailRecipientNotFound: unknown mailbox")

    kwargs = protocol.get_free_busy_info.call_args.kwargs
    assert kwargs["accounts"] == [(r.email, "Room", False) for r in (ROOM_A, ROOM_B, ROOM_C)]
    assert kwargs["requested_view"] == "Detailed"
    assert kwargs["start"].tzinfo is not None  # exchangelib needs aware datetimes


@pytest.mark.parametrize(
    ("busy_type", "free"), [("Free", True), ("NoData", True), ("Tentative", False), ("OOF", False)]
)
def test_busy_types(busy_type, free):
    client, _ = calendar_client([FreeBusyView(calendar_events=[event(at(13), at(15), busy_type)])])
    (result,) = client.room_availability([ROOM_A], at(13, 30), at(14, 30))
    assert result.free is free


def test_no_rooms_makes_no_call():
    client, protocol = calendar_client([])
    assert client.room_availability([], at(13), at(14)) == []
    protocol.get_free_busy_info.assert_not_called()


# -- booking -------------------------------------------------------------------


def test_booking_invites_the_room_as_resource(monkeypatch):
    item_cls = MagicMock()
    monkeypatch.setattr(exchangelib, "CalendarItem", item_cls)
    client = CalendarClient(SimpleNamespace(calendar="cal"))
    client.create_event("Planning", at(13), at(14), location="Havgus (8 pers)", resources=[ROOM_A.email])

    item = item_cls.return_value
    assert [a.mailbox.email_address for a in item.resources] == [ROOM_A.email]
    item.save.assert_called_once_with(send_meeting_invitations=SEND_TO_ALL_AND_SAVE_COPY)
    # ...and that call matches exchangelib's real CalendarItem.save signature/choices
    inspect.signature(CalendarItem.save).bind(None, send_meeting_invitations=SEND_TO_ALL_AND_SAVE_COPY)
    assert SEND_TO_ALL_AND_SAVE_COPY in SEND_MEETING_INVITATIONS_CHOICES


def test_plain_event_sends_no_invites(monkeypatch):
    item_cls = MagicMock()
    monkeypatch.setattr(exchangelib, "CalendarItem", item_cls)
    CalendarClient(SimpleNamespace(calendar="cal")).create_event("Focus time", at(9), at(10))
    item_cls.return_value.save.assert_called_once_with()


# -- app flow (demo backend) ---------------------------------------------------


def make_app(tmp_path, rooms) -> tuple[EwstuiApp, DemoCalendarClient]:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt")])
    cfg.rooms = rooms
    calendar = DemoCalendarClient()
    return EwstuiApp(DemoMailClient(), calendar, cfg), calendar


async def test_find_and_book_a_free_room(tmp_path):
    app, calendar = make_app(tmp_path, list(DEMO_ROOMS))
    two_pm = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)  # demo Room 4B is busy then
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("3", "f")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, FindRoomScreen)
        screen.query_one("#room-start", Input).value = two_pm.strftime("%Y-%m-%d %H:%M")
        screen.query_one("#room-duration", Input).value = "30"
        screen.action_check()
        await app.workers.wait_for_complete()
        await pilot.pause()

        results = screen.query_one("#room-results", ListView)
        assert [r.free for r in screen._results] == [False, True, True]
        assert results.index == 1  # cursor starts on the first free room

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, NewEventScreen)
        assert app.screen.query_one("#event-location", Input).value == "Aquarium (4 pers)"
        app.screen.query_one("#event-subject", Input).value = "Planning"
        await pilot.press("ctrl+s")
        await pilot.pause()

    booked = [e for e in calendar._events if e.subject == "Planning"]
    assert booked and booked[0].location == "Aquarium (4 pers)"
    assert booked[0].start == two_pm and booked[0].end == two_pm + timedelta(minutes=30)
    assert calendar.room_bookings["aquarium@corp.example"] == [(two_pm, two_pm + timedelta(minutes=30), "Busy")]


async def test_busy_room_cannot_be_picked(tmp_path):
    app, _ = make_app(tmp_path, [DEMO_ROOMS[0]])
    two_pm = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3", "f")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#room-start", Input).value = two_pm.strftime("%Y-%m-%d %H:%M")
        screen.action_check()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is screen  # still choosing; busy room refused


async def test_no_rooms_configured_explains_where(tmp_path):
    app, _ = make_app(tmp_path, [])
    seen = []
    async with app.run_test() as pilot:
        original = app.notify
        app.notify = lambda msg, **kw: (seen.append(msg), original(msg, **kw))
        await pilot.press("3", "f")
        await pilot.pause()
        assert not isinstance(app.screen, FindRoomScreen)
    assert any("No meeting rooms configured" in m and ".rooms]" in m for m in seen)


def test_next_half_hour():
    assert _next_half_hour(datetime(2026, 9, 25, 13, 5, 42)) == datetime(2026, 9, 25, 13, 30)
    assert _next_half_hour(datetime(2026, 9, 25, 13, 30)) == datetime(2026, 9, 25, 14, 0)
