"""`m`: move the selected message to a folder via the fuzzy folder picker."""
from __future__ import annotations

import pytest
from textual.widgets import Input, ListView

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import FolderSummary
from ewstui.folder_picker import MoveToFolderScreen, folder_paths, fuzzy_score, rank
from ewstui.widgets.message_table import MessageTable


def folder(fid, name, depth=0, unread=0) -> FolderSummary:
    return FolderSummary(id=fid, name=name, total_count=0, unread_count=unread, depth=depth)


# Shaped like a live mailbox: a lone root with everything below it.
LIVE = [
    folder("root", "Top of Information Store", 0),
    folder("inbox", "Indbakke", 1),
    folder("proj", "Projects", 2),
    folder("ews", "EWS", 3),
    folder("sent", "Sent Items", 1),
    folder("archive", "Archive", 1),
    folder("search", "Search Folders", 1),
    folder("conv", "Conversation History", 2),
    folder("aebler", "Æbler", 1),
]


# -- fuzzy_score -----------------------------------------------------------------


def test_fuzzy_score_matches_subsequences_case_insensitively():
    assert fuzzy_score("arc", "Archive") is not None
    assert fuzzy_score("ARC", "archive") == fuzzy_score("arc", "Archive")
    assert fuzzy_score("prew", "Projects/EWS") is not None
    assert fuzzy_score("xyz", "Archive") is None
    assert fuzzy_score("cra", "Archive") is None  # order matters
    assert fuzzy_score("", "Anything") == 0
    assert fuzzy_score("æb", "Indbakke/Æbler") is not None
    assert fuzzy_score("pro ews", "Projects/EWS") == fuzzy_score("proews", "Projects/EWS")  # spaces ignored


def test_word_starts_and_runs_beat_scattered_matches():
    assert fuzzy_score("arc", "Archive") > fuzzy_score("arc", "Search Folders/Conversation History")
    assert fuzzy_score("ews", "Projects/EWS") > fuzzy_score("ews", "Newsletters")


# -- paths and ranking -------------------------------------------------------------


def test_folder_paths_skip_the_lone_root():
    paths = folder_paths(LIVE)
    assert "root" not in paths
    assert paths["ews"] == "Indbakke/Projects/EWS"
    assert paths["conv"] == "Search Folders/Conversation History"


def test_folder_paths_keep_several_top_level_folders():
    flat = [folder("inbox", "Inbox"), folder("sub", "Sub", 1), folder("sent", "Sent")]
    assert folder_paths(flat) == {"inbox": "Inbox", "sub": "Inbox/Sub", "sent": "Sent"}


def test_empty_query_lists_recent_first_then_pane_order_without_current():
    ranked = [f.id for f, _ in rank(LIVE, "", recent_ids=["archive", "ews"], exclude_id="inbox")]
    assert ranked == ["archive", "ews", "proj", "sent", "search", "conv", "aebler"]


def test_query_ranks_by_score_with_recent_breaking_ties():
    ranked = [f.id for f, _ in rank(LIVE, "arc", recent_ids=[], exclude_id=None)]
    assert ranked[0] == "archive" and "conv" in ranked and "sent" not in ranked
    twins = [folder("a", "Box"), folder("b", "Box")]
    assert [f.id for f, _ in rank(twins, "box", recent_ids=["b"], exclude_id=None)] == ["b", "a"]


# -- the picker screen ---------------------------------------------------------------


def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt")])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


async def open_picker(app, pilot, recent=(), current="inbox"):
    results = []
    screen = MoveToFolderScreen("Hello", LIVE, list(recent), current)
    app.push_screen(screen, results.append)
    await pilot.pause()
    return screen, results


def shown(screen) -> list[str]:
    return [f.id for f, _ in screen._shown]


async def test_typing_narrows_and_arrows_pick(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        screen, results = await open_picker(app, pilot)
        await pilot.press(*"ar")
        await pilot.pause()
        assert shown(screen)[0] == "archive" and "sent" not in shown(screen)
        results_list = screen.query_one("#move-results", ListView)
        trace = []
        for key in ("down", "ctrl+p", "ctrl+n"):  # ends on the second match
            await pilot.press(key)
            await pilot.pause()
            trace.append((key, type(app.screen).__name__, results_list.index))
        assert trace == [("down", "MoveToFolderScreen", 1), ("ctrl+p", "MoveToFolderScreen", 0), ("ctrl+n", "MoveToFolderScreen", 1)]
        await pilot.press("enter")
        await pilot.pause()
    assert results == [shown(screen)[1]]


async def test_escape_cancels_and_j_is_text(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        screen, results = await open_picker(app, pilot)
        await pilot.press("j", "k")
        await pilot.pause()
        assert screen.query_one("#move-query", Input).value == "jk"
        await pilot.press("escape")
        await pilot.pause()
    assert results == [None]


async def test_recent_folders_are_marked_and_listed_first(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        screen, _ = await open_picker(app, pilot, recent=["sent"])
        assert shown(screen)[0] == "sent"
        first = screen.query_one("#move-results", ListView).children[0]
        assert "recent" in str(first.query_one("Label").render())


async def test_no_matches_enter_does_nothing(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        screen, results = await open_picker(app, pilot)
        await pilot.press(*"zzzz", "enter")
        await pilot.pause()
        assert app.screen is screen and results == []


# -- end to end in the mail view ----------------------------------------------------------


def inbox_ids(app) -> list[str]:
    return [m.id for m in app.mail_client.list_messages("inbox")]


async def test_m_moves_message_keeps_cursor_row_and_u_undoes(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        await pilot.press("j")  # m2
        await pilot.press("m")
        await pilot.pause()
        assert isinstance(app.screen, MoveToFolderScreen)
        await pilot.press(*"arch", "enter")
        await pilot.pause()

        assert inbox_ids(app) == ["m1", "m3", "m4"]
        assert "m2" in [m.id for m in app.mail_client.list_messages("archive")]
        assert table._current_message_id() == "m3"  # next message, not back to the top

        await pilot.press("u")
        await pilot.pause()
        assert inbox_ids(app) == ["m1", "m2", "m3", "m4"]

        await pilot.press("m")  # Archive is now remembered as recent
        await pilot.pause()
        assert app.screen._shown[0][0].id == "archive"


async def test_move_failure_is_reported_and_nothing_changes(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    seen = []
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        original = app.notify
        app.notify = lambda msg, **kw: (seen.append(msg), original(msg, **kw))

        def fail(*a, **kw):
            raise RuntimeError("server said no")

        monkeypatch.setattr(app.mail_client, "move_message", fail)
        await pilot.press("m", *"arch", "enter")
        await pilot.pause()
        assert inbox_ids(app) == ["m1", "m2", "m3", "m4"] and app.undo_stack == []
    assert any("Move failed: server said no" in m for m in seen)


@pytest.mark.parametrize("key", ["d", "A"])
async def test_delete_and_archive_also_keep_the_cursor_row(tmp_path, key):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        await pilot.press("j", "j", key)  # remove m3
        await pilot.pause()
        assert table._current_message_id() == "m4"


async def test_removing_the_last_row_selects_the_new_last(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        await pilot.press("G", "d")
        await pilot.pause()
        assert table._current_message_id() == "m3"
