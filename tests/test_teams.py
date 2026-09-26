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
        app.screen.query_one("#event-attendees").value = "a@corp.example; b@corp.example"
        app.screen.query_one("#event-notes").text = "Bring estimates"
        before = len(app.calendar_client._events)
        await pilot.press("ctrl+t")
        await settle(app, pilot)
    subject, start, end, attendees, content = opened[0]
    assert subject == "Sprint planning" and end > start
    assert attendees == ["a@corp.example", "b@corp.example"]
    assert content == "Location: Room 4B\n\nBring estimates"
    assert len(app.calendar_client._events) == before  # Teams makes it, not ewstui


async def test_ctrl_s_invites_the_attendees_with_the_notes(tmp_path, opened):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("3", "n")
        await settle(app, pilot)
        app.screen.query_one("#event-subject").value = "Sync"
        app.screen.query_one("#event-attendees").value = "a@corp.example, b@corp.example"
        app.screen.query_one("#event-notes").text = "Agenda: budget"
        await pilot.press("ctrl+s")
        await settle(app, pilot)
    assert opened == []
    assert app.calendar_client._events[-1].subject == "Sync"
    assert app.calendar_client.last_created_attendees == ["a@corp.example", "b@corp.example"]
    assert app.calendar_client.last_created_body == "Agenda: budget"


def test_find_join_link():
    text = ("Microsoft Teams meeting\nJoin on your computer: "
            "<https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=%7b%22Tid%22%7d>\n")
    assert teams.find_join_link(text) == \
        "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=%7b%22Tid%22%7d"
    assert teams.find_join_link("Join: https://teams.microsoft.com/meet/3812345?p=abc") == \
        "https://teams.microsoft.com/meet/3812345?p=abc"
    assert teams.find_join_link("see https://example.com") is None


def test_join_opens_the_app_on_macos(monkeypatch):
    runs, browsed = [], []
    monkeypatch.setattr(teams.sys, "platform", "darwin")
    monkeypatch.setattr(teams.webbrowser, "open", browsed.append)
    monkeypatch.setattr(teams.subprocess, "run", lambda cmd, **kw: runs.append(cmd) or SimpleNamespace(returncode=0))
    assert teams.join_meeting("https://teams.microsoft.com/l/meetup-join/19%3ax/0") == "app"
    assert runs[0] == ["open", "msteams:/l/meetup-join/19%3ax/0"]
    assert teams.join_meeting("https://teams.microsoft.com/meet/123?p=x") == "browser"  # not an app link: the browser
    assert browsed == ["https://teams.microsoft.com/meet/123?p=x"]


async def test_T_joins_an_events_teams_meeting(tmp_path, opened, monkeypatch):
    joined = []
    monkeypatch.setattr(teams, "join_meeting", lambda link: joined.append(link) or "app")
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("3")
        await settle(app, pilot)
        cal = app.query_one("#calendar")
        cal.move_cursor(row=cal.get_row_index("e2"))  # the demo 1:1: a Teams meeting
        await pilot.press("T")
        await settle(app, pilot)
    assert joined == ["https://teams.microsoft.com/l/meetup-join/19%3ameeting_demo%40thread.v2/0?context=%7b%7d"]
    assert opened == []


async def test_T_on_an_event_without_one_makes_a_new_teams_meeting(tmp_path, opened, monkeypatch):
    monkeypatch.setattr(teams, "join_meeting", lambda link: pytest.fail("nothing to join"))
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("3")
        await settle(app, pilot)
        cal = app.query_one("#calendar")
        cal.move_cursor(row=cal.get_row_index("e1"))  # Standup: not a Teams meeting
        await pilot.press("T")
        await settle(app, pilot)
    subject, start, end, attendees, content = opened[0]
    assert subject == "Standup" and content == "Location: Zoom"


def test_live_get_event_has_the_text_and_attendees():
    from exchangelib import Attendee, EWSDateTime, EWSTimeZone, Mailbox

    from ewstui.ews_client import CalendarClient

    utc = EWSTimeZone("UTC")
    item = SimpleNamespace(
        id="ev1", changekey="c", subject="Sync", location="Room 4B", is_all_day=False,
        start=EWSDateTime(2026, 10, 14, 8, tzinfo=utc), end=EWSDateTime(2026, 10, 14, 9, tzinfo=utc),
        organizer=Mailbox(email_address="me@corp.example"), text_body="Agenda",
        required_attendees=[Attendee(mailbox=Mailbox(email_address="a@corp.example"), response_type="Unknown")],
        optional_attendees=[Attendee(mailbox=Mailbox(email_address="b@corp.example"), response_type="Unknown")],
        resources=[Attendee(mailbox=Mailbox(email_address="room@corp.example"), response_type="Unknown")],
    )
    client = CalendarClient(SimpleNamespace(calendar=SimpleNamespace(get=lambda id: item)))
    client.tz = utc
    detail = client.get_event("ev1")
    assert (detail.attendees, detail.resources, detail.body_text) == (["a@corp.example", "b@corp.example"],
                                                                      ["room@corp.example"], "Agenda")
    assert detail.start == datetime(2026, 10, 14, 8)


def test_live_create_event_invites_attendees(monkeypatch):
    from unittest.mock import MagicMock

    import exchangelib
    from exchangelib.items import SEND_TO_ALL_AND_SAVE_COPY

    from ewstui.ews_client import CalendarClient

    item_cls = MagicMock()
    monkeypatch.setattr(exchangelib, "CalendarItem", item_cls)
    CalendarClient(SimpleNamespace(calendar="cal")).create_event("Sync", START, END, body="Agenda", attendees=["a@corp.example"])
    item = item_cls.return_value
    assert [a.mailbox.email_address for a in item.required_attendees] == ["a@corp.example"]
    assert item_cls.call_args.kwargs["body"] == "Agenda"
    item.save.assert_called_once_with(send_meeting_invitations=SEND_TO_ALL_AND_SAVE_COPY)


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
