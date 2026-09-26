"""A month calendar to pick a day with the keyboard, tuxedo-style:
h / l a day, j / k a week, [ / ] a month, t today, n the next marked date.
v starts selecting a range of days (the same keys extend it; v again, or
clear_range(), ends it). Marked dates (e.g. ones an email mentions) stand
out; today is underlined.
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
        Binding("v", "toggle_range", "Select days", show=False),
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
        """The day, or the selected range of days, changed."""

        def __init__(self, picker: DatePicker, value: date) -> None:
            self.picker = picker
            self.value = value
            self.first, self.last = picker.days
            super().__init__()

    def __init__(self, value: date | None = None, marked: list[date] | None = None, today: date | None = None,
                 anchor: date | None = None, **kwargs):
        super().__init__(**kwargs)
        self.today = today or date.today()
        self.marked = list(marked or [])
        self.anchor = anchor  # where v started a range of days, if selecting
        self.set_reactive(DatePicker.value, value or self.today)

    @property
    def days(self) -> tuple[date, date]:
        """(first, last) day chosen: the same day unless a range is selected."""
        if self.anchor is None:
            return self.value, self.value
        return min(self.anchor, self.value), max(self.anchor, self.value)

    def watch_value(self, value: date) -> None:
        self.refresh()
        self.post_message(self.Changed(self, value))

    def action_toggle_range(self) -> None:
        self.anchor = self.value if self.anchor is None else None
        self.refresh()
        self.post_message(self.Changed(self, self.value))

    def clear_range(self) -> bool:
        """End a range selection (back to one day). False if there was none."""
        if self.anchor is None:
            return False
        self.action_toggle_range()
        return True

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
        first, last = self.days
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
                in_range = self.anchor is not None and first <= day <= last
                if day == v:
                    style.append("reverse b")
                elif in_range:  # the selected days: a band in the accent colour
                    style = [f"b {c.get('tux-mode-fg', 'black')} on {c.get('tux-mode-bg', 'blue')}"]
                text = f"{day.day:>2}"
                cell = Content.from_markup(f"[{' '.join(style)}]$t[/]", t=text) if style else Content(text)
                # The gap between two selected days is filled too, so the range reads as one band.
                gap_on = in_range and i < 6 and day < last
                gap = Content.from_markup(f"[on {c.get('tux-mode-bg', 'blue')}] [/]") if gap_on else Content(" ")
                line += cell + (gap if i < 6 else Content(""))
            lines.append(line)
        return Content("\n").join(lines)
