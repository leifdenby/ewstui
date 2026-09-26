"""The message list keeps some emails visible above and below the cursor
(vim's scrolloff), and deleting / archiving an email doesn't move the list."""
from __future__ import annotations

from datetime import datetime, timedelta

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import MessageDetail
from ewstui.widgets.message_table import MessageTable


def long_inbox(n: int = 60) -> DemoMailClient:
    mail = DemoMailClient()
    now = datetime.now()
    mail._messages["inbox"] = [
        MessageDetail(
            id=f"x{i}", changekey="c", subject=f"Email {i}", sender="a@corp.example",
            received=now - timedelta(hours=i), is_read=True, has_attachments=False, body_text="hi",
        )
        for i in range(n)
    ]
    return mail


def make_app(tmp_path, mail) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt"), "--layout", "stacked"])
    return EwstuiApp(mail, DemoCalendarClient(), cfg)


def visible_rows(table: MessageTable) -> range:
    """Row numbers on screen (row i sits at virtual y = i; the header is fixed)."""
    top = int(table.scroll_y)
    height = table.scrollable_content_region.height - 1  # minus the header row
    return range(top, top + height)


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_scrolling_down_keeps_emails_visible_below_the_cursor(tmp_path):
    app = make_app(tmp_path, long_inbox())
    async with app.run_test(size=(120, 50)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#messages", MessageTable)
        rows = visible_rows(table)
        assert len(rows) >= 12
        margin = table.scroll_margin()
        assert margin >= 3
        for _ in range(len(rows) + 5):  # well past the first screenful
            await pilot.press("j")
        await pilot.pause()
        rows = visible_rows(table)
        assert table.cursor_row + margin in rows  # still `margin` emails below
        assert table.cursor_row + margin == rows[-1]  # and it scrolled only as far as needed
        for _ in range(len(rows)):
            await pilot.press("k")
        await pilot.pause()
        rows = visible_rows(table)
        assert table.cursor_row - margin == rows[0]  # the same above, going up


async def test_no_margin_at_the_ends_of_the_list(tmp_path):
    app = make_app(tmp_path, long_inbox())
    async with app.run_test(size=(120, 50)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#messages", MessageTable)
        await pilot.press("G")
        await pilot.pause()
        assert visible_rows(table)[-1] >= table.row_count - 1  # the last email is on screen
        await pilot.press("g")
        await pilot.pause()
        assert table.scroll_y == 0


async def test_deleting_keeps_the_scroll_position(tmp_path):
    mail = long_inbox()
    app = make_app(tmp_path, mail)
    async with app.run_test(size=(120, 50)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#messages", MessageTable)
        for _ in range(30):
            await pilot.press("j")
        for _ in range(8):  # back up a bit: the cursor now sits near the top of the screen
            await pilot.press("k")
        await settle(app, pilot)
        scroll_before, row_before = table.scroll_y, table.cursor_row
        assert scroll_before > 0
        await pilot.press("d")
        await settle(app, pilot)
        assert table.cursor_row == row_before  # on the next email, same row
        assert table.scroll_y == scroll_before
        await pilot.press("A")
        await settle(app, pilot)
        assert table.scroll_y == scroll_before
