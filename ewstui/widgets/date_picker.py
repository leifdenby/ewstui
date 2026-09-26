"""A month calendar to pick a day with the keyboard, tuxedo-style:
h / l a day, j / k a week, [ / ] a month, t today, n the next marked date.
Marked dates (e.g. ones an email mentions) stand out; today is underlined.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from textual.binding import Binding
from textual.content import Content
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget


def add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


class DatePicker(Widget, can_focus=True):
    BINDINGS = [
        Binding("h,left", "shift(-1)", "Previous day", show=False),
        Binding("l,right", "shift(1)", "Next day", show=False),
        Binding("k,up", "shift(-7)", "Previous week", show=False),
        Binding("j,down", "shift(7)", "Next week", show=False),
        Binding("left_square_bracket", "month(-1)", "Previous month", show=False),
        Binding("right_square_bracket", "month(1)", "Next month", show=False),
        Binding("t", "today", "Today", show=False),
        Binding("n", "next_marked", "Next date from the email", show=False),
    ]

    DEFAULT_CSS = """
    DatePicker {
        width: 22;
        height: 8;
    }
    DatePicker:focus {
        background: $boost;
    }
    """

    value: reactive[date] = reactive(date.today, layout=False)

    class Changed(Message):
        def __init__(self, picker: DatePicker, value: date) -> None:
            self.picker = picker
            self.value = value
            super().__init__()

    def __init__(self, value: date | None = None, marked: list[date] | None = None, today: date | None = None, **kwargs):
        super().__init__(**kwargs)
        self.today = today or date.today()
        self.marked = list(marked or [])
        self.set_reactive(DatePicker.value, value or self.today)

    def watch_value(self, value: date) -> None:
        self.refresh()
        self.post_message(self.Changed(self, value))

    def action_shift(self, days: int) -> None:
        self.value = self.value + timedelta(days=days)

    def action_month(self, months: int) -> None:
        self.value = add_months(self.value, months)

    def action_today(self) -> None:
        self.value = self.today

    def action_next_marked(self) -> None:
        """Cycle through the marked dates (in the order they were given)."""
        if not self.marked:
            return
        i = self.marked.index(self.value) + 1 if self.value in self.marked else 0
        self.value = self.marked[i % len(self.marked)]

    def render(self) -> Content:
        c = self.app.theme_variables
        v = self.value
        lines = [
            Content.from_markup(f"[b {c.get('accent', '')}]$t[/]", t=f"{v:%B %Y}".center(20)),
            Content.from_markup(f"[{c.get('tux-dim', '')}]$t[/]", t="Mo Tu We Th Fr Sa Su"),
        ]
        for week in calendar.Calendar().monthdatescalendar(v.year, v.month):
            line = Content("")
            for i, day in enumerate(week):
                style = []
                if day.month != v.month:
                    style.append(c.get("tux-dim", "dim"))
                elif day in self.marked:
                    style.append(f"b {c.get('tux-key', '')}")
                if day == self.today:
                    style.append("underline")
                if day == v:
                    style.append("reverse b")
                text = f"{day.day:>2}"
                cell = Content.from_markup(f"[{' '.join(style)}]$t[/]", t=text) if style else Content(text)
                line += cell + (Content(" ") if i < 6 else Content(""))
            lines.append(line)
        return Content("\n").join(lines)
