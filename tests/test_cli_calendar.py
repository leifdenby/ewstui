"""`ewstui calendar`: list calendar events over a date range from the command line."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from ewstui import cli_calendar
from ewstui.__main__ import main
from ewstui.demo_backend import DemoCalendarClient
from ewstui.ews_client import EventSummary


class _MultiMonthClient:
    """A calendar with one event spanning a month boundary, and one per month."""

    def __init__(self) -> None:
        self.calls: list[tuple[datetime, datetime]] = []
        self.events = [
            EventSummary(id="trip", changekey="c", subject="Trip", start=datetime(2026, 1, 30),
                         end=datetime(2026, 2, 3), location="Madrid", organizer="", is_all_day=True, show_as="OOF"),
            EventSummary(id="jan", changekey="c", subject="Jan", start=datetime(2026, 1, 5, 9),
                         end=datetime(2026, 1, 5, 10), location="", organizer="", is_all_day=False),
            EventSummary(id="mar", changekey="c", subject="Mar", start=datetime(2026, 3, 2, 9),
                         end=datetime(2026, 3, 2, 10), location="", organizer="", is_all_day=False),
        ]

    def list_events(self, start: datetime, end: datetime) -> list[EventSummary]:
        """Return the events overlapping [start, end), recording the call.

        Parameters
        ----------
        start, end : datetime
            The window.

        Returns
        -------
        list[EventSummary]
            The overlapping events.
        """
        self.calls.append((start, end))
        return [e for e in self.events if e.start < end and e.end > start]


def test_range_is_split_into_calendar_months():
    chunks = cli_calendar.split_into_months(date(2025, 12, 15), date(2026, 2, 10))
    assert chunks == [
        (datetime(2025, 12, 15), datetime(2026, 1, 1)),
        (datetime(2026, 1, 1), datetime(2026, 2, 1)),
        (datetime(2026, 2, 1), datetime(2026, 2, 10)),
    ]


def test_events_spanning_months_are_listed_once_in_start_order():
    client = _MultiMonthClient()
    events = cli_calendar.fetch_events(client, date(2026, 1, 1), date(2026, 4, 1))
    assert [e.id for e in events] == ["jan", "trip", "mar"]
    assert len(client.calls) == 3


def test_command_line_lists_demo_events(tmp_path, capsys):
    today = date.today()
    common = ["--demo", "--config", str(tmp_path / "config.toml")]
    assert main(["calendar", "--from", today.isoformat(), "--to", (today + timedelta(days=7)).isoformat(),
                 *common]) == 0
    out = capsys.readouterr().out
    assert "Standup @ Zoom" in out and "[OOF]  Company holiday" in out and "(all day)" in out


def test_command_line_json(tmp_path, capsys):
    today = date.today().isoformat()
    common = ["--demo", "--config", str(tmp_path / "config.toml")]
    assert main(["calendar", "--from", today, "--to", today, "--json", *common]) == 0
    records = json.loads(capsys.readouterr().out)
    assert [r["subject"] for r in records] == ["Standup", "1:1 with manager"]
    assert set(records[0]) == {"subject", "start", "end", "is_all_day", "location", "organizer", "show_as"}


def test_demo_client_satisfies_the_interface():
    assert cli_calendar.fetch_events(DemoCalendarClient(), date.today(), date.today()) == []
