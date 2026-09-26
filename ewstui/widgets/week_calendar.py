"""Your calendar as a month-like grid for glancing at while writing an
email: the working days (Mon–Fri) as columns, a few weeks as rows, and in
each day the time slots you have something ("09:00–09:15 Standup").
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from textual.content import Content
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


class WeekCalendar(Widget):
    DEFAULT_CSS = """
    WeekCalendar {
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(self, weeks: int = 3, today: date | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.weeks = weeks
        self.today = today or date.today()
        # This week; at the weekend (this week's working days are over) the next.
        self.first = monday_of(self.today) + (timedelta(weeks=1) if self.today.weekday() >= WORKDAYS else timedelta())
        self.events: list | None = None  # None while being looked up

    @property
    def last(self) -> date:
        return self.first + timedelta(weeks=self.weeks) - timedelta(days=1)

    def shift(self, weeks: int) -> None:
        self.first += timedelta(weeks=weeks)
        self.events = None
        self.refresh()

    def set_events(self, first: date, events: list) -> None:
        if first == self.first:  # not an answer for weeks since moved away from
            self.events = events
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
            for i, day in enumerate(week_days):
                label = f"{day:%a %d %b}".ljust(col)[:col]
                style = f"b reverse {accent}" if day == self.today else f"b {dim}" if day < self.today else "b"
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
