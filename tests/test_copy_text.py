"""Copying text from the reading pane: select with the mouse, then y (or
Ctrl+C); Y copies the whole body. The system clipboard (pbcopy) is stubbed
by conftest's `system_clipboard` fixture."""
from __future__ import annotations

import subprocess

from ewstui import clipboard
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.widgets.preview import PreviewPane
from textual.geometry import Offset
from textual.selection import Selection
from textual.widgets import Static

# The real pbcopy wrapper (conftest swaps the module's for a stub in each test).
copy_to_system_clipboard = clipboard.copy_to_system_clipboard


def make_app(tmp_path) -> EwstuiApp:
    """Return a demo app with its priority list in `tmp_path`."""
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


async def settle(app, pilot) -> None:
    """Let workers finish and the screen catch up."""
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def read(app, pilot, downs: int = 1) -> None:
    """Open the email `downs` rows down and put focus in the reading pane."""
    await pilot.pause()
    await pilot.press(*["j"] * downs, "o")
    await settle(app, pilot)
    assert isinstance(app.focused, PreviewPane)


def notices(app) -> list[str]:
    """Return the text of the notices currently shown."""
    return [n.message for n in app._notifications]


def body_text(app) -> str:
    """Return all the text shown in the reading pane (header included)."""
    return str(app.query_one("#preview-body", Static).render())


async def test_double_click_then_y_copies_the_selection(tmp_path, system_clipboard):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await read(app, pilot)
        await pilot.double_click("#preview-body")  # selects all its text
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)
        assert system_clipboard and "This is a demo message body." in system_clipboard[-1]
        assert "Re: EWS bridge project" in system_clipboard[-1]  # the header too
        assert app.clipboard == system_clipboard[-1]  # the app's own (Ctrl+V) too
        assert f"Copied {len(system_clipboard[-1])} characters" in notices(app)


async def test_mouse_drag_selects_and_ctrl_c_copies(tmp_path, system_clipboard):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await read(app, pilot)
        lines = body_text(app).splitlines()
        row = next(i for i, line in enumerate(lines) if line.startswith("This is a demo"))
        await pilot.mouse_down("#preview-body", offset=(0, row))
        await pilot.hover("#preview-body", offset=(14, row))
        await pilot.mouse_up("#preview-body", offset=(14, row))
        await pilot.pause()
        await pilot.press("ctrl+c")
        await settle(app, pilot)
        assert system_clipboard[-1].startswith("This is a demo")
        assert len(system_clipboard[-1]) < 20
        assert isinstance(app.focused, PreviewPane)


async def test_y_with_nothing_selected_rings_and_hints(tmp_path, system_clipboard):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await read(app, pilot)
        await pilot.press("y")
        await settle(app, pilot)
        assert system_clipboard == []
        assert any(n.startswith("Nothing selected") for n in notices(app))


async def test_capital_y_copies_the_whole_body(tmp_path, system_clipboard):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await read(app, pilot)
        await pilot.press("Y")
        await settle(app, pilot)
        message = app.mail_client.get_message("inbox", "m2")
        assert system_clipboard == [message.body_text]
        assert f"Copied {len(message.body_text)} characters" in notices(app)


async def test_capital_y_copies_the_body_as_displayed(tmp_path, system_clipboard):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await read(app, pilot, downs=2)  # m3: an HTML email
        await pilot.press("Y")
        await settle(app, pilot)
        formatted = system_clipboard[-1]
        assert "unavailable Saturday 02:00-06:00" in formatted
        assert "<b>" not in formatted
        await pilot.press("H", "Y")  # plain text
        await settle(app, pilot)
        assert system_clipboard[-1] == app.mail_client.get_message("inbox", "m3").body_text


async def test_a_new_email_drops_the_selection(tmp_path, system_clipboard):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await read(app, pilot)
        body = app.query_one("#preview-body", Static)
        app.screen.selections = {body: Selection(Offset(0, 0), Offset(5, 0))}
        await pilot.press("h", "j", "o")  # back to the list, the next email
        await settle(app, pilot)
        assert body not in app.screen.selections
        await pilot.press("y")
        await settle(app, pilot)
        assert system_clipboard == []


def test_pbcopy_gets_the_text_as_utf8(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/pbcopy")
    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    monkeypatch.setenv("LANG", "C")
    assert copy_to_system_clipboard("Blåbærgrød") is True
    (args, kwargs), = calls
    assert args == ["/usr/bin/pbcopy"]
    assert kwargs["input"] == "Blåbærgrød".encode()
    assert kwargs["env"]["LANG"] == "en_US.UTF-8"


def test_no_pbcopy_off_macos(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    assert copy_to_system_clipboard("text") is False


def test_a_failing_pbcopy_is_not_fatal(monkeypatch):
    def fail(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 2.0)

    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/pbcopy")
    monkeypatch.setattr(clipboard.subprocess, "run", fail)
    assert copy_to_system_clipboard("text") is False
