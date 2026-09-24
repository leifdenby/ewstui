from __future__ import annotations

import pytest

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.widgets.message_table import MessageTable
from ewstui.widgets.preview import PreviewPane

LONG_BODY = "\n".join(f"line {i}" for i in range(300))


@pytest.fixture
def app(tmp_path) -> EwstuiApp:
    mail = DemoMailClient()
    mail.get_message("inbox", "m1").body_text = LONG_BODY
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "priorities.todo.txt")])
    return EwstuiApp(mail, DemoCalendarClient(), cfg)


async def focus_preview(pilot, app) -> PreviewPane:
    await pilot.pause()
    await pilot.press("l")  # open m1 and move focus into the reading pane
    await pilot.pause()
    preview = app.query_one("#preview", PreviewPane)
    assert app.focused is preview
    assert preview.max_scroll_y > 0
    return preview


async def test_j_k_scroll_one_line(app):
    async with app.run_test(size=(120, 30)) as pilot:
        preview = await focus_preview(pilot, app)
        await pilot.press("j", "j", "j")
        await pilot.pause()
        assert preview.scroll_y == 3
        await pilot.press("k")
        await pilot.pause()
        assert preview.scroll_y == 2


async def test_half_and_full_page(app):
    async with app.run_test(size=(120, 30)) as pilot:
        preview = await focus_preview(pilot, app)
        height = preview.scrollable_content_region.height
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert preview.scroll_y == height // 2
        await pilot.press("ctrl+u")
        await pilot.pause()
        assert preview.scroll_y == 0
        await pilot.press("ctrl+f")
        await pilot.pause()
        assert preview.scroll_y >= height - 1
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert preview.scroll_y == 0


async def test_g_G_jump_to_ends(app):
    async with app.run_test(size=(120, 30)) as pilot:
        preview = await focus_preview(pilot, app)
        await pilot.press("G")
        await pilot.pause()
        assert preview.scroll_y == preview.max_scroll_y
        await pilot.press("g")
        await pilot.pause()
        assert preview.scroll_y == 0


async def test_j_in_message_list_still_moves_cursor(app):
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        await pilot.press("j")
        await pilot.pause()
        assert table.cursor_row == 1
        assert app.query_one("#preview", PreviewPane).scroll_y == 0
