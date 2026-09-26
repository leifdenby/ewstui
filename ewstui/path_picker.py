"""Pick a folder on disk with a fuzzy search, starting in your home folder
— where to save an attachment (`s` in the attachments list).

The folders under ~ are listed a few levels deep (in a thread, so a big
home folder never freezes the UI), leaving out hidden ones and the usual
clutter (Library, node_modules, virtualenvs, caches). Typing ranks them
fzf-style on their ~/path — by `fzf --filter` itself when fzf is
installed (much faster on a big list, so the listing goes deeper then),
otherwise by folder_picker.fuzzy_score. A path you type yourself (~/…,
/…) that exists is offered first. Folders used before, and the usual
attachment folder, come first while nothing is typed.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView

from .folder_picker import fuzzy_score

log = logging.getLogger(__name__)

SKIP = {"Library", "node_modules", "__pycache__", "venv", "site-packages", "Applications", "build", "dist", "target"}
MAX_DEPTH = 4
MAX_DIRS = 20000
# With fzf doing the matching, a much bigger list is still instant.
FZF_MAX_DEPTH = 6
FZF_MAX_DIRS = 100000
SHOWN = 200


def fzf_available() -> bool:
    return shutil.which("fzf") is not None


def fzf_filter(lines: list[str], query: str) -> list[str] | None:
    """`lines` matching `query`, best first, as fzf ranks them (fzf --filter:
    no UI, just the matches). None if fzf isn't there or fails."""
    fzf = shutil.which("fzf")
    if fzf is None:
        return None
    try:
        done = subprocess.run([fzf, "--filter", query], input="\n".join(lines), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        log.info("fzf failed", exc_info=True)
        return None
    if done.returncode == 1:  # ran fine, nothing matched
        return []
    if done.returncode != 0:
        log.info("fzf exited %s: %s", done.returncode, done.stderr.strip())
        return None
    return [line for line in done.stdout.splitlines() if line]


def list_dirs(root: Path, max_depth: int = MAX_DEPTH, limit: int = MAX_DIRS) -> list[Path]:
    """Folders under `root` (not itself), breadth first: shallow ones first."""
    out: list[Path] = []
    level = [root]
    for _ in range(max_depth):
        below: list[Path] = []
        for folder in level:
            try:
                entries = sorted(os.scandir(folder), key=lambda e: e.name.casefold())
            except OSError:
                continue
            for entry in entries:
                if entry.name.startswith(".") or entry.name in SKIP:
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                path = Path(entry.path)
                out.append(path)
                below.append(path)
                if len(out) >= limit:
                    return out
        level = below
    return out


def display(path: Path, root: Path) -> str:
    """~/Documents/Work for paths under the home folder."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return str(path)
    return "~" if str(rel) == "." else f"~/{rel}"


def typed_path(query: str, root: Path) -> Path | None:
    """A folder typed out in full (~/..., /...) that exists."""
    q = query.strip()
    if q == "~" or q.startswith("~/"):  # (not "~name": that'd be another user's home — "~N" is just typing)
        path = root / q[2:]
    elif q.startswith("/"):
        path = Path(q)
    else:
        return None
    try:
        return path if path.is_dir() else None
    except OSError:  # e.g. a name too long
        return None


def rank(dirs: list[Path], query: str, root: Path, first: list[Path], use_fzf: bool = False) -> list[Path]:
    """Display order: `first` (recent / usual folders) and ~ itself, then
    the rest; with a query, best fuzzy match first (a typed existing path
    at the very top). `use_fzf`: let fzf rank (falls back to our own
    matching if it can't)."""
    if not query.strip():
        seen, out = set(), []
        for path in [*first, root, *dirs]:
            if path not in seen:
                seen.add(path)
                out.append(path)
        return out[:SHOWN]
    candidates = [root, *dirs]
    ranked = None
    if use_fzf:
        by_text = {display(p, root): p for p in candidates}
        matches = fzf_filter(list(by_text), query)
        if matches is not None:
            ranked = [by_text[m] for m in matches if m in by_text]
    if ranked is None:
        boost = {p: i for i, p in enumerate(first)}
        scored = []
        for i, path in enumerate(candidates):
            score = fuzzy_score(query, display(path, root))
            if score is not None:
                scored.append((-score, boost.get(path, len(boost)), i, path))
        ranked = [path for *_, path in sorted(scored)]
    out = ranked
    exact = typed_path(query, root)
    if exact is not None:
        out = [exact, *[p for p in out if p != exact]]
    return out[:SHOWN]


class PathPickerScreen(ModalScreen[Path | None]):
    """Dismisses with the chosen folder, or None. Letters go to the search
    box; ↓/↑ or Ctrl+n/p move in the list, Enter chooses."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("down,ctrl+n", "cursor(1)", "Next", show=False),
        Binding("up,ctrl+p", "cursor(-1)", "Previous", show=False),
    ]

    DEFAULT_CSS = """
    PathPickerScreen {
        align: center middle;
    }
    #path-box {
        width: 70%;
        height: auto;
        max-height: 80%;
        border: round $accent;
        padding: 0 1;
        background: $surface;
    }
    #path-results {
        height: auto;
        max-height: 20;
    }
    #path-help {
        color: $text-muted;
    }
    """

    def __init__(self, title: str, root: Path | None = None, first: list[Path] | None = None, loader=list_dirs,
                 use_fzf: bool | None = None) -> None:
        super().__init__()
        self._title = title
        self.root = root or Path.home()
        self._first = [p for p in (first or []) if p.is_dir()]
        self._loader = loader
        self.use_fzf = fzf_available() if use_fzf is None else use_fzf
        self._dirs: list[Path] = []
        self._shown: list[Path] = []
        self._shown_for: str | None = None  # the text the shown list was ranked for

    def compose(self) -> ComposeResult:
        with Vertical(id="path-box"):
            yield Label(self._title, markup=False)
            yield Input(placeholder="Type to find a folder under ~ (or type a path)", id="path-query")
            yield ListView(id="path-results")
            yield Label("↓/↑ or Ctrl+n/p choose · Enter save here · Esc cancel"
                    + (" · matching by fzf" if self.use_fzf else ""), id="path-help", markup=False)

    def on_mount(self) -> None:
        self._show(self._query(), rank(self._dirs, "", self.root, self._first))
        self.query_one("#path-query", Input).focus()

        def load() -> None:
            if self.use_fzf:
                dirs = self._loader(self.root, FZF_MAX_DEPTH, FZF_MAX_DIRS)
            else:
                dirs = self._loader(self.root)
            self.app.call_from_thread(self._loaded, dirs)

        self.run_worker(load, thread=True, exclusive=True, group="path-picker")

    def _query(self) -> str:
        return self.query_one("#path-query", Input).value

    def _loaded(self, dirs: list[Path]) -> None:
        self._dirs = dirs
        self._refresh_list(self._query())

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_list(event.value)

    def _refresh_list(self, query: str) -> None:
        if not (self.use_fzf and query.strip()):
            self._show(query, rank(self._dirs, query, self.root, self._first))
            return
        dirs, first = list(self._dirs), list(self._first)

        def ranked_by_fzf() -> None:  # a process per keystroke: off the UI thread
            try:
                shown = rank(dirs, query, self.root, first, use_fzf=True)
            except Exception:  # noqa: BLE001 - never take the app down over a ranking
                log.warning("ranking folders with fzf failed", exc_info=True)
                shown = rank(dirs, query, self.root, first)
            self.app.call_from_thread(self._show, query, shown)

        self.run_worker(ranked_by_fzf, thread=True, exclusive=True, group="path-rank")

    def _show(self, query: str, shown: list[Path]) -> None:
        if query != self._query():
            return  # typed on since: a newer ranking is on its way
        self._shown = shown
        self._shown_for = query
        results = self.query_one("#path-results", ListView)
        results.clear()
        for path in self._shown:
            label = Text(display(path, self.root))
            if path in self._first and not query.strip():
                label.append("  recent", style="italic dim")
            results.append(ListItem(Label(label)))
        if self._shown:
            results.index = 0

    def action_cursor(self, step: int) -> None:
        results = self.query_one("#path-results", ListView)
        if not self._shown:
            return
        index = 0 if results.index is None else results.index + step
        results.index = max(0, min(index, len(self._shown) - 1))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._choose()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._choose()

    def _choose(self) -> None:
        if self._shown_for != self._query():  # Enter before fzf answered: rank now
            self._show(self._query(), rank(self._dirs, self._query(), self.root, self._first, use_fzf=self.use_fzf))
        index = self.query_one("#path-results", ListView).index
        if not self._shown or index is None:
            self.app.bell()
            return
        self.dismiss(self._shown[index])

    def action_cancel(self) -> None:
        self.dismiss(None)
