from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest
from exchangelib import Message
from textual.widgets import Input, TextArea

from ewstui.app import EwstuiApp, _split_addresses
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import MailClient
from ewstui.screens import ComposeScreen

# -- live client against exchangelib's real method signatures --------------


@pytest.fixture
def live():
    # autospec enforces exchangelib's real signatures: Message.reply(subject, body, ...)
    item = create_autospec(Message, instance=True)
    folder = SimpleNamespace(id="inbox", get=lambda id: item, children=[])
    client = MailClient(SimpleNamespace(msg_folder_root=folder))
    return client, item


def test_reply_passes_subject_body_and_recipients(live):
    client, item = live
    client.reply("inbox", "m1", subject="Re: Hi", body="thanks", to=["a@x.test"])
    item.reply.assert_called_once_with(subject="Re: Hi", body="thanks", to_recipients=["a@x.test"])


def test_reply_all_passes_subject_and_body(live):
    client, item = live
    client.reply("inbox", "m1", subject="Re: Hi", body="thanks", to=["ignored@x.test"], reply_all=True)
    item.reply_all.assert_called_once_with(subject="Re: Hi", body="thanks")


def test_send_mail_uses_exchangelib_message(monkeypatch):
    from exchangelib import Account

    from ewstui import ews_client

    # autospec Account: calling a method it doesn't have (the old
    # account.send_mail bug) raises AttributeError here too.
    account = create_autospec(Account, instance=True)
    message_cls = create_autospec(Message)
    monkeypatch.setattr(ews_client, "Message", message_cls)

    MailClient(account).send_mail(to=["a@x.test", "b@y.test"], subject="Hi", body="Hello")

    message_cls.assert_called_once_with(
        account=account,
        folder=account.sent,
        subject="Hi",
        body="Hello",
        to_recipients=["a@x.test", "b@y.test"],
        cc_recipients=None,
    )
    message_cls.return_value.send_and_save.assert_called_once_with()


def test_split_addresses():
    assert _split_addresses(" a@x.test, b@y.test; c@z.test ,") == ["a@x.test", "b@y.test", "c@z.test"]


# -- app flow ----------------------------------------------------------------


@pytest.fixture
def mail() -> DemoMailClient:
    return DemoMailClient()


@pytest.fixture
def app(tmp_path, mail) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "priorities.todo.txt")])
    return EwstuiApp(mail, DemoCalendarClient(), cfg)


async def open_reply(pilot, app, extra_text: str) -> None:
    await pilot.pause()
    await pilot.press("r")
    await pilot.pause()
    assert isinstance(app.screen, ComposeScreen)
    app.screen.query_one("#compose-body", TextArea).insert(extra_text, (0, 0))


async def test_reply_sends_with_subject_and_recipient(app, mail):
    async with app.run_test() as pilot:
        await open_reply(pilot, app, "Sounds good")
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert len(mail.sent) == 1
    sent = mail.sent[0]
    assert sent["in_reply_to"] == "m1"
    assert sent["subject"] == "Re: Q3 budget review"
    assert sent["to"] == ["finance@corp.example"]
    assert sent["body"].startswith("Sounds good")


async def test_reply_shows_who_sent_it_and_when(app, mail):
    received = mail.get_message("inbox", "m1").received
    when = received.strftime("%a %d %b %Y %H:%M")
    async with app.run_test() as pilot:
        await open_reply(pilot, app, "")
        context = str(app.screen.query_one("#compose-context").render())
        assert context == f"Replying to Finance Team <finance@corp.example> · sent {when}"
        body = app.screen.query_one("#compose-body", TextArea).text
        assert f"On {when}, Finance Team <finance@corp.example> wrote:\n> " in body


async def test_failed_send_keeps_the_reply_context(app, mail, monkeypatch):
    monkeypatch.setattr(mail, "reply", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no")))
    async with app.run_test() as pilot:
        await open_reply(pilot, app, "x")
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert "Replying to Finance Team" in str(app.screen.query_one("#compose-context").render())


def test_live_drafts_are_saved_to_the_drafts_folder(monkeypatch):
    from exchangelib import Account

    from ewstui import ews_client

    account = create_autospec(Account, instance=True)
    message_cls = create_autospec(Message)
    monkeypatch.setattr(ews_client, "Message", message_cls)
    MailClient(account).save_draft(to=["a@x.test"], subject="Hi", body="Hello")
    message_cls.assert_called_once_with(
        account=account, folder=account.drafts, subject="Hi", body="Hello", to_recipients=["a@x.test"], cc_recipients=None,
    )
    message_cls.return_value.save.assert_called_once_with()  # saved, not sent


def test_live_reply_drafts_stay_replies(live):
    client, item = live
    client.account.drafts = "drafts-folder"
    client.save_reply_draft("inbox", "m1", subject="Re: Hi", body="later", to=["a@x.test"])
    item.create_reply.assert_called_once_with(subject="Re: Hi", body="later", to_recipients=["a@x.test"])
    item.create_reply.return_value.save.assert_called_once_with("drafts-folder")
    client.save_reply_draft("inbox", "m1", subject="Re: Hi", body="all", reply_all=True)
    item.create_reply_all.return_value.save.assert_called_once_with("drafts-folder")


def drafts(mail) -> list:
    return mail.list_messages("drafts")


async def test_escape_saves_a_reply_to_drafts(app, mail):
    async with app.run_test() as pilot:
        await open_reply(pilot, app, "Half-written thought")
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ComposeScreen)
    assert mail.sent == []
    draft = mail.get_message("drafts", drafts(mail)[0].id)
    assert draft.subject == "Re: Q3 budget review" and draft.body_text.startswith("Half-written thought")
    assert draft.to == ["finance@corp.example"]


async def test_escape_saves_a_new_email_to_drafts_and_untouched_ones_just_close(app, mail):
    before = len(drafts(mail))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause()
        await pilot.press("escape")  # nothing written
        await pilot.pause()
        assert len(drafts(mail)) == before
        await pilot.press("w")
        await pilot.pause()
        app.screen.query_one("#compose-subject", Input).value = "Idea"
        await pilot.press("escape")
        await pilot.pause()
    assert len(drafts(mail)) == before + 1 and drafts(mail)[0].subject == "Idea"


async def test_a_failed_draft_save_keeps_the_text(app, mail, monkeypatch):
    def fail(*a, **kw):
        raise RuntimeError("server said no")

    monkeypatch.setattr(mail, "save_reply_draft", fail)
    async with app.run_test() as pilot:
        await open_reply(pilot, app, "Precious words")
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)
        assert app.screen.query_one("#compose-body", TextArea).text.startswith("Precious words")
        monkeypatch.undo()
        await pilot.press("escape")  # unchanged since reopening, but still saved
        await pilot.pause()
        assert not isinstance(app.screen, ComposeScreen)
    assert mail.get_message("drafts", drafts(mail)[0].id).body_text.startswith("Precious words")


async def test_cmd_enter_sends(app, mail):
    async with app.run_test() as pilot:
        await open_reply(pilot, app, "Via Cmd+Enter")
        await pilot.press("super+enter")
        await pilot.pause()
        assert not isinstance(app.screen, ComposeScreen)
    assert len(mail.sent) == 1 and mail.sent[0]["body"].startswith("Via Cmd+Enter")


async def test_failed_send_reopens_compose_with_draft(app, mail, monkeypatch):
    def fail(*a, **kw):
        raise RuntimeError("server said no")

    monkeypatch.setattr(mail, "reply", fail)
    async with app.run_test() as pilot:
        await open_reply(pilot, app, "Important words")
        await pilot.press("ctrl+s")
        await pilot.pause()
        # still composing, nothing lost
        assert isinstance(app.screen, ComposeScreen)
        assert app.screen.query_one("#compose-body", TextArea).text.startswith("Important words")
        assert app.screen.query_one("#compose-subject", Input).value == "Re: Q3 budget review"

        # a retry that succeeds goes through normally
        monkeypatch.undo()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, ComposeScreen)
    assert mail.sent and mail.sent[0]["body"].startswith("Important words")


async def test_compose_new_splits_recipients(app, mail):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause()
        app.screen.query_one("#compose-to", Input).value = "a@x.test; b@y.test"
        app.screen.query_one("#compose-subject", Input).value = "Hello"
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert mail.sent == [{"to": ["a@x.test", "b@y.test"], "subject": "Hello", "body": "", "cc": []}]
