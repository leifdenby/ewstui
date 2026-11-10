"""Date separators in the message list: Today, Yesterday, This week, ..."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.date_groups import build_date_group_label
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.widgets.message_table import MessageTable

WED = date(2026, 11, 11)  # a Wednesday


# -- the labels ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 11, 12), "Today"),  # the future: still at the top
        (date(2026, 11, 11), "Today"),
        (date(2026, 11, 10), "Yesterday"),
        (date(2026, 11, 9), "This week"),  # Monday
        (date(2026, 11, 8), "Last week"),  # Sunday
        (date(2026, 11, 2), "Last week"),  # Monday a week back
        (date(2026, 11, 1), "This month"),
        (date(2026, 10, 31), "October"),
        (date(2026, 1, 5), "January"),
        (date(2025, 12, 24), "December 2025"),
    ],
)
def test_build_date_group_label(day: date, expected: str) -> None:
    """Each day lands in the right group, counted from a Wednesday."""
    assert build_date_group_label(datetime.combine(day, time(12, 0)), WED) == expected


def test_build_date_group_label_on_a_monday() -> None:
    """On a Monday, yesterday (Sunday) is last week: "Yesterday" wins."""
    monday = date(2026, 11, 9)
    assert build_date_group_label(datetime(2026, 11, 8, 12), monday) == "Yesterday"
    assert build_date_group_label(datetime(2026, 11, 7, 12), monday) == "Last week"
    assert build_date_group_label(datetime(2026, 11, 1, 12), monday) == "This month"


def test_build_date_group_label_uses_local_time() -> None:
    """A UTC time from EWS is converted to local time before picking the day."""
    received = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    today = received.astimezone().date()
    assert build_date_group_label(received, today) == "Today"


def test_build_date_group_label_without_a_date() -> None:
    """A message with no received time goes under "No date"."""
    assert build_date_group_label(None, WED) == "No date"


# -- the message list ------------------------------------------------------------------


def make_app(tmp_path, *extra: str) -> EwstuiApp:
    """A demo app whose inbox has two emails today, one yesterday and one
    40 days old (so the groups don't depend on the time the tests run).

    Parameters
    ----------
    tmp_path : Path
        Where the priority file goes.
    *extra : str
        More command-line options.

    Returns
    -------
    EwstuiApp
        The app, not yet running.
    """
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt"), *extra])
    mail = DemoMailClient()
    midnight = datetime.combine(date.today(), time(0, 0))
    for mid, received in [
        ("m1", midnight + timedelta(minutes=2)),
        ("m2", midnight + timedelta(minutes=1)),
        ("m3", midnight - timedelta(hours=12)),
        ("m4", midnight - timedelta(days=40)),
    ]:
        mail.get_message("inbox", mid).received = received
    return EwstuiApp(mail, DemoCalendarClient(), cfg)


def list_row_ids(app: EwstuiApp) -> list[str]:
    """The table's rows: message ids, and separators as their label.

    Parameters
    ----------
    app : EwstuiApp
        The running app.

    Returns
    -------
    list of str
        One entry per row, top to bottom.
    """
    table = app.query_one("#messages", MessageTable)
    out = []
    for i in range(table.row_count):
        key = table.coordinate_to_cell_key((i, 0)).row_key.value
        out.append(f"[{table.get_row_at(i)[2]}]" if key.startswith("day:") else key)
    return out


def build_old_label() -> str:
    """The label of the 40-day-old email's group.

    Returns
    -------
    str
        The month name (with year if not this year).
    """
    midnight = datetime.combine(date.today(), time(0, 0))
    return f"[{build_date_group_label(midnight - timedelta(days=40), date.today())}]"


async def test_separators_between_the_days(tmp_path) -> None:
    """Separators head each group and the cursor starts on the first email."""
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        assert list_row_ids(app) == ["[Today]", "m1", "m2", "[Yesterday]", "m3", build_old_label(), "m4"]
        assert app.query_one("#messages", MessageTable)._current_message_id() == "m1"
        assert app.current_message_id == "m1"


async def test_cursor_steps_over_separators(tmp_path) -> None:
    """j/k, g and G never stop on a separator."""
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        visited = []
        for _ in range(3):
            await pilot.press("j")
            visited.append(table._current_message_id())
        assert visited == ["m2", "m3", "m4"]
        await pilot.press("k", "k")
        assert table._current_message_id() == "m2"
        await pilot.press("k", "k")  # m1, then nowhere further up
        assert table._current_message_id() == "m1"
        await pilot.press("G")
        await pilot.pause()
        assert table._current_message_id() == "m4"
        await pilot.press("g")
        assert table._current_message_id() == "m1"


async def test_deleting_the_last_email_of_a_day_goes_to_the_next_email(tmp_path) -> None:
    """After deleting m3 (Yesterday's only email) the cursor is on m4."""
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("j", "j", "d")  # m3
        await pilot.pause()
        assert list_row_ids(app) == ["[Today]", "m1", "m2", build_old_label(), "m4"]
        assert app.query_one("#messages", MessageTable)._current_message_id() == "m4"


async def test_visual_selection_leaves_out_separators(tmp_path) -> None:
    """Selecting across a separator selects just the emails."""
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("j", "V", "j")  # m2 .. m3, across "Yesterday"
        assert app.query_one("#messages", MessageTable).selected_ids() == ["m2", "m3"]


async def test_thread_goes_under_the_day_of_its_newest_message(tmp_path) -> None:
    """In thread view, m2's thread (with s1 from yesterday) stays under Today."""
    app = make_app(tmp_path, "--threads")
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        assert list_row_ids(app) == ["[Today]", "m1", "m2", "s1", "[Yesterday]", "m3", build_old_label(), "m4"]


async def test_no_separators_in_search_results(tmp_path) -> None:
    """Search results are ranked, not by date: no separators."""
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        app.query_one("#messages", MessageTable).set_search("e")
        await pilot.pause()
        assert not any(row.startswith("[") for row in list_row_ids(app))


async def test_no_date_groups_turns_them_off(tmp_path) -> None:
    """--no-date-groups: just the emails."""
    app = make_app(tmp_path, "--no-date-groups")
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        assert list_row_ids(app) == ["m1", "m2", "m3", "m4"]


async def test_prefetch_skips_separators(tmp_path) -> None:
    """The emails below one are listed without the separators between them."""
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        assert table.list_message_ids_after("m2", 5) == ["m3", "m4"]
        assert table.list_message_ids_after("nope", 5) == []


def test_date_groups_setting_from_config_and_cli(tmp_path) -> None:
    """On by default; off from the config file or --no-date-groups."""
    path = tmp_path / "config.toml"
    path.write_text('[accounts.work]\nemail = "me@corp.example"\ndate_groups = false\n')
    assert config_from_args(["--demo"]).date_groups is True
    assert config_from_args(["--demo", "--no-date-groups"]).date_groups is False
    assert config_from_args(["--config", str(path), "--account", "work"]).date_groups is False
    assert config_from_args(["--config", str(path), "--account", "work", "--date-groups"]).date_groups is True
