"""V in the attachments list: select several, then Enter opens them all or
s saves them all to one folder."""
from __future__ import annotations

from pathlib import Path

import pytest

from ewstui import app as app_module
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.path_picker import PathPickerScreen
from ewstui.screens import AttachmentListScreen


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    (home / "Documents" / "Invoices").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def opened(monkeypatch) -> list:
    calls = []
    monkeypatch.setattr(app_module, "open_with_default_app", lambda path: calls.append(Path(path).name))
    return calls


def make_app(tmp_path) -> EwstuiApp:
    mail = DemoMailClient()
    mail._attachments["m1"] += [("notes.txt", "text/plain", b"notes"), ("photo.jpg", "image/jpeg", b"jpg")]
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt"),
                            "--attachment-dir", str(tmp_path / "attachments")])
    return EwstuiApp(mail, DemoCalendarClient(), cfg)


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def open_list(app, pilot) -> AttachmentListScreen:
    await settle(app, pilot)
    await pilot.press("v")
    await settle(app, pilot)
    assert isinstance(app.screen, AttachmentListScreen)
    return app.screen


async def test_select_several_and_open_them_all(tmp_path, home, opened):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        screen = await open_list(app, pilot)
        await pilot.press("V", "j", "j")
        await pilot.pause()
        assert [a.name for a in screen.selected()] == ["Q3-budget.xlsx", "notes.txt", "photo.jpg"]
        assert "3 selected" in str(screen.query_one("#attachment-help").render())
        await pilot.press("enter")
        await settle(app, pilot)
    assert opened == ["Q3-budget.xlsx", "notes.txt", "photo.jpg"]
    assert sorted(p.name for p in (tmp_path / "attachments").iterdir()) == ["Q3-budget.xlsx", "notes.txt", "photo.jpg"]


async def test_select_several_and_save_them_to_one_folder(tmp_path, home, opened):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_list(app, pilot)
        await pilot.press("j", "V", "j", "s")  # notes + photo
        await settle(app, pilot)
        assert isinstance(app.screen, PathPickerScreen)
        await pilot.press(*"invoi", "enter")
        await settle(app, pilot)
    assert sorted(p.name for p in (home / "Documents" / "Invoices").iterdir()) == ["notes.txt", "photo.jpg"]
    assert opened == []  # saved, not opened


async def test_escape_ends_the_selection_before_closing(tmp_path, home, opened):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        screen = await open_list(app, pilot)
        await pilot.press("V", "j", "escape")
        await pilot.pause()
        assert app.screen is screen and len(screen.selected()) == 1
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, AttachmentListScreen)


async def test_one_attachment_still_saves_and_opens(tmp_path, home, opened):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_list(app, pilot)
        await pilot.press("j", "enter")
        await settle(app, pilot)
    assert opened == ["notes.txt"]
