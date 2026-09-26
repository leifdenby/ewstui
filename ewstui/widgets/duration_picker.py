"""A row of durations to pick how long something will take (dur: in the
priority note): h/l (or j/k) move, 1-7 pick one straight away; the value
is e.g. "30m", "2h", "1d".
"""
from __future__ import annotations

from textual.binding import Binding
from textual.content import Content
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget

DURATIONS = ["5m", "15m", "30m", "1h", "2h", "4h", "1d"]


class DurationPicker(Widget, can_focus=True):
    BINDINGS = [
        Binding("h,left,k,up", "shift(-1)", "Shorter", show=False),
        Binding("l,right,j,down", "shift(1)", "Longer", show=False),
        *[Binding(str(i + 1), f"pick({i})", show=False) for i in range(len(DURATIONS))],
    ]

    DEFAULT_CSS = """
    DurationPicker {
        height: 1;
        width: auto;
    }
    """

    index: reactive[int] = reactive(2)  # 30m to start with

    class Picked(Message):
        """1-7: a duration chosen straight away."""

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    @property
    def value(self) -> str:
        return DURATIONS[self.index]

    def action_shift(self, step: int) -> None:
        self.index = max(0, min(len(DURATIONS) - 1, self.index + step))

    def action_pick(self, index: int) -> None:
        self.index = index
        self.post_message(self.Picked(self.value))

    def render(self) -> Content:
        c = self.app.theme_variables
        parts = []
        for i, text in enumerate(DURATIONS):
            key = Content.from_markup(f"[{c.get('tux-dim', 'grey50')}]$k[/]", k=f"{i + 1} ")
            label = Content.from_markup("[b reverse] $t [/]", t=text) if i == self.index else Content(f" {text} ")
            parts.append(key + label)
        return Content("  ").join(parts)
