"""The ? help overlay: visible, tuxedo-style two columns, scrollable."""
from __future__ import annotations

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Static

from ewstui import keymap
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.screens import HelpScreen


def make_app(tmp_path, *extra) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt"), *extra])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


async def open_help(app, pilot) -> HelpScreen:
    await pilot.pause()
    await pilot.press("question_mark")
    await pilot.pause()
    assert isinstance(app.screen, HelpScreen)
    return app.screen


def plain(screen, which) -> str:
    return screen.query_one(f"#help-{which}", Static).render().plain


async def test_popups_dim_the_view_behind_them_instead_of_hiding_it(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 50)) as pilot:
        screen = await open_help(app, pilot)
        assert screen.styles.background.a < 1
        assert "Inbox" in app.export_screenshot()  # the folder list shows through


async def test_help_is_visible_in_two_columns_on_a_wide_terminal(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 50)) as pilot:
        screen = await open_help(app, pilot)
        left, right = screen.query_one("#help-left").region, screen.query_one("#help-right").region
        assert left.width > 30 and left.height > 10 and right.width > 30  # the old bug: 0x0
        assert left.y == right.y and left.right <= right.x  # side by side
        assert plain(screen, "left").startswith("MAIL") and plain(screen, "right").startswith("GLOBAL")


async def test_help_stacks_and_scrolls_on_a_small_terminal(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        screen = await open_help(app, pilot)
        left, right = screen.query_one("#help-left").region, screen.query_one("#help-right").region
        assert left.height > 0 and right.y >= left.bottom  # one column
        scroll = screen.query_one("#help-scroll", VerticalScroll)
        assert scroll.max_scroll_y > 0
        await pilot.press("j", "j", "ctrl+d")
        await pilot.pause()
        assert scroll.scroll_y == 12
        await pilot.press("k")
        await pilot.pause()
        assert scroll.scroll_y == 11


def test_help_lists_every_documented_key():
    documented = set()
    for mode, columns in keymap.HELP_COLUMNS.items():
        assert any(title == "GLOBAL" for column in columns for title, _ in column), mode  # everywhere
        for column in columns:
            for title, keys in column:
                assert keys, title
                documented |= {k for k, _ in keys}
    assert {"m", "t", "v", "Ctrl+l", "?", "[ / ]", "A-Z", "x"} <= documented


@pytest.mark.parametrize(
    ("tab", "view", "first", "absent"),
    [
        (None, "Mail", "MAIL", "PRIORITY"),
        ("2", "Priority", "PRIORITY", "MAIL"),
        ("3", "Calendar", "CALENDAR", "PRIORITY"),
    ],
)
async def test_help_shows_the_current_views_keys(tmp_path, tab, view, first, absent):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        if tab:
            await pilot.press(tab)
            await pilot.pause()
        screen = await open_help(app, pilot)
        left, right = plain(screen, "left"), plain(screen, "right")
        assert left.startswith(first) and right.startswith("GLOBAL")
        assert absent not in left + right
        assert view in str(screen.query_one("#help-box").border_title)
        if view == "Calendar":
            assert "FIND A ROOM" in left


async def test_help_colours_follow_the_theme(tmp_path):
    for theme, accent in (("muted-slate", "#8aa9c9"), ("nord", "#88c0d0")):
        app = make_app(tmp_path, "--theme", theme)
        async with app.run_test(size=(140, 50)) as pilot:
            screen = await open_help(app, pilot)
            text = screen.query_one("#help-left", Static).render()
            title_style = next(span.style for span in text.spans if text.plain[span.start:span.end] == "MAIL\n")
            rich_style = title_style.rich_style  # Textual Style -> Rich Style
            assert rich_style.color.triplet.hex == accent and rich_style.bold


@pytest.mark.parametrize("key", ["escape", "question_mark", "q"])
async def test_help_closes_with_esc_question_mark_or_q(tmp_path, key):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 50)) as pilot:
        await open_help(app, pilot)
        await pilot.press(key)
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)
        assert app.is_running  # q closed the help, not the app


async def test_other_keys_do_not_close_help(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 50)) as pilot:
        await open_help(app, pilot)
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
