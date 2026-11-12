"""F in the message list: show only emails without a priority letter (not on
the priority list, or on it as "-"), and all again."""
from __future__ import annotations

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.widgets.chrome import TopBar
from ewstui.widgets.message_table import MessageTable

# m1 has a priority, m3 is on the list without one; m2 and m4 aren't on it.
LINES = (
    "(A) 2026-09-24 Q3 budget review @email id:m1 folder:inbox\n"
    "2026-09-24 IT maintenance @email id:m3 folder:inbox\n"
)


def make_app(tmp_path, *args: str) -> EwstuiApp:
    """A demo app with the priority list above.

    Parameters
    ----------
    tmp_path : Path
        Where to write the todo.txt.
    *args : str
        Extra command-line arguments.

    Returns
    -------
    EwstuiApp
        The app, not started.
    """
    todo = tmp_path / "todo.txt"
    todo.write_text(LINES)
    cfg = config_from_args(["--demo", "--no-date-groups", "--priority-file", str(todo), *args])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


def list_row_ids(app: EwstuiApp) -> list[str]:
    """The message ids in the list, top to bottom (separators skipped).

    Parameters
    ----------
    app : EwstuiApp
        The running app.

    Returns
    -------
    list of str
        Message ids.
    """
    table = app.query_one("#messages", MessageTable)
    return [mid for mid in table._row_ids if mid is not None]


async def test_f_hides_prioritized_and_shows_all_again(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        assert list_row_ids(app) == ["m1", "m2", "m3", "m4"]
        await pilot.press("j", "F")  # cursor on m2, which stays
        await pilot.pause()
        assert list_row_ids(app) == ["m2", "m3", "m4"]
        assert table._current_message_id() == "m2"
        assert "3 without a priority" in app.query_one(TopBar).render().plain
        await pilot.press("F")
        await pilot.pause()
        assert list_row_ids(app) == ["m1", "m2", "m3", "m4"]
        assert table._current_message_id() == "m2"
        assert "without a priority" not in app.query_one(TopBar).render().plain


async def test_cursor_on_a_hidden_email_moves_to_the_next(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("F")  # cursor was on m1, which is hidden
        await pilot.pause()
        assert app.query_one("#messages", MessageTable)._current_message_id() == "m2"


async def test_giving_a_priority_hides_the_email(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        app.priority_store.add_email("m2", "inbox", "Hi", "c@corp.example", priority="B")
        app._priorities_changed()
        await pilot.press("F")
        await pilot.pause()
        assert list_row_ids(app) == ["m3", "m4"]
        await pilot.press("j")  # m4
        (entry,) = [e for e in app.priority_store.list_entries(email_only=True) if e.message_id == "m3"]
        app.priority_store.set_priority(entry.key, "C")
        app._priorities_changed()
        await pilot.pause()
        assert list_row_ids(app) == ["m4"]
        assert app.query_one("#messages", MessageTable)._current_message_id() == "m4"


async def test_adding_without_a_priority_keeps_the_email(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("F", "P")  # m2 onto the list as "-": still shown
        await pilot.pause()
        assert list_row_ids(app) == ["m2", "m3", "m4"]
        assert app.query_one("#messages", MessageTable)._current_message_id() == "m2"


async def test_filter_works_in_thread_view(tmp_path):
    app = make_app(tmp_path, "--threads")
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("F")
        await pilot.pause()
        assert "m1" not in list_row_ids(app)
