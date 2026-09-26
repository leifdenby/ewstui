"""Ctrl+T: schedule the meeting in Teams (its "New meeting" form, filled in
through the msteams: deep link), since EWS can't make Teams join links."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from ewstui import teams
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.event_panel import EventFromEmailPanel
from ewstui.screens import NewEventScreen
from ewstui.widgets.message_table import MessageTable

START = datetime(2026, 10, 14, 10, 0)
END = datetime(2026, 10, 14, 11, 30)


def params(link: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(link).query).items()}


def test_meeting_link_fills_in_the_form():
    link = teams.meeting_link("Sprint & review", START, END, ["a@corp.example", "room-4b@corp.example"], "Agenda: plan")
    assert link.startswith("msteams:/l/meeting/new?")
    p = params(link)
    assert p["subject"] == "Sprint & review" and p["content"] == "Agenda: plan"
    assert p["attendees"] == "a@corp.example,room-4b@corp.example"
    start = datetime.fromisoformat(p["startTime"])
    assert start.utcoffset() is not None and start.astimezone(timezone.utc) == START.astimezone(timezone.utc)
    assert teams.meeting_link("x", START, END, app=False).startswith("https://teams.microsoft.com/l/meeting/new?")


def test_long_descriptions_are_shortened():
    p = params(teams.meeting_link("x", START, END, content="word " * 1000))
    assert len(p["content"]) == teams.MAX_CONTENT and p["content"].endswith("…")


def test_opens_the_app_on_macos_and_falls_back_to_the_browser(monkeypatch):
    runs, browsed = [], []
    monkeypatch.setattr(teams.sys, "platform", "darwin")
    monkeypatch.setattr(teams.webbrowser, "open", browsed.append)
    monkeypatch.setattr(teams.subprocess, "run", lambda cmd, **kw: runs.append(cmd) or SimpleNamespace(returncode=0))
    assert teams.open_in_teams("x", START, END) == "app"
    assert runs[0][0] == "open" and runs[0][1].startswith("msteams:") and browsed == []
    # No Teams app: open says it can't open msteams: links
    monkeypatch.setattr(teams.subprocess, "run", lambda cmd, **kw: SimpleNamespace(returncode=1, stderr=b"no app"))
    assert teams.open_in_teams("x", START, END) == "browser"
    assert browsed[0].startswith("https://teams.microsoft.com/")


# -- in the app -----------------------------------------------------------------

@pytest.fixture
def opened(monkeypatch) -> list:
    calls = []
    monkeypatch.setattr(teams, "open_in_teams", lambda *a: calls.append(a) or "app")
    return calls


def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    calendar = DemoCalendarClient()
    return EwstuiApp(DemoMailClient(calendar=calendar, with_invites=True), calendar, cfg)


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_ctrl_t_in_the_new_event_screen(tmp_path, opened):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("3", "n")
        await settle(app, pilot)
        assert isinstance(app.screen, NewEventScreen)
        app.screen.query_one("#event-subject").value = "Sprint planning"
        app.screen.query_one("#event-location").value = "Room 4B"
        before = len(app.calendar_client._events)
        await pilot.press("ctrl+t")
        await settle(app, pilot)
    subject, start, end, attendees, content = opened[0]
    assert subject == "Sprint planning" and end > start and content == "Location: Room 4B"
    assert len(app.calendar_client._events) == before  # Teams makes it, not ewstui


async def test_ctrl_t_in_the_event_from_email_panel(tmp_path, opened):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#messages", MessageTable)
        await pilot.press(*["j"] * table.get_row_index("std1"), "o", "c")
        await settle(app, pilot)
        await pilot.press("ctrl+t")
        await settle(app, pilot)
        assert not app.screen.query(EventFromEmailPanel)
    subject, start, end, attendees, content = opened[0]
    workshop = date.today() + timedelta(days=18)
    assert subject == "Autumn ML workshop" and start == datetime.combine(workshop, datetime.min.time()).replace(hour=10)
    assert "save the date" in content.lower()  # the email's text as the description
