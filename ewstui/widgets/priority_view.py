from __future__ import annotations

from datetime import date

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable

from ..priority_store import PriorityEntry, due_status

_ASCII_A = ord("A")


class PriorityView(DataTable):
    """The local, todo.txt-backed priority list, grouped by priority under
    header rows (the cursor skips them); overdue items are red, items due
    soon yellow.

    Normal mode: j/k/g/G to move, a capital letter (A-Z) sets the
    priority of the row under the cursor.

    Visual mode: press `V` to start a selection at the current row;
    j/k extend it (vim visual-line style); pressing a capital letter
    applies that priority to every selected row at once and exits
    visual mode; Escape cancels the selection without changing anything.
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "cursor_top", "Top", show=False),
        Binding("G", "cursor_bottom", "Bottom", show=False),
        Binding("o", "select_cursor", "Open", show=False),
        Binding("V", "toggle_visual", "Visual select"),
        Binding("escape", "cancel_visual", "Cancel select", show=False),
        Binding("x", "toggle_complete", "Complete"),
        Binding("d", "remove_entry", "Remove"),
        *[Binding(chr(_ASCII_A + i), f"set_priority('{chr(_ASCII_A + i)}')", show=False) for i in range(26)],
    ]

    class EntryOpened(Message):
        def __init__(self, entry_key: str) -> None:
            self.entry_key = entry_key
            super().__init__()

    class PriorityChanged(Message):
        def __init__(self, entry_keys: set[str], priority: str) -> None:
            self.entry_keys = entry_keys
            self.priority = priority
            super().__init__()

    class CompleteToggled(Message):
        def __init__(self, entry_key: str) -> None:
            self.entry_key = entry_key
            super().__init__()

    class RemoveRequested(Message):
        def __init__(self, entry_key: str) -> None:
            self.entry_key = entry_key
            super().__init__()

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.add_columns("Pri", "Sel", "Due", "Created", "Subject", "From")
        self._entries: list[PriorityEntry] = []
        # What each table row shows: an entry, or None for a group header.
        self._rows: list[PriorityEntry | None] = []
        self._visual_anchor: int | None = None
        self.watch(self.app, "theme", self._redraw, init=False)  # colours come from the theme

    @property
    def in_visual_mode(self) -> bool:
        return self._visual_anchor is not None

    def set_entries(self, entries: list[PriorityEntry]) -> None:
        self._entries = entries
        self._visual_anchor = None
        self._redraw()

    def current_entry(self) -> PriorityEntry | None:
        key = self._current_key()
        return next((e for e in self._entries if e.key == key), None)

    def select_matching(self, entry: PriorityEntry) -> None:
        """Put the cursor on the same item after a reload. Keys change when
        a line is edited, so match on the email (Message-ID, then EWS id),
        falling back to the description; stay put if it's gone."""

        def same(e: PriorityEntry) -> bool:
            if entry.internet_id and e.internet_id:
                return e.internet_id == entry.internet_id
            if entry.message_id and e.message_id:
                return e.message_id == entry.message_id
            return e.description == entry.description

        for row, e in enumerate(self._rows):
            if e is not None and same(e):
                self.move_cursor(row=row)
                return

    def _redraw(self) -> None:
        """The entries grouped by priority (they come sorted A..Z, then
        unprioritized), each group under a header row, tuxedo style.
        Overdue items are red, items due soon yellow."""
        # Capture cursor position and selection *before* clear(), since
        # DataTable.clear() resets the cursor coordinate as a side effect
        # — reading self.cursor_row after that would silently compute the
        # wrong selection range for the '*' markers below.
        cursor_row = self.cursor_row if self.row_count else 0
        selected = self._selected_indices()
        colours = self.app.theme_variables
        status_style = {"overdue": colours.get("error", "red"), "soon": colours.get("warning", "yellow")}
        header_style = f"bold {colours.get('accent', '')}"
        today = date.today()
        self.clear()
        self._rows = []
        group: object = object()  # no group yet
        for e in self._entries:
            if e.priority != group:
                group = e.priority
                label = f"({e.priority})" if e.priority else "(-)"
                self.add_row(Text(label, style=header_style), "", "", "", "", "", key=f"group:{label}")
                self._rows.append(None)
            style = status_style.get(due_status(e, today), "")
            mark = "x" if e.completed else ("*" if len(self._rows) in selected else " ")
            subject = Text(e.description, style=style)
            if e.completed:
                subject.append("  (done)", style="dim")
            due = Text(e.kv.get("due", ""), style=style)
            self.add_row("", mark, due, e.creation_date or "", subject, e.kv.get("from", ""), key=e.key)
            self._rows.append(e)
        if self.row_count:
            self.move_cursor(row=self._entry_row(min(cursor_row, self.row_count - 1), 1))

    def _entry_row(self, row: int, step: int) -> int:
        """`row`, or if that's a group header the nearest entry row going in
        direction `step` (+1 down, -1 up), or the other way if there's none."""
        for direction in (step, -step):
            r = row
            while 0 <= r < len(self._rows):
                if self._rows[r] is not None:
                    return r
                r += direction
        return row

    def _selected_indices(self) -> set[int]:
        if self._visual_anchor is None:
            return set()
        lo, hi = sorted((self._visual_anchor, self.cursor_row))
        return set(range(lo, hi + 1))

    def _selected_keys(self) -> set[str]:
        rows = [self._rows[i] for i in self._selected_indices() if i < len(self._rows)]
        return {e.key for e in rows if e is not None}

    def _current_key(self) -> str | None:
        if self.row_count == 0:
            return None
        e = self._rows[self.cursor_row] if self.cursor_row < len(self._rows) else None
        return e.key if e is not None else None

    # -- navigation (skips group headers; extends selection live in visual mode)

    def _move_to(self, row: int, step: int) -> None:
        if self.row_count:
            self.move_cursor(row=self._entry_row(max(0, min(row, self.row_count - 1)), step))
        if self.in_visual_mode:
            self._redraw()

    def action_cursor_down(self) -> None:
        self._move_to(self.cursor_row + 1, 1)

    def action_cursor_up(self) -> None:
        self._move_to(self.cursor_row - 1, -1)

    def action_cursor_top(self) -> None:
        self._move_to(0, 1)

    def action_cursor_bottom(self) -> None:
        self._move_to(self.row_count - 1, -1)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        # A click or page up/down can land on a header: step off it. (Look
        # at where the cursor is now: the event may be stale, e.g. from the
        # clear() in a redraw.)
        row = self.cursor_row
        if 0 <= row < len(self._rows) and self._rows[row] is None:
            self.move_cursor(row=self._entry_row(row, 1))

    # -- visual selection ---------------------------------------------

    def action_toggle_visual(self) -> None:
        if self.in_visual_mode:
            self._visual_anchor = None
        else:
            self._visual_anchor = self.cursor_row
        self._redraw()

    def action_cancel_visual(self) -> None:
        if self.in_visual_mode:
            self._visual_anchor = None
            self._redraw()

    # -- actions --------------------------------------------------------

    def action_select_cursor(self) -> None:
        key = self._current_key()
        if key:
            self.post_message(self.EntryOpened(key))

    def action_set_priority(self, letter: str) -> None:
        if self.row_count == 0:
            return
        keys = self._selected_keys() if self.in_visual_mode else {self._current_key()}
        keys.discard(None)
        if not keys:
            return
        self._visual_anchor = None
        self.post_message(self.PriorityChanged(keys, letter))

    def action_toggle_complete(self) -> None:
        key = self._current_key()
        if key:
            self.post_message(self.CompleteToggled(key))

    def action_remove_entry(self) -> None:
        key = self._current_key()
        if key:
            self.post_message(self.RemoveRequested(key))
