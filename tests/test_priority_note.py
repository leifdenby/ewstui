"""p on an email that's already on the priority list edits its note (the
same todo.txt line), and P there leaves the entry, priority and all, alone."""
from __future__ import annotations

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.priority_store import PriorityStore, split_note
from ewstui.screens import AddNoteScreen


def test_split_note():
    assert split_note("Hello — call Bo", "Hello") == ("Hello", "call Bo")
    assert split_note("Hello", "Hello") == ("Hello", "")
    assert split_note("A — B — note", "A — B") == ("A — B", "note")  # the subject has the separator too
    assert split_note("Edited elsewhere — note") == ("Edited elsewhere", "note")
    assert split_note("No note here") == ("No note here", "")


def test_set_note_replaces_or_removes_the_note_on_the_same_line(tmp_path):
    path = tmp_path / "todo.txt"
    path.write_text("(B) 2026-09-01 Other task\n")
    store = PriorityStore(path)
    store.add_email("m1", "inbox", "Hello", "a@x.test", priority=None, note="first")
    store.set_priority(store.find_by_message_id("m1").key, "A")
    assert store.set_note("m1", "second  thoughts", "Hello")
    lines = path.read_text().splitlines()
    assert lines[0] == "(B) 2026-09-01 Other task" and len(lines) == 2
    assert lines[1].startswith("(A) ") and "Hello — second thoughts @email" in lines[1]
    assert store.set_note("m1", "", "Hello")
    assert "Hello @email" in path.read_text()
    assert not store.set_note("nope", "x")


def test_adding_again_keeps_priority_and_note(tmp_path):
    store = PriorityStore(tmp_path / "todo.txt")
    store.add_email("m1", "inbox", "Hello", "a@x.test", priority=None, note="keep me")
    store.set_priority(store.find_by_message_id("m1").key, "A")
    store.add_email("m1", "inbox", "Hello", "a@x.test", priority=None)
    entry = store.find_by_message_id("m1")
    assert entry.priority == "A" and entry.description == "Hello — keep me"
    assert len(store.list_entries()) == 1


# -- in the app -----------------------------------------------------------------

def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_p_on_a_prioritised_email_edits_its_note(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)
        await pilot.press(*"call finance", "enter")
        await settle(app, pilot)
        store = PriorityStore(tmp_path / "todo.txt")
        store.set_priority(store.find_by_message_id("m1").key, "A")

        await pilot.press("p")  # again: the note, filled in, to edit
        await settle(app, pilot)
        assert isinstance(app.screen, AddNoteScreen)
        field = app.screen.query_one("#note-text")
        assert field.value == "call finance"
        field.value = "call finance on Monday"
        await pilot.press("enter")
        await settle(app, pilot)
    store = PriorityStore(tmp_path / "todo.txt")
    entries = store.list_entries(email_only=True)
    assert len(entries) == 1
    assert entries[0].priority == "A" and entries[0].description == "Q3 budget review — call finance on Monday"


async def test_capital_p_on_a_prioritised_email_changes_nothing(tmp_path):
    app = make_app(tmp_path)
    seen = []
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("P")
        await settle(app, pilot)
        store = PriorityStore(tmp_path / "todo.txt")
        store.set_priority(store.find_by_message_id("m1").key, "B")
        original_notify = app.notify
        app.notify = lambda msg, **kw: (seen.append(msg), original_notify(msg, **kw))
        await pilot.press("P")
        await settle(app, pilot)
    assert PriorityStore(tmp_path / "todo.txt").find_by_message_id("m1").priority == "B"
    assert any("Already on the priority list (priority B)" in m for m in seen)
