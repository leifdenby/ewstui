from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable

from ..ews_client import EventSummary


class CalendarView(DataTable):
    """Agenda-style list of events for the currently loaded date range."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "cursor_top", "Top", show=False),
        Binding("G", "cursor_bottom", "Bottom", show=False),
        Binding("l", "select_cursor", "Open", show=False),
        Binding("n", "new_event", "New event"),
        Binding("d", "delete_event", "Delete"),
        Binding("[", "prev_range", "Earlier", show=False),
        Binding("]", "next_range", "Later", show=False),
    ]

    class EventOpened(Message):
        def __init__(self, event_id: str) -> None:
            self.event_id = event_id
            super().__init__()

    class NewEventRequested(Message):
        pass

    class DeleteEventRequested(Message):
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

    def set_events(self, events: list[EventSummary]) -> None:
        self.clear()
        for ev in sorted(events, key=lambda e: e.start):
            date_str = ev.start.strftime("%a %Y-%m-%d")
            time_str = "all day" if ev.is_all_day else f"{ev.start.strftime('%H:%M')}-{ev.end.strftime('%H:%M')}"
            self.add_row(date_str, time_str, ev.subject, ev.location, key=ev.id)

    def _current_event_id(self) -> str | None:
        if self.row_count == 0:
            return None
        try:
            cell_key = self.coordinate_to_cell_key(self.cursor_coordinate)
        except Exception:
            return None
        return cell_key.row_key.value

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

    def action_delete_event(self) -> None:
        eid = self._current_event_id()
        if eid:
            self.post_message(self.DeleteEventRequested(eid))

    def action_prev_range(self) -> None:
        self.post_message(self.RangeShiftRequested(-1))

    def action_next_range(self) -> None:
        self.post_message(self.RangeShiftRequested(1))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is not None and event.row_key.value:
            self.post_message(self.EventOpened(event.row_key.value))
