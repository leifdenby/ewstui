"""Room finder: attendees added with Enter (`a` to get there) appear as
rows under the rooms with their day's free/busy, the status line says
who's busy for the cell under the cursor, and they're invited with the room."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from textual.widgets import Input

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DEMO_ROOMS, DemoCalendarClient, DemoMailClient
from ewstui.ews_client import BusySlot, Room, RoomAvailability, RoomsDay
from ewstui.room_grid import BUSY, CHECKING, FREE, UNKNOWN, FindRoomScreen, RoomGrid
from ewstui.screens import NewEventScreen

DAY = date(2026, 9, 25)
ROOM = Room("Havgus", "havgus@corp.example")


def at(hour, minute=0) -> datetime:
    return datetime.combine(DAY, time(hour, minute))


def fetch_day(rooms, day) -> RoomsDay:
    return RoomsDay(day, (time(8), time(17)), [RoomAvailability(r, free=True) for r in rooms])


class FakePeople:
    """boss is in a meeting 10:00-11:00; typo@ isn't a mailbox."""

    def __init__(self):
        self.calls = []

    def __call__(self, emails, start, end):
        self.calls.append((list(emails), start, end))
        out = []
        for e in emails:
            if e.startswith("typo"):
                out.append(RoomAvailability(Room(e, e), free=False, error="ErrorMailRecipientNotFound"))
            elif e.startswith("boss"):
                out.append(RoomAvailability(Room(e, e), free=False, busy=[BusySlot(at(10), at(11), "Busy", "Board")]))
            else:
                out.append(RoomAvailability(Room(e, e), free=True))
        return out


def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    calendar = DemoCalendarClient()
    return EwstuiApp(DemoMailClient(calendar=calendar), calendar, cfg)


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def status(screen) -> str:
    return str(screen.query_one("#room-status").render())


def row(screen, i) -> list:
    grid = screen.query_one(RoomGrid)
    return [grid.get_cell_at((i, c)) for c in range(len(grid.columns))]


async def add(app, pilot, text):
    app.screen.query_one("#room-attendees", Input).value = text
    await pilot.press("enter")
    await settle(app, pilot)


async def test_attendees_become_rows_with_their_free_busy(tmp_path):
    app, people, results = make_app(tmp_path), FakePeople(), []
    async with app.run_test(size=(160, 40)) as pilot:
        screen = FindRoomScreen([ROOM], fetch_day, today=DAY, now=at(8), free_busy=people)
        app.push_screen(screen, results.append)
        await settle(app, pilot)
        await pilot.press("a")
        assert screen.focused is screen.query_one("#room-attendees")
        await add(app, pilot, "boss@corp.example, typo@corp.example")
        await add(app, pilot, "ann@corp.example")
        assert screen.attendees == ["boss@corp.example", "typo@corp.example", "ann@corp.example"]
        assert people.calls[0][1:] == (at(0), at(0) + timedelta(days=1))  # the whole shown day

        ten = 1 + 4  # 08:00 + 4 half hours
        boss, typo, ann = row(screen, 1), row(screen, 2), row(screen, 3)
        assert boss[0].plain == "👤 boss@corp.example"
        assert boss[ten] is BUSY and boss[ten + 2] is FREE and typo[ten] is UNKNOWN and ann[ten] is FREE

        await pilot.press("escape")  # out of the field, back to the grid (not closing)
        assert app.screen is screen and screen.focused is screen.query_one(RoomGrid)
        grid = screen.query_one(RoomGrid)
        grid.move_cursor(row=0, column=ten)
        await pilot.pause()
        assert "Havgus is free 10:00–11:00" in status(screen)
        assert "busy: boss@corp.example (10:00–11:00 (Board))" in status(screen)
        assert "not known: typo@corp.example" in status(screen)

        grid.move_cursor(row=1, column=ten)  # on boss's own row
        await pilot.pause()
        assert status(screen) == "boss@corp.example is busy 10:00–11:00 (Board)"
        await pilot.press("enter")  # people aren't booked
        await pilot.pause()
        assert app.screen is screen and "attendee" in status(screen)

        screen.query_one("#room-attendees").focus()
        await pilot.press("backspace")  # empty field: ann off again
        await settle(app, pilot)
        assert screen.attendees == ["boss@corp.example", "typo@corp.example"]
        assert len(grid.rows) == 3

        grid.focus()
        grid.move_cursor(row=0, column=ten + 2)
        await pilot.press("enter")
        await pilot.pause()
    assert results == [{"rooms": [ROOM], "start": at(11), "end": at(12),
                        "attendees": ["boss@corp.example", "typo@corp.example"]}]


async def test_typing_two_attendees_one_after_the_other(tmp_path):
    app, people = make_app(tmp_path), FakePeople()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = FindRoomScreen([ROOM], fetch_day, today=DAY, now=at(8), free_busy=people)
        app.push_screen(screen)
        await settle(app, pilot)
        await pilot.press("a", *"ann@corp.example", "enter")
        await settle(app, pilot)
        await pilot.press(*"bob@corp.example", "enter")
        await settle(app, pilot)
        assert screen.attendees == ["ann@corp.example", "bob@corp.example"]
        assert len(screen.query_one(RoomGrid).rows) == 3


async def test_attendees_show_as_checking_until_looked_up_and_again_per_day(tmp_path):
    app, people = make_app(tmp_path), FakePeople()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = FindRoomScreen([ROOM], fetch_day, today=DAY, now=at(8), free_busy=people)
        app.push_screen(screen)
        await settle(app, pilot)
        screen._add_attendees("ann@corp.example")
        assert row(screen, 1)[1] is CHECKING  # before the lookup's answer arrives
        await settle(app, pilot)
        assert row(screen, 1)[1] is FREE
        await pilot.press("]")
        await settle(app, pilot)
        assert [c[1].date() for c in people.calls] == [DAY, DAY + timedelta(days=1)]
        await pilot.press("[")
        await settle(app, pilot)
        assert len(people.calls) == 2  # that day's answer is remembered


async def test_attendees_from_the_room_finder_are_invited(tmp_path):
    """Demo backend: the demo boss is busy 14:00-14:30; booking a room with
    them carries them into the new-event form, checked again there."""
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt")])
    cfg.rooms = list(DEMO_ROOMS)
    calendar = DemoCalendarClient()
    app = EwstuiApp(DemoMailClient(), calendar, cfg)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.press("3", "f")
        await settle(app, pilot)
        screen = app.screen
        await pilot.press("a")
        await add(app, pilot, "boss@corp.example")
        await pilot.press("escape")
        grid = screen.query_one(RoomGrid)
        two_pm = datetime.combine(screen._day, time(14))
        grid.move_cursor(row=1, column=1 + screen._slots.index(two_pm))  # Aquarium 14:00
        await pilot.pause()
        assert "busy: boss@corp.example" in status(screen)
        await pilot.press("enter")
        await settle(app, pilot)
        form = app.screen
        assert isinstance(form, NewEventScreen) and form.attendees == ["boss@corp.example"]
        assert "✗ boss@corp.example  busy 14:00–14:30" in str(form.query_one("#event-attendee-list").render())
        form.query_one("#event-subject", Input).value = "Planning"
        await pilot.press("ctrl+s")
        await settle(app, pilot)
    assert calendar.last_created_attendees == ["boss@corp.example"]
