from __future__ import annotations

from textual.binding import Binding
from textual.content import Content
from textual.message import Message
from textual.widgets import ListItem, ListView, Label

from ..ews_client import FolderSummary
from ..folder_tree import FolderPrefs, arrange, hidden_ids, hidden_itself, move, toggle_hidden


class FolderList(ListView):
    """Left pane: mail folders. j/k to move, Enter/l to open.

    Your arrangement (folder_tree.FolderPrefs) is applied on top of what
    Exchange returns: V picks up the folder under the cursor and j/k move it
    among its siblings (Enter/V puts it down, Esc puts it back); H hides or
    unhides a folder; . shows hidden folders (dimmed) so they can be unhidden.
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "cursor_first", "Top", show=False),
        Binding("G", "cursor_last", "Bottom", show=False),
        Binding("l", "select_cursor", "Open", show=False),
        Binding("o", "select_cursor", "Open", show=False),
        Binding("V", "toggle_move", "Move folder", show=False),
        Binding("escape", "cancel_move", "Cancel move", show=False),
        Binding("H", "toggle_hidden", "Hide / unhide folder", show=False),
        Binding("full_stop", "toggle_show_hidden", "Show hidden folders", show=False),
    ]

    class FolderSelected(Message):
        def __init__(self, folder: FolderSummary) -> None:
            self.folder = folder
            super().__init__()

    class PrefsChanged(Message):
        """The order or the hidden folders changed (to be saved)."""

        def __init__(self, prefs: FolderPrefs, notice: str = "") -> None:
            self.prefs = prefs
            self.notice = notice
            super().__init__()

    class MoveModeChanged(Message):
        """V picked a folder up / put it down (the status bar follows)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._all: list[FolderSummary] = []  # everything Exchange returned
        self._folders: list[FolderSummary] = []  # what's listed, in pane order
        self._hidden: set[str] = set()
        self.prefs = FolderPrefs()
        self.show_hidden = False
        self.moving: str | None = None  # id of the folder picked up with V
        self._order_before_move: list[str] = []

    def set_folders(self, folders: list[FolderSummary], prefs: FolderPrefs | None = None) -> None:
        self._all = list(folders)
        if prefs is not None:
            self.prefs = prefs
        self._rebuild(keep=self.highlighted_folder_id())

    def _rebuild(self, keep: str | None = None) -> None:
        tree = arrange(self._all, self.prefs.order)
        self._hidden = hidden_ids(self._all, self.prefs)
        self._folders = [f for f in tree if self.show_hidden or f.id not in self._hidden]
        items = list(self.query_children(ListItem))
        if len(items) == len(self._folders):
            # Same number of rows (a move, a refresh): relabel in place, so
            # the cursor highlight stays put and nothing flickers.
            for item, f in zip(items, self._folders):  # rows go by position: _folders[i] is row i
                item.query_one(Label).update(self._label(f))
            if keep is not None:
                self.highlight_folder(keep)
            return
        self.clear()
        for f in self._folders:
            self.append(ListItem(Label(self._label(f))))
        if keep is not None:
            self.highlight_folder(keep)
            # The new rows aren't mounted yet, so their highlight didn't
            # take: apply it again once they are.
            self.call_after_refresh(self._rehighlight, keep)

    def _rehighlight(self, folder_id: str) -> None:
        self.index = None
        self.highlight_folder(folder_id)

    def _label(self, f: FolderSummary) -> Content:
        indent = "  " * f.depth
        unread = f" ({f.unread_count})" if f.unread_count else ""
        if f.id == self.moving:
            return Content.from_markup("[b reverse]$t[/]", t=f"{indent}▸ {f.name}{unread}")
        if f.id in self._hidden:
            return Content.from_markup("[dim]$t (hidden)[/]", t=f"{indent}{f.name}")
        return Content(f"{indent}{f.name}{unread}")

    @property
    def hidden_folder_ids(self) -> set[str]:
        return set(self._hidden)

    @property
    def folders(self) -> list[FolderSummary]:
        """The listed folders, in pane order (read-only copy)."""
        return list(self._folders)

    def highlighted_folder_id(self) -> str | None:
        if self.index is None or not 0 <= self.index < len(self._folders):
            return None
        return self._folders[self.index].id

    def highlight_folder(self, folder_id: str) -> None:
        """Move the cursor to `folder_id` (no-op if it isn't listed)."""
        for i, f in enumerate(self._folders):
            if f.id == folder_id:
                self.index = i
                return

    def action_cursor_first(self) -> None:
        if self._folders and self.moving is None:
            self.index = 0

    def action_cursor_last(self) -> None:
        if self._folders and self.moving is None:
            self.index = len(self._folders) - 1

    # -- moving folders (V) ----------------------------------------------------------

    def action_cursor_down(self) -> None:
        if self.moving is not None:
            self._step(1)
        else:
            super().action_cursor_down()

    def action_cursor_up(self) -> None:
        if self.moving is not None:
            self._step(-1)
        else:
            super().action_cursor_up()

    def _step(self, step: int) -> None:
        skip = set() if self.show_hidden else self._hidden
        order = move(self._all, self.prefs.order, self.moving, step, skip)
        self.prefs = FolderPrefs(order=order, hidden=list(self.prefs.hidden), shown=list(self.prefs.shown))
        self._rebuild(keep=self.moving)

    def action_toggle_move(self) -> None:
        folder_id = self.highlighted_folder_id()
        if self.moving is not None:
            self._drop()
        elif folder_id is not None:
            self.moving = folder_id
            self._order_before_move = list(self.prefs.order)
            self._rebuild(keep=folder_id)
            self.post_message(self.MoveModeChanged())

    def action_select_cursor(self) -> None:
        if self.moving is not None:
            self._drop()  # Enter puts the folder down rather than opening it
        else:
            super().action_select_cursor()

    def _drop(self) -> None:
        moved, self.moving = self.moving, None
        self._rebuild(keep=moved)
        self.post_message(self.MoveModeChanged())
        if self.prefs.order != self._order_before_move:
            self.post_message(self.PrefsChanged(self.prefs))

    def action_cancel_move(self) -> None:
        if self.moving is None:
            return
        moved, self.moving = self.moving, None
        self.prefs = FolderPrefs(order=self._order_before_move, hidden=list(self.prefs.hidden), shown=list(self.prefs.shown))
        self._rebuild(keep=moved)
        self.post_message(self.MoveModeChanged())

    # -- hiding folders (H, .) --------------------------------------------------------

    def action_toggle_hidden(self) -> None:
        folder_id = self.highlighted_folder_id()
        if folder_id is None or self.moving is not None:
            return
        folder = next(f for f in self._folders if f.id == folder_id)
        if folder_id in self._hidden and not hidden_itself(folder, self.prefs):
            self.app.notify(f"{folder.name} is hidden because a folder it's in is — unhide that one")
            return
        was_hidden = folder_id in self._hidden
        self.prefs = toggle_hidden(folder, self.prefs)
        row = self.index or 0
        self._rebuild(keep=folder_id)
        if not was_hidden and not self.show_hidden and self._folders:
            self.index = min(row, len(self._folders) - 1)  # it's gone: stay about where it was
        notice = (
            f"Unhid {folder.name}" if was_hidden
            else f"Hid {folder.name}" + ("" if self.show_hidden else " — . shows hidden folders")
        )
        self.post_message(self.PrefsChanged(self.prefs, notice))

    def action_toggle_show_hidden(self) -> None:
        if self.moving is not None:
            return
        self.show_hidden = not self.show_hidden
        self._rebuild(keep=self.highlighted_folder_id())
        n = len(self._hidden)
        self.app.notify(f"Showing {n} hidden folder(s) — H unhides one" if self.show_hidden else "Hidden folders hidden again")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self.index is None:
            return
        folder = self._folders[self.index]
        self.post_message(self.FolderSelected(folder))
