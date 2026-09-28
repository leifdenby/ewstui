from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest
from exchangelib import Message
from textual.widgets import Input, TextArea

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import MailClient
from ewstui.screens import ComposeScreen

# -- live client against exchangelib's real method signatures --------------


@pytest.fixture
def live():
    item = create_autospec(Message, instance=True)
    folder = SimpleNamespace(id="inbox", get=lambda id: item, children=[])
    client = MailClient(SimpleNamespace(msg_folder_root=folder, drafts="drafts-folder"))
    return client, item


def test_live_forward_is_exchanges_forward(live):
    client, item = live
    client.forward("inbox", "m1", subject="Fwd: Hi", body="FYI", to=["a@x.test"], cc=["b@x.test"])
    item.forward.assert_called_once_with(subject="Fwd: Hi", body="FYI", to_recipients=["a@x.test"], cc_recipients=["b@x.test"])


def test_live_forward_drafts_stay_forwards(live):
    client, item = live
    client.save_forward_draft("inbox", "m1", subject="Fwd: Hi", body="later", to=[], cc=[])
    item.create_forward.assert_called_once_with(subject="Fwd: Hi", body="later", to_recipients=None, cc_recipients=None)
    item.create_forward.return_value.save.assert_called_once_with("drafts-folder")


# -- app flow ----------------------------------------------------------------


@pytest.fixture
def mail() -> DemoMailClient:
    return DemoMailClient()


@pytest.fixture
def app(tmp_path, mail) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "priorities.todo.txt")])
    return EwstuiApp(mail, DemoCalendarClient(), cfg)


async def open_forward(pilot, app, keys=("f",)) -> ComposeScreen:
    await pilot.pause()
    await pilot.press(*keys)
    await pilot.pause()
    assert isinstance(app.screen, ComposeScreen)
    return app.screen


async def test_f_forwards_the_email_to_who_you_write_in(app, mail):
    async with app.run_test() as pilot:
        screen = await open_forward(pilot, app)
        assert screen.title_text == "Forward"
        assert screen.focused is screen.query_one("#compose-to", Input)  # empty: who to is the first thing
        assert screen.query_one("#compose-subject", Input).value == "Fwd: Q3 budget review"
        assert "Forwarding Finance Team <finance@corp.example>'s email" in str(
            screen.query_one("#compose-context").render())
        await pilot.press(*"boss@corp.example")
        screen.query_one("#compose-body", TextArea).insert("FYI", (0, 0))
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert mail.sent == [{"forward_of": "m1", "to": ["boss@corp.example"], "cc": [],
                          "subject": "Fwd: Q3 budget review", "body": "FYI"}]


async def test_forward_without_recipients_isnt_sent(app, mail):
    async with app.run_test() as pilot:
        await open_forward(pilot, app)
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)  # still writing, with a note why
    assert mail.sent == []


async def test_forward_keeps_an_existing_fwd_prefix(app, mail):
    mail.get_message("inbox", "m1").subject = "FW: Q3 budget review"
    async with app.run_test() as pilot:
        screen = await open_forward(pilot, app)
        assert screen.query_one("#compose-subject", Input).value == "FW: Q3 budget review"


async def test_f_in_the_reading_pane_forwards_that_email(app, mail):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("j", "enter")  # read m2
        await pilot.pause()
        screen = await open_forward(pilot, app)
        assert screen.query_one("#compose-subject", Input).value.endswith(mail.get_message("inbox", "m2").subject)


async def test_escape_saves_a_forward_to_drafts(app, mail):
    original = mail.get_message("inbox", "m1")
    async with app.run_test() as pilot:
        screen = await open_forward(pilot, app)
        screen.query_one("#compose-body", TextArea).insert("Have a look", (0, 0))
        await pilot.press("escape")  # focus is in To (not the vim body), so Esc closes
        await pilot.pause()
        assert not isinstance(app.screen, ComposeScreen)
    assert mail.sent == []
    draft = mail.get_message("drafts", mail.list_messages("drafts")[0].id)
    assert draft.subject == "Fwd: Q3 budget review"
    assert draft.body_text.startswith("Have a look") and original.body_text in draft.body_text
