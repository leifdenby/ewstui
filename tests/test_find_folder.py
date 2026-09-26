"""/ in the folder pane: find a folder by name (the folder list narrows)."""
from __future__ import annotations

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import FolderSummary
from ewstui.widgets.folder_list import FolderList
from ewstui.widgets.message_table import MessageTable
from ewstui.widgets.search_bar import SearchBar


def make_app(tmp_path) -> EwstuiApp:
    mail = DemoMailClient()
    mail._folders += [
        FolderSummary("projects", "Projects", 0, 0, 0),
        FolderSummary("deode", "DEODE", 3, 0, 1, parent_id="projects"),
        FolderSummary("deode-ml", "DEODE - ML DT", 1, 0, 1, parent_id="projects"),
    ]
    mail._messages["deode"] = []
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    return EwstuiApp(mail, DemoCalendarClient(), cfg)


def rows(app) -> list[str]:
    return [f.id for f in app.query_one("#folders", FolderList)._rows]


def labels(app) -> list[str]:
    return [str(label.render()) for label in app.query_one("#folders", FolderList).query("Label")]


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def find(app, pilot, text: str):
    await settle(app, pilot)
    await pilot.press("h", "slash")
    await pilot.pause()
    bar = app.query_one(SearchBar)
    assert bar.scope == "folders" and bar.parent.id == "folder-pane"  # in the folder pane, above the folders
    await pilot.press(*text)
    await pilot.pause()


async def test_typing_narrows_the_folder_list(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await find(app, pilot, "deod")
        assert rows(app) == ["deode", "deode-ml"]
        assert labels(app) == ["Projects/DEODE", "Projects/DEODE - ML DT"]  # shown as paths
        assert app.query_one("#messages", MessageTable).search == ""  # emails untouched
        assert "part of a folder's name" in str(app.query_one("#statusbar").render())


async def test_enter_opens_the_top_match(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await find(app, pilot, "deod")
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.current_folder_id == "deode"
        assert not app.query(SearchBar)
        folders = app.query_one("#folders", FolderList)
        assert len(rows(app)) == len(folders.folders)  # the whole tree again
        assert folders.highlighted_folder_id() == "deode"
        assert isinstance(app.focused, MessageTable)


async def test_down_to_choose_among_the_matches(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await find(app, pilot, "deod")
        await pilot.press("down")
        await pilot.pause()
        assert isinstance(app.focused, FolderList)
        await pilot.press("j", "enter")
        await settle(app, pilot)
        assert app.current_folder_id == "deode-ml"


async def test_escape_shows_all_folders_again(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await find(app, pilot, "sent")
        assert rows(app) == ["sent"]
        await pilot.press("escape")
        await pilot.pause()
        assert not app.query(SearchBar)
        assert "inbox" in rows(app) and "deode" in rows(app)
        assert app.current_folder_id == "inbox"  # nothing opened
        assert app.query_one("#folders", FolderList).highlighted_folder_id() == "sent"


async def test_moving_folders_waits_for_the_search_to_end(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await find(app, pilot, "deod")
        await pilot.press("down", "V")
        await pilot.pause()
        assert app.query_one("#folders", FolderList).moving is None
