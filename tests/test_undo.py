from __future__ import annotations

import pytest

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.widgets.message_table import MessageTable


@pytest.fixture
def mail() -> DemoMailClient:
    return DemoMailClient()


@pytest.fixture
def app(tmp_path, mail) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "priorities.todo.txt")])
    return EwstuiApp(mail, DemoCalendarClient(), cfg)


def ids(mail: DemoMailClient, folder_id: str) -> list[str]:
    return [m.id for m in mail.list_messages(folder_id)]


# -- demo backend ------------------------------------------------------


def test_delete_moves_to_deleted_items(mail):
    moved = mail.delete_message("inbox", "m2")
    assert moved.folder_id == "trash"
    assert "m2" not in ids(mail, "inbox")
    assert ids(mail, "trash") == ["m2"]


def test_delete_from_deleted_items_is_not_undoable(mail):
    mail.delete_message("inbox", "m2")
    assert mail.delete_message("trash", "m2") is None
    assert ids(mail, "trash") == []


def test_move_back_keeps_received_order(mail):
    moved = mail.delete_message("inbox", "m2")
    mail.move_message(moved.folder_id, moved.message_id, "inbox")
    assert ids(mail, "inbox") == ["m1", "m2", "m3", "m4"]


# -- app ---------------------------------------------------------------


async def test_u_undoes_delete_and_reselects_message(app, mail):
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        await pilot.press("j", "d")  # delete m2
        await pilot.pause()
        assert "m2" not in ids(mail, "inbox")

        await pilot.press("u")
        await pilot.pause()
        assert ids(mail, "inbox") == ["m1", "m2", "m3", "m4"]
        assert ids(mail, "trash") == []
        assert table._current_message_id() == "m2"
        assert app.undo_stack == []


async def test_u_walks_back_through_delete_and_archive(app, mail):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")  # delete m1
        await pilot.pause()
        await pilot.press("A")  # archive m2 (now at top)
        await pilot.pause()
        assert ids(mail, "inbox") == ["m3", "m4"]

        await pilot.press("u")  # undo archive first
        await pilot.pause()
        assert ids(mail, "inbox") == ["m2", "m3", "m4"]
        assert "m2" not in ids(mail, "archive")

        await pilot.press("u")  # then the delete
        await pilot.pause()
        assert ids(mail, "inbox") == ["m1", "m2", "m3", "m4"]


async def test_u_with_empty_history_does_nothing(app, mail):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        assert ids(mail, "inbox") == ["m1", "m2", "m3", "m4"]
