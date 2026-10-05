"""The priority view groups items by priority under header rows, and
colours overdue items red and items due soon yellow."""
from __future__ import annotations

from datetime import date, timedelta

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.priority_store import parse_line, due_status
from ewstui.widgets.priority_view import PriorityView

TODAY = date.today()


def day(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


LINES = (
    f"(B) 2026-09-20 Bee one @email id:b1 folder:inbox due:{day(-1)}\n"
    f"(A) 2026-09-21 Ay one @email id:a1 folder:inbox due:{day(1)}\n"
    "2026-09-22 Loose one @email id:n1 folder:inbox\n"
    f"(A) 2026-09-23 Ay two @email id:a2 folder:inbox due:{day(10)}\n"
)


def make_app(tmp_path, lines: str = LINES) -> EwstuiApp:
    todo = tmp_path / "todo.txt"
    todo.write_text(lines)
    cfg = config_from_args(["--demo", "--priority-file", str(todo)])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


def test_due_status():
    def status(fields: str, today: date = date(2026, 10, 5)):
        return due_status(parse_line(f"(A) Task {fields}"), today)

    assert status("due:2026-10-04") == "overdue"
    assert status("due:2026-10-05") == "soon"
    assert status("due:2026-10-07") == "soon"
    assert status("due:2026-10-08") is None
    assert status("") is None
    assert status("due:garbage") is None
    assert due_status(parse_line("x 2026-10-05 Done due:2026-10-01"), date(2026, 10, 5)) is None


async def open_priorities(app, pilot) -> PriorityView:
    await pilot.pause()
    await pilot.press("2")
    await pilot.pause()
    return app.query_one("#priority", PriorityView)


def subjects(view: PriorityView) -> list[str]:
    return [str(view.get_row_at(r)[0]) or str(view.get_row_at(r)[4]) for r in range(view.row_count)]


async def test_grouped_under_headers(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        view = await open_priorities(app, pilot)
        assert subjects(view) == ["(A)", "Ay one", "Ay two", "(B)", "Bee one", "(-)", "Loose one"]
        assert view.cursor_row == 1  # not on the header
        assert view.current_entry().message_id == "a1"


async def test_cursor_skips_headers(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        view = await open_priorities(app, pilot)
        await pilot.press("j", "j")
        assert view.current_entry().message_id == "b1"
        await pilot.press("j")
        assert view.current_entry().message_id == "n1"
        await pilot.press("j")  # already last
        assert view.current_entry().message_id == "n1"
        await pilot.press("k")
        assert view.current_entry().message_id == "b1"
        await pilot.press("g")
        assert view.current_entry().message_id == "a1"
        await pilot.press("k")  # already first: stays off the header
        assert view.current_entry().message_id == "a1"
        await pilot.press("G")
        assert view.current_entry().message_id == "n1"


async def test_visual_selection_across_groups_skips_headers(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        view = await open_priorities(app, pilot)
        await pilot.press("j", "V", "j")  # Ay two .. Bee one, a header between
        assert {app.priority_store._find(k).message_id for k in view._selected_keys()} == {"a2", "b1"}
        await pilot.press("C")
        await pilot.pause()
        assert subjects(view)[:3] == ["(A)", "Ay one", "(C)"]


async def test_overdue_red_due_soon_yellow(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        view = await open_priorities(app, pilot)
        colours = app.theme_variables

        def style(subject: str) -> str:
            row = next(view.get_row_at(r) for r in range(view.row_count) if str(view.get_row_at(r)[4]) == subject)
            assert str(row[4].style) == str(row[2].style)  # the due date too
            return str(row[4].style)

        assert style("Bee one") == colours["error"]
        assert style("Ay one") == colours["warning"]
        assert style("Ay two") == ""
        assert style("Loose one") == ""
