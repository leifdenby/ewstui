"""Going on writing a draft: Enter / o on it in Drafts opens the compose
view; Ctrl+S sends that draft, Esc saves the changes into it."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import create_autospec

from exchangelib import Message
from textual.widgets import Input, TextArea

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import MailClient
from ewstui.screens import ComposeScreen
from ewstui.widgets.folder_list import FolderList
from ewstui.widgets.message_table import MessageTable
from ewstui.widgets.preview import PreviewPane


def make_app(tmp_path):
    mail = DemoMailClient()
    mail._add_draft("Budget question", "Hi Finance,\n\nhalf-written", ["finance@corp.example"], ["boss@corp.example"])
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    return EwstuiApp(mail, DemoCalendarClient(), cfg), mail


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def open_drafts(app, pilot):
    await settle(app, pilot)
    folders = app.query_one("#folders", FolderList)
    folders.highlight_folder("drafts")
    app.select_folder("drafts")
    app.query_one("#messages", MessageTable).focus()
    await settle(app, pilot)


async def test_enter_on_a_draft_opens_it_for_writing(tmp_path):
    app, mail = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_drafts(app, pilot)
        await pilot.press("enter")  # the newest draft: Budget question
        await settle(app, pilot)
        screen = app.screen
        assert isinstance(screen, ComposeScreen) and screen.title_text == "Draft"
        assert screen.query_one("#compose-to", Input).value == "finance@corp.example"
        assert screen.query_one("#compose-cc", Input).value == "boss@corp.example"
        assert screen.query_one("#compose-subject", Input).value == "Budget question"
        assert screen.query_one("#compose-body", TextArea).text == "Hi Finance,\n\nhalf-written"


async def test_ctrl_s_sends_the_draft(tmp_path):
    app, mail = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_drafts(app, pilot)
        draft_id = mail.list_messages("drafts")[0].id
        await pilot.press("enter")
        await settle(app, pilot)
        app.screen.query_one("#compose-body", TextArea).insert("Could you check the numbers?\n", (2, 0))
        await pilot.press("ctrl+s")
        await settle(app, pilot)
        assert not isinstance(app.screen, ComposeScreen)
        assert draft_id not in [m.id for m in mail.list_messages("drafts")]
        assert draft_id in [m.id for m in mail.list_messages("sent")]
    assert mail.sent[-1]["draft"] == draft_id and "Could you check the numbers?" in mail.sent[-1]["body"]


async def test_escape_saves_the_changes_into_the_same_draft(tmp_path):
    app, mail = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_drafts(app, pilot)
        before = [m.id for m in mail.list_messages("drafts")]
        await pilot.press("enter")
        await settle(app, pilot)
        app.screen.query_one("#compose-subject", Input).value = "Budget question (v2)"
        await pilot.press("escape")
        await settle(app, pilot)
        assert [m.id for m in mail.list_messages("drafts")] == before  # no new draft
        assert mail.get_message("drafts", before[0]).subject == "Budget question (v2)"


async def test_the_status_bar_says_edit_draft_in_drafts(tmp_path):
    app, mail = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert "edit draft" not in str(app.query_one("#statusbar").render())
        await open_drafts(app, pilot)
        app._update_status()
        assert "edit draft" in str(app.query_one("#statusbar").render())


async def test_j_k_still_just_preview_drafts(tmp_path):
    app, mail = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_drafts(app, pilot)
        await pilot.press("j")
        await settle(app, pilot)
        assert not isinstance(app.screen, ComposeScreen)
        assert isinstance(app.query_one("#preview"), PreviewPane)


async def test_emails_elsewhere_still_open_for_reading(tmp_path):
    app, mail = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("enter")  # the Inbox
        await settle(app, pilot)
        assert not isinstance(app.screen, ComposeScreen) and isinstance(app.focused, PreviewPane)


def test_live_send_draft_updates_then_sends_the_same_item():
    item = create_autospec(Message, instance=True)
    folder = SimpleNamespace(id="drafts", get=lambda id: item, children=[])
    client = MailClient(SimpleNamespace(msg_folder_root=folder))
    client.send_draft("drafts", "d1", ["a@x.test"], [], "Hi", "Hello")
    assert item.subject == "Hi" and item.body == "Hello" and item.cc_recipients is None
    item.save.assert_called_once_with(update_fields=["to_recipients", "cc_recipients", "subject", "body"])
    item.send.assert_called_once_with(save_copy=True)
