"""
`ewstui calendar`: list your calendar events over a date range from the
command line, as text or JSON (e.g. to look back over a year's trips).

Works on the CalendarClient interface only (list_events), so the demo
backend stands in for tests.
"""
from __future__ import annotations

from datetime import date, datetime, time

from .ews_client import EventSummary


def split_into_months(start: date, end: date) -> list[tuple[datetime, datetime]]:
    """Split [start, end) into calendar-month chunks.

    Exchange caps how many items one calendar view returns, so long ranges
    are fetched a month at a time.

    Parameters
    ----------
    start : date
        First day of the range.
    end : date
        Day after the last day of the range.

    Returns
    -------
    list[tuple[datetime, datetime]]
        Consecutive ``(chunk_start, chunk_end)`` pairs, local midnight to
        local midnight, covering the range exactly.
    """
    chunks = []
    cursor = start
    while cursor < end:
        next_month = date(cursor.year + cursor.month // 12, cursor.month % 12 + 1, 1)
        chunk_end = min(next_month, end)
        chunks.append((datetime.combine(cursor, time.min), datetime.combine(chunk_end, time.min)))
        cursor = chunk_end
    return chunks


def fetch_events(client, start: date, end: date) -> list[EventSummary]:
    """Fetch every event overlapping [start, end), month by month.

    Parameters
    ----------
    client : CalendarClient or DemoCalendarClient
        The calendar.
    start : date
        First day of the range.
    end : date
        Day after the last day of the range.

    Returns
    -------
    list[EventSummary]
        The events sorted by start, each once even if it spans several
        months.
    """
    seen = {}
    for chunk_start, chunk_end in split_into_months(start, end):
        for event in client.list_events(chunk_start, chunk_end):
            seen.setdefault((event.id, event.start), event)
    return sorted(seen.values(), key=_sort_key)


def _sort_key(event: EventSummary) -> float:
    """Return the event's start as a timestamp, for sorting.

    Parameters
    ----------
    event : EventSummary
        The event.

    Returns
    -------
    float
        Seconds since the epoch; naive times are taken as local.
    """
    return event.start.timestamp()


def _format_time(when: datetime) -> str:
    """Format a time in local time, minutes resolution.

    Parameters
    ----------
    when : datetime
        The time (tz-aware from Exchange, naive in the demo).

    Returns
    -------
    str
        ``YYYY-MM-DD HH:MM``.
    """
    if when.tzinfo is not None:
        when = when.astimezone()
    return when.strftime("%Y-%m-%d %H:%M")


def build_event_record(event: EventSummary) -> dict:
    """Build the ``--json`` record of one event.

    Parameters
    ----------
    event : EventSummary
        The event.

    Returns
    -------
    dict
        Subject, start/end (local, ISO 8601), all-day flag, location,
        organizer and Exchange's "show as" status.
    """
    start, end = event.start, event.end
    if start.tzinfo is not None:
        start, end = start.astimezone(), end.astimezone()
    return {
        "subject": event.subject,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "is_all_day": event.is_all_day,
        "location": event.location,
        "organizer": event.organizer,
        "show_as": event.show_as,
    }


def format_event_lines(events: list[EventSummary]) -> list[str]:
    """Format events as one text line each.

    Parameters
    ----------
    events : list[EventSummary]
        The events, in the order to print them.

    Returns
    -------
    list[str]
        ``start  end  [show_as]  subject @ location`` per event; all-day
        events show ``all day`` in place of the clock times' minutes.
    """
    lines = []
    for event in events:
        when = f"{_format_time(event.start)} - {_format_time(event.end)}"
        if event.is_all_day:
            when += " (all day)"
        where = f" @ {event.location}" if event.location else ""
        lines.append(f"{when}  [{event.show_as}]  {event.subject}{where}")
    return lines
