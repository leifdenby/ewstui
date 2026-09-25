from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import MailClient, MessageDetail
from ewstui.screens import ComposeScreen
from ewstui.widgets.folder_list import FolderList
from ewstui.widgets.message_table import MessageTable
from ewstui.widgets.preview import PreviewPane


class CountingMail(DemoMailClient):
    """Demo backend whose unread counts follow the messages (like the
    live server) and that counts get_message calls (preview fetches).
    """

    def __init__(self):
        super().__init__()
        self.get_calls = 0
        self.fail = False

    def list_folders(self):
        if self.fail:
            raise RuntimeError("server unreachable")
        for f in self._folders:
            f.unread_count = sum(not m.is_read for m in self._messages.get(f.id, []))
        return super().list_folders()

    def get_message(self, folder_id, message_id):
        self.get_calls += 1
        return super().get_message(folder_id, message_id)

    def deliver(self, msg_id: str, subject: str) -> None:
        self._messages["inbox"].insert(
            0,
            MessageDetail(
                id=msg_id, changekey="c", subject=subject, sender="new@corp.example",
                received=datetime.now(), is_read=False, has_attachments=False,
            ),
        )


@pytest.fixture
def mail() -> CountingMail:
    return CountingMail()


def make_app(tmp_path, mail, *extra) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt"), *extra])
    return EwstuiApp(mail, DemoCalendarClient(), cfg)


@pytest.fixture
def app(tmp_path, mail) -> EwstuiApp:
    return make_app(tmp_path, mail)


@pytest.fixture
def notices(app, monkeypatch) -> list[str]:
    seen: list[str] = []
    original = app.notify
    monkeypatch.setattr(app, "notify", lambda msg, **kw: (seen.append(msg), original(msg, **kw)))
    return seen


async def refresh(pilot, app) -> None:
    await pilot.press("ctrl+l")
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_ctrl_l_shows_new_mail_and_keeps_cursor(app, mail, notices):
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        await pilot.press("j")  # select m2
        await pilot.pause()
        calls_before = mail.get_calls

        mail.deliver("new1", "Fresh news")
        await refresh(pilot, app)

        assert table.row_count == 5
        assert table.get_row_at(0)[4] == "Fresh news"  # columns: flag, P, received, from, subject
        assert table._current_message_id() == "m2"
        assert mail.get_calls == calls_before  # no preview refetch
        assert "New mail: Inbox (+1)" in notices


async def test_unread_counts_update_in_folder_pane(app, mail):
    async with app.run_test() as pilot:
        await pilot.pause()
        mail.deliver("new1", "Fresh news")
        await refresh(pilot, app)
        labels = [str(item.query_one("Label").render()) for item in app.query_one("#folders", FolderList).children]
        assert "Inbox (3)" in labels


async def test_no_change_leaves_table_alone(app, mail, notices):
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        await pilot.press("j", "j")
        await pilot.pause()
        calls_before = mail.get_calls
        await refresh(pilot, app)
        assert table.cursor_row == 2
        assert mail.get_calls == calls_before
        assert notices[-1] == "No new mail"


async def test_folder_highlight_survives_refresh(app, mail):
    async with app.run_test() as pilot:
        await pilot.pause()
        folders = app.query_one("#folders", FolderList)
        folders.focus()
        await pilot.press("j", "j")  # highlight Drafts without opening it
        await pilot.pause()
        await refresh(pilot, app)
        assert folders.highlighted_folder_id() == "drafts"


async def test_stale_result_for_other_folder_is_ignored(app, mail):
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        app.select_folder("sent")
        await pilot.pause()
        inbox_msgs = mail.list_messages("inbox")
        app._apply_refresh("inbox", mail.list_folders(), inbox_msgs, manual=False)
        await pilot.pause()
        assert table.row_count == 2  # still the Sent folder's messages


async def test_refresh_is_skipped_while_composing(app, mail):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause()
        mail.deliver("new1", "Fresh news")
        app._apply_refresh("inbox", mail.list_folders(), mail.list_messages("inbox"), manual=False)
        await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert app.query_one("#messages", MessageTable).row_count == 4  # unchanged until next refresh


async def test_failures_notify_once_for_auto_refresh(app, mail, notices):
    async with app.run_test() as pilot:
        await pilot.pause()
        mail.fail = True
        for _ in range(3):
            app._background_refresh()
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert sum("Refresh failed" in n for n in notices) == 1

        mail.fail = False  # recovers; a later failure is reported again
        app._background_refresh()
        await app.workers.wait_for_complete()
        mail.fail = True
        app._background_refresh()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert sum("Refresh failed" in n for n in notices) == 2


async def test_ctrl_l_in_calendar_mode_reloads_calendar(app, monkeypatch):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("3")
        await pilot.pause()
        calls = []
        monkeypatch.setattr(app, "load_calendar_range", lambda: calls.append(1))
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert calls == [1]


@pytest.mark.parametrize(("interval", "timers"), [("0", 0), ("5", 1)])
async def test_refresh_interval_timer(tmp_path, mail, monkeypatch, interval, timers):
    app = make_app(tmp_path, mail, "--refresh-interval", interval)
    created = []
    original = EwstuiApp.set_interval
    monkeypatch.setattr(EwstuiApp, "set_interval", lambda self, secs, cb, **kw: (created.append((secs, cb)), original(self, secs, cb, **kw))[1])
    async with app.run_test() as pilot:
        await pilot.pause()
    ours = [c for c in created if c[1] == app._background_refresh]
    assert len(ours) == timers
    if timers:
        assert ours[0][0] == 300
        assert callable(ours[0][1])  # not shadowed by a Textual attribute


def test_live_list_folders_clears_exchangelib_folder_cache():
    root_folder = SimpleNamespace(id="r", name="Top of Information Store", total_count=0, unread_count=0, children=[])
    account = SimpleNamespace(root=MagicMock(), msg_folder_root=root_folder)
    MailClient(account).list_folders()
    account.root.clear_cache.assert_called_once()
