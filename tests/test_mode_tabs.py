from __future__ import annotations

import pytest
from textual.widgets import Tabs

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.widgets.calendar_view import CalendarView
from ewstui.widgets.message_table import MessageTable
from ewstui.widgets.priority_view import PriorityView

VIEWS = {"mail": "#main", "priority": "#priority", "calendar": "#calendar"}


@pytest.fixture
def app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "priorities.todo.txt")])
    return EwstuiApp(DemoMailClient(page_size=cfg.page_size), DemoCalendarClient(), cfg)


def assert_mode(app: EwstuiApp, mode: str) -> None:
    assert app.query_one("#modes", Tabs).active == f"mode-{mode}"
    visible = [name for name, sel in VIEWS.items() if not app.query_one(sel).has_class("hidden")]
    assert visible == [mode]


async def test_starts_in_mail_mode(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert_mode(app, "mail")
        assert isinstance(app.focused, MessageTable)


@pytest.mark.parametrize(
    ("key", "mode", "focused"),
    [("2", "priority", PriorityView), ("3", "calendar", CalendarView), ("1", "mail", MessageTable)],
)
async def test_hotkeys_switch_mode_and_highlight_tab(app, key, mode, focused):
    async with app.run_test() as pilot:
        # start from a mode other than the target so the switch is observable
        await pilot.press("3" if key != "3" else "2")
        await pilot.press(key)
        await pilot.pause()
        assert_mode(app, mode)
        assert isinstance(app.focused, focused)


async def test_clicking_tab_switches_mode(app):
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.click("#mode-calendar")
        await pilot.pause()
        assert_mode(app, "calendar")
        await pilot.click("#mode-priority")
        await pilot.pause()
        assert_mode(app, "priority")


async def test_mode_bar_is_not_in_focus_cycle(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(5):
            await pilot.press("tab")
            assert not isinstance(app.focused, Tabs)
