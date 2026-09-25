"""Mail view layout (columns vs stacked) and the message list's date column."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import tomlkit

from ewstui import __main__ as entry
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.widgets.folder_list import FolderList
from ewstui.widgets.message_table import MessageTable, _fmt_when
from ewstui.widgets.preview import PreviewPane


def make_app(tmp_path, *extra) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt"), *extra])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


def regions(app):
    return (
        app.query_one("#folders", FolderList).region,
        app.query_one("#messages", MessageTable).region,
        app.query_one("#preview", PreviewPane).region,
    )


async def test_columns_is_the_default_and_puts_list_beside_email(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        folders, messages, preview = regions(app)
        assert app.query_one("#reading").has_class("columns")
        assert folders.right <= messages.x < preview.x  # three side-by-side panes
        assert messages.y == preview.y


async def test_stacked_puts_list_above_email_right_of_folders(tmp_path):
    app = make_app(tmp_path, "--layout", "stacked")
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        folders, messages, preview = regions(app)
        assert messages.x == preview.x >= folders.right  # same column, right of the folders
        assert messages.bottom <= preview.y  # list on top, email below
        assert messages.width == preview.width
        assert messages.height < preview.height  # the email gets most of the space
        assert folders.height >= messages.height + preview.height  # folders span the full height


async def test_stacked_pane_navigation_still_works(tmp_path):
    app = make_app(tmp_path, "--layout", "stacked")
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("l")  # open message, focus the email below
        await pilot.pause()
        assert isinstance(app.focused, PreviewPane)
        await pilot.press("h", "h")  # back to the list, then the folders
        await pilot.pause()
        assert isinstance(app.focused, FolderList)


def test_layout_from_config_file_and_saved_with_account(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[accounts.work]\nemail = "me@corp.example"\nlayout = "stacked"\n')
    assert config_from_args(["--config", str(path), "--account", "work"]).layout == "stacked"
    assert config_from_args(["--config", str(path), "--account", "work", "--layout", "columns"]).layout == "columns"

    monkeypatch.setattr(entry.EwstuiApp, "run", lambda self: None)
    entry.main(["--config", str(path), "--account", "work", "--demo", "--layout", "columns"])
    assert tomlkit.parse(path.read_text())["accounts"]["work"]["layout"] == "columns"


def test_invalid_layout_in_config_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[accounts.work]\nemail = "me@corp.example"\nlayout = "sideways"\n')
    with pytest.raises(SystemExit, match="invalid layout 'sideways'"):
        config_from_args(["--config", str(path), "--account", "work"])


# -- date column ---------------------------------------------------------------


def test_fmt_when_shows_local_date_and_time():
    now = datetime(2026, 9, 25, 12, 0)
    utc = datetime(2026, 9, 25, 12, 5, tzinfo=timezone.utc)
    local = utc.astimezone()  # whatever this machine's zone is
    assert _fmt_when(utc, now) == local.strftime("%a %d %b %H:%M")
    assert _fmt_when(datetime(2026, 9, 24, 8, 30), now) == "Thu 24 Sep 08:30"  # naive: shown as is
    assert _fmt_when(datetime(2025, 12, 31, 23, 59), now) == "31 Dec 2025 23:59"  # other year
    assert _fmt_when(None, now) == ""


@pytest.mark.parametrize("layout", ["columns", "stacked"])
async def test_date_column_comes_before_from_and_subject(tmp_path, layout):
    app = make_app(tmp_path, "--layout", layout)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        columns = list(table.columns.values())
        assert [str(c.label) for c in columns] == [" ", "Received", "From", "Subject"]
        m1 = app.mail_client.get_message("inbox", "m1")
        assert table.get_row_at(0)[1] == _fmt_when(m1.received)
        # flag + date fit inside the pane even at this narrow size, so the
        # date is never scrolled out of view
        assert columns[0].get_render_width(table) + columns[1].get_render_width(table) <= table.region.width
