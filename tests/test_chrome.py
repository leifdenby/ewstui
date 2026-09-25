"""tuxedo-style chrome: themes, header (TopBar) and status bar."""
from __future__ import annotations

import pytest
import tomlkit

from ewstui import __main__ as entry
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.widgets.chrome import StatusBar, TopBar


def make_app(tmp_path, *extra) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt"), *extra])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


def top(app) -> str:
    return app.query_one(TopBar).render().plain


def status(app) -> str:
    return app.query_one(StatusBar).render().plain


# -- themes ----------------------------------------------------------------------


async def test_muted_slate_is_the_default_theme(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert app.theme == "muted-slate"
        assert app.get_css_variables()["background"].lower() == "#1a1d23"


@pytest.mark.parametrize(("name", "expected"), [("nord", "nord"), ("Muted", "muted-slate"), ("slate", "muted-slate")])
def test_theme_names_and_aliases(name, expected):
    assert config_from_args(["--demo", "--theme", name]).theme == expected


async def test_nord_theme_applies(tmp_path):
    app = make_app(tmp_path, "--theme", "nord")
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert app.theme == "nord" and app.get_css_variables()["accent"].lower() == "#88c0d0"


def test_unknown_theme_and_saving_with_account(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="unknown theme 'dracula'"):
        config_from_args(["--demo", "--theme", "dracula"])
    path = tmp_path / "config.toml"
    path.write_text('[accounts.work]\nemail = "me@corp.example"\n')
    monkeypatch.setattr(entry.EwstuiApp, "run", lambda self: None)
    entry.main(["--config", str(path), "--account", "work", "--demo", "--theme", "nord"])
    assert tomlkit.parse(path.read_text())["accounts"]["work"]["theme"] == "nord"
    assert config_from_args(["--config", str(path)]).theme == "nord"


# -- header -------------------------------------------------------------------------


async def test_header_shows_logo_account_folder_and_counts(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        text = top(app)
        assert text.startswith("▶▮◀ ewstui  demo")
        assert "Inbox  •  4 messages  •  2 unread" in text
        await pilot.press("t")  # thread view is noted
        await pilot.pause()
        assert "•  threads" in top(app)


async def test_header_follows_the_mode(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("3")
        await pilot.pause()
        assert "Calendar  •  " in top(app)
        await pilot.press("2")
        await pilot.pause()
        text = top(app)
        assert "Priority list  •  " in text and "p.todo.txt" in text and "•  0 items" in text


async def test_header_truncates_on_a_narrow_terminal(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(40, 20)) as pilot:
        await pilot.pause()
        assert len(top(app)) <= 40 - 2  # padding 0 1


# -- status bar -----------------------------------------------------------------------


async def test_status_bar_mode_chip_and_hints_follow_focus(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert status(app).startswith(" MAIL ") and "r reply" in status(app)  # message list focused
        await pilot.press("l")  # into the reading pane
        await pilot.pause()
        assert "j/k scroll" in status(app)
        await pilot.press("h", "h")  # folder pane
        await pilot.pause()
        assert "j/k folder" in status(app)
        await pilot.press("3")
        await pilot.pause()
        assert status(app).startswith(" CALENDAR ") and "f find a room" in status(app)


async def test_status_bar_right_side_shows_counts_and_version(tmp_path):
    from ewstui import __version__

    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert status(app).rstrip().endswith(f"4 messages · v{__version__}")
