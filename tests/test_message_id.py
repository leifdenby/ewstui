"""Priority entries follow their email by Internet Message-ID: moves made in
ewstui update the entry at once; moves made elsewhere are found by a search
when jumping. All on temp files and the demo backend."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import MailClient, MovedMessage
from ewstui.priority_store import PriorityStore
from ewstui.widgets.message_table import MessageTable


def make_app(tmp_path, lines: str = "") -> tuple[EwstuiApp, object]:
    todo = tmp_path / "todo.txt"
    todo.write_text(lines)
    cfg = config_from_args(["--demo", "--priority-file", str(todo)])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg), todo


def entry(todo):
    (e,) = PriorityStore(todo).list_entries(email_only=True)
    return e


async def jump(app, pilot):
    await pilot.press("2")
    await pilot.pause()
    await pilot.press("o")
    await app.workers.wait_for_complete()
    await pilot.pause()
    await app.workers.wait_for_complete()  # the preview load started by the jump
    await pilot.pause()


def on_message(app) -> tuple[str, str]:
    return app.current_folder_id, app.query_one("#messages", MessageTable)._current_message_id()


# -- storing the Message-ID -------------------------------------------------------------


async def test_adding_stores_the_message_id(tmp_path):
    app, todo = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("j", "P")  # m2
        await pilot.pause()
    e = entry(todo)
    assert e.internet_id == "<m2@demo.corp.example>" and e.message_id == "m2" and e.folder_id == "inbox"


async def test_opening_a_message_fills_in_a_missing_message_id(tmp_path):
    app, todo = make_app(tmp_path, "(A) 2026-09-24 IT maintenance @email id:m3 folder:inbox from:it@x\n")
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("j", "j")  # preview m3
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert entry(todo).internet_id == "<m3@demo.corp.example>"


# -- moves in ewstui update the entry ---------------------------------------------------


@pytest.mark.parametrize("how", ["move", "archive", "delete"])
async def test_moving_in_ewstui_updates_the_entry_and_jump_still_works(tmp_path, how):
    app, todo = make_app(tmp_path, "(A) 2026-09-24 Q3 budget @email id:m1 folder:inbox from:f@x\n")
    dest = {"move": "archive", "archive": "archive", "delete": "trash"}[how]
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        if how == "move":
            await pilot.press("m", *"arch", "enter")
        else:
            await pilot.press({"archive": "A", "delete": "d"}[how])
        await pilot.pause()
        assert entry(todo).folder_id == dest
        await jump(app, pilot)
        assert on_message(app) == (dest, "m1")
        assert app.query_one("#messages", MessageTable).get_row_at(0)[1] == "A"  # P column follows too


async def test_undo_moves_the_entry_back(tmp_path):
    app, todo = make_app(tmp_path, "(A) 2026-09-24 Q3 budget @email id:m1 folder:inbox from:f@x\n")
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
    assert entry(todo).folder_id == "inbox"


# -- moves elsewhere are found by Message-ID ----------------------------------------------


def move_behind_our_back(app, message_id, dest, new_id):
    """As if moved in Outlook: new folder and (like EWS) a new item id."""
    mail = app.mail_client
    msg = mail.get_message("inbox", message_id)
    mail._messages["inbox"].remove(msg)
    msg.id = new_id
    mail._messages[dest].append(msg)


async def test_jump_finds_an_email_moved_elsewhere_and_updates_the_entry(tmp_path):
    app, todo = make_app(
        tmp_path, "(A) 2026-09-24 Lunch @email id:m4 folder:inbox from:f@x msgid:<m4@demo.corp.example>\n"
    )
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        move_behind_our_back(app, "m4", "archive", "m4-moved")
        await jump(app, pilot)
        assert on_message(app) == ("archive", "m4-moved")
    e = entry(todo)
    assert (e.folder_id, e.message_id) == ("archive", "m4-moved")


async def test_jump_reports_an_email_that_is_nowhere(tmp_path):
    app, todo = make_app(
        tmp_path, "(A) 2026-09-24 Gone @email id:zz folder:inbox from:f@x msgid:<zz@demo.corp.example>\n"
    )
    seen = []
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        original = app.notify
        app.notify = lambda msg, **kw: (seen.append(msg), original(msg, **kw))
        await jump(app, pilot)
    assert any("wasn't found in any folder" in m for m in seen)


# -- live client ------------------------------------------------------------------------


def queryset_returning(items):
    qs = MagicMock()
    qs.only.return_value.__getitem__.return_value = items
    return qs


def test_live_find_message_checks_the_hint_folder_first():
    item = SimpleNamespace(id="new-id", parent_folder_id=SimpleNamespace(id="archive"))
    hint = SimpleNamespace(id="archive", children=[], filter=MagicMock(return_value=queryset_returning([item])))
    root = SimpleNamespace(id="root", children=[hint])
    client = MailClient(SimpleNamespace(msg_folder_root=root))
    assert client.find_message("<x@y>", hint_folder_id="archive") == MovedMessage("archive", "new-id")
    hint.filter.assert_called_once_with(message_id="<x@y>")


def test_live_find_message_searches_all_mail_folders_in_one_request(monkeypatch):
    item = SimpleNamespace(id="new-id", parent_folder_id=SimpleNamespace(id="projects"))
    inbox = SimpleNamespace(id="inbox", folder_class="IPF.Note", children=[],
                            filter=MagicMock(return_value=queryset_returning([])))
    projects = SimpleNamespace(id="projects", folder_class="IPF.Note", children=[])
    calendar = SimpleNamespace(id="cal", folder_class="IPF.Appointment", children=[])
    inbox.children = [projects]
    root = SimpleNamespace(id="root", children=[inbox, calendar])
    seen = {}

    class FakeCollection:
        def __init__(self, account, folders):
            seen["folders"] = [f.id for f in folders]

        def filter(self, **kw):
            seen["filter"] = kw
            return queryset_returning([item])

    monkeypatch.setattr("exchangelib.folders.FolderCollection", FakeCollection)
    client = MailClient(SimpleNamespace(msg_folder_root=root))
    assert client.find_message("<x@y>", hint_folder_id="inbox") == MovedMessage("projects", "new-id")
    assert sorted(seen["folders"]) == ["inbox", "projects"]  # mail folders only, calendar skipped
    assert seen["filter"] == {"message_id": "<x@y>"}
