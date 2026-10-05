"""Enter / o in the priority view jumps to that email in the mail view."""
from __future__ import annotations

from types import SimpleNamespace

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


def make_paged_app(tmp_path, lines: str, page_size: int) -> EwstuiApp:
    todo = tmp_path / "todo.txt"
    todo.write_text(lines)
    cfg = config_from_args(["--demo", "--priority-file", str(todo), "--page-size", str(page_size)])
    return EwstuiApp(DemoMailClient(page_size=page_size), DemoCalendarClient(), cfg)


async def test_jump_to_email_older_than_the_loaded_page(tmp_path):
    """Older than the first page of its folder: it's added to the list and
    the cursor put on it, not left on the top email."""
    app = make_paged_app(
        tmp_path, "(A) 2026-09-24 Lunch Friday? @email id:m4 folder:inbox msgid:<m4@demo.corp.example>\n", page_size=2
    )
    async with app.run_test(size=(160, 40)) as pilot:
        await jump(app, pilot)
        await pilot.pause()
        assert_on(app, "inbox", "m4", "Lunch Friday?")
        assert app.current_message_id == "m4"
        # the background refresh lists it too, until another folder is picked
        assert "m4" in {m.id for m in app._fetch_messages("inbox")}
        app.on_folder_list_folder_selected(SimpleNamespace(folder=SimpleNamespace(id="inbox")))
        assert "m4" not in {m.id for m in app._fetch_messages("inbox")}


async def test_jump_to_old_email_in_another_folder(tmp_path):
    app = make_paged_app(
        tmp_path, "(A) 2026-09-24 Report @email id:s2 folder:sent msgid:<s2@demo.corp.example>\n", page_size=1
    )
    async with app.run_test(size=(160, 40)) as pilot:
        await jump(app, pilot)
        await pilot.pause()
        assert_on(app, "sent", "s2", "Weekly report")


async def test_jump_to_email_that_is_gone(tmp_path):
    app = make_app(tmp_path, "(A) 2026-09-24 Old mail @email id:gone folder:inbox from:x@corp.example\n")
    seen = []
    async with app.run_test(size=(160, 40)) as pilot:
        original = app.notify
        app.notify = lambda msg, **kw: (seen.append(msg), original(msg, **kw))
        await jump(app, pilot)
    assert any("moved or been deleted" in m and "predates Message-ID tracking" in m for m in seen)
