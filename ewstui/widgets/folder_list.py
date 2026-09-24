from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import ListItem, ListView, Label

from ..ews_client import FolderSummary


class FolderList(ListView):
    """Left pane: mail folders. j/k to move, Enter/l to open."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "cursor_first", "Top", show=False),
        Binding("G", "cursor_last", "Bottom", show=False),
        Binding("l", "select_cursor", "Open", show=False),
    ]

    class FolderSelected(Message):
        def __init__(self, folder: FolderSummary) -> None:
            self.folder = folder
            super().__init__()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._folders: list[FolderSummary] = []

    def set_folders(self, folders: list[FolderSummary]) -> None:
        self._folders = folders
        self.clear()
        for f in folders:
            indent = "  " * f.depth
            unread = f" ({f.unread_count})" if f.unread_count else ""
            self.append(ListItem(Label(f"{indent}{f.name}{unread}"), name=f.id))

    def highlight_folder(self, folder_id: str) -> None:
        """Move the cursor to `folder_id` (no-op if it isn't listed)."""
        for i, f in enumerate(self._folders):
            if f.id == folder_id:
                self.index = i
                return

    def action_cursor_first(self) -> None:
        if self._folders:
            self.index = 0

    def action_cursor_last(self) -> None:
        if self._folders:
            self.index = len(self._folders) - 1

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self.index is None:
            return
        folder = self._folders[self.index]
        self.post_message(self.FolderSelected(folder))
