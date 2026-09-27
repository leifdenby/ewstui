"""Your calendar as a month-like grid for glancing at while writing an
email: the working days (Mon–Fri) as columns, a few weeks as rows, and in
each day the time slots you have something ("09:00–09:15 Standup").
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from textual.binding import Binding
from textual.content import Content
from textual.message import Message
from textual.widget import Widget

WORKDAYS = 5


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def by_day(events: list, first: date, last: date) -> dict[date, list]:
    """Events per day in [first, last], earliest first; an all-day event
    spanning several days is listed on each (all-day ones before the rest)."""
    out: dict[date, list] = defaultdict(list)
    for e in events:
        start_day = e.start.date()
        end_day = (e.end - timedelta(microseconds=1)).date() if e.end > e.start else start_day
        day = max(start_day, first)
        while day <= min(end_day, last):
            out[day].append(e)
            day += timedelta(days=1)
    for day in out:
        out[day].sort(key=lambda e: (not e.is_all_day, e.start))
    return out


def slot_text(e, width: int) -> str:
    when = "all day" if e.is_all_day else f"{e.start:%H:%M}–{e.end:%H:%M}"
    text = f"{when} {e.subject}"
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


class WeekCalendar(Widget, can_focus=True):
    """With focus: a cursor day (h/l a working day, j/k a week; going past
    the weeks shown pages them); Space picks / unpicks the day, v selects
    a run of days; Enter asks for your free time on the chosen days (or the
    cursor day), Esc drops the choice, then gives focus back."""

    BINDINGS = [
        Binding("h,left", "move(-1)", "Previous day", show=False),
        Binding("l,right", "move(1)", "Next day", show=False),
        Binding("k,up", "move(-5)", "Previous week", show=False),
        Binding("j,down", "move(5)", "Next week", show=False),
        Binding("space", "toggle_day", "Pick day", show=False),
        Binding("v", "toggle_range", "Select days", show=False),
        Binding("enter", "availability", "Free times into the email", show=False),
        Binding("escape", "leave", "Back to the email", show=False),
    ]

    DEFAULT_CSS = """
    WeekCalendar {
        width: 1fr;
        height: 1fr;
    }
    """

    class AvailabilityRequested(Message):
        """Enter: your free time on these days, into the email."""

        def __init__(self, days: list[date]) -> None:
            self.days = days
            super().__init__()

    class WeeksChanged(Message):
        """The cursor paged the weeks shown: their events are needed."""

    class Left(Message):
        """Esc with nothing chosen: back to writing."""

    def __init__(self, weeks: int = 3, today: date | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.weeks = weeks
        self.today = today or date.today()
        # This week; at the weekend (this week's working days are over) the next.
        self.first = monday_of(self.today) + (timedelta(weeks=1) if self.today.weekday() >= WORKDAYS else timedelta())
        self.events: list | None = None  # None while being looked up
        self.cursor = self.today if self.today.weekday() < WORKDAYS else self.first
        self.picked: set[date] = set()  # days chosen with Space
        self.anchor: date | None = None  # where v started a run of days

    @property
    def last(self) -> date:
        return self.first + timedelta(weeks=self.weeks) - timedelta(days=1)

    def shift(self, weeks: int) -> None:
        self.first += timedelta(weeks=weeks)
        self.events = None
        if not self.first <= self.cursor <= self.last:  # (Ctrl+B/F) the cursor goes along
            self.cursor += timedelta(weeks=weeks)
        self.refresh()

    # -- choosing days --------------------------------------------------------------

    def chosen(self) -> list[date]:
        """The days chosen: picked ones and the v run (working days), in
        order; none chosen: the cursor day."""
        days = set(self.picked)
        if self.anchor is not None:
            lo, hi = sorted((self.anchor, self.cursor))
            days |= {lo + timedelta(days=i) for i in range((hi - lo).days + 1)}
        days = {d for d in days if d.weekday() < WORKDAYS}
        return sorted(days) or [self.cursor]

    def action_move(self, workdays: int) -> None:
        """Along the working days (Mon-Fri); a week is 5 of them."""
        day, step = self.cursor, (1 if workdays > 0 else -1)
        for _ in range(abs(workdays)):
            day += timedelta(days=step)
            while day.weekday() >= WORKDAYS:
                day += timedelta(days=step)
        self.cursor = day
        if day < self.first or day > self.last:  # page the weeks along
            self.first += timedelta(weeks=1 if day > self.last else -1)
            self.events = None
            self.post_message(self.WeeksChanged())
        self.refresh()

    def action_toggle_day(self) -> None:
        self.picked ^= {self.cursor}
        self.refresh()

    def action_toggle_range(self) -> None:
        if self.anchor is None:
            self.anchor = self.cursor
        else:  # the run stays chosen, as picked days
            self.picked |= set(self.chosen())
            self.anchor = None
        self.refresh()

    def action_availability(self) -> None:
        self.post_message(self.AvailabilityRequested(self.chosen()))
        self.picked, self.anchor = set(), None
        self.refresh()

    def action_leave(self) -> None:
        if self.picked or self.anchor is not None:  # Esc first drops the choice
            self.picked, self.anchor = set(), None
            self.refresh()
            return
        self.post_message(self.Left())

    def set_events(self, first: date, events: list) -> None:
        if first == self.first:  # not an answer for weeks since moved away from
            self.events = events
            self.refresh()

    def on_focus(self) -> None:
        self.refresh()  # the cursor shows only with focus

    def on_blur(self) -> None:
        self.refresh()

    def render(self) -> Content:
        c = self.app.theme_variables
        dim, accent, key = c.get("tux-dim", "grey50"), c.get("accent", "blue"), c.get("tux-key", "yellow")
        col = max(8, (self.size.width - (WORKDAYS - 1)) // WORKDAYS)
        gap = Content(" ")
        lines = [Content.from_markup(f"[b {accent}]$t[/]", t="Your calendar")]  # its keys are in the hint bar
        days = by_day(self.events or [], self.first, self.last)
        now = datetime.now()
        for week in range(self.weeks):
            monday = self.first + timedelta(weeks=week)
            week_days = [monday + timedelta(days=i) for i in range(WORKDAYS)]
            lines.append(Content.from_markup(f"[{dim}]$r[/]", r="─" * (col * WORKDAYS + WORKDAYS - 1)))
            header = Content("")
            chosen = set(self.chosen()) if (self.picked or self.anchor is not None) else set()
            for i, day in enumerate(week_days):
                mark = "✓ " if day in chosen else ""
                label = f"{mark}{day:%a %d %b}".ljust(col)[:col]
                style = f"b reverse {accent}" if day == self.today else f"b {dim}" if day < self.today else "b"
                if day in chosen:
                    style = f"b {c.get('tux-mode-fg', 'black')} on {c.get('tux-mode-bg', 'blue')}"
                if self.has_focus and day == self.cursor:
                    style += " underline reverse"
                header += Content.from_markup(f"[{style}]$l[/]", l=label) + (gap if i < WORKDAYS - 1 else Content(""))
            lines.append(header)
            if self.events is None:
                lines.append(Content.from_markup(f"[{dim}]$t[/]", t="checking your calendar…"))
                continue
            rows = max([len(days.get(d, [])) for d in week_days] + [1])
            for row in range(rows):
                line = Content("")
                for i, day in enumerate(week_days):
                    todays = days.get(day, [])
                    if row < len(todays):
                        e = todays[row]
                        past = (e.end if not e.is_all_day else datetime.combine(day, datetime.max.time())) < now
                        text = slot_text(e, col).ljust(col)
                        color = dim if past else (key if e.is_all_day else "")
                        cell = Content.from_markup(f"[{color}]$t[/]", t=text) if color else Content(text)
                    elif row == 0:
                        cell = Content.from_markup(f"[{dim}]$t[/]", t="free".ljust(col))
                    else:
                        cell = Content(" " * col)
                    line += cell + (gap if i < WORKDAYS - 1 else Content(""))
                lines.append(line)
        return Content("\n").join(lines)
