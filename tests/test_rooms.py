from __future__ import annotations

import inspect
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import exchangelib
import pytest
from exchangelib import CalendarItem, EWSDateTime, EWSTimeZone
from exchangelib.errors import ErrorMailRecipientNotFound
from exchangelib.items import SEND_MEETING_INVITATIONS_CHOICES, SEND_TO_ALL_AND_SAVE_COPY
from exchangelib.properties import CalendarEvent, CalendarEventDetails, FreeBusyView, WorkingPeriod
from textual.widgets import Input

from ewstui import config_file
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DEMO_ROOMS, DemoCalendarClient, DemoMailClient
from ewstui.ews_client import BusySlot, CalendarClient, Room, RoomAvailability, RoomsDay
from ewstui.room_grid import (
    BUSY,
    FREE,
    FREE_PAST,
    SELECTED,
    UNKNOWN,
    FindRoomScreen,
    RoomGrid,
    day_slots,
    is_free,
)
from ewstui.screens import NewEventScreen

TZ = EWSTimeZone.localzone()
DAY = date(2026, 9, 25)  # a Friday
ROOM_A = Room("Havgus (8 pers)", "havgus@corp.example")
ROOM_B = Room("Stormen", "stormen@corp.example")
ROOM_C = Room("Typo", "no-such-room@corp.example")


def at(hour, minute=0, day=DAY) -> datetime:
    return datetime.combine(day, time(hour, minute))


def ews(dt: datetime) -> EWSDateTime:
    return EWSDateTime.from_datetime(dt).astimezone(TZ)


def event(start, end, busy_type="Busy", details=None) -> CalendarEvent:
    return CalendarEvent(start=ews(start), end=ews(end), busy_type=busy_type, details=details)


def calendar_client(views) -> tuple[CalendarClient, MagicMock]:
    protocol = MagicMock()
    protocol.get_free_busy_info.return_value = iter(views)
    return CalendarClient(SimpleNamespace(protocol=protocol, primary_smtp_address="me@corp.example")), protocol


OWN_HOURS = FreeBusyView(working_hours=[WorkingPeriod(weekdays=[1, 2, 3, 4, 5], start=time(8, 30), end=time(16, 0))])


# -- config ------------------------------------------------------------------


def test_rooms_as_table_or_list():
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


# -- one day of free/busy --------------------------------------------------------


def test_rooms_day_fetches_whole_day_with_own_mailbox_first():
    views = [
        OWN_HOURS,
        FreeBusyView(calendar_events=[event(at(13), at(14)), event(at(15), at(16), "Free")]),
        FreeBusyView(calendar_events=[]),
        ErrorMailRecipientNotFound("unknown mailbox"),
    ]
    client, protocol = calendar_client(views)
    result = client.rooms_day([ROOM_A, ROOM_B, ROOM_C], DAY)

    kwargs = protocol.get_free_busy_info.call_args.kwargs
    assert kwargs["accounts"][0] == ("me@corp.example", "Required", False)
    assert kwargs["accounts"][1:] == [(r.email, "Room", False) for r in (ROOM_A, ROOM_B, ROOM_C)]
    assert kwargs["start"] == ews(at(0)) and kwargs["end"] == ews(at(0) + timedelta(days=1))
    assert kwargs["requested_view"] == "Detailed"

    assert result.work_hours == (time(8, 30), time(16, 0))
    a, b, c = result.rooms
    assert a.busy == [BusySlot(at(13), at(14), "Busy")]  # "Free" entries dropped; times naive local
    assert b.busy == [] and c.error == "ErrorMailRecipientNotFound: unknown mailbox"


def test_booking_subject_from_detailed_view():
    """What the room shares about each booking: its subject (on Exchange
    rooms usually the organizer's name), 'private', or nothing."""
    views = [
        OWN_HOURS,
        FreeBusyView(
            calendar_events=[
                event(at(9), at(10), details=CalendarEventDetails(subject="Leif Denby")),
                event(at(11), at(12), details=CalendarEventDetails(subject="Board meeting", is_private=True)),
                event(at(13), at(14)),  # room shares only free/busy
                event(at(15), at(16), details=CalendarEventDetails(subject="  ")),
            ]
        ),
    ]
    client, _ = calendar_client(views)
    (room,) = client.rooms_day([ROOM_A], DAY).rooms
    assert [b.subject for b in room.busy] == ["Leif Denby", "private", None, None]


def test_clashes_text_names_bookings_when_known():
    from ewstui.room_grid import clashes

    r = RoomAvailability(
        room=ROOM_A,
        free=False,
        busy=[BusySlot(at(13), at(14), "Busy", "Leif Denby"), BusySlot(at(15), at(15, 30), "Busy")],
    )
    assert clashes(r, at(12), at(17)) == "13:00–14:00 (Leif Denby), 15:00–15:30"
    assert clashes(r, at(14), at(15)) == ""


@pytest.mark.parametrize(
    ("own", "expected"),
    [
        (FreeBusyView(working_hours=[WorkingPeriod(weekdays=[6, 7], start=time(10), end=time(12))]), None),  # not Fridays
        (FreeBusyView(), None),  # server gave no working hours
        (ErrorMailRecipientNotFound("?"), None),
    ],
)
def test_work_hours_fall_back_when_unknown(own, expected):
    client, _ = calendar_client([own, FreeBusyView()])
    assert client.rooms_day([ROOM_A], DAY).work_hours is expected


@pytest.mark.parametrize(
    ("busy_type", "free"), [("Free", True), ("NoData", True), ("Tentative", False), ("OOF", False)]
)
def test_busy_types(busy_type, free):
    client, _ = calendar_client([OWN_HOURS, FreeBusyView(calendar_events=[event(at(13), at(15), busy_type)])])
    (room,) = client.rooms_day([ROOM_A], DAY).rooms
    assert is_free(room, at(13, 30), at(14, 30)) is free


def test_slots_and_free_checks():
    assert day_slots(DAY, (time(8), time(9, 15))) == [at(8), at(8, 30), at(9)]
    assert day_slots(DAY, (time(8, 10), time(9))) == [at(8), at(8, 30)]  # start rounded down
    r = RoomAvailability(room=ROOM_A, free=False, busy=[BusySlot(at(13), at(14), "Busy")])
    assert is_free(r, at(12), at(13))  # touching is fine
    assert not is_free(r, at(12, 30), at(13, 30))
    assert not is_free(RoomAvailability(room=ROOM_C, free=False, error="x"), at(9), at(10))


# -- booking ---------------------------------------------------------------


def test_booking_invites_the_room_as_resource(monkeypatch):
    item_cls = MagicMock()
    monkeypatch.setattr(exchangelib, "CalendarItem", item_cls)
    client = CalendarClient(SimpleNamespace(calendar="cal"))
    client.create_event("Planning", at(13), at(14), location="Havgus (8 pers)", resources=[ROOM_A.email])

    item = item_cls.return_value
    assert [a.mailbox.email_address for a in item.resources] == [ROOM_A.email]
    item.save.assert_called_once_with(send_meeting_invitations=SEND_TO_ALL_AND_SAVE_COPY)
    inspect.signature(CalendarItem.save).bind(None, send_meeting_invitations=SEND_TO_ALL_AND_SAVE_COPY)
    assert SEND_TO_ALL_AND_SAVE_COPY in SEND_MEETING_INVITATIONS_CHOICES


def test_plain_event_sends_no_invites(monkeypatch):
    item_cls = MagicMock()
    monkeypatch.setattr(exchangelib, "CalendarItem", item_cls)
    CalendarClient(SimpleNamespace(calendar="cal")).create_event("Focus time", at(9), at(10))
    item_cls.return_value.save.assert_called_once_with()


# -- the grid screen ----------------------------------------------------------


class FakeDays:
    """fetch_day stand-in: Havgus busy 13:00-14:00 every day, Typo errors."""

    def __init__(self, hours=(time(8), time(17))):
        self.hours = hours
        self.calls: list[date] = []

    def __call__(self, rooms, day):
        self.calls.append(day)
        return RoomsDay(
            day=day,
            work_hours=self.hours,
            rooms=[
                RoomAvailability(
                    room=ROOM_A, free=False, busy=[BusySlot(at(13, day=day), at(14, day=day), "Busy", "Leif Denby")]
                ),
                RoomAvailability(room=ROOM_B, free=True),
                RoomAvailability(room=ROOM_C, free=False, error="unknown mailbox"),
            ],
        )


def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt")])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


async def open_grid(app, pilot, fetch, now=at(10, 5)) -> FindRoomScreen:
    screen = FindRoomScreen([ROOM_A, ROOM_B, ROOM_C], fetch, now=now)
    app.push_screen(screen)
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    return screen


def cells(screen) -> list[list]:
    grid = screen.query_one(RoomGrid)
    return [grid.get_row_at(i) for i in range(grid.row_count)]


async def test_grid_shows_rooms_by_half_hour(tmp_path):
    app, fetch = make_app(tmp_path), FakeDays()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await open_grid(app, pilot, fetch)
        grid = screen.query_one(RoomGrid)
        headers = [str(c.label) for c in grid.columns.values()]
        assert headers[:3] == ["Room", "08:00", "  :30"] and headers[-1] == "  :30"
        assert len(headers) == 1 + 18  # 08:00-17:00 in half hours
        havgus, stormen, typo = cells(screen)
        assert str(havgus[0]) == "Havgus (8 pers)"
        slot_13 = 1 + 10  # 08:00 + 10 half hours
        assert havgus[slot_13] is BUSY and havgus[slot_13 + 1] is BUSY and havgus[slot_13 + 2] is FREE
        assert stormen[1] is FREE_PAST  # 08:00 is before "now" (10:05)
        assert all(c is UNKNOWN for c in typo[1:])
        # cursor starts on the first upcoming slot (10:30)
        assert grid.cursor_coordinate.column == 1 + 5
        assert "default hours" not in str(screen.query_one("#room-title").render())


async def test_brackets_page_days_and_t_returns_to_today(tmp_path):
    app, fetch = make_app(tmp_path), FakeDays()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await open_grid(app, pilot, fetch)
        steps = (("]", DAY + timedelta(days=1)), ("]", DAY + timedelta(days=2)), ("[", DAY + timedelta(days=1)), ("t", DAY))
        for key, expected in steps:
            await pilot.press(key)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert screen._shown.day == expected
            assert screen.query_one("#room-date", Input).value == expected.isoformat()
        assert fetch.calls == [DAY, DAY + timedelta(days=1), DAY + timedelta(days=2)]  # revisits come from cache


async def test_h_l_move_along_the_day(tmp_path):
    app, fetch = make_app(tmp_path), FakeDays()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await open_grid(app, pilot, fetch)
        grid = screen.query_one(RoomGrid)
        start = grid.cursor_coordinate.column
        await pilot.press("l", "l", "h")
        await pilot.pause()
        assert grid.cursor_coordinate.column == start + 1
        assert screen._shown.day == DAY  # no longer changes the day


async def test_typed_date_is_loaded_on_enter(tmp_path):
    app, fetch = make_app(tmp_path), FakeDays()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await open_grid(app, pilot, fetch)
        date_input = screen.query_one("#room-date", Input)
        date_input.focus()
        date_input.value = "2026-10-05"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert screen._shown.day == date(2026, 10, 5)
        assert app.focused is screen.query_one(RoomGrid)  # back to the grid for h/l


async def test_hjkl_typed_in_date_input_is_text_not_navigation(tmp_path):
    app, fetch = make_app(tmp_path), FakeDays()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await open_grid(app, pilot, fetch)
        date_input = screen.query_one("#room-date", Input)
        date_input.focus()
        date_input.value = ""
        await pilot.press("h", "l", "j", "k")
        await pilot.pause()
        assert date_input.value == "hljk" and screen._shown.day == DAY


async def test_default_hours_when_server_has_none(tmp_path):
    app, fetch = make_app(tmp_path), FakeDays(hours=None)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await open_grid(app, pilot, fetch)
        assert "default hours" in str(screen.query_one("#room-title").render())
        assert len(screen.query_one(RoomGrid).columns) == 1 + 18


async def test_enter_books_free_slot_and_refuses_busy_one(tmp_path):
    app, fetch = make_app(tmp_path), FakeDays()
    results = []
    async with app.run_test(size=(160, 40)) as pilot:
        screen = FindRoomScreen([ROOM_A, ROOM_B, ROOM_C], fetch, now=at(10, 5))
        app.push_screen(screen, results.append)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        grid = screen.query_one(RoomGrid)

        grid.move_cursor(row=0, column=1 + 9)  # Havgus 12:30, 60 min overlaps 13:00
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is screen and "is busy 13:00–14:00 (Leif Denby)" in str(screen.query_one("#room-status").render())

        grid.move_cursor(row=0, column=1 + 12)  # Havgus 14:00, free
        await pilot.press("enter")
        await pilot.pause()
    assert results == [{"rooms": [ROOM_A], "start": at(14), "end": at(15), "attendees": []}]


# -- visual selection (v) -----------------------------------------------------

SLOT_14 = 1 + 12  # grid column of 14:00 (08:00 + 12 half hours)


def painted_selected(screen) -> list[tuple[int, int]]:
    """Cells drawn in the selected style. By identity: rich Text equality
    ignores the base style, so a plain " ? " would == the selected " ? "."""
    return [
        (i, j)
        for i, row in enumerate(cells(screen))
        for j, c in enumerate(row[1:], 1)
        if any(c is s for s in SELECTED.values())
    ]


async def open_grid_collecting(app, pilot, fetch, results):
    screen = FindRoomScreen([ROOM_A, ROOM_B, ROOM_C], fetch, now=at(10, 5))
    app.push_screen(screen, results.append)
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    return screen, screen.query_one(RoomGrid)


async def test_visual_selection_books_several_rooms_for_the_span(tmp_path):
    app, fetch, results = make_app(tmp_path), FakeDays(), []
    async with app.run_test(size=(160, 40)) as pilot:
        screen, grid = await open_grid_collecting(app, pilot, fetch, results)
        grid.move_cursor(row=0, column=SLOT_14)  # Havgus 14:00
        await pilot.press("v", "j", "l", "l")  # + Stormen, until 15:30
        await pilot.pause()
        row = cells(screen)
        assert all(row[r][c] is SELECTED["free"] for r in (0, 1) for c in range(SLOT_14, SLOT_14 + 3))
        assert row[0][SLOT_14 + 3] is FREE and row[2][SLOT_14] is UNKNOWN  # outside stays normal
        status = str(screen.query_one("#room-status").render())
        assert "Havgus (8 pers), Stormen" in status and "14:00–15:30" in status
        await pilot.press("enter")
        await pilot.pause()
    assert results == [{"rooms": [ROOM_A, ROOM_B], "start": at(14), "end": at(15, 30), "attendees": []}]


async def test_visual_selection_over_a_busy_cell_is_refused(tmp_path):
    app, fetch, results = make_app(tmp_path), FakeDays(), []
    async with app.run_test(size=(160, 40)) as pilot:
        screen, grid = await open_grid_collecting(app, pilot, fetch, results)
        grid.move_cursor(row=1, column=SLOT_14 - 2)  # Stormen 13:00
        await pilot.press("v", "k", "l", "l")  # up to Havgus, 13:00-14:30
        await pilot.pause()
        assert cells(screen)[0][SLOT_14 - 2] is SELECTED["busy"]
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is screen
        assert "Havgus (8 pers) busy 13:00–14:00 (Leif Denby)" in str(screen.query_one("#room-status").render())
    assert results == []


async def test_escape_leaves_visual_mode_before_closing(tmp_path):
    app, fetch, results = make_app(tmp_path), FakeDays(), []
    async with app.run_test(size=(160, 40)) as pilot:
        screen, grid = await open_grid_collecting(app, pilot, fetch, results)
        grid.move_cursor(row=1, column=SLOT_14)
        await pilot.press("v", "l")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen and grid.anchor is None
        assert not painted_selected(screen)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not screen
    assert results == [None]


async def test_v_again_cancels_and_changing_day_drops_selection(tmp_path):
    app, fetch, results = make_app(tmp_path), FakeDays(), []
    async with app.run_test(size=(160, 40)) as pilot:
        screen, grid = await open_grid_collecting(app, pilot, fetch, results)
        await pilot.press("v", "v")
        assert grid.anchor is None
        await pilot.press("v", "l", "]")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert grid.anchor is None and screen._shown.day == DAY + timedelta(days=1)
        assert not painted_selected(screen)


async def test_v_on_room_name_column_starts_at_first_slot(tmp_path):
    app, fetch, results = make_app(tmp_path), FakeDays(), []
    async with app.run_test(size=(160, 40)) as pilot:
        screen, grid = await open_grid_collecting(app, pilot, fetch, results)
        grid.move_cursor(row=1, column=0)
        await pilot.press("v")
        await pilot.pause()
        assert grid.anchor == (1, 1) and grid.cursor_coordinate.column == 1


async def test_find_and_book_through_the_calendar_tab(tmp_path):
    """End to end with the demo backend: 3, f, Enter on a free slot,
    prefilled new event, Ctrl+S books the room."""
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt")])
    cfg.rooms = list(DEMO_ROOMS)
    calendar = DemoCalendarClient()
    app = EwstuiApp(DemoMailClient(), calendar, cfg)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.press("3", "f")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, FindRoomScreen)
        grid = screen.query_one(RoomGrid)
        two_pm = datetime.combine(screen._day, time(14))
        column = 1 + screen._slots.index(two_pm)
        grid.move_cursor(row=1, column=column)  # Aquarium, 14:00 (Room 4B is busy then)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, NewEventScreen)
        assert app.screen.query_one("#event-location", Input).value == "Aquarium (4 pers)"
        app.screen.query_one("#event-subject", Input).value = "Planning"
        await pilot.press("ctrl+s")
        await pilot.pause()
    booked = [e for e in calendar._events if e.subject == "Planning"]
    assert booked and (booked[0].start, booked[0].end) == (two_pm, two_pm + timedelta(hours=1))
    booked_spans = [b[:3] for b in calendar.room_bookings["aquarium@corp.example"]]
    assert (two_pm, two_pm + timedelta(hours=1), "Busy") in booked_spans


async def test_visual_multi_room_booking_through_the_calendar_tab(tmp_path):
    """Demo backend: select Aquarium + Boardroom 15:00-16:00 with v, book
    them in one invite."""
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt")])
    cfg.rooms = list(DEMO_ROOMS)
    calendar = DemoCalendarClient()
    app = EwstuiApp(DemoMailClient(), calendar, cfg)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.press("3", "f")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        screen = app.screen
        grid = screen.query_one(RoomGrid)
        three_pm = datetime.combine(screen._day, time(15))
        grid.move_cursor(row=1, column=1 + screen._slots.index(three_pm))  # Aquarium 15:00
        await pilot.press("v", "j", "l", "enter")  # + Boardroom, until 16:00
        await pilot.pause()
        assert isinstance(app.screen, NewEventScreen)
        assert app.screen.query_one("#event-location", Input).value == "Aquarium (4 pers), Boardroom (16 pers)"
        app.screen.query_one("#event-subject", Input).value = "Workshop"
        await pilot.press("ctrl+s")
        await pilot.pause()
    span = (three_pm, three_pm + timedelta(hours=1), "Busy")
    for room in ("aquarium@corp.example", "boardroom@corp.example"):
        assert span in [b[:3] for b in calendar.room_bookings[room]]
    assert [e.subject for e in calendar._events].count("Workshop") == 1  # one invite, not two


async def test_no_rooms_configured_opens_empty_grid_with_search_hint(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.press("3", "f")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, FindRoomScreen)
        assert app.screen.query_one(RoomGrid).row_count == 0
        assert "press / to find one" in str(app.screen.query_one("#room-status").render())
