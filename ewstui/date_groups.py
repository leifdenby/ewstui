"""Date groups for the message list: the separator rows "Today",
"Yesterday", "This week", "Last week", "This month", then one per month
("October", or "October 2025" for another year).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta


def build_date_group_label(received: datetime | None, today: date) -> str:
    """The separator a message received at `received` goes under.

    Parameters
    ----------
    received : datetime or None
        When the message arrived. A timezone-aware time (EWS gives UTC) is
        converted to the machine's local time first.
    today : date
        The local date to count from.

    Returns
    -------
    str
        "Today", "Yesterday", "This week" (earlier this week, weeks start
        on Monday), "Last week", "This month" (earlier this month), else the
        month's name, with the year if it isn't `today`'s; "No date" when
        `received` is None. Future dates count as "Today".
    """
    if received is None:
        return "No date"
    if received.tzinfo is not None:
        received = received.astimezone()  # system local time
    day = received.date()
    if day >= today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    week_start = today - timedelta(days=today.weekday())
    if day >= week_start:
        return "This week"
    if day >= week_start - timedelta(weeks=1):
        return "Last week"
    if (day.year, day.month) == (today.year, today.month):
        return "This month"
    return day.strftime("%B" if day.year == today.year else "%B %Y")
