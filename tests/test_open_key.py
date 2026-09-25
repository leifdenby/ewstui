"""`o` opens, like Enter; `l`/`h` still move between panes."""
from __future__ import annotations

import pytest

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.widgets.chrome import StatusBar
from ewstui.widgets.folder_list import FolderList
from ewstui.widgets.message_table import MessageTable
from ewstui.widgets.preview import PreviewPane


def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt")])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


@pytest.mark.parametrize("key", ["o", "enter", "l"])
async def test_open_folder_then_email(tmp_path, key):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        folders = app.query_one("#folders", FolderList)
        folders.focus()
        await pilot.press("j")  # Sent Items
        await pilot.press(key)
        await pilot.pause()
        assert app.current_folder_id == "sent" and isinstance(app.focused, MessageTable)
        await pilot.press(key)  # into the email
        await pilot.pause()
        assert isinstance(app.focused, PreviewPane)
        assert "Re: EWS bridge project" in str(app.query_one("#preview-body").render())


@pytest.mark.parametrize(("layout", "list_hint"), [("columns", "l open"), ("stacked", "o open")])
async def test_open_hint_follows_the_layout(tmp_path, layout, list_hint):
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt"), "--layout", layout])
    app = EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        status = app.query_one(StatusBar)
        assert status.render().plain.startswith(f" MAIL   {list_hint} ·")  # message list focused
        app.query_one("#folders", FolderList).focus()
        await pilot.pause()
        assert "l open" in status.render().plain  # folders always open to the right
