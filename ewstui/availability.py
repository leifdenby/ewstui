"""Your free time on some days, as text to paste into an email (v on days
in the calendar beside the compose view, then Enter):

    I am currently available these times (CEST):
    - Monday 5th Oct, 1000-1200
    - Tuesday 6th Oct, 1200-1300

Free = inside the working day, in whole hours (at least one), not in the
past, and not overlapping an event shown as Busy, Tentative or Out of
office (Exchange's "show as"): an all-day one of those (a holiday) takes
the whole day; Free / Working elsewhere events (a birthday, "working from
home") don't count.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .ews_client import DEFAULT_WORK_HOURS

HOUR = timedelta(hours=1)
BLOCKING = {"Busy", "Tentative", "OOF"}  # show-as states you're not available in


def blocks(event) -> bool:
    return getattr(event, "show_as", "Busy") in BLOCKING


def _ceil_hour(when: datetime) -> datetime:
    floor = when.replace(minute=0, second=0, microsecond=0)
    return floor if floor == when else floor + HOUR


def _floor_hour(when: datetime) -> datetime:
    return when.replace(minute=0, second=0, microsecond=0)


def free_slots(events: list, day: date, hours: tuple[time, time] = DEFAULT_WORK_HOURS,
               now: datetime | None = None) -> list[tuple[datetime, datetime]]:
    """The free whole-hour stretches of `day` within `hours`."""
    start = datetime.combine(day, hours[0])
    end = datetime.combine(day, hours[1])
    if now is not None and now > start:
        start = now
    day_start, day_end = datetime.combine(day, time.min), datetime.combine(day, time.min) + timedelta(days=1)
    if any(e.is_all_day and blocks(e) and e.start < day_end and e.end > day_start for e in events):
        return []  # a holiday / out of office all day
    busy = sorted(
        (max(e.start, start), min(e.end, end))
        for e in events
        if not e.is_all_day and blocks(e) and e.start < end and e.end > start
    )
    out, cursor = [], start
    for b_start, b_end in [*busy, (end, end)]:
        s, f = _ceil_hour(cursor), _floor_hour(b_start)
        if f - s >= HOUR:
            out.append((s, f))
        cursor = max(cursor, b_end)
    return out


def ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def zone_name(when: datetime) -> str:
    """This machine's time zone name at that (local) time: CEST in summer,
    CET in winter — for the date offered, not today."""
    return when.astimezone().tzname() or ""


def availability_text(slots_by_day: dict[date, list[tuple[datetime, datetime]]], tz_name: str | None = None) -> str:
    """The text to paste: one line per free stretch, day by day. The time
    zone (`tz_name`, else the local one on each slot's own date) goes in
    the heading, or on every line if the slots straddle the switch between
    summer and winter time."""
    slots = [(day, s, f) for day in sorted(slots_by_day) for s, f in slots_by_day[day]]
    if not slots:
        return "I don't have any free time on those days, unfortunately.\n"
    zones = [tz_name if tz_name is not None else zone_name(s) for _, s, _ in slots]
    one_zone = len(set(zones)) == 1
    lines = [
        f"- {day:%A} {ordinal(day.day)} {day:%b}, {s:%H%M}-{f:%H%M}" + ("" if one_zone or not zone else f" ({zone})")
        for (day, s, f), zone in zip(slots, zones)
    ]
    heading = "I am currently available these times"
    if one_zone and zones[0]:
        heading += f" ({zones[0]})"
    return "\n".join([heading + ":", *lines]) + "\n"


def availability_on(events: list, days: list[date], hours: tuple[time, time] = DEFAULT_WORK_HOURS,
                    now: datetime | None = None, workdays_only: bool = True, tz_name: str | None = None) -> str:
    """The text for these days (working days only, by default; past days
    and days with nothing free left out)."""
    now = now or datetime.now()
    slots = {}
    for day in sorted(set(days)):
        if not (workdays_only and day.weekday() >= 5) and day >= now.date():
            found = free_slots(events, day, hours, now)
            if found:
                slots[day] = found
    return availability_text(slots, tz_name)


def availability(events: list, first: date, last: date, hours: tuple[time, time] = DEFAULT_WORK_HOURS,
                 now: datetime | None = None, workdays_only: bool = True, tz_name: str | None = None) -> str:
    """The text for the days first..last."""
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    return availability_on(events, days, hours, now, workdays_only, tz_name)
