"""The search bar above the message list (`/`): what you type narrows the
list as you go. Enter or ↓ goes into the results (the search stays), Esc
ends it. The app does the searching (see EwstuiApp's search section).
"""
from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import Input


class SearchBar(Input):
    BINDINGS = [
        Binding("escape", "close", "End search", show=False),
        Binding("down", "to_results", "To the results", show=False),
    ]

    DEFAULT_CSS = """
    SearchBar {
        border: round $accent;
        border-title-color: $accent;
        border-subtitle-color: $tux-dim;
        margin: 0;
        height: 3;
    }
    """

    class Closed(Message):
        pass

    class ToResults(Message):
        def __init__(self, open: bool) -> None:
            self.open = open  # Enter (open the best match) rather than ↓ (go choose)
            super().__init__()

    def __init__(self, scope: str, **kwargs) -> None:
        # "folder": this folder's emails; "folders": find a folder by name.
        placeholder = (
            "part of a folder's name or path (fuzzy)" if scope == "folders"
            else "words from the subject or sender (fuzzy); Exchange also searches the text"
        )
        super().__init__(placeholder=placeholder, id="search-bar", **kwargs)
        self.scope = scope
        self.border_title = "find a folder" if scope == "folders" else "search this folder"
        self.border_subtitle = "Enter open · ↓ choose · Esc" if scope == "folders" else "↓ results · Esc end"

    def action_close(self) -> None:
        self.post_message(self.Closed())

    def action_to_results(self) -> None:
        self.post_message(self.ToResults(open=False))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.post_message(self.ToResults(open=True))
