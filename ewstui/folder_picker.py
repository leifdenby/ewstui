"""
"Move to folder" picker (`m` in the message list): recently used folders
first, then the rest in folder-pane order; typing narrows and ranks the
list with an fzf-style fuzzy match on each folder's full path.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView

from .ews_client import FolderSummary

SEPARATORS = "/ -_."
WORD_START_BONUS = 8
CONSECUTIVE_BONUS = 5
MAX_GAP_PENALTY = 5


def fuzzy_score(query: str, text: str) -> int | None:
    """Score how well `query`'s characters appear, in order, in `text`
    (case-insensitive; spaces in the query are ignored). Higher is better;
    None if they don't all appear. Matches at the start of a word or path
    segment and runs of consecutive characters score higher, gaps lower,
    so "arc" ranks "Archive" above "Search Folders".
    """
    q = "".join(query.split()).casefold()
    if not q:
        return 0
    t = text.casefold()
    # best[j]: best score for the query so far with its last char at t[j].
    best: dict[int, int] = {}
    for i, ch in enumerate(q):
        row: dict[int, int] = {}
        for j, tc in enumerate(t):
            if tc != ch:
                continue
            gain = 1 + (WORD_START_BONUS if j == 0 or t[j - 1] in SEPARATORS else 0)
            if i == 0:
                row[j] = gain
                continue
            candidates = [
                score + gain + (CONSECUTIVE_BONUS if pj == j - 1 else -min(j - pj - 1, MAX_GAP_PENALTY))
                for pj, score in best.items()
                if pj < j
            ]
            if candidates:
                row[j] = max(candidates)
        if not row:
            return None
        best = row
    return max(best.values()) - len(t) // 8  # slight preference for shorter names


def folder_paths(folders: list[FolderSummary]) -> dict[str, str]:
    """id -> 'Parent/Child' from the pane's preorder list. A lone root
    (exchangelib's "Top of Information Store", with every other folder
    below it) is left out: it isn't a place to file mail.
    """
    skip_root = len(folders) > 1 and folders[0].depth == 0 and all(f.depth > 0 for f in folders[1:])
    paths: dict[str, str] = {}
    stack: list[str] = []
    for i, f in enumerate(folders):
        del stack[f.depth:]
        stack.append(f.name)
        if skip_root and i == 0:
            continue
        paths[f.id] = "/".join(stack[1:] if skip_root else stack)
    return paths


def rank(
    folders: list[FolderSummary], query: str, recent_ids: list[str], exclude_id: str | None
) -> list[tuple[FolderSummary, str]]:
    """(folder, path) in display order. Empty query: recent folders (most
    recent first), then pane order. Otherwise best fuzzy match first, recent
    folders winning ties. `exclude_id` (the current folder) is left out.
    """
    paths = folder_paths(folders)
    order = {f.id: i for i, f in enumerate(folders)}
    recent = {fid: i for i, fid in enumerate(recent_ids)}
    candidates = [f for f in folders if f.id != exclude_id and f.id in paths]
    if not query.strip():
        candidates.sort(key=lambda f: (recent.get(f.id, len(recent)), order[f.id]))
        return [(f, paths[f.id]) for f in candidates]
    scored = []
    for f in candidates:
        score = fuzzy_score(query, paths[f.id])
        if score is not None:
            scored.append((score, f))
    scored.sort(key=lambda sf: (-sf[0], recent.get(sf[1].id, len(recent)), order[sf[1].id]))
    return [(f, paths[f.id]) for _, f in scored]


class MoveToFolderScreen(ModalScreen[str | None]):
    """Dismisses with the chosen folder id, or None.

    Letters go to the search box, so the list moves with ↓/↑ or
    Ctrl+n/Ctrl+p (Ctrl+k is the input's delete-to-end-of-line).
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        # (The app disables Textual's command palette, which would otherwise
        # take Ctrl+p before this screen sees it.)
        Binding("down,ctrl+n", "cursor(1)", "Next", show=False),
        Binding("up,ctrl+p", "cursor(-1)", "Previous", show=False),
    ]

    DEFAULT_CSS = """
    MoveToFolderScreen {
        align: center middle;
    }
    #move-box {
        width: 70%;
        height: auto;
        max-height: 80%;
        border: round $accent;
        padding: 0 1;
        background: $surface;
    }
    #move-results {
        height: auto;
        max-height: 20;
    }
    #move-help {
        color: $text-muted;
    }
    """

    def __init__(
        self, subject: str, folders: list[FolderSummary], recent_ids: list[str], current_id: str | None
    ) -> None:
        super().__init__()
        self._subject = subject
        self._folders = folders
        self._recent = set(recent_ids)
        self._recent_ids = recent_ids
        self._current = current_id
        self._shown: list[tuple[FolderSummary, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="move-box"):
            yield Label(f"Move “{self._subject}” to…", markup=False)
            yield Input(placeholder="Type to filter folders", id="move-query")
            yield ListView(id="move-results")
            yield Label("↓/↑ or Ctrl+n/p choose · Enter move · Esc cancel", id="move-help", markup=False)

    def on_mount(self) -> None:
        self._refresh_list("")
        self.query_one("#move-query", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_list(event.value)

    def _refresh_list(self, query: str) -> None:
        self._shown = rank(self._folders, query, self._recent_ids, self._current)
        results = self.query_one("#move-results", ListView)
        results.clear()
        for folder, path in self._shown:
            label = Text(path)
            if folder.unread_count:
                label.append(f"  ({folder.unread_count})", style="dim")
            if folder.id in self._recent and not query.strip():
                label.append("  recent", style="italic dim")
            results.append(ListItem(Label(label)))
        if self._shown:
            results.index = 0

    def action_cursor(self, step: int) -> None:
        results = self.query_one("#move-results", ListView)
        if not self._shown:
            return
        index = 0 if results.index is None else results.index + step
        results.index = max(0, min(index, len(self._shown) - 1))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._choose()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._choose()

    def _choose(self) -> None:
        index = self.query_one("#move-results", ListView).index
        if not self._shown or index is None:
            self.app.bell()
            return
        self.dismiss(self._shown[index][0].id)

    def action_cancel(self) -> None:
        self.dismiss(None)
