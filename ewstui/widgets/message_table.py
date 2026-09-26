from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable
from textual.widgets.data_table import RowDoesNotExist

from ..ews_client import MessageSummary
from ..threads import build_threads, topic, tree_rows


MEETING_TAGS = {"invite": "invite · ", "cancellation": "cancelled · "}


def _fmt_when(dt, now: datetime | None = None) -> str:
    """Compact local date + time: 'Thu 25 Sep 14:05', or '25 Sep 2025 14:05'
    for another year. EWS gives UTC; convert to the machine's timezone.
    """
    if dt is None:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone()  # system local time
    now = now or datetime.now()
    return dt.strftime("%a %d %b %H:%M" if dt.year == now.year else "%d %b %Y %H:%M")


class MessageTable(DataTable):
    """Middle pane: messages in the current folder."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "cursor_top", "Top", show=False),
        Binding("G", "cursor_bottom", "Bottom", show=False),
        Binding("l", "select_cursor", "Open", show=False),
        Binding("o", "select_cursor", "Open", show=False),
        Binding("h", "focus_folders", "Focus folders", show=False),
        Binding("r", "reply", "Reply"),
        Binding("R", "reply_all", "Reply all"),
        Binding("d", "delete", "Delete"),
        Binding("space", "toggle_read", "Toggle read", show=False),
        Binding("P", "add_to_priority", "Add to priority (unset)"),
        Binding("p", "add_to_priority_with_note", "Add to priority + note"),
        Binding("A", "archive", "Archive"),
        Binding("m", "move", "Move"),
        Binding("t", "toggle_threads", "Threads"),
        Binding("v", "view_attachments", "Attachments"),
        Binding("V", "toggle_visual", "Select", show=False),
        Binding("escape", "cancel_visual", "Cancel selection", show=False),
    ]

    class MessageOpened(Message):
        def __init__(self, message_id: str, explicit: bool = False) -> None:
            self.message_id = message_id
            # True only when the user explicitly opened the message
            # (Enter/l) — false when this fires just from moving the
            # cursor with j/k, which should update the preview pane's
            # *content* without stealing keyboard focus away from the
            # list you're still navigating.
            self.explicit = explicit
            super().__init__()

    class ReplyRequested(Message):
        def __init__(self, message_id: str, reply_all: bool) -> None:
            self.message_id = message_id
            self.reply_all = reply_all
            super().__init__()

    class DeleteRequested(Message):
        def __init__(self, message_id: str) -> None:
            self.message_id = message_id
            super().__init__()

    class ToggleReadRequested(Message):
        def __init__(self, message_id: str) -> None:
            self.message_id = message_id
            super().__init__()

    class AddToPriorityRequested(Message):
        def __init__(self, message_id: str) -> None:
            self.message_id = message_id
            super().__init__()

    class AddToPriorityWithNoteRequested(Message):
        def __init__(self, message_id: str) -> None:
            self.message_id = message_id
            super().__init__()

    class ArchiveRequested(Message):
        def __init__(self, message_id: str) -> None:
            self.message_id = message_id
            super().__init__()

    class ThreadsToggled(Message):
        """t: the app refetches (threads need your Sent Items replies) and
        re-renders with `threaded`, keeping the cursor on `message_id`."""

        def __init__(self, threaded: bool, message_id: str | None) -> None:
            self.threaded = threaded
            self.message_id = message_id
            super().__init__()

    class MoveRequested(Message):
        def __init__(self, message_id: str) -> None:
            self.message_id = message_id
            super().__init__()

    class ViewAttachmentsRequested(Message):
        def __init__(self, message_id: str) -> None:
            self.message_id = message_id
            super().__init__()

    class FocusFoldersRequested(Message):
        """h: move keyboard focus back to the folder pane."""

    class BulkRequested(Message):
        """An action on every message of a visual selection (V): `action`
        is "delete", "archive", "move", "toggle_read" or "add_to_priority"."""

        def __init__(self, action: str, message_ids: list[str]) -> None:
            self.action = action
            self.message_ids = message_ids
            super().__init__()

    class VisualChanged(Message):
        """Visual selection started, changed size or ended (status bar)."""

        def __init__(self, count: int) -> None:
            self.count = count  # selected messages; 0 = not in visual mode
            super().__init__()

    def __init__(self, *args, threaded: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.threaded = threaded
        self._messages, self._by_id = [], {}
        self._priorities: dict[str, str | None] = {}  # message id -> priority letter (see _pri_cell)
        # Visual selection (V): the row where it started; the selection is
        # every row between it and the cursor.
        self._anchor: int | None = None
        self._row_cells: dict[str, tuple] = {}  # message id -> cells as added (to un-highlight)
        self._painted: set[str] = set()  # rows currently drawn as selected

    def on_mount(self) -> None:
        self.cursor_type = "row"
        # Date before From/Subject, so it isn't pushed off the pane by a
        # long subject. P: the email's priority on the todo.txt list.
        self.add_column(" ")
        self.add_column("P", key="pri")
        self.add_columns("Received", "From", "Subject")

    def _pri_cell(self, message_id: str) -> str:
        """'A'-'Z' for a prioritized email, '-' for one on the list without
        a priority yet, blank if it isn't on the list."""
        if message_id not in self._priorities:
            return ""
        return self._priorities[message_id] or "-"

    def set_priorities(self, priorities: dict[str, str | None]) -> None:
        """Update just the P column (cursor and everything else untouched)."""
        self._priorities = dict(priorities)
        for m in self._messages:
            if m.id in self._row_cells:
                cells = list(self._row_cells[m.id])
                cells[1] = self._pri_cell(m.id)
                self._row_cells[m.id] = tuple(cells)
            try:
                self.update_cell(m.id, "pri", self._pri_cell(m.id))
            except (RowDoesNotExist, KeyError):
                pass

    # -- rows: flat list, or threads ------------------------------------------
    #
    # Thread view: every message is still its own row (so every action
    # applies to exactly the row you're on), but grouped by conversation:
    # each thread is a tree — its first message, replies nested under the
    # message they answer with ├─/└─ guides — and threads are placed in the
    # newest-first order by their most recent message.

    threaded = False
    _messages: list[MessageSummary]
    _by_id: dict[str, MessageSummary]
    _home_folder: str | None = None

    def set_messages(
        self,
        messages: list[MessageSummary],
        keep_cursor_on: str | None = None,
        home_folder_id: str | None = None,
        priorities: dict[str, str | None] | None = None,
    ) -> None:
        """Replace the rows. With `keep_cursor_on` (a message id), the
        cursor stays on that message (if still listed) and no MessageOpened
        is posted, so a background refresh doesn't refetch/reset the preview.
        `priorities` (message id -> letter) fills the P column; omitted, the
        last one given is kept.
        """
        self._messages = list(messages)
        self._by_id = {m.id: m for m in messages}
        if home_folder_id is not None:
            self._home_folder = home_folder_id
        if priorities is not None:
            self._priorities = dict(priorities)
        self._render(keep_cursor_on)

    def _render(self, keep: str | None = None) -> None:
        if keep is None:
            self._fill()
            return
        with self.prevent(DataTable.RowHighlighted):
            self._fill()
            try:
                self.move_cursor(row=self.get_row_index(keep), animate=False)
            except RowDoesNotExist:
                pass  # gone (deleted/moved elsewhere); cursor stays at the top

    def _fill(self) -> None:
        self.clear()
        self._row_cells, self._painted = {}, set()
        if self._anchor is not None:  # the rows changed under the selection: drop it
            self._anchor = None
            self.post_message(self.VisualChanged(0))
        if not self.threaded:
            for m in self._messages:
                self._add_message_row(m, m.subject)
            return
        for thread in build_threads(self._messages):
            # Upside-down tree: newest message on top, the original at the bottom.
            for i, (m, prefix) in enumerate(tree_rows(thread, newest_on_top=True)):
                # The top row (newest) and the original show the subject; a
                # reply in between with the thread's own topic shows only its
                # tree guide (mutt-style); a changed subject is shown in full.
                show = i == 0 or not prefix or topic(m.subject) != thread.topic
                self._add_message_row(m, prefix + (m.subject if show else ""))

    def _add_message_row(self, m: MessageSummary, subject: str) -> None:
        flag = "" if m.is_read else "●"
        if self._home_folder and m.folder_id and m.folder_id != self._home_folder:
            # A thread reply from Sent Items; after a bare tree guide ("└─ ")
            # no extra space is needed.
            subject += "(sent)" if subject.endswith(" ") else " (sent)"
        tag = MEETING_TAGS.get(getattr(m, "kind", "mail"))
        if tag:  # invites and cancellations: after any tree guide, before the subject
            guide = subject[: len(subject) - len(subject.lstrip("│├└┌─ "))]
            subject = Text.assemble(guide, (tag, "dim"), subject[len(guide):], end="", no_wrap=True)
        cells = (flag, self._pri_cell(m.id), _fmt_when(m.received), m.sender, subject)
        self._row_cells[m.id] = cells
        self.add_row(*cells, key=m.id)

    # -- visual selection (V) ---------------------------------------------------

    @property
    def in_visual_mode(self) -> bool:
        return self._anchor is not None

    def selected_ids(self) -> list[str]:
        """Message ids of the selected rows, top to bottom (empty if not in
        visual mode)."""
        if self._anchor is None or not self.row_count:
            return []
        lo, hi = sorted((self._anchor, self.cursor_row))
        return [self.coordinate_to_cell_key((row, 0)).row_key.value for row in range(lo, min(hi, self.row_count - 1) + 1)]

    def action_toggle_visual(self) -> None:
        if self._anchor is None and self.row_count:
            self._anchor = self.cursor_row
        else:
            self._anchor = None
        self._paint()

    def action_cancel_visual(self) -> None:
        if self._anchor is not None:
            self._anchor = None
            self._paint()

    def _paint(self) -> None:
        """Draw the selected rows highlighted; restore rows that left the
        selection. Tells the app how many are selected (status bar)."""
        wanted = set(self.selected_ids())
        colour = self.app.get_css_variables().get("input-selection-background", "blue")
        for message_id in wanted ^ self._painted:
            cells = self._row_cells.get(message_id)
            if cells is None:
                continue
            for column_key, value in zip(list(self.columns), cells):
                shown = Text(str(value), style=f"bold on {colour}") if message_id in wanted else value
                try:
                    self.update_cell(message_id, column_key, shown)
                except (RowDoesNotExist, KeyError):
                    pass
        self._painted = wanted
        self.post_message(self.VisualChanged(len(wanted)))

    def _bulk(self, action: str) -> bool:
        """In visual mode: end the selection and ask the app to apply
        `action` to all of it. False when not selecting (single-row action)."""
        if self._anchor is None:
            return False
        ids = self.selected_ids()
        self._anchor = None
        self._paint()
        if ids:
            self.post_message(self.BulkRequested(action, ids))
        return True

    def _current_message_id(self) -> str | None:
        if self.row_count == 0:
            return None
        try:
            cell_key = self.coordinate_to_cell_key(self.cursor_coordinate)
        except Exception:
            return None
        return cell_key.row_key.value

    def folder_of(self, message_id: str) -> str | None:
        """The folder a listed message lives in (Sent Items for thread extras)."""
        m = self._by_id.get(message_id)
        return m.folder_id if m else None

    def action_cursor_top(self) -> None:
        if self.row_count:
            self.move_cursor(row=0)

    def action_cursor_bottom(self) -> None:
        if self.row_count:
            self.move_cursor(row=self.row_count - 1)

    def action_select_cursor(self) -> None:
        mid = self._current_message_id()
        if mid:
            self.post_message(self.MessageOpened(mid, explicit=True))

    def action_toggle_threads(self) -> None:
        self.post_message(self.ThreadsToggled(not self.threaded, self._current_message_id()))

    def action_reply(self) -> None:
        mid = self._current_message_id()
        if mid:
            self.post_message(self.ReplyRequested(mid, reply_all=False))

    def action_reply_all(self) -> None:
        mid = self._current_message_id()
        if mid:
            self.post_message(self.ReplyRequested(mid, reply_all=True))

    def action_delete(self) -> None:
        if self._bulk("delete"):
            return
        mid = self._current_message_id()
        if mid:
            self.post_message(self.DeleteRequested(mid))

    def action_toggle_read(self) -> None:
        if self._bulk("toggle_read"):
            return
        mid = self._current_message_id()
        if mid:
            self.post_message(self.ToggleReadRequested(mid))

    def action_add_to_priority(self) -> None:
        if self._bulk("add_to_priority"):
            return
        mid = self._current_message_id()
        if mid:
            self.post_message(self.AddToPriorityRequested(mid))

    def action_add_to_priority_with_note(self) -> None:
        if self.in_visual_mode:  # one note for many emails doesn't make sense; P adds them all
            self.app.bell()
            return
        mid = self._current_message_id()
        if mid:
            self.post_message(self.AddToPriorityWithNoteRequested(mid))

    def action_archive(self) -> None:
        if self._bulk("archive"):
            return
        mid = self._current_message_id()
        if mid:
            self.post_message(self.ArchiveRequested(mid))

    def action_move(self) -> None:
        if self._bulk("move"):
            return
        mid = self._current_message_id()
        if mid:
            self.post_message(self.MoveRequested(mid))

    def action_view_attachments(self) -> None:
        mid = self._current_message_id()
        if mid:
            self.post_message(self.ViewAttachmentsRequested(mid))

    def action_focus_folders(self) -> None:
        self.post_message(self.FocusFoldersRequested())

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        # Used by the app to drive the preview pane as you move j/k
        # through the list (mutt/aerc-style live preview).
        if event.row_key is not None and event.row_key.value:
            self.post_message(self.MessageOpened(event.row_key.value))
        if self._anchor is not None:  # j/k grow or shrink the selection
            self._paint()
