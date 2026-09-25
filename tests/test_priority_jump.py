"""Enter / o in the priority view jumps to that email in the mail view."""
from __future__ import annotations

import pytest
from textual.widgets import Tabs

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.widgets.folder_list import FolderList
from ewstui.widgets.message_table import MessageTable
from ewstui.widgets.preview import PreviewPane


def make_app(tmp_path, lines: str) -> EwstuiApp:
    todo = tmp_path / "todo.txt"
    todo.write_text(lines)
    cfg = config_from_args(["--demo", "--priority-file", str(todo)])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


async def jump(app, pilot, key="o"):
    await pilot.pause()
    await pilot.press("2")
    await pilot.pause()
    await pilot.press(key)
    await app.workers.wait_for_complete()
    await pilot.pause()


def assert_on(app, folder_id, message_id, subject):
    assert app.query_one("#modes", Tabs).active == "mode-mail"
    assert app.current_folder_id == folder_id
    assert app.query_one("#folders", FolderList).highlighted_folder_id() == folder_id
    assert app.query_one("#messages", MessageTable)._current_message_id() == message_id
    assert subject in str(app.query_one("#preview-body").render())


@pytest.mark.parametrize("key", ["o", "enter"])
async def test_jump_to_email_in_the_inbox(tmp_path, key):
    app = make_app(tmp_path, "(A) 2026-09-24 IT maintenance @email id:m3 folder:inbox from:it@corp.example\n")
    async with app.run_test(size=(160, 40)) as pilot:
        await jump(app, pilot, key)
        assert_on(app, "inbox", "m3", "IT maintenance window this weekend")
        assert isinstance(app.focused, PreviewPane)  # like opening it with o in the list


async def test_jump_to_email_in_another_folder(tmp_path):
    app = make_app(tmp_path, "(B) 2026-09-24 Weekly report @email id:s2 folder:sent from:you@corp.example\n")
    async with app.run_test(size=(160, 40)) as pilot:
        await jump(app, pilot)
        assert_on(app, "sent", "s2", "Weekly report")


async def test_jump_to_email_that_is_gone(tmp_path):
    app = make_app(tmp_path, "(A) 2026-09-24 Old mail @email id:gone folder:inbox from:x@corp.example\n")
    seen = []
    async with app.run_test(size=(160, 40)) as pilot:
        original = app.notify
        app.notify = lambda msg, **kw: (seen.append(msg), original(msg, **kw))
        await jump(app, pilot)
    assert any("moved or been deleted" in m and "predates Message-ID tracking" in m for m in seen)
