"""Free time as text for an email."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ewstui.availability import availability, availability_text, free_slots, ordinal
from ewstui.ews_client import EventSummary

MON = date(2026, 10, 5)


def at(day: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(day, time(h, m))


def ev(start, end, all_day=False, show_as="Busy"):
    return EventSummary("e", "c", "x", start, end, "", "o@x", all_day, show_as)


def test_free_slots_around_events_in_whole_hours():
    events = [ev(at(MON, 9), at(MON, 9, 15)), ev(at(MON, 12), at(MON, 13, 30)), ev(at(MON, 16, 30), at(MON, 18))]
    assert free_slots(events, MON) == [
        (at(MON, 8), at(MON, 9)),
        (at(MON, 10), at(MON, 12)),  # 09:15 rounds up to 10:00
        (at(MON, 14), at(MON, 16)),  # 13:30 -> 14:00; 16:30 -> 16:00
    ]


def test_show_as_decides_what_blocks():
    whole_day = (at(MON, 0), at(MON, 0) + timedelta(days=1))
    assert free_slots([ev(*whole_day, all_day=True, show_as="OOF")], MON) == []  # a holiday
    assert free_slots([ev(*whole_day, all_day=True, show_as="Free")], MON) == [(at(MON, 8), at(MON, 17))]  # a birthday
    assert free_slots([ev(*whole_day, all_day=True, show_as="WorkingElsewhere")], MON) == [(at(MON, 8), at(MON, 17))]
    assert free_slots([ev(at(MON, 10), at(MON, 12), show_as="Free")], MON) == [(at(MON, 8), at(MON, 17))]
    assert free_slots([ev(at(MON, 10), at(MON, 12), show_as="Tentative")], MON) == [
        (at(MON, 8), at(MON, 10)), (at(MON, 12), at(MON, 17))]
    # an all-day event on another day doesn't matter
    assert free_slots([ev(at(MON, 0) - timedelta(days=1), at(MON, 0), all_day=True)], MON) == [(at(MON, 8), at(MON, 17))]


def test_the_past():
    assert free_slots([], MON, now=at(MON, 14, 20)) == [(at(MON, 15), at(MON, 17))]
    assert free_slots([], MON, now=at(MON, 16, 30)) == []  # less than an hour left


def test_overlapping_events():
    events = [ev(at(MON, 9), at(MON, 11)), ev(at(MON, 10), at(MON, 12))]
    assert free_slots(events, MON) == [(at(MON, 8), at(MON, 9)), (at(MON, 12), at(MON, 17))]


def test_ordinal():
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 31)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "31st"]


def test_text():
    text = availability_text({MON: [(at(MON, 10), at(MON, 12))], date(2026, 10, 6): [(at(date(2026, 10, 6), 12), at(date(2026, 10, 6), 13))]}, "CEST")
    assert text == (
        "I am currently available these times (CEST):\n"
        "- Monday 5th Oct, 1000-1200\n"
        "- Tuesday 6th Oct, 1200-1300\n"
    )


def test_the_zone_is_the_one_on_the_date_offered(monkeypatch):
    import time as time_module

    monkeypatch.setenv("TZ", "Europe/Copenhagen")
    time_module.tzset()
    try:
        summer, winter = date(2026, 10, 23), date(2026, 10, 26)  # CEST ends on 25 Oct 2026
        assert availability_text({summer: [(at(summer, 10), at(summer, 12))]}).startswith(
            "I am currently available these times (CEST):")
        assert availability_text({winter: [(at(winter, 10), at(winter, 12))]}).startswith(
            "I am currently available these times (CET):")
        both = availability_text({summer: [(at(summer, 10), at(summer, 12))], winter: [(at(winter, 9), at(winter, 11))]})
        assert both == (
            "I am currently available these times:\n"
            "- Friday 23rd Oct, 1000-1200 (CEST)\n"
            "- Monday 26th Oct, 0900-1100 (CET)\n"
        )
    finally:
        monkeypatch.undo()  # TZ as it was
        time_module.tzset()


def test_availability_skips_weekends_past_days_and_full_days():
    events = [ev(at(MON, 8), at(MON, 17))]  # Monday fully booked
    text = availability(events, date(2026, 10, 3), date(2026, 10, 6), now=at(date(2026, 10, 1), 9), tz_name="CEST")
    assert text == "I am currently available these times (CEST):\n- Tuesday 6th Oct, 0800-1700\n"
    assert "don't have any free time" in availability(events, MON, MON, now=at(MON, 7), tz_name="CEST")
