"""The open priority view picks up edits made to todo.txt elsewhere."""
from __future__ import annotations

import os

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.widgets.message_table import MessageTable
from ewstui.widgets.priority_view import PriorityView

LINES = (
    "(A) 2026-09-24 Q3 budget @email id:m1 folder:inbox from:f@x msgid:<m1@demo.corp.example>\n"
    "(B) 2026-09-24 Lunch @email id:m4 folder:inbox from:l@x msgid:<m4@demo.corp.example>\n"
)


def external_write(path, text):
    """Another application saves the file (bump mtime so it's seen even
    within the same filesystem timestamp tick)."""
    path.write_text(text)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def make_app(tmp_path):
    todo = tmp_path / "todo.txt"
    todo.write_text(LINES)
    cfg = config_from_args(["--demo", "--priority-file", str(todo)])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg), todo


def descriptions(app) -> list[str]:
    return [e.description for e in app.query_one("#priority", PriorityView)._entries]


async def open_priority(app, pilot):
    await pilot.pause()
    await pilot.press("2")
    await pilot.pause()


async def test_external_edit_appears_in_the_open_view(tmp_path):
    app, todo = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await open_priority(app, pilot)
        assert descriptions(app) == ["Q3 budget", "Lunch"]
        external_write(todo, LINES + "(C) 2026-09-25 New from outside @email id:m3 folder:inbox from:x@x\n")
        app._watch_priority_file()  # the 1-second timer's tick
        await pilot.pause()
        assert descriptions(app) == ["Q3 budget", "Lunch", "New from outside"]


async def test_watch_runs_on_a_timer(tmp_path, monkeypatch):
    monkeypatch.setattr("ewstui.app.PRIORITY_WATCH_SECONDS", 0.05)
    app, todo = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await open_priority(app, pilot)
        external_write(todo, LINES.replace("(B) 2026-09-24 Lunch", "(A) 2026-09-24 Lunch changed"))
        await pilot.pause(0.3)
        assert "Lunch changed" in descriptions(app)


async def test_cursor_stays_on_the_same_item_when_lines_move(tmp_path):
    app, todo = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await open_priority(app, pilot)
        view = app.query_one("#priority", PriorityView)
        await pilot.press("j")  # on "Lunch"
        await pilot.pause()
        # outside edit: Lunch becomes (A) and moves to the top, and gets retitled
        external_write(todo, LINES.replace("(B) 2026-09-24 Lunch", "(A) 2026-09-20 Lunch with the team"))
        app._watch_priority_file()
        await pilot.pause()
        assert view.current_entry().description == "Lunch with the team"


async def test_p_column_follows_outside_edits(tmp_path):
    app, todo = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await open_priority(app, pilot)
        external_write(todo, LINES.replace("(B) 2026-09-24 Lunch", "(D) 2026-09-24 Lunch"))
        app._watch_priority_file()
        await pilot.press("1")
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        pri = {table.coordinate_to_cell_key((i, 0)).row_key.value: table.get_row_at(i)[1] for i in range(table.row_count)}
        assert pri["m4"] == "D"


async def test_not_watched_outside_the_priority_view_or_during_a_selection(tmp_path, monkeypatch):
    app, todo = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        reloads = []
        monkeypatch.setattr(app, "_priorities_changed", lambda: reloads.append(1))
        external_write(todo, LINES + "x 2026-09-25 done\n")
        app._watch_priority_file()  # mail view: ignored
        assert reloads == []
        await pilot.press("2", "V")  # opening the view reads the file; then a selection starts
        await pilot.pause()
        external_write(todo, LINES + "x 2026-09-25 done\nanother edit\n")
        app._watch_priority_file()  # changed, but a selection is in progress
        assert reloads == []
        await pilot.press("escape")
        await pilot.pause()
        app._watch_priority_file()  # selection over: now it reloads
        assert reloads == [1]
