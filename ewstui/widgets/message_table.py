from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable
from textual.widgets.data_table import RowDoesNotExist

from ..ews_client import MessageSummary


def _fmt_when(dt) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


class MessageTable(DataTable):
    """Middle pane: messages in the current folder."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "cursor_top", "Top", show=False),
        Binding("G", "cursor_bottom", "Bottom", show=False),
        Binding("l", "select_cursor", "Open", show=False),
        Binding("h", "focus_folders", "Focus folders", show=False),
        Binding("r", "reply", "Reply"),
        Binding("R", "reply_all", "Reply all"),
        Binding("d", "delete", "Delete"),
        Binding("space", "toggle_read", "Toggle read", show=False),
        Binding("P", "add_to_priority", "Add to priority (unset)"),
        Binding("p", "add_to_priority_with_note", "Add to priority + note"),
        Binding("A", "archive", "Archive"),
        Binding("v", "view_attachments", "Attachments"),
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

    class ViewAttachmentsRequested(Message):
        def __init__(self, message_id: str) -> None:
            self.message_id = message_id
            super().__init__()

    class FocusFoldersRequested(Message):
        """h: move keyboard focus back to the folder pane."""

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.add_columns(" ", "From", "Subject", "Received")

    def set_messages(self, messages: list[MessageSummary], keep_cursor_on: str | None = None) -> None:
        """Replace the rows. With `keep_cursor_on`, the cursor stays on
        that message (if still listed) and no MessageOpened is posted, so
        a background refresh doesn't refetch/reset the preview.
        """
        if keep_cursor_on is None:
            self._fill(messages)
            return
        with self.prevent(DataTable.RowHighlighted):
            self._fill(messages)
            try:
                self.move_cursor(row=self.get_row_index(keep_cursor_on), animate=False)
            except RowDoesNotExist:
                pass  # gone (deleted/moved elsewhere); cursor stays at the top

    def _fill(self, messages: list[MessageSummary]) -> None:
        self.clear()
        for m in messages:
            flag = "" if m.is_read else "●"
            self.add_row(flag, m.sender, m.subject, _fmt_when(m.received), key=m.id)

    def _current_message_id(self) -> str | None:
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
        mid = self._current_message_id()
        if mid:
            self.post_message(self.MessageOpened(mid, explicit=True))

    def action_reply(self) -> None:
        mid = self._current_message_id()
        if mid:
            self.post_message(self.ReplyRequested(mid, reply_all=False))

    def action_reply_all(self) -> None:
        mid = self._current_message_id()
        if mid:
            self.post_message(self.ReplyRequested(mid, reply_all=True))

    def action_delete(self) -> None:
        mid = self._current_message_id()
        if mid:
            self.post_message(self.DeleteRequested(mid))

    def action_toggle_read(self) -> None:
        mid = self._current_message_id()
        if mid:
            self.post_message(self.ToggleReadRequested(mid))

    def action_add_to_priority(self) -> None:
        mid = self._current_message_id()
        if mid:
            self.post_message(self.AddToPriorityRequested(mid))

    def action_add_to_priority_with_note(self) -> None:
        mid = self._current_message_id()
        if mid:
            self.post_message(self.AddToPriorityWithNoteRequested(mid))

    def action_archive(self) -> None:
        mid = self._current_message_id()
        if mid:
            self.post_message(self.ArchiveRequested(mid))

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
