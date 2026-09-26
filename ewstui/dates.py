"""Dates in plain text: the ones an email mentions ("Save the date: 14
October 2026", "on 3/11", "Oct 14–16"), a time range next to them
("10:00–12:00"), and the dates you type into the date field, tuxedo-style
("tomorrow", "fri", "+3d", "in 2 weeks", "april 15", "2026-10-14").

Day-first (European) for numeric dates: 3/11 is 3 November.
"""
from __future__ import annotations

import re
from datetime import date, time, timedelta

MONTHS = {
    name: i + 1
    for i, names in enumerate([
        ("january", "jan", "januar"), ("february", "feb", "februar"), ("march", "mar", "marts"),
        ("april", "apr"), ("may", "maj"), ("june", "jun", "juni"), ("july", "jul", "juli"),
        ("august", "aug"), ("september", "sep", "sept"), ("october", "oct", "okt", "oktober"),
        ("november", "nov"), ("december", "dec"),
    ])
    for name in names
}
WEEKDAYS = {
    name: i
    for i, names in enumerate([
        ("monday", "mon", "mandag", "man"), ("tuesday", "tue", "tues", "tirsdag", "tir"),
        ("wednesday", "wed", "onsdag", "ons"), ("thursday", "thu", "thur", "thurs", "torsdag", "tor"),
        ("friday", "fri", "fredag", "fre"), ("saturday", "sat", "lørdag", "lør"), ("sunday", "sun", "søndag", "søn"),
    ])
    for name in names
}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th|\.)?"
_PATTERNS = [
    # 2026-10-14
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    # 14 October 2026, 14. oktober, 14th of October
    (re.compile(rf"\b(\d{{1,2}}){_ORD}\s+(?:of\s+)?({_MONTH})\.?,?(?:\s+(\d{{4}}))?\b", re.I),
     lambda m: (m[3] and int(m[3]), MONTHS[m[2].lower()], int(m[1]))),
    # October 14, 2026 / Oct 14
    (re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}}){_ORD}(?:,?\s+(\d{{4}}))?\b", re.I),
     lambda m: (m[3] and int(m[3]), MONTHS[m[1].lower()], int(m[2]))),
    # 14.10.2026 (with dots only with a year: "3.11" is more likely a number)
    (re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b"), lambda m: (int(m[3]), int(m[2]), int(m[1]))),
    # 14/10/2026, 14/10/26, 14/10
    (re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b"),
     lambda m: (m[3] and (int(m[3]) + 2000 if len(m[3]) == 2 else int(m[3])), int(m[2]), int(m[1]))),
]
_TIME_RANGE = re.compile(r"\b(\d{1,2})[:.](\d{2})\s*(?:-|–|—|to|til)\s*(\d{1,2})[:.](\d{2})\b", re.I)


def _make(year: int | None, month: int, day: int, today: date) -> date | None:
    """A date; without a year, the next one on or after today."""
    try:
        if year:
            return date(year, month, day)
        found = date(today.year, month, day)
        return found if found >= today else date(today.year + 1, month, day)
    except ValueError:
        return None


def find_dates(text: str, today: date) -> list[date]:
    """Dates the text mentions, in the order they appear, each once."""
    hits: list[tuple[int, int, date]] = []  # (position, end, date)
    for pattern, parts in _PATTERNS:
        for m in pattern.finditer(text):
            if any(start <= m.start() < end for start, end, _ in hits):
                continue  # already read as part of an earlier, more specific pattern
            found = _make(*parts(m), today)
            if found:
                hits.append((m.start(), m.end(), found))
    out: list[date] = []
    for _, _, found in sorted(hits):
        if found not in out:
            out.append(found)
    return out


def find_time_range(text: str) -> tuple[time, time] | None:
    """The first "10:00–12:00" style range in the text."""
    for m in _TIME_RANGE.finditer(text):
        try:
            return time(int(m[1]), int(m[2])), time(int(m[3]), int(m[4]))
        except ValueError:
            continue
    return None


def parse_date(text: str, today: date) -> date | None:
    """What you type in the date field: today / tomorrow / yesterday, a
    weekday (the next one), +3d / -1w / in 2 weeks / 1m, or a date in any
    form find_dates reads."""
    s = " ".join(text.strip().lower().split())
    if not s:
        return None
    if s in ("today", "idag", "i dag"):
        return today
    if s in ("tomorrow", "imorgen", "i morgen"):
        return today + timedelta(days=1)
    if s in ("yesterday", "igår", "i går"):
        return today - timedelta(days=1)
    if s.removeprefix("next ") in WEEKDAYS:
        ahead = (WEEKDAYS[s.removeprefix("next ")] - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead)
    m = re.fullmatch(r"(?:in\s+)?([+-]?)(\d+)\s*(d|day|days|w|week|weeks|m|month|months)", s)
    if m:
        n = int(m[2]) * (-1 if m[1] == "-" else 1)
        unit = m[3][0]
        if unit == "d":
            return today + timedelta(days=n)
        if unit == "w":
            return today + timedelta(weeks=n)
        month = today.month - 1 + n
        year, month = today.year + month // 12, month % 12 + 1
        for day in (today.day, 30, 29, 28):
            try:
                return date(year, month, day)
            except ValueError:
                continue
    found = find_dates(s, today)
    return found[0] if found else None
