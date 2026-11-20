"""Delete / archive / move run in the background: the email leaves the list
at once and you can go on working while Exchange catches up. Attachments
are listed and downloaded off the UI thread too. And the live client
never downloads the raw message (`mime_content`) for any of this."""
from __future__ import annotations

import io
import threading
from types import SimpleNamespace

import pytest
from exchangelib import FileAttachment
from exchangelib.folders import Inbox
from exchangelib.queryset import QuerySet
from exchangelib.version import EXCHANGE_2016, Version

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import MailClient
from ewstui.widgets.chrome import StatusBar
from ewstui.widgets.message_table import MessageTable


class SlowMail(DemoMailClient):
    """Deletes wait until `release` is set (a slow server), or fail."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()
        self.fail = False

    def delete_message(self, folder_id, message_id):
        assert self.release.wait(10), "test never released the delete"
        if self.fail:
            raise RuntimeError("server said no")
        return super().delete_message(folder_id, message_id)


@pytest.fixture
def mail() -> SlowMail:
    return SlowMail()


@pytest.fixture
def app(tmp_path, mail) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    return EwstuiApp(mail, DemoCalendarClient(), cfg)


def listed(app) -> list[str]:
    table = app.query_one("#messages", MessageTable)
    return [m.id for m in table.shown_messages()]


def notices(app) -> list[str]:
    return [n.message for n in app._notifications]


async def test_deleted_email_leaves_the_list_before_the_server_answers(app, mail):
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        await pilot.press("d")  # m1, while the server is still busy
        await pilot.pause()
        assert "m1" not in listed(app)
        assert table._current_message_id() == "m2"  # on to the next one
        assert "deleting 1" in app.query_one(StatusBar).activity

        app._refresh_mail(manual=False)  # a refresh racing the delete
        await pilot.pause(0.2)
        assert "m1" not in listed(app)

        mail.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "m1" not in listed(app)
        assert app.query_one(StatusBar).activity == ""
        assert len(app.undo_stack) == 1
        assert any("Moved to Deleted Items" in n for n in notices(app))


async def test_failed_delete_brings_the_email_back(app, mail):
    mail.fail = True
    mail.release.set()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()  # the refresh after it
        await pilot.pause()
        assert "m1" in listed(app)
        assert any("Delete failed: server said no" in n for n in notices(app))
        assert app.undo_stack == []


async def test_undo_waits_for_pending_actions(app, mail):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        assert any("Still working on 1 message" in n for n in notices(app))
        mail.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("u")  # now it undoes the delete
        await pilot.pause()
        assert "m1" in [m.id for m in mail.list_messages("inbox")]


async def test_deleting_several_in_a_row(app, mail):
    mail.release.set()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d", "d")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert [m.id for m in mail.list_messages("trash")] == ["m1", "m2"]
        assert listed(app)[0] == "m3"


# -- the live client: what it asks Exchange for ------------------------------


@pytest.fixture
def client(monkeypatch):
    """A MailClient over a real exchangelib Inbox (no network): records the
    fields each GetItem asks for, and what got moved where."""
    account = SimpleNamespace(version=Version(build=EXCHANGE_2016), trash=SimpleNamespace(id="trash"))
    inbox = Inbox(root=SimpleNamespace(account=account), id="inbox", changekey="c")
    calls = SimpleNamespace(fields=[], moved=[])

    class Item:
        id = "new-id"
        attachments = []

        def move(self, dest):
            calls.moved.append(dest)

    def fake_get(qs, *args, **kwargs):
        calls.fields.append(sorted(fp.field.name for fp in qs.only_fields))
        return Item()

    monkeypatch.setattr(QuerySet, "get", fake_get)
    c = MailClient(account)
    monkeypatch.setattr(c, "_folder_by_id", lambda folder_id: inbox)
    c.calls = calls
    return c


def test_get_item_skips_the_raw_message(client):
    client._get_item(client._folder_by_id("inbox"), "m1")
    fields = client.calls.fields[0]
    assert "mime_content" not in fields
    assert {"subject", "body", "attachments", "to_recipients"} <= set(fields)


@pytest.mark.parametrize("call", [
    lambda c: c.delete_message("inbox", "m1"),
    lambda c: c.move_message("inbox", "m1", "inbox"),
    lambda c: c.mark_read("inbox", "m1"),
    lambda c: c.list_attachments("inbox", "m1"),
])
def test_actions_fetch_only_what_they_need(client, call):
    try:
        call(client)
    except AttributeError:
        pass  # mark_read's fake item can't save; the fetch is what's checked
    assert client.calls.fields and len(client.calls.fields[0]) == 1


def test_archive_folder_looked_up_once(client, monkeypatch):
    found = []
    archive = SimpleNamespace(id="archive")
    monkeypatch.setattr(client, "_find_archive_folder", lambda: found.append(1) or archive)
    client.archive_message("inbox", "m1")
    client.archive_message("inbox", "m2")
    assert found == [1]
    assert client.calls.moved == [archive, archive]


class StreamedFile(FileAttachment):
    """A file attachment whose content comes from memory, not GetAttachment."""

    DATA = b"x" * 600_000

    @property
    def fp(self):
        return io.BytesIO(self.DATA)


def test_save_attachment_streams_with_progress(client, monkeypatch, tmp_path):
    att = StreamedFile(name="big.bin", size=600_000)
    monkeypatch.setattr(client, "_get_item", lambda *a: SimpleNamespace(attachments=[att]))
    seen = []
    path = client.save_attachment("inbox", "m1", "big.bin", tmp_path, progress=lambda d, t: seen.append((d, t)))
    assert path.read_bytes() == StreamedFile.DATA
    assert seen[-1] == (600_000, 600_000) and len(seen) == 3  # 256 KB chunks


def test_failed_download_leaves_no_partial_file(client, monkeypatch, tmp_path):
    class Broken(io.RawIOBase):
        def readinto(self, b):
            raise ConnectionError("dropped")

    class BrokenFile(FileAttachment):
        @property
        def fp(self):
            return Broken()

    monkeypatch.setattr(client, "_get_item", lambda *a: SimpleNamespace(attachments=[BrokenFile(name="a.bin", size=10)]))
    with pytest.raises(ConnectionError):
        MailClient.save_attachment.__wrapped__(client, "inbox", "m1", "a.bin", tmp_path)
    assert list(tmp_path.iterdir()) == []


# -- sending in the background -------------------------------------------------


class SlowSend(DemoMailClient):
    """send_mail waits for `release` (a stalled server), or fails."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()
        self.fail = False

    def send_mail(self, *args, **kwargs):
        assert self.release.wait(10), "test never released the send"
        if self.fail:
            raise TimeoutError("Read timed out")
        return super().send_mail(*args, **kwargs)


async def write_email(app, pilot):
    from textual.widgets import Input, TextArea

    await pilot.press("w")
    await pilot.pause()
    app.screen.query_one("#compose-to").text = "you@example.test"
    app.screen.query_one("#compose-subject", Input).value = "Numbers"
    app.screen.query_one("#compose-body", TextArea).text = "Here they are"
    await pilot.press("ctrl+s")
    await pilot.pause()


async def test_sending_closes_the_compose_view_at_once(tmp_path):
    from ewstui.screens import ComposeScreen

    mail = SlowSend()
    app = EwstuiApp(mail, DemoCalendarClient(), config_from_args(["--demo", "--priority-file", str(tmp_path / "t.txt")]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await write_email(app, pilot)
        assert not isinstance(app.screen, ComposeScreen)  # back to work while it sends
        assert "sending 1" in app.query_one(StatusBar).activity
        await pilot.press("j")  # the app responds meanwhile
        await pilot.pause()
        mail.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert mail.sent[-1]["subject"] == "Numbers"
        assert app.query_one(StatusBar).activity == ""
        assert "Message sent" in notices(app)


async def test_failed_send_reopens_the_email(tmp_path):
    from textual.widgets import Input

    from ewstui.screens import ComposeScreen

    mail = SlowSend()
    mail.fail = True
    mail.release.set()
    app = EwstuiApp(mail, DemoCalendarClient(), config_from_args(["--demo", "--priority-file", str(tmp_path / "t.txt")]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await write_email(app, pilot)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)
        assert app.screen.query_one("#compose-subject", Input).value == "Numbers"
        assert any("Send failed: Read timed out — draft kept" in n for n in notices(app))


async def test_quitting_while_sending_asks_first(tmp_path):
    mail = SlowSend()
    app = EwstuiApp(mail, DemoCalendarClient(), config_from_args(["--demo", "--priority-file", str(tmp_path / "t.txt")]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await write_email(app, pilot)
        await pilot.press("q")
        await pilot.pause()
        assert app.is_running
        assert any("Still sending 1 email" in n for n in notices(app))
        mail.release.set()
        await app.workers.wait_for_complete()
