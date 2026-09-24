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
