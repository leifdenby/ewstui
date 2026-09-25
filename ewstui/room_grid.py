"""
"Find a room" screen: one day as a grid, rooms as rows and half-hour
slots (your working hours) as columns. [/] change the day, the date can
also be typed; Enter on a free cell books from that slot for the chosen
duration, or `v` selects a rectangle of rooms × times and Enter books
all those rooms for that span in one invite.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Input, Label, ListItem, ListView
from textual.worker import get_current_worker

from .ews_client import DEFAULT_WORK_HOURS, Room, RoomAvailability, RoomMatch, RoomsDay

SLOT = timedelta(minutes=30)
FREE = Text(" · ", style="green")
FREE_PAST = Text(" · ", style="dim")
BUSY = Text("███", style="red")
UNKNOWN = Text(" ? ", style="yellow")
CELL = {"free": FREE, "past": FREE_PAST, "busy": BUSY, "unknown": UNKNOWN}
# Same cells inside a visual selection: filled in, so the rectangle stands
# out, with busy/unknown cells still obviously different from free ones.
SELECTED = {
    "free": Text(" ▪ ", style="bold black on green"),
    "past": Text(" ▪ ", style="black on grey50"),
    "busy": Text(" × ", style="bold white on red"),
    "unknown": Text(" ? ", style="bold black on yellow"),
}


def day_slots(day: date, hours: tuple[time, time]) -> list[datetime]:
    """Half-hour slot starts covering `hours` (start rounded down, end up)."""
    start = datetime.combine(day, hours[0]).replace(second=0, microsecond=0)
    start -= timedelta(minutes=start.minute % 30)
    end = datetime.combine(day, hours[1])
    slots = []
    while start < end:
        slots.append(start)
        start += SLOT
    return slots


def is_free(result: RoomAvailability, start: datetime, end: datetime) -> bool:
    if result.error:
        return False
    return not any(b.start < end and b.end > start for b in result.busy)


def clashes(result: RoomAvailability, start: datetime, end: datetime) -> str:
    """'13:00–14:00 (Leif Denby), 15:00–15:30' for bookings overlapping
    [start, end); the parenthesis shows what the room shares about the
    booking (subject, often the organizer's name) when it shares anything.
    """
    return ", ".join(
        f"{b.start:%H:%M}–{b.end:%H:%M}" + (f" ({b.subject})" if b.subject else "")
        for b in result.busy
        if b.start < end and b.end > start
    )


class RoomGrid(DataTable):
    """[/] change day (posted to the screen); j/k rooms; h/l, w/b or
    arrows move along the day; v starts a visual (rectangle) selection
    across rooms and times. Bindings live here, not on the screen, so they
    never swallow typing in the date/duration inputs.
    """

    BINDINGS = [
        Binding("[", "shift_day(-1)", "Previous day"),
        Binding("]", "shift_day(1)", "Next day"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("h", "cursor_left", "Earlier", show=False),
        Binding("l", "cursor_right", "Later", show=False),
        Binding("w", "cursor_right", "Later", show=False),
        Binding("b", "cursor_left", "Earlier", show=False),
        Binding("v", "toggle_visual", "Select"),
        Binding("t", "today", "Today"),
        Binding("slash", "search", "Search rooms"),
        Binding("r", "refresh", "Refresh"),
    ]

    # Visual-mode anchor (row, column) where `v` was pressed; the
    # selection is the rectangle between it and the cursor.
    anchor: tuple[int, int] | None = None

    class DayShift(Message):
        def __init__(self, days: int | None) -> None:
            self.days = days  # None = jump to today
            super().__init__()

    class VisualChanged(Message):
        """Visual mode started/ended; the screen restyles cells and status."""

    class SearchRequested(Message):
        """/: look up rooms that aren't in the grid yet."""

    def action_search(self) -> None:
        self.post_message(self.SearchRequested())

    class RefreshRequested(Message):
        """r: drop cached days and fetch the shown day again."""

    def action_refresh(self) -> None:
        self.post_message(self.RefreshRequested())

    def action_shift_day(self, days: int) -> None:
        self.post_message(self.DayShift(days))

    def action_today(self) -> None:
        self.post_message(self.DayShift(None))

    def action_toggle_visual(self) -> None:
        if self.anchor is None:
            row, col = self.cursor_coordinate
            if col == 0:  # on the room-name column: start at the first slot
                col = 1
                self.move_cursor(column=col, animate=False)
            self.anchor = (row, col)
        else:
            self.anchor = None
        self.post_message(self.VisualChanged())

    def cancel_visual(self) -> None:
        if self.anchor is not None:
            self.anchor = None
            self.post_message(self.VisualChanged())

    def selection(self) -> tuple[range, range] | None:
        """(rows, slot columns) of the visual selection, or None."""
        if self.anchor is None:
            return None
        (r0, c0), (r1, c1) = self.anchor, tuple(self.cursor_coordinate)
        c0, c1 = max(c0, 1), max(c1, 1)  # never include the room-name column
        return range(min(r0, r1), max(r0, r1) + 1), range(min(c0, c1), max(c0, c1) + 1)


class _Results(ListView):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]


class RoomSearchScreen(ModalScreen[Room | None]):
    """Type part of a room's name, Enter to search; pick a result with
    j/k (or ↑/↓) and Enter. Dismisses with the Room, or None.

    `search(query) -> list[RoomMatch]` does the EWS lookups and runs in a
    thread. Directory results can be people, so each shows its source.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    RoomSearchScreen {
        align: center middle;
    }
    #search-box {
        width: 80%;
        height: auto;
        max-height: 80%;
        border: round $accent;
        padding: 0 1;
        background: $surface;
    }
    #search-results {
        height: auto;
        max-height: 20;
    }
    #search-status {
        color: $text-muted;
    }
    """

    def __init__(self, search, listed: set[str]) -> None:
        super().__init__()
        self._search = search
        self._listed = {e.casefold() for e in listed}
        self._matches: list[RoomMatch] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="search-box"):
            yield Label("Find a room not in your list — Enter to search, j/k + Enter to add, Esc cancel", markup=False)
            yield Input(placeholder="Part of the room name, e.g. Havgus", id="search-query")
            yield _Results(id="search-results")
            yield Label("", id="search-status", markup=False)

    def on_mount(self) -> None:
        self.query_one("#search-query", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
        self.query_one("#search-status", Label).update(f"Searching for {query!r} …")
        self._run_search(query)

    @work(thread=True, exclusive=True, group="room-search")
    def _run_search(self, query: str) -> None:
        worker = get_current_worker()
        try:
            matches = self._search(query)
        except Exception as e:  # noqa: BLE001 - show any EWS error here
            if not worker.is_cancelled:
                self.app.call_from_thread(self.query_one("#search-status", Label).update, f"Search failed: {e}")
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._show_matches, query, matches)

    def _show_matches(self, query: str, matches: list[RoomMatch]) -> None:
        self._matches = matches
        results = self.query_one(_Results)
        results.clear()
        for m in matches:
            listed = "  (already in your list)" if m.room.email.casefold() in self._listed else ""
            results.append(ListItem(Label(f"{m.room.name}  <{m.room.email}>  · {m.source}{listed}", markup=False)))
        status = self.query_one("#search-status", Label)
        if not matches:
            status.update(f"No rooms matching {query!r}")
            return
        status.update(f"{len(matches)} found — j/k to choose, Enter to add")
        results.index = 0
        results.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = self.query_one(_Results).index
        if index is not None and index < len(self._matches):
            self.dismiss(self._matches[index].room)

    def action_cancel(self) -> None:
        self.dismiss(None)


class FindRoomScreen(ModalScreen[dict | None]):
    """Dismisses with {"rooms", "start", "end"} for a free slot/selection,
    or None.

    `fetch_day(rooms, day) -> RoomsDay` does the EWS call; it runs in a
    thread so a slow server never freezes the UI. Days are cached so
    paging back and forth is instant; `r` drops the cache and refetches.
    `search(query) -> list[RoomMatch]` backs `/`; a picked room is added
    to the grid and passed to `on_room_added(room)` (which saves it).
    """

    BINDINGS = [Binding("escape", "cancel", "Close")]

    DEFAULT_CSS = """
    FindRoomScreen {
        align: center middle;
    }
    #room-box {
        width: 95%;
        height: auto;
        max-height: 95%;
        border: round $accent;
        padding: 0 1;
        background: $surface;
    }
    #room-inputs {
        height: auto;
    }
    #room-inputs Label {
        padding: 1 1 0 0;
    }
    #room-date {
        width: 16;
    }
    #room-duration {
        width: 8;
    }
    #room-grid {
        height: auto;
        max-height: 30;
    }
    #room-help {
        color: $text-muted;
    }
    """

    def __init__(
        self,
        rooms: list,
        fetch_day,
        today: date | None = None,
        now: datetime | None = None,
        search=None,
        on_room_added=None,
    ) -> None:
        super().__init__()
        self._rooms = list(rooms)
        self._fetch_day = fetch_day
        self._search = search
        self._on_room_added = on_room_added
        self._now_fixed = now is not None  # tests pin "now"; otherwise r refreshes it
        self._now = now or datetime.now()
        self._focus_room: str | None = None  # move the cursor to this room once it's shown
        self._day = today or self._now.date()
        self._cache: dict[date, RoomsDay] = {}
        self._shown: RoomsDay | None = None
        self._slots: list[datetime] = []
        self._kinds: list[list[str]] = []  # per row, per slot: a CELL/SELECTED key
        self._painted: set[tuple[int, int]] = set()  # cells currently drawn as selected

    def compose(self) -> ComposeResult:
        with Vertical(id="room-box"):
            yield Label("", id="room-title", markup=False)
            with Horizontal(id="room-inputs"):
                yield Label("Date")
                yield Input(value=self._day.isoformat(), placeholder="YYYY-MM-DD", id="room-date")
                yield Label("Duration (min)")
                yield Input(value="60", placeholder="60", id="room-duration")
            yield RoomGrid(id="room-grid", cursor_type="cell", zebra_stripes=True)
            yield Label("", id="room-status", markup=False)
            yield Label(
                "[/] day · j/k room · h/l time · v select rooms × times · Enter book · / find a room · "
                "r refresh · t today · Tab edit date · Esc close",
                id="room-help",
                markup=False,  # "[/]" would otherwise parse as a closing tag
            )

    def on_mount(self) -> None:
        self.query_one(RoomGrid).focus()
        self.load_day(self._day)

    # -- loading ---------------------------------------------------------

    def load_day(self, day: date) -> None:
        self.query_one(RoomGrid).cancel_visual()  # a selection belongs to one day
        self._day = day
        self.query_one("#room-date", Input).value = day.isoformat()
        if day in self._cache:
            self._show(self._cache[day])
            return
        self._set_status(f"Loading {len(self._rooms)} rooms for {day:%a %d %b} …")
        self._fetch(day)

    @work(thread=True, exclusive=True, group="room-day")
    def _fetch(self, day: date) -> None:
        worker = get_current_worker()
        try:
            result = self._fetch_day(self._rooms, day)
        except Exception as e:  # noqa: BLE001 - show any EWS error in the screen
            if not worker.is_cancelled:
                self.app.call_from_thread(self._set_status, f"Couldn't load {day:%a %d %b}: {e}")
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._loaded, result)

    def _loaded(self, result: RoomsDay) -> None:
        self._cache[result.day] = result
        if result.day == self._day:  # ignore a day the user already paged away from
            self._show(result)

    def _show(self, result: RoomsDay) -> None:
        grid = self.query_one(RoomGrid)
        keep = grid.cursor_coordinate if self._shown is not None else None
        hours = result.work_hours or DEFAULT_WORK_HOURS
        self._shown, self._slots = result, day_slots(result.day, hours)

        source = "your Outlook working hours" if result.work_hours else "default hours"
        self.query_one("#room-title", Label).update(
            f"Rooms — {result.day:%A %d %B %Y} · {hours[0]:%H:%M}–{hours[1]:%H:%M} ({source})"
        )
        grid.clear(columns=True)
        grid.add_column("Room", key="room")
        for slot in self._slots:
            grid.add_column(slot.strftime("%H:%M") if slot.minute == 0 else "  :30", key=slot.isoformat())
        self._kinds, self._painted = [], set()
        for r in result.rooms:
            kinds = []
            for slot in self._slots:
                if r.error:
                    kinds.append("unknown")
                elif not is_free(r, slot, slot + SLOT):
                    kinds.append("busy")
                else:
                    kinds.append("past" if slot + SLOT <= self._now else "free")
            self._kinds.append(kinds)
            grid.add_row(Text(r.room.name), *(CELL[k] for k in kinds), key=r.room.email)

        if keep is not None:
            grid.move_cursor(row=keep.row, column=keep.column, animate=False)
        else:
            upcoming = next((i for i, s in enumerate(self._slots) if s >= self._now), 0)
            grid.move_cursor(row=0, column=upcoming + 1, animate=False)
        if self._focus_room is not None:
            emails = [r.room.email for r in result.rooms]
            if self._focus_room in emails:
                grid.move_cursor(row=emails.index(self._focus_room), animate=False)
            self._focus_room = None
        if not result.rooms:
            self._set_status("No rooms in your list yet — press / to find one")
            return
        self._describe_cursor()

    # -- cursor / booking -----------------------------------------------

    def _duration(self) -> timedelta | None:
        try:
            minutes = int(self.query_one("#room-duration", Input).value.strip())
        except ValueError:
            return None
        return timedelta(minutes=minutes) if minutes > 0 else None

    def _cell(self) -> tuple[RoomAvailability, datetime] | None:
        grid = self.query_one(RoomGrid)
        if self._shown is None or not self._shown.rooms:
            return None
        row, col = grid.cursor_coordinate
        if col == 0 or not (0 <= row < len(self._shown.rooms)) or col - 1 >= len(self._slots):
            return None  # the room-name column
        return self._shown.rooms[row], self._slots[col - 1]

    def _selected_booking(self) -> tuple[list[RoomAvailability], datetime, datetime] | None:
        """(rooms, start, end) covered by the visual selection, or None."""
        sel = self.query_one(RoomGrid).selection()
        if sel is None or self._shown is None or not self._slots:
            return None
        rows, cols = sel
        rooms = [self._shown.rooms[r] for r in rows if r < len(self._shown.rooms)]
        first, last = cols.start - 1, min(cols.stop - 1, len(self._slots)) - 1
        return rooms, self._slots[first], self._slots[last] + SLOT

    def _paint_selection(self) -> None:
        """Redraw only the cells whose selected/unselected state changed."""
        grid = self.query_one(RoomGrid)
        sel = grid.selection()
        wanted = {(r, c) for r in sel[0] for c in sel[1]} if sel else set()
        wanted = {(r, c) for r, c in wanted if r < len(self._kinds) and c - 1 < len(self._slots)}
        for r, c in wanted ^ self._painted:
            style = SELECTED if (r, c) in wanted else CELL
            grid.update_cell_at(Coordinate(r, c), style[self._kinds[r][c - 1]])
        self._painted = wanted

    def _describe_selection(self) -> None:
        booking = self._selected_booking()
        if booking is None:
            return
        rooms, start, end = booking
        problems = []
        for r in rooms:
            if r.error:
                problems.append(f"{r.room.name} couldn't be checked")
            elif not is_free(r, start, end):
                problems.append(f"{r.room.name} busy {clashes(r, start, end)}")
        names = ", ".join(r.room.name for r in rooms)
        if problems:
            self._set_status(f"Can't book {start:%H:%M}–{end:%H:%M}: " + "; ".join(problems))
        else:
            self._set_status(f"{names} · {start:%H:%M}–{end:%H:%M} — Enter to book, Esc/v to cancel")

    def _describe_cursor(self) -> None:
        if self.query_one(RoomGrid).anchor is not None:
            self._describe_selection()
            return
        cell = self._cell()
        duration = self._duration()
        if cell is None:
            self._set_status("Move onto a time slot (w/b or ←/→) and press Enter to book")
            return
        result, start = cell
        if result.error:
            self._set_status(f"{result.room.name}: couldn't check — {result.error}")
            return
        if duration is None:
            self._set_status("Duration must be a positive number of minutes")
            return
        end = start + duration
        if is_free(result, start, end):
            self._set_status(f"{result.room.name} is free {start:%H:%M}–{end:%H:%M} — Enter to book")
        else:
            self._set_status(f"{result.room.name} is busy {clashes(result, start, end)}")

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        self._paint_selection()
        self._describe_cursor()

    def on_room_grid_visual_changed(self, event: RoomGrid.VisualChanged) -> None:
        self._paint_selection()
        self._describe_cursor()

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        booking = self._selected_booking()
        if booking is not None:
            rooms, start, end = booking
            if any(r.error or not is_free(r, start, end) for r in rooms):
                self.app.bell()
                self._describe_selection()
                return
            self.dismiss({"rooms": [r.room for r in rooms], "start": start, "end": end})
            return
        cell = self._cell()
        duration = self._duration()
        if cell is None or duration is None:
            self.app.bell()
            return
        result, start = cell
        if not is_free(result, start, start + duration):
            self.app.bell()
            self._describe_cursor()
            return
        self.dismiss({"rooms": [result.room], "start": start, "end": start + duration})

    def on_room_grid_refresh_requested(self, event: RoomGrid.RefreshRequested) -> None:
        self._reload()

    def _reload(self) -> None:
        """Forget every cached day and fetch the shown one again (cursor
        stays put, since _show keeps it once a day has been shown)."""
        if not self._now_fixed:
            self._now = datetime.now()  # slots that have passed since opening turn grey
        self._cache.clear()
        self.load_day(self._day)

    def on_room_grid_search_requested(self, event: RoomGrid.SearchRequested) -> None:
        if self._search is None:
            self.app.bell()
            return
        self.app.push_screen(RoomSearchScreen(self._search, {r.email for r in self._rooms}), self._room_picked)

    def _room_picked(self, room: Room | None) -> None:
        if room is None:
            return
        existing = next((r for r in self._rooms if r.email.casefold() == room.email.casefold()), None)
        if existing is not None:  # already in the grid: just go to it
            self._focus_room = existing.email
            if self._shown is not None:
                self._show(self._shown)
            return
        self._focus_room = room.email
        self._rooms.append(room)
        if self._on_room_added is not None:
            self._on_room_added(room)
        self._reload()

    def on_room_grid_day_shift(self, event: RoomGrid.DayShift) -> None:
        self.load_day(self._now.date() if event.days is None else self._day + timedelta(days=event.days))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "room-date":
            try:
                day = date.fromisoformat(event.value.strip())
            except ValueError:
                self._set_status("Date must be YYYY-MM-DD")
                self.app.bell()
                return
            self.load_day(day)
        else:
            self._describe_cursor()
        self.query_one(RoomGrid).focus()

    def _set_status(self, text: str) -> None:
        self.query_one("#room-status", Label).update(text)

    def action_cancel(self) -> None:
        grid = self.query_one(RoomGrid)
        if grid.anchor is not None:  # Esc leaves visual mode first, like vim
            grid.cancel_visual()
            return
        self.dismiss(None)
