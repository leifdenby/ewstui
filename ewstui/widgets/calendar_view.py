from __future__ import annotations

from datetime import date, timedelta

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable

from ..ews_client import EventSummary

# Row keys of the rows that aren't events
GAP = "gap:"  # the blank row between two days
EMPTY_DAY = "empty:"  # a day with nothing on


def working_week_start(day: date) -> date:
    """The Monday of `day`'s working week, or of the coming one at the weekend."""
    monday = day - timedelta(days=day.weekday())
    return monday + timedelta(weeks=1) if day.weekday() >= 5 else monday


class CalendarView(DataTable):
    """Agenda-style list of events for the currently loaded date range."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "cursor_top", "Top", show=False),
        Binding("G", "cursor_bottom", "Bottom", show=False),
        Binding("l", "select_cursor", "Open", show=False),
        Binding("n", "new_event", "New event"),
        Binding("f", "find_room", "Find room"),
        Binding("d", "delete_event", "Delete"),
        Binding("T", "open_in_teams", "Open in Teams"),
        Binding("[", "prev_range", "Earlier", show=False),
        Binding("]", "next_range", "Later", show=False),
    ]

    class EventOpened(Message):
        def __init__(self, event_id: str) -> None:
            self.event_id = event_id
            super().__init__()

    class NewEventRequested(Message):
        pass

    class FindRoomRequested(Message):
        """f: check which meeting rooms are free, then book one."""

    class DeleteEventRequested(Message):
        def __init__(self, event_id: str) -> None:
            self.event_id = event_id
            super().__init__()

    class TeamsRequested(Message):
        """T: join the event's Teams meeting, or make a Teams meeting of it."""

        def __init__(self, event_id: str) -> None:
            self.event_id = event_id
            super().__init__()

    class RangeShiftRequested(Message):
        def __init__(self, direction: int) -> None:
            self.direction = direction  # -1 or +1
            super().__init__()

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.add_columns("Date", "Time", "Subject", "Location")
        self._row = 0  # last highlighted row, to tell which way the cursor moved

    def set_events(self, events: list[EventSummary], days: list[date] | None = None) -> None:
        """Events grouped by day, a blank row between days. With `days`,
        each of those days is listed, also one with nothing on, and only
        those (an event spanning several days shows on the first)."""
        self.clear()
        by_day: dict[date, list[EventSummary]] = {}
        for ev in sorted(events, key=lambda e: e.start):
            by_day.setdefault(ev.start.date(), []).append(ev)
        if days is not None:
            by_day = {d: by_day.get(d, []) for d in days}
        for i, (day, day_events) in enumerate(sorted(by_day.items())):
            if i:
                self.add_row("", "", "", "", key=f"{GAP}{day}")
            label = Text(day.strftime("%a %Y-%m-%d"), style="bold" if day == date.today() else "")
            if not day_events:
                self.add_row(label, "", Text("nothing on", style="dim"), "", key=f"{EMPTY_DAY}{day}")
            for n, ev in enumerate(day_events):
                time_str = "all day" if ev.is_all_day else f"{ev.start.strftime('%H:%M')}-{ev.end.strftime('%H:%M')}"
                self.add_row(label if n == 0 else "", time_str, ev.subject, ev.location, key=ev.id)

    def _current_event_id(self) -> str | None:
        if self.row_count == 0:
            return None
        try:
            cell_key = self.coordinate_to_cell_key(self.cursor_coordinate)
        except Exception:
            return None
        key = cell_key.row_key.value
        return None if not key or key.startswith((GAP, EMPTY_DAY)) else key

    def action_cursor_top(self) -> None:
        if self.row_count:
            self.move_cursor(row=0)

    def action_cursor_bottom(self) -> None:
        if self.row_count:
            self.move_cursor(row=self.row_count - 1)

    def action_select_cursor(self) -> None:
        eid = self._current_event_id()
        if eid:
            self.post_message(self.EventOpened(eid))

    def action_new_event(self) -> None:
        self.post_message(self.NewEventRequested())

    def action_find_room(self) -> None:
        self.post_message(self.FindRoomRequested())

    def action_open_in_teams(self) -> None:
        eid = self._current_event_id()
        if eid:
            self.post_message(self.TeamsRequested(eid))

    def action_delete_event(self) -> None:
        eid = self._current_event_id()
        if eid:
            self.post_message(self.DeleteEventRequested(eid))

    def action_prev_range(self) -> None:
        self.post_message(self.RangeShiftRequested(-1))

    def action_next_range(self) -> None:
        self.post_message(self.RangeShiftRequested(1))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value if event.row_key is not None else None
        if key and key.startswith(GAP):  # step over the blank row between days
            step = 1 if event.cursor_row >= self._row else -1
            self._row = event.cursor_row
            self.move_cursor(row=max(0, min(self.row_count - 1, event.cursor_row + step)))
            return
        self._row = event.cursor_row
        if key and not key.startswith(EMPTY_DAY):
            self.post_message(self.EventOpened(key))
