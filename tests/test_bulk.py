"""V in the message list: select several emails, then delete / archive /
move / mark read / prioritise them all at once; one u undoes the batch."""
from __future__ import annotations

import pytest

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.folder_picker import MoveToFolderScreen
from ewstui.priority_store import PriorityStore
from ewstui.widgets.chrome import StatusBar
from ewstui.widgets.message_table import MessageTable


def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


def ids(app, folder="inbox") -> list[str]:
    return [m.id for m in app.mail_client.list_messages(folder)]


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def select_m2_to_m4(pilot):
    await pilot.pause()
    await pilot.press("j", "V", "j", "j")  # m2, m3, m4
    await pilot.pause()


async def test_selection_is_highlighted_and_shown_in_the_status_bar(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await select_m2_to_m4(pilot)
        table = app.query_one("#messages", MessageTable)
        assert table.in_visual_mode and table.selected_ids() == ["m2", "m3", "m4"]
        assert table._painted == {"m2", "m3", "m4"}
        assert "3 selected" in app.query_one(StatusBar).render().plain
        await pilot.press("k")  # shrink
        await pilot.pause()
        assert table.selected_ids() == ["m2", "m3"] and table._painted == {"m2", "m3"}
        await pilot.press("escape")
        await pilot.pause()
        assert not table.in_visual_mode and table._painted == set()
        assert "selected" not in app.query_one(StatusBar).render().plain


async def test_archive_many_and_undo_them_in_one_go(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await select_m2_to_m4(pilot)
        await pilot.press("A")
        await settle(app, pilot)
        assert ids(app) == ["m1"]
        assert {"m2", "m3", "m4"} <= set(ids(app, "archive"))
        table = app.query_one("#messages", MessageTable)
        assert not table.in_visual_mode
        await pilot.press("u")
        await settle(app, pilot)
        assert ids(app) == ["m1", "m2", "m3", "m4"]
        assert app.undo_stack == []


async def test_delete_many(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await select_m2_to_m4(pilot)
        await pilot.press("d")
        await settle(app, pilot)
        assert ids(app) == ["m1"] and sorted(ids(app, "trash")) == ["m2", "m3", "m4"]


async def test_move_many_through_the_folder_picker(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await select_m2_to_m4(pilot)
        await pilot.press("m")
        await pilot.pause()
        assert isinstance(app.screen, MoveToFolderScreen)
        assert "3 messages" in str(app.screen.query("Label").first().render())
        await pilot.press(*"draft", "enter")
        await settle(app, pilot)
        assert ids(app) == ["m1"] and {"m2", "m3", "m4"} <= set(ids(app, "drafts"))
        assert app._recent_move_targets[0] == "drafts"


async def test_space_marks_all_read_if_any_unread_else_all_unread(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("V", "j")  # m1 + m2, both unread
        await pilot.press("space")
        await settle(app, pilot)
        read = {m.id: m.is_read for m in app.mail_client.list_messages("inbox")}
        assert read["m1"] and read["m2"]
        await pilot.press("g", "V", "j", "space")  # both read now -> both unread
        await settle(app, pilot)
        read = {m.id: m.is_read for m in app.mail_client.list_messages("inbox")}
        assert not read["m1"] and not read["m2"]


async def test_add_many_to_the_priority_list(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await select_m2_to_m4(pilot)
        await pilot.press("P")
        await settle(app, pilot)
    entries = PriorityStore(tmp_path / "todo.txt").list_entries(email_only=True)
    assert sorted(e.message_id for e in entries) == ["m2", "m3", "m4"]
    assert all(e.internet_id for e in entries)


async def test_single_actions_unchanged_outside_visual_mode(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("A")  # just m1
        await pilot.pause()
        assert ids(app) == ["m2", "m3", "m4"]


async def test_p_with_note_is_refused_during_a_selection(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await select_m2_to_m4(pilot)
        await pilot.press("p")
        await pilot.pause()
        assert app.query_one("#messages", MessageTable).in_visual_mode  # nothing happened
        assert not (tmp_path / "todo.txt").exists()


async def test_one_failure_does_not_stop_the_rest(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    seen = []
    async with app.run_test(size=(160, 40)) as pilot:
        original_archive = app.mail_client.archive_message

        def flaky(folder_id, message_id):
            if message_id == "m3":
                raise RuntimeError("server said no")
            return original_archive(folder_id, message_id)

        monkeypatch.setattr(app.mail_client, "archive_message", flaky)
        original_notify = app.notify
        app.notify = lambda msg, **kw: (seen.append(msg), original_notify(msg, **kw))
        await select_m2_to_m4(pilot)
        await pilot.press("A")
        await settle(app, pilot)
        assert ids(app) == ["m1", "m3"]
    assert any("1 message(s) failed: server said no" in m for m in seen)


async def test_selection_works_in_thread_view(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("j", "V", "j")  # s1 (sent reply) + m2 in the thread
        await pilot.pause()
        assert app.query_one("#messages", MessageTable).selected_ids() == ["s1", "m2"]
        await pilot.press("A")
        await settle(app, pilot)
        assert {"s1", "m2"} <= set(ids(app, "archive"))  # s1 archived from Sent Items
