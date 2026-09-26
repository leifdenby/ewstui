"""
Header and status bar, after tuxedo's:

    ▶▮◀ ewstui  lcd@dmi.dk  •  Inbox  •  42 messages  •  3 unread       Thu 25 Sep 14:05
    ...
     MAIL   l open · r reply · d delete · m move · ? help        42 messages · v0.1.0

Colours come from the active theme (see ewstui/theme.py).
"""
from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from .. import __version__


def _vars(widget: Widget) -> dict[str, str]:
    return widget.app.get_css_variables()


def _line(left: Text, right: Text, width: int) -> Text:
    """left, then right flush against the right edge (right is dropped
    first when there isn't room for both)."""
    gap = width - left.cell_len - right.cell_len
    if gap < 1:
        left.truncate(width, overflow="ellipsis")
        return left
    return Text.assemble(left, " " * gap, right)


class TopBar(Widget):
    """Logo, account and counts for what's on screen, clock on the right."""

    DEFAULT_CSS = """
    TopBar {
        height: 1;
        background: $panel;
        padding: 0 1;
    }
    """

    account: reactive[str] = reactive("")
    items: reactive[tuple] = reactive(())  # (text, role) pairs after the account
    now: reactive[datetime] = reactive(datetime.now)

    def on_mount(self) -> None:
        self.set_interval(30, self._tick)

    def _tick(self) -> None:
        self.now = datetime.now()

    def render(self) -> Text:
        v = _vars(self)
        left = Text.assemble(
            ("▶", f"bold {v['accent']}"),
            ("▮", f"bold {v['foreground']}"),
            ("◀", f"bold {v['accent']}"),
            (" ewstui", f"bold {v['foreground']}"),
        )
        if self.account:
            left.append(f"  {self.account}", style=v["tux-dim"])
        for text, role in self.items:
            left.append("  •  ", style=v["tux-dim"])
            left.append(text, style={"accent": v["accent"], "dim": v["tux-dim"]}.get(role, v["foreground"]))
        right = Text(self.now.strftime("%a %d %b %H:%M"), style=v["tux-dim"])
        return _line(left, right, self.size.width)


class HintBar(Widget):
    """A status-bar lookalike (mode chip and key hints) for full-window
    popups such as the compose view. Not a StatusBar, so the app never
    mistakes it for the main one."""

    DEFAULT_CSS = """
    HintBar {
        dock: bottom;
        height: 1;
        background: $tux-statusbar;
    }
    """

    def __init__(self, mode: str, hints: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.mode = mode
        self.hints = hints

    def set_hints(self, hints: str) -> None:
        self.hints = hints
        self.refresh()

    def render(self) -> Text:
        v = _vars(self)
        left = Text.assemble(
            (f" {self.mode} ", f"bold {v['tux-mode-fg']} on {v['tux-mode-bg']}"),
            (f"  {self.hints}", v["tux-status-fg"]),
        )
        return _line(left, Text(), self.size.width)


class StatusBar(Widget):
    """Mode chip, hints for the focused pane, counts and version."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $tux-statusbar;
    }
    """

    mode: reactive[str] = reactive("MAIL")
    hints: reactive[str] = reactive("")
    info: reactive[str] = reactive("")
    # Connection indicator: (text, role), role "ok" / "busy" / "slow" /
    # "error"; ("", "") hides it (demo mode).
    conn: reactive[tuple] = reactive(("", ""))
    prefetching: reactive[int] = reactive(0)  # background downloads in progress

    def render(self) -> Text:
        v = _vars(self)
        left = Text.assemble(
            (f" {self.mode} ", f"bold {v['tux-mode-fg']} on {v['tux-mode-bg']}"),
            (f"  {self.hints}", v["tux-status-fg"]),
        )
        right = Text()
        if self.prefetching:
            right.append(f"⇣ prefetching {self.prefetching}", style=v["tux-dim"])
            right.append(" · ", style=v["tux-dim"])
        text, role = self.conn
        if text:
            colour = {"ok": v["success"], "busy": v["accent"], "slow": v["warning"], "error": v["error"]}.get(
                role, v["tux-dim"]
            )
            right.append(text, style=f"bold {colour}" if role in ("slow", "error") else colour)
            right.append(" · ", style=v["tux-dim"])
        right.append(" · ".join(p for p in (self.info, f"v{__version__}") if p) + " ", style=v["tux-dim"])
        return _line(left, right, self.size.width)
