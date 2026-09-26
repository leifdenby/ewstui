"""/ : search the current folder (message list) or all folders (folder pane)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ewstui import app as app_module
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import MessageDetail
from ewstui.widgets.message_table import MessageTable
from ewstui.widgets.preview import PreviewPane
from ewstui.widgets.search_bar import SearchBar


@pytest.fixture(autouse=True)
def quick_search(monkeypatch):
    monkeypatch.setattr(app_module, "SEARCH_DELAY", 0.01)


def make_app(tmp_path, mail=None, page_size=50) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt"), "--page-size", str(page_size)])
    return EwstuiApp(mail or DemoMailClient(), DemoCalendarClient(), cfg)


def rows(app) -> list[str]:
    table = app.query_one("#messages", MessageTable)
    return [table.coordinate_to_cell_key((r, 0)).row_key.value for r in range(table.row_count)]


async def settle(app, pilot, wait=0.1):
    await pilot.pause(wait)
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_search_this_folder_as_you_type(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("slash")
        await pilot.pause()
        assert isinstance(app.focused, SearchBar) and app.focused.scope == "folder"
        assert "type to search" in str(app.query_one("#statusbar").render())
        await pilot.press(*"lunch")
        await settle(app, pilot)
        assert rows(app) == ["m4"]
        assert "1 found" in str(app.query_one("#topbar").render())
        await pilot.press("enter")  # into the results, still searching
        await settle(app, pilot)
        assert isinstance(app.focused, MessageTable) and rows(app) == ["m4"]
        assert "Lunch Friday?" in str(app.query_one("#preview-body").render())
        await pilot.press("escape")  # the whole folder again
        await pilot.pause()
        assert rows(app) == ["m1", "m2", "m3", "m4"] and not app.query(SearchBar)


async def test_exchange_finds_older_emails_and_text_matches(tmp_path):
    mail = DemoMailClient(page_size=4)
    old = MessageDetail(
        id="old1", changekey="c", subject="Minutes", sender="secretary@corp.example",
        received=datetime.now() - timedelta(days=200), is_read=True, has_attachments=False,
        body_text="We discussed the kangaroo budget at length.",
    )
    mail._messages["inbox"].append(old)  # beyond the first page: not loaded
    app = make_app(tmp_path, mail, page_size=4)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert "old1" not in rows(app)
        await pilot.press("slash", *"kangaroo")
        await settle(app, pilot)
        assert rows(app) == ["old1"]  # found by Exchange, in the text
        await pilot.press("down", "enter")
        await settle(app, pilot)
        assert isinstance(app.focused, PreviewPane)
        assert "kangaroo" in str(app.query_one("#preview-body").render())


async def test_search_all_folders_from_the_folder_pane(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("h", "slash")
        await pilot.pause()
        bar = app.query_one(SearchBar)
        assert bar.scope == "all" and rows(app) == []
        assert bar.parent.id == "folder-pane"  # in the folder pane, above the folders
        await pilot.press(*"ews bridge")
        await settle(app, pilot)
        assert set(rows(app)) == {"m2", "s1"}  # the Inbox and Sent Items
        table = app.query_one("#messages", MessageTable)
        subjects = [str(table.get_row(r)[-1]) for r in rows(app)]
        assert any(s.startswith("Sent Items · ") for s in subjects) and any(s.startswith("Inbox · ") for s in subjects)
        assert "Search all folders" in str(app.query_one("#topbar").render())
        await pilot.press("escape")
        await pilot.pause()
        assert rows(app) == ["m1", "m2", "m3", "m4"] and app.current_folder_id == "inbox"


async def test_acting_on_a_search_result_updates_the_results(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("slash", *"lunch")  # this folder
        await settle(app, pilot)
        await pilot.press("enter", "A")
        await settle(app, pilot)
        assert rows(app) == []  # archived: no longer in this folder's results
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("h", "slash", *"lunch")  # all folders: found again, now in the Archive
        await settle(app, pilot)
        table = app.query_one("#messages", MessageTable)
        assert rows(app) == ["m4"] and str(table.get_row("m4")[-1]).startswith("Archive · ")
        await pilot.press("enter", "d")
        await settle(app, pilot)
        assert table.search == "lunch"  # still searching
        assert rows(app) == []  # it left the folder it was found in


async def test_background_refresh_leaves_all_folder_results_alone(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("h", "slash", *"ews bridge")
        await settle(app, pilot)
        before = rows(app)
        app._apply_refresh("inbox", app.mail_client.list_folders(), app.mail_client.list_messages("inbox"), manual=False)
        await pilot.pause()
        assert rows(app) == before
