"""Priority list: always in step with todo.txt on disk, never rewriting other
tools' lines, email-only view, and the P column in the message list.
All on temp files."""
from __future__ import annotations

import os

import pytest

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.priority_store import PriorityStore
from ewstui.widgets.chrome import TopBar
from ewstui.widgets.message_table import MessageTable
from ewstui.widgets.priority_view import PriorityView

# A todo.txt shared with other tools: own tasks with tokens in an order
# ewstui would never write, a blank line, and one email entry — in the older
# +email style, which must still be recognised (ewstui now writes @email).
OTHER_TOOLS = (
    "(B) Call the plumber @home +house due:2026-10-01\n"
    "\n"
    "x 2026-09-20 2026-09-01 +garden Water the plants\n"
    "(A) 2026-09-24 Q3 budget review +email id:m1 folder:inbox from:finance@corp.example\n"
)


def bump_mtime(path):
    """Make sure an external write is seen even within the same mtime tick."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


@pytest.fixture
def todo(tmp_path):
    path = tmp_path / "todo.txt"
    path.write_text(OTHER_TOOLS)
    return path


# -- 1. no stale cache, no rewriting ------------------------------------------------------


def test_external_edits_are_seen_without_restarting(todo):
    store = PriorityStore(todo)
    assert [e.description for e in store.list_entries(email_only=True)] == ["Q3 budget review"]
    with todo.open("a") as f:
        f.write("(C) 2026-09-25 Lunch Friday? +email id:m4 folder:inbox from:friend@corp.example\n")
    bump_mtime(todo)
    assert [e.message_id for e in store.list_entries(email_only=True)] == ["m1", "m4"]


def test_mutation_after_external_edit_keeps_that_edit(todo):
    store = PriorityStore(todo)
    todo.write_text(todo.read_text() + "Buy milk @shop\n")  # another tool adds a task
    bump_mtime(todo)
    store.add_email("m2", "inbox", "Re: EWS bridge project", "colleague@corp.example", priority=None)
    text = todo.read_text()
    assert "Buy milk @shop" in text and "id:m2" in text


def test_other_lines_survive_byte_for_byte(todo):
    store = PriorityStore(todo)
    (entry,) = store.list_entries(email_only=True)
    assert store.set_priority(entry.key, "C")
    lines = todo.read_text().splitlines()
    original = OTHER_TOOLS.splitlines()
    assert lines[:3] == original[:3]  # own tasks (odd token order kept) and the blank line
    # the changed (old-style +email) entry is rewritten with the @email context
    assert lines[3].startswith("(C) 2026-09-24 Q3 budget review @email id:m1")
    assert len(lines) == 4


def test_add_email_appends_one_line(todo):
    store = PriorityStore(todo)
    store.add_email("m2", "inbox", "Re: EWS bridge project", "c@corp.example", priority=None)
    assert todo.read_text().startswith(OTHER_TOOLS)
    assert todo.read_text().splitlines()[-1].endswith(" @email id:m2 folder:inbox from:c@corp.example")


def test_stale_key_after_external_reorder_changes_nothing(todo):
    store = PriorityStore(todo)
    (entry,) = store.list_entries(email_only=True)
    reordered = "\n".join(reversed(OTHER_TOOLS.splitlines())) + "\n"
    todo.write_text(reordered)
    bump_mtime(todo)
    assert store.set_priority(entry.key, "Z") is False
    assert store.remove(entry.key) is False
    assert todo.read_text() == reordered


def test_keys_are_stable_while_the_file_is_unchanged(todo):
    a = [e.key for e in PriorityStore(todo).list_entries(include_completed=True)]
    b = [e.key for e in PriorityStore(todo).list_entries(include_completed=True)]
    assert a == b


def test_new_entries_use_the_email_context_and_both_tags_count(todo):
    with todo.open("a") as f:
        f.write("Plain task with an email word +emailing @phone\n")  # neither tag: not an email item
    store = PriorityStore(todo)
    entry = store.add_email("m2", "inbox", "Hello", "c@corp.example", priority=None)
    assert entry.contexts == ["email"] and entry.projects == []
    assert "+email" not in todo.read_text().splitlines()[-1]
    assert [e.message_id for e in store.list_entries(email_only=True)] == ["m1", "m2"]  # +email and @email


def test_priorities_by_message_covers_open_email_entries_only(todo):
    store = PriorityStore(todo)
    store.add_email("m3", "inbox", "IT maintenance", "it@corp.example", priority=None)
    assert store.priorities_by_message() == {"m1": "A", "m3": None}


# -- 2. the view lists only email items ------------------------------------------------------


def make_app(tmp_path, todo) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(todo)])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


async def test_priority_view_shows_only_email_entries(tmp_path, todo):
    app = make_app(tmp_path, todo)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        view = app.query_one("#priority", PriorityView)
        assert [e.description for e in view._entries] == ["Q3 budget review"]
        header = app.query_one(TopBar).render().plain
        assert "todo.txt  •  1 items" in header  # counts email items only, names the file


async def test_add_notice_names_the_file(tmp_path, todo):
    app = make_app(tmp_path, todo)
    seen = []
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        original = app.notify
        app.notify = lambda msg, **kw: (seen.append(msg), original(msg, **kw))
        await pilot.press("j", "P")  # m2
        await pilot.pause()
    assert any("Added to priority list" in m and str(todo) in m for m in seen)


# -- 3. P column in the message list ------------------------------------------------------------


def pri_column(app) -> dict[str, str]:
    table = app.query_one("#messages", MessageTable)
    return {table.coordinate_to_cell_key((i, 0)).row_key.value: table.get_row_at(i)[1] for i in range(table.row_count)}


async def test_p_column_shows_priorities(tmp_path, todo):
    app = make_app(tmp_path, todo)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        assert pri_column(app) == {"m1": "A", "m2": "", "m3": "", "m4": ""}
        await pilot.press("j", "P")  # m2 onto the list, no priority yet
        await pilot.pause()
        assert pri_column(app)["m2"] == "-"
        assert app.query_one("#messages", MessageTable)._current_message_id() == "m2"  # cursor untouched


async def test_setting_a_priority_in_the_view_updates_the_column(tmp_path, todo):
    app = make_app(tmp_path, todo)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("D")  # set m1's entry to (D)
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        assert pri_column(app)["m1"] == "D"
        assert "(D) 2026-09-24 Q3 budget review" in todo.read_text()


async def test_external_change_shows_up_after_refresh(tmp_path, todo):
    app = make_app(tmp_path, todo)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        with todo.open("a") as f:
            f.write("(B) 2026-09-25 Lunch +email id:m4 folder:inbox from:friend@corp.example\n")
        bump_mtime(todo)
        app.select_folder("inbox")  # any reload re-reads the file
        await pilot.pause()
        assert pri_column(app)["m4"] == "B"
