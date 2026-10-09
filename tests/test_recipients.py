from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from textual.widgets import Static

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import MailClient
from ewstui.recipients import format_recipients, parse_recipients
from ewstui.screens import ComposeScreen, PickPersonScreen, RecipientsArea


def test_parse_recipients_one_per_line_or_separated():
    text = "Casey Colleague <colleague@corp.example>\nb@x.test; c@x.test, d@x.test\n\nB@X.test\n"
    assert parse_recipients(text) == ["colleague@corp.example", "b@x.test", "c@x.test", "d@x.test"]


def test_parse_recipients_keeps_a_comma_in_a_name():
    assert parse_recipients("Doe, Jane <jane@x.test>") == ["jane@x.test"]


def test_format_recipients_one_per_line_with_names():
    text = format_recipients(["a@x.test", "b@x.test"], {"a@x.test": "Anna"})
    assert text == "Anna <a@x.test>\nb@x.test"
    assert parse_recipients(text) == ["a@x.test", "b@x.test"]


@asynccontextmanager
async def composing(screen: ComposeScreen, tmp_path, find_people=None):
    """The demo app with this compose window open."""
    screen.find_people = find_people
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    app = EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(screen)
        await pilot.pause()
        yield app, pilot


async def settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_typing_in_to_adds_to_what_is_there(tmp_path):
    screen = ComposeScreen(to="Casey Colleague <colleague@corp.example>", subject="Re: x", context="ctx")
    async with composing(screen, tmp_path) as (app, pilot):
        to = screen.query_one("#compose-to", RecipientsArea)
        to.focus()
        await pilot.pause()
        assert to.selected_text == ""  # nothing preselected
        to.move_cursor(to.document.end)
        await pilot.press("enter", *"b@x.test")
        assert to.text == "Casey Colleague <colleague@corp.example>\nb@x.test"


async def test_vim_in_to_visual_line_drops_addresses(tmp_path):
    screen = ComposeScreen(to="a@x.test\nb@x.test\nc@x.test\nd@x.test", context="ctx")
    async with composing(screen, tmp_path) as (app, pilot):
        to = screen.query_one("#compose-to", RecipientsArea)
        to.focus()
        to.move_cursor((1, 0))
        await pilot.press("escape", "V", "j", "d")
        await pilot.pause()
        assert to.text == "a@x.test\nd@x.test" and to.mode == "NORMAL"
        hints = str(screen.query_one("#compose-hints").render())
        assert ":wq save to Drafts & close" in hints
        await pilot.press("escape")  # Esc in NORMAL: still writing
        await pilot.pause()
        assert app.screen is screen


async def test_ctrl_space_fills_in_the_one_match(tmp_path):
    screen = ComposeScreen()
    async with composing(screen, tmp_path, DemoMailClient().find_people) as (app, pilot):
        to = screen.query_one("#compose-to", RecipientsArea)
        to.focus()
        await pilot.press(*"casey", "ctrl+space")
        await settle(app, pilot)
        assert to.text == "Casey Colleague <colleague@corp.example>"
        assert "✓ 1 in the directory" in str(screen.query_one("#compose-to-check", Static).render())


async def test_ctrl_space_with_several_matches_lets_you_pick(tmp_path):
    screen = ComposeScreen()
    async with composing(screen, tmp_path, DemoMailClient().find_people) as (app, pilot):
        to = screen.query_one("#compose-to", RecipientsArea)
        to.focus()
        await pilot.press(*"team", "ctrl+space")
        await settle(app, pilot)
        assert isinstance(app.screen, PickPersonScreen)
        people = app.screen._people
        assert len(people) > 1
        await pilot.press("j", "enter")
        await settle(app, pilot)
        assert app.screen is screen
        name, email = people[1]
        assert to.text == f"{name} <{email}>"


async def test_unknown_addresses_are_flagged_under_the_field(tmp_path):
    screen = ComposeScreen(to="colleague@corp.example\nstranger@elsewhere.test")
    async with composing(screen, tmp_path, DemoMailClient().find_people) as (app, pilot):
        await settle(app, pilot)
        check = str(screen.query_one("#compose-to-check", Static).render())
        assert "✓ 1 in the directory" in check
        assert "not in the directory: stranger@elsewhere.test" in check


def test_live_find_people_uses_resolve_names():
    calls = []

    def resolve_names(names, search_scope):
        calls.append((names, search_scope))
        return [SimpleNamespace(name="Casey Colleague", email_address="casey@corp.example"),
                SimpleNamespace(name=None, email_address="CASEY@corp.example"),  # the same person twice
                SimpleNamespace(name="Casey Two", email_address="casey2@corp.example")]

    client = MailClient(SimpleNamespace(protocol=SimpleNamespace(resolve_names=resolve_names)))
    assert client.find_people(" casey ") == [("Casey Colleague", "casey@corp.example"),
                                             ("Casey Two", "casey2@corp.example")]
    assert calls == [(["casey"], "ActiveDirectoryContacts")]
    assert client.find_people("") == []


def test_live_find_people_with_no_match_is_empty():
    def resolve_names(names, search_scope):
        raise RuntimeError("ErrorNameResolutionNoResults")

    client = MailClient(SimpleNamespace(protocol=SimpleNamespace(resolve_names=resolve_names)))
    assert client.find_people("nobody") == []
