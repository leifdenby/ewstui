"""
Links in an email (`U`): find them in the message text, pick one, open it
in the default browser.

Exchange's plain-text body keeps a link's target, usually as "text
<https://...>", so the text just before a link is shown alongside it to
tell "Unsubscribe" from "Join the meeting".
"""
from __future__ import annotations

import re
import webbrowser
from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView

# http(s)://..., www.... or mailto:... up to whitespace or a closing
# bracket/quote; trailing punctuation is trimmed afterwards.
_URL = re.compile(r"""(?:https?://|www\.|mailto:)[^\s<>"'`\[\]{}|\\^]+""", re.IGNORECASE)
_TRAILING = ".,;:!?*"
_SAFE_SCHEMES = ("http://", "https://", "mailto:")
CONTEXT_CHARS = 50


@dataclass(frozen=True)
class Link:
    url: str
    context: str  # the text just before the link on its line ("" if none)


def _trim(url: str) -> str:
    while url and url[-1] in _TRAILING:
        url = url[:-1]
    # A closing parenthesis belongs to the URL only if it opened one inside
    # it (e.g. Wikipedia links); otherwise it's the sentence's.
    while url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1]
        while url and url[-1] in _TRAILING:
            url = url[:-1]
    return url


def extract_links(text: str) -> list[Link]:
    """Links in order of first appearance, without duplicates."""
    links: list[Link] = []
    seen: set[str] = set()
    for line in text.splitlines():
        for m in _URL.finditer(line):
            url = _trim(m.group(0))
            if url.lower().startswith("www."):
                url = "https://" + url
            if not url.lower().startswith(_SAFE_SCHEMES) or len(url) <= len("https://"):
                continue
            key = url.lower()
            if key in seen:
                continue
            seen.add(key)
            context = line[: m.start()].rstrip(" <(:-–—\t")
            if len(context) > CONTEXT_CHARS:
                context = "…" + context[-CONTEXT_CHARS:].lstrip()
            links.append(Link(url=url, context=context.strip()))
    return links


def open_link(url: str) -> None:
    """Open in the default browser (or mail app for mailto:). Only web and
    mail links: never file:, javascript: or other schemes from an email."""
    if not url.lower().startswith(_SAFE_SCHEMES):
        raise ValueError(f"not opening {url!r}: only http(s) and mailto links")
    if not webbrowser.open_new_tab(url):
        raise RuntimeError("no browser available")


class _LinkList(ListView):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("o", "select_cursor", "Open", show=False),
    ]


class LinkPickerScreen(ModalScreen[str | None]):
    """Dismisses with the chosen URL, or None. j/k (or arrows) and Enter/o,
    or a digit 1-9 to pick that link directly; Esc cancels."""

    BINDINGS = [
        Binding("escape,q", "cancel", "Cancel"),
        *[Binding(str(n), f"pick({n})", show=False) for n in range(1, 10)],
    ]

    DEFAULT_CSS = """
    LinkPickerScreen {
        align: center middle;
    }
    #links-box {
        width: 90%;
        max-width: 140;
        height: auto;
        max-height: 80%;
        border: round $border;
        border-title-align: left;
        background: $panel;
        padding: 0 1;
    }
    #links-list {
        height: auto;
        max-height: 30;
    }
    #links-help {
        color: $tux-dim;
    }
    """

    def __init__(self, subject: str, links: list[Link]) -> None:
        super().__init__()
        self._subject = subject
        self._links = links

    def compose(self) -> ComposeResult:
        with Vertical(id="links-box"):
            with _LinkList(id="links-list"):
                for n, link in enumerate(self._links, 1):
                    yield ListItem(Label(self._label(n, link)))
            yield Label("j/k choose · Enter/o or 1-9 open in browser · Esc cancel", id="links-help", markup=False)

    def _label(self, n: int, link: Link) -> Text:
        v = self.app.get_css_variables()
        label = Text(f"{n:>2}  " if n <= 9 else "    ", style=f"bold {v['tux-key']}")
        if link.context:
            label.append(f"{link.context}  ", style=v["foreground"])
        label.append(link.url, style=f"underline {v['accent']}")
        return label

    def on_mount(self) -> None:
        box = self.query_one("#links-box")
        n = len(self._links)
        box.border_title = f" Links in “{self._subject}” — {n} link{'s' if n != 1 else ''} "
        links = self.query_one(_LinkList)
        links.index = 0
        links.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = self.query_one(_LinkList).index
        if index is not None:
            self.dismiss(self._links[index].url)

    def action_pick(self, n: int) -> None:
        if n <= len(self._links):
            self.dismiss(self._links[n - 1].url)

    def action_cancel(self) -> None:
        self.dismiss(None)
