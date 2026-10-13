from __future__ import annotations

from types import SimpleNamespace

import pytest

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.widgets.chrome import TopBar
from ewstui.widgets.message_table import MessageTable

# The demo Inbox has 4 emails, newest first: m1 … m4. Two per page here.


@pytest.fixture
def mail() -> DemoMailClient:
    return DemoMailClient(page_size=2)


@pytest.fixture
def app(tmp_path, mail) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--page-size", "2", "--priority-file", str(tmp_path / "priorities.todo.txt")])
    return EwstuiApp(mail, DemoCalendarClient(), cfg)


def listed(app) -> list[str]:
    return [m.id for m in app.query_one("#messages", MessageTable)._messages if m.folder_id == "inbox"]


async def settle(app, pilot) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_one_page_at_first_and_the_header_says_theres_more(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert listed(app) == ["m1", "m2"]
        assert any("2 of 4 messages" in text for text, _ in app.query_one(TopBar).items)


async def test_G_loads_the_whole_folder_and_ends_on_the_oldest(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("G")
        await settle(app, pilot)
        assert listed(app) == ["m1", "m2", "m3", "m4"]
        assert app.current_message_id == "m4"
        assert any("4 messages" == text for text, _ in app.query_one(TopBar).items)


async def test_j_past_the_last_row_loads_the_next_page(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("j")  # m2, the last loaded
        await settle(app, pilot)
        assert listed(app) == ["m1", "m2"]
        await pilot.press("j")  # past it: the next page, cursor stays on m2
        await settle(app, pilot)
        assert listed(app) == ["m1", "m2", "m3", "m4"]
        assert app.current_message_id == "m2"
        await pilot.press("j")
        await pilot.pause()
        assert app.current_message_id == "m3"


async def test_a_fully_loaded_folder_isnt_fetched_again(app, mail):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("G")
        await settle(app, pilot)
        calls = []
        original = mail.list_messages
        mail.list_messages = lambda *a, **kw: calls.append(a) or original(*a, **kw)
        await pilot.press("g", "G")
        await settle(app, pilot)
        assert calls == []
        assert app.current_message_id == "m4"


async def test_another_folder_starts_at_one_page_again(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("G")
        await settle(app, pilot)
        for folder_id in ("sent", "inbox"):
            app.on_folder_list_folder_selected(SimpleNamespace(folder=SimpleNamespace(id=folder_id)))
            await pilot.pause()
        assert listed(app) == ["m1", "m2"]
