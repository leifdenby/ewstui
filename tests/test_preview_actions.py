"""The email actions work while reading it (focus in the reading pane), on
the email shown: P / p priority, m move, A archive, d delete, r / R reply,
v attachments."""
from __future__ import annotations

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.folder_picker import MoveToFolderScreen
from ewstui.priority_store import PriorityStore
from ewstui.screens import AddNoteScreen, AttachmentListScreen, ComposeScreen
from ewstui.widgets.preview import PreviewPane


def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


def inbox(app) -> list[str]:
    return [m.id for m in app.mail_client.list_messages("inbox")]


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def read(app, pilot, downs: int = 1):
    """Open the email `downs` rows down and put focus in the reading pane."""
    await pilot.pause()
    await pilot.press(*["j"] * downs, "o")
    await settle(app, pilot)
    assert isinstance(app.focused, PreviewPane)


async def test_p_prioritises_the_email_being_read(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await read(app, pilot)  # m2
        await pilot.press("P")
        await settle(app, pilot)
    entries = PriorityStore(tmp_path / "todo.txt").list_entries(email_only=True)
    assert [e.message_id for e in entries] == ["m2"]


async def test_lowercase_p_asks_for_a_note(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await read(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)
        assert isinstance(app.screen, AddNoteScreen)


async def test_m_moves_the_email_being_read(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await read(app, pilot)
        await pilot.press("m")
        await settle(app, pilot)
        assert isinstance(app.screen, MoveToFolderScreen)
        await pilot.press(*"draft", "enter")
        await settle(app, pilot)
        assert "m2" not in inbox(app) and "m2" in [m.id for m in app.mail_client.list_messages("drafts")]
        assert isinstance(app.focused, PreviewPane)  # still reading: the next email


async def test_archive_and_delete_from_the_reading_pane(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await read(app, pilot)
        await pilot.press("A")
        await settle(app, pilot)
        assert "m2" not in inbox(app)
        await pilot.press("d")
        await settle(app, pilot)
        assert len(inbox(app)) == 2


async def test_reply_and_attachments_from_the_reading_pane(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await read(app, pilot, downs=0)  # m1 has an attachment
        await pilot.press("v")
        await settle(app, pilot)
        assert isinstance(app.screen, AttachmentListScreen)
        await pilot.press("escape", "r")
        await settle(app, pilot)
        assert isinstance(app.screen, ComposeScreen)


async def test_space_still_pages_the_email(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await read(app, pilot)
        read_before = {m.id: m.is_read for m in app.mail_client.list_messages("inbox")}
        await pilot.press("space")
        await settle(app, pilot)
        assert {m.id: m.is_read for m in app.mail_client.list_messages("inbox")} == read_before
