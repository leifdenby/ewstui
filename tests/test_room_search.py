"""Room grid: / to find and add rooms not in your list, r to refresh."""
from __future__ import annotations

from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import tomlkit
from exchangelib.errors import ErrorNameResolutionNoResults

from ewstui import config_file
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DEMO_ROOMS, DemoCalendarClient, DemoMailClient
from ewstui.ews_client import BusySlot, CalendarClient, Room, RoomAvailability, RoomMatch, RoomsDay
from ewstui.room_grid import BUSY, FREE, FindRoomScreen, RoomGrid, RoomSearchScreen

DAY = date(2026, 9, 25)
HAVGUS = Room("Havgus (8 pers)", "havgus@corp.example")
STORMEN = Room("Stormen", "stormen@corp.example")
AQUARIUM = Room("Aquarium (4 pers)", "aquarium@corp.example")


def at(hour, minute=0) -> datetime:
    return datetime.combine(DAY, time(hour, minute))


class Fetch:
    """fetch_day stand-in answering for whatever rooms it's asked about;
    `busy` maps room email -> slots, and can change between calls."""

    def __init__(self):
        self.busy: dict[str, list[BusySlot]] = {}
        self.calls: list[list[str]] = []

    def __call__(self, rooms, day):
        self.calls.append([r.email for r in rooms])
        return RoomsDay(
            day=day,
            work_hours=(time(8), time(17)),
            rooms=[
                RoomAvailability(room=r, free=not self.busy.get(r.email), busy=list(self.busy.get(r.email, [])))
                for r in rooms
            ],
        )


def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt")])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def open_grid(app, pilot, fetch, rooms, **kw) -> FindRoomScreen:
    screen = FindRoomScreen(rooms, fetch, now=at(10, 5), **kw)
    app.push_screen(screen)
    await settle(app, pilot)
    return screen


def status(screen) -> str:
    return str(screen.query_one("#room-status" if isinstance(screen, FindRoomScreen) else "#search-status").render())


# -- r: refresh ----------------------------------------------------------------


async def test_r_refetches_the_day_and_keeps_the_cursor(tmp_path):
    app, fetch = make_app(tmp_path), Fetch()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await open_grid(app, pilot, fetch, [HAVGUS, STORMEN])
        grid = screen.query_one(RoomGrid)
        column_14 = 1 + 12
        grid.move_cursor(row=1, column=column_14)
        assert grid.get_row_at(1)[column_14] is FREE

        fetch.busy[STORMEN.email] = [BusySlot(at(14), at(15), "Busy", "Leif Denby")]  # someone books it
        await pilot.press("r")
        await settle(app, pilot)

        assert len(fetch.calls) == 2
        assert grid.get_row_at(1)[column_14] is BUSY
        assert tuple(grid.cursor_coordinate) == (1, column_14)
        assert "busy 14:00–15:00 (Leif Denby)" in status(screen)


async def test_r_drops_every_cached_day(tmp_path):
    app, fetch = make_app(tmp_path), Fetch()
    async with app.run_test(size=(160, 40)) as pilot:
        await open_grid(app, pilot, fetch, [HAVGUS])
        for key in ("]", "[", "r", "]"):  # tomorrow, back (cached), refresh, tomorrow again (refetched)
            await pilot.press(key)
            await settle(app, pilot)
        assert len(fetch.calls) == 4


# -- /: search -----------------------------------------------------------------


class Search:
    def __init__(self, matches):
        self.matches = matches
        self.queries: list[str] = []

    def __call__(self, query):
        self.queries.append(query)
        return [m for m in self.matches if query.casefold() in m.room.name.casefold()]


async def search_for(app, pilot, text):
    await pilot.press("slash")
    await pilot.pause()
    assert isinstance(app.screen, RoomSearchScreen)
    app.screen.query_one("#search-query").value = text
    await pilot.press("enter")
    await settle(app, pilot)


async def test_search_adds_room_to_grid_and_reports_it(tmp_path):
    app, fetch, added = make_app(tmp_path), Fetch(), []
    search = Search([RoomMatch(AQUARIUM, "room list"), RoomMatch(Room("Aqua Person", "ap@corp.example"), "directory")])
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await open_grid(app, pilot, fetch, [HAVGUS], search=search, on_room_added=added.append)
        await search_for(app, pilot, "aqua")
        labels = [str(item.query_one("Label").render()) for item in app.screen.query("ListItem")]
        assert labels == [
            "Aquarium (4 pers)  <aquarium@corp.example>  · room list",
            "Aqua Person  <ap@corp.example>  · directory",
        ]
        await pilot.press("enter")  # first result
        await settle(app, pilot)

        assert app.screen is screen
        assert added == [AQUARIUM]
        assert fetch.calls[-1] == [HAVGUS.email, AQUARIUM.email]  # refetched with the new room
        grid = screen.query_one(RoomGrid)
        assert grid.row_count == 2 and grid.cursor_coordinate.row == 1  # cursor on the new room


async def test_picking_a_listed_room_just_moves_to_it(tmp_path):
    app, fetch, added = make_app(tmp_path), Fetch(), []
    search = Search([RoomMatch(STORMEN, "room list")])
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await open_grid(app, pilot, fetch, [HAVGUS, STORMEN], search=search, on_room_added=added.append)
        await search_for(app, pilot, "storm")
        assert "(already in your list)" in str(app.screen.query_one("ListItem Label").render())
        await pilot.press("enter")
        await settle(app, pilot)
        assert added == [] and len(fetch.calls) == 1
        assert screen.query_one(RoomGrid).cursor_coordinate.row == 1


async def test_search_with_no_hits_and_escape(tmp_path):
    app, fetch, added = make_app(tmp_path), Fetch(), []
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await open_grid(app, pilot, fetch, [HAVGUS], search=Search([]), on_room_added=added.append)
        await search_for(app, pilot, "zzz")
        assert "No rooms matching 'zzz'" in status(app.screen)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen and added == []


async def test_search_error_is_shown(tmp_path):
    def broken(query):
        raise RuntimeError("server said no")

    app, fetch = make_app(tmp_path), Fetch()
    async with app.run_test(size=(160, 40)) as pilot:
        await open_grid(app, pilot, fetch, [HAVGUS], search=broken)
        await search_for(app, pilot, "x")
        assert "Search failed: server said no" in status(app.screen)


async def test_typing_j_k_in_the_search_box_is_text(tmp_path):
    app, fetch = make_app(tmp_path), Fetch()
    async with app.run_test(size=(160, 40)) as pilot:
        await open_grid(app, pilot, fetch, [HAVGUS], search=Search([]))
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("j", "k", "r", "slash")
        assert app.screen.query_one("#search-query").value == "jkr/"


# -- live client ---------------------------------------------------------------


def live_client(room_lists=(), rooms_by_list=None, resolved=(), roomlist_error=None):
    protocol = MagicMock()
    if roomlist_error:
        protocol.get_roomlists.side_effect = roomlist_error
    else:
        protocol.get_roomlists.return_value = [SimpleNamespace(email_address=e) for e in room_lists]
    protocol.get_rooms.side_effect = lambda email: [
        SimpleNamespace(name=r.name, email_address=r.email) for r in (rooms_by_list or {}).get(email, [])
    ]
    protocol.resolve_names.return_value = list(resolved)
    account = SimpleNamespace(protocol=protocol)
    client = CalendarClient.__new__(CalendarClient)  # skip the timezone lookup
    client.account, client._room_directory = account, None
    return client, protocol


def test_live_search_room_lists_first_then_directory_deduplicated():
    client, protocol = live_client(
        room_lists=["kefm@corp.example"],
        rooms_by_list={"kefm@corp.example": [HAVGUS, STORMEN]},
        resolved=[
            SimpleNamespace(name="Havgus (8 pers)", email_address="HAVGUS@corp.example"),  # same room, other case
            SimpleNamespace(name="Havgus Hansen", email_address="hh@corp.example"),
            ErrorNameResolutionNoResults("none"),  # EWS returns errors inline
        ],
    )
    matches = client.search_rooms("havgus")
    assert matches == [
        RoomMatch(HAVGUS, "room list"),
        RoomMatch(Room("Havgus Hansen", "hh@corp.example"), "directory"),
    ]
    assert protocol.resolve_names.call_args.kwargs == {"names": ["havgus"], "search_scope": "ActiveDirectory"}
    client.search_rooms("storm")
    assert protocol.get_roomlists.call_count == 1  # room lists fetched once per session


def test_live_search_without_room_lists_uses_directory():
    client, _ = live_client(
        roomlist_error=RuntimeError("no room lists"),
        resolved=[SimpleNamespace(name="Havgus (8 pers)", email_address="havgus@corp.example")],
    )
    assert client.search_rooms("havgus") == [RoomMatch(HAVGUS, "directory")]
    assert client.search_rooms("   ") == []


# -- saving found rooms --------------------------------------------------------


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        ("", {"Havgus (8 pers)": "havgus@corp.example"}),
        ('[accounts.work.rooms]\n"Stormen" = "stormen@corp.example"\n',
         {"Stormen": "stormen@corp.example", "Havgus (8 pers)": "havgus@corp.example"}),
        ('rooms = ["stormen@corp.example"]\n', ["stormen@corp.example", "havgus@corp.example"]),
    ],
    ids=["no-rooms-yet", "table", "list"],
)
def test_add_room_keeps_the_files_shape(tmp_path, existing, expected):
    path = tmp_path / "config.toml"
    header = '[accounts.work]\nemail = "me@corp.example"  # mine\n'
    if existing.startswith("rooms"):
        path.write_text(header + existing)
    else:
        path.write_text(header + ("\n" + existing if existing else ""))
    assert config_file.add_room(path, "work", HAVGUS) is True
    assert config_file.add_room(path, "work", Room("Havgus", "HAVGUS@corp.example")) is False  # already there
    data = tomlkit.parse(path.read_text()).unwrap()
    assert data["accounts"]["work"]["rooms"] == expected
    assert "# mine" in path.read_text()


def test_add_room_needs_the_account(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[accounts.other]\nemail = "x@y"\n')
    with pytest.raises(config_file.ConfigFileError, match=r"no \[accounts.work\]"):
        config_file.add_room(path, "work", HAVGUS)


async def test_found_room_is_saved_to_the_account_end_to_end(tmp_path, monkeypatch):
    """Demo backend + real config file: 3, f, / havgus, pick, and the room
    lands in [accounts.work.rooms] and in the running config."""
    path = tmp_path / "config.toml"
    path.write_text('default_account = "work"\n\n[accounts.work]\nemail = "me@corp.example"\n')
    cfg = config_from_args(["--config", str(path), "--demo", "--priority-file", str(tmp_path / "p.todo.txt")])
    app = EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.press("3", "f")
        await settle(app, pilot)
        await search_for(app, pilot, "havgus")
        labels = [str(item.query_one("Label").render()) for item in app.screen.query("ListItem")]
        assert labels[0].startswith("Havgus (8 pers)") and "directory" in labels[1]  # room, then a person
        await pilot.press("enter")
        await settle(app, pilot)
        assert [r.email for r in app.config.rooms] == ["havgus@corp.example"]
    assert config_file.account_rooms(tomlkit.parse(path.read_text()), "work") == [
        Room("Havgus (8 pers)", "havgus@corp.example")
    ]


async def test_found_room_without_account_is_session_only(tmp_path):
    app = make_app(tmp_path)
    seen = []
    async with app.run_test(size=(160, 40)) as pilot:
        original = app.notify
        app.notify = lambda msg, **kw: (seen.append(msg), original(msg, **kw))
        await pilot.press("3", "f")
        await settle(app, pilot)
        await search_for(app, pilot, "stormvejr")
        await pilot.press("enter")
        await settle(app, pilot)
        assert [r.name for r in app.config.rooms] == ["Stormvejr (12 pers)"]
    assert any("for this session" in m and "--account" in m for m in seen)
