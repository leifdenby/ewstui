"""Dates in email text, and dates typed into the date field."""
from __future__ import annotations

from datetime import date, time

import pytest

from ewstui.dates import find_date_ranges, find_dates, find_time_range, parse_date

TODAY = date(2026, 9, 26)  # a Saturday


def test_find_dates_in_a_save_the_date():
    text = (
        "Save the date! Our workshop is on 14 October 2026, with a dinner on Oct 15th. "
        "Deadline for abstracts: 2026-10-01. Reminder: 3/11 is the follow-up (see v3.11 notes)."
    )
    assert find_dates(text, TODAY) == [date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 1), date(2026, 11, 3)]


@pytest.mark.parametrize(("text", "expected"), [
    ("14. oktober", date(2026, 10, 14)),
    ("the 1st of March", date(2027, 3, 1)),  # no year: the next one
    ("March 1, 2026", date(2026, 3, 1)),
    ("24.12.2026", date(2026, 12, 24)),
    ("24/12/26", date(2026, 12, 24)),
    ("31/02/2026", None),  # no such day
    ("version 3.11", None),
])
def test_find_dates_forms(text, expected):
    assert find_dates(text, TODAY) == ([expected] if expected else [])


def test_the_same_date_twice_is_found_once():
    assert find_dates("14 Oct ... again 14/10", TODAY) == [date(2026, 10, 14)]


def test_find_date_ranges():
    text = "The conference runs 14–16 October 2026, with a hackathon Nov 2-3 and dinner on 20 Oct."
    assert find_date_ranges(text, TODAY) == [(date(2026, 10, 14), date(2026, 10, 16)), (date(2026, 11, 2), date(2026, 11, 3))]
    assert find_date_ranges("14.-16. oktober", TODAY) == [(date(2026, 10, 14), date(2026, 10, 16))]
    assert find_date_ranges("October 20 to 22, 2027", TODAY) == [(date(2027, 10, 20), date(2027, 10, 22))]
    assert find_date_ranges("16-14 October", TODAY) == []  # backwards: not a range
    assert find_date_ranges("from 10:00-12:00", TODAY) == []


def test_find_time_range():
    assert find_time_range("from 10:00 – 12:30 in room 4B") == (time(10), time(12, 30))
    assert find_time_range("9.15-16.00") == (time(9, 15), time(16))
    assert find_time_range("at 10:00") is None


@pytest.mark.parametrize(("typed", "expected"), [
    ("today", TODAY),
    ("tomorrow", date(2026, 9, 27)),
    ("mon", date(2026, 9, 28)),
    ("sat", date(2026, 10, 3)),  # a weekday is the next one, never today
    ("next friday", date(2026, 10, 2)),
    ("+3d", date(2026, 9, 29)),
    ("in 2 weeks", date(2026, 10, 10)),
    ("-1w", date(2026, 9, 19)),
    ("1m", date(2026, 10, 26)),
    ("april 15", date(2027, 4, 15)),
    ("2026-10-14", date(2026, 10, 14)),
    ("gibberish", None),
    ("", None),
])
def test_parse_date(typed, expected):
    assert parse_date(typed, TODAY) == expected
