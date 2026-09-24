from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable

from ..priority_store import PriorityEntry

_ASCII_A = ord("A")


class PriorityView(DataTable):
    """The local, todo.txt-backed priority list.

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
        self.add_columns("Pri", "Sel", "Created", "Subject", "From")
        self._entries: list[PriorityEntry] = []
        self._visual_anchor: int | None = None

    @property
    def in_visual_mode(self) -> bool:
        return self._visual_anchor is not None

    def set_entries(self, entries: list[PriorityEntry]) -> None:
        self._entries = entries
        self._visual_anchor = None
        self._redraw()

    def _redraw(self) -> None:
        # Capture cursor position and selection *before* clear(), since
        # DataTable.clear() resets the cursor coordinate as a side effect
        # — reading self.cursor_row after that would silently compute the
        # wrong selection range for the '*' markers below.
        cursor_row = self.cursor_row if self.row_count else 0
        selected = self._selected_indices()
        self.clear()
        for i, e in enumerate(self._entries):
            pri = f"({e.priority})" if e.priority else "  -"
            mark = "x" if e.completed else ("*" if i in selected else " ")
            subject = e.description + ("  [dim](done)[/dim]" if e.completed else "")
            self.add_row(pri, mark, e.creation_date or "", subject, e.kv.get("from", ""), key=e.key)
        if self.row_count:
            self.move_cursor(row=min(cursor_row, self.row_count - 1))

    def _selected_indices(self) -> set[int]:
        if self._visual_anchor is None:
            return set()
        lo, hi = sorted((self._visual_anchor, self.cursor_row))
        return set(range(lo, hi + 1))

    def _selected_keys(self) -> set[str]:
        return {self._entries[i].key for i in self._selected_indices() if i < len(self._entries)}

    def _current_key(self) -> str | None:
        if self.row_count == 0:
            return None
        try:
            cell_key = self.coordinate_to_cell_key(self.cursor_coordinate)
        except Exception:
            return None
        return cell_key.row_key.value

    # -- navigation (extends selection live when in visual mode) ----------

    def action_cursor_down(self) -> None:
        super().action_cursor_down()
        if self.in_visual_mode:
            self._redraw()

    def action_cursor_up(self) -> None:
        super().action_cursor_up()
        if self.in_visual_mode:
            self._redraw()

    def action_cursor_top(self) -> None:
        if self.row_count:
            self.move_cursor(row=0)
        if self.in_visual_mode:
            self._redraw()

    def action_cursor_bottom(self) -> None:
        if self.row_count:
            self.move_cursor(row=self.row_count - 1)
        if self.in_visual_mode:
            self._redraw()

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
