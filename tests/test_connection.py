"""Surviving idle periods: keepalive sockets, idle-session replacement,
one retry for reads, and a slow server not freezing the UI."""
from __future__ import annotations

import socket
import threading
from types import SimpleNamespace

import pytest
from exchangelib.errors import ErrorTimeoutExpired, UnauthorizedError
from exchangelib.protocol import BaseProtocol

from conftest import fake_protocol
from ewstui import auth, connection
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import CalendarClient, MailClient, retry_on_dead_connection
from ewstui.widgets.message_table import MessageTable

# -- 1. TCP keepalive ----------------------------------------------------------------


def test_keepalive_socket_options():
    opts = connection.keepalive_socket_options()
    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in opts
    assert (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) in opts  # urllib3's default kept
    idle_opt = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
    assert (socket.IPPROTO_TCP, idle_opt, connection.KEEPALIVE_IDLE) in opts


@pytest.mark.parametrize("adapter_cls", [connection.KeepAliveHTTPAdapter, connection.KeepAliveNoVerifyHTTPAdapter])
def test_adapters_open_keepalive_sockets(adapter_cls):
    adapter = adapter_cls()
    assert adapter.poolmanager.connection_pool_kw["socket_options"] == connection.keepalive_socket_options()


def test_keepalive_sockets_really_get_the_option():
    """End to end at the socket level: a connection made through the
    adapter's pool has SO_KEEPALIVE set by the OS."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        pool = connection.KeepAliveHTTPAdapter().poolmanager.connection_from_url(f"http://127.0.0.1:{port}")
        conn = pool._new_conn()  # noqa: SLF001
        conn.connect()
        assert conn.sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
        conn.close()
    finally:
        server.close()


@pytest.mark.parametrize(
    ("verify", "expected"), [(True, connection.KeepAliveHTTPAdapter), (False, connection.KeepAliveNoVerifyHTTPAdapter)]
)
def test_get_account_installs_adapter_and_hardens(monkeypatch, verify, expected):
    monkeypatch.setattr(BaseProtocol, "HTTP_ADAPTER_CLS", BaseProtocol.HTTP_ADAPTER_CLS)
    account = SimpleNamespace(protocol=fake_protocol())
    monkeypatch.setattr(auth, "_get_account", lambda cfg: account)
    cfg = config_from_args(["--email", "me@x.test", "--auth", "ntlm", *([] if verify else ["--no-verify-ssl"])])
    assert auth.get_account(cfg) is account
    assert BaseProtocol.HTTP_ADAPTER_CLS is expected
    assert account.protocol.TIMEOUT == connection.REQUEST_TIMEOUT


# -- 2. idle sessions are replaced ------------------------------------------------------


class Pool:
    """Records what harden() does with sessions."""

    def __init__(self):
        self.pool = [SimpleNamespace(name="s1", usage_count=0)]
        self.closed, self.created = [], 0

    def protocol(self):
        def create():
            self.created += 1
            return SimpleNamespace(name=f"new{self.created}", usage_count=0)

        return fake_protocol(
            get_session=lambda: self.pool.pop(),
            release_session=lambda s: self.pool.append(s),
            close_session=lambda s: self.closed.append(s.name),
            create_session=create,
        )


def test_idle_sessions_are_kept_but_replaced_after_sleep():
    wall, mono = [1000.0], [500.0]
    pool = Pool()
    protocol = pool.protocol()
    connection.harden(protocol, clock=lambda: wall[0], mono=lambda: mono[0])
    assert protocol.TIMEOUT == connection.REQUEST_TIMEOUT

    s = protocol.get_session()  # never used before: trusted
    assert s.name == "s1"
    protocol.release_session(s)

    wall[0] += 3600
    mono[0] += 3600  # an hour idle but awake: keepalive held it — keep it (no new NTLM login)
    s = protocol.get_session()
    assert s.name == "s1" and pool.closed == []
    protocol.release_session(s)

    wall[0] += 3600
    mono[0] += 60  # wall clock ran on while monotonic stopped: the Mac slept ~59 min
    s = protocol.get_session()
    assert s.name == "new1" and pool.closed == ["s1"] and s.usage_count == 1


def test_harden_is_idempotent():
    protocol = fake_protocol()
    connection.harden(protocol)
    wrapped = protocol.get_session
    connection.harden(protocol)
    assert protocol.get_session is wrapped


# -- 3. reads retry once, writes never ---------------------------------------------------


def test_read_retries_once_on_a_dead_connection():
    calls = []

    @retry_on_dead_connection
    def read():
        calls.append(1)
        if len(calls) == 1:
            raise ErrorTimeoutExpired("Reraised from ReadTimeout(...)")
        return "ok"

    assert read() == "ok" and len(calls) == 2


def test_read_gives_up_after_one_retry_and_ignores_other_errors():
    @retry_on_dead_connection
    def always_dead():
        raise ErrorTimeoutExpired("dead")

    with pytest.raises(ErrorTimeoutExpired):
        always_dead()

    calls = []

    @retry_on_dead_connection
    def unauthorized():
        calls.append(1)
        raise UnauthorizedError("no")

    with pytest.raises(UnauthorizedError):
        unauthorized()
    assert len(calls) == 1


def test_only_read_methods_retry():
    reads = {"list_folders", "list_messages", "get_message", "list_attachments", "save_attachment"}
    writes = {"send_mail", "reply", "move_message", "delete_message", "archive_message", "mark_read"}
    assert all(hasattr(getattr(MailClient, m), "__wrapped__") for m in reads)
    assert not any(hasattr(getattr(MailClient, m), "__wrapped__") for m in writes)
    assert hasattr(CalendarClient.rooms_day, "__wrapped__")
    assert not hasattr(CalendarClient.create_event, "__wrapped__")


# -- 4. a slow server doesn't freeze the UI ------------------------------------------------


def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt")])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


def preview_text(app) -> str:
    return str(app.query_one("#preview-body").render())


async def test_slow_message_fetch_keeps_the_ui_responsive(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    release = threading.Event()
    original = app.mail_client.get_message

    def stalled(folder_id, message_id):
        if message_id == "m2":
            release.wait(5)  # a stalled connection
        return original(folder_id, message_id)

    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        monkeypatch.setattr(app.mail_client, "get_message", stalled)
        table = app.query_one("#messages", MessageTable)

        await pilot.press("j")  # m2: fetch stalls
        await pilot.pause()
        assert "Loading" in preview_text(app)
        await pilot.press("j")  # the UI still reacts while m2 is stuck
        await pilot.pause()
        assert table._current_message_id() == "m3"
        await pilot.pause(0.2)  # m3's fetch (not stalled) completes; m2's is still stuck
        assert "IT maintenance window" in preview_text(app)  # m3 shown

        release.set()  # the stale m2 answer arrives late...
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "IT maintenance window" in preview_text(app)  # ...and is ignored


def _timeout(*args, **kwargs):
    raise ErrorTimeoutExpired("Reraised from ReadTimeout(read timeout=30)")


async def test_reply_timeout_is_a_notice_not_a_crash(tmp_path, monkeypatch):
    """The reported crash: r -> get_message timed out on the UI thread."""
    app = make_app(tmp_path)
    seen = []
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        app._preview_cache = None  # not loaded in the reading pane: must fetch
        monkeypatch.setattr(app.mail_client, "get_message", _timeout)
        original = app.notify
        app.notify = lambda msg, **kw: (seen.append(msg), original(msg, **kw))
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.is_running
    assert any("Reply failed" in m for m in seen)


async def test_reply_and_priority_reuse_the_loaded_message(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()  # m1 loaded in the reading pane
        await pilot.pause()
        monkeypatch.setattr(app.mail_client, "get_message", _timeout)  # any fetch would fail
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        from ewstui.screens import ComposeScreen

        assert isinstance(app.screen, ComposeScreen)  # opened without a request
    from ewstui.priority_store import PriorityStore

    (entry,) = PriorityStore(tmp_path / "p.todo.txt").list_entries(email_only=True)
    assert entry.message_id == "m1" and entry.internet_id == "<m1@demo.corp.example>"


async def test_toggle_read_uses_the_list_and_keeps_the_cursor(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("j")  # m2 (unread)
        await app.workers.wait_for_complete()
        inbox = app.mail_client._messages["inbox"]

        def mark_read(folder_id, message_id, read=True):  # like the live client: no lookup
            next(m for m in inbox if m.id == message_id).is_read = read

        calls = []
        monkeypatch.setattr(app.mail_client, "mark_read", mark_read)
        monkeypatch.setattr(app.mail_client, "get_message", lambda *a: calls.append(a) or _timeout())
        monkeypatch.setattr(app, "_load_preview", lambda *a: None)  # the reload's preview fetch isn't the toggle's
        await pilot.press("space")
        await pilot.pause()
        assert calls == []  # read state came from the list
        assert next(m for m in app.mail_client._messages["inbox"] if m.id == "m2").is_read
        assert app.query_one("#messages", MessageTable)._current_message_id() == "m2"


async def test_ui_thread_errors_are_notices(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    seen = []
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        monkeypatch.setattr(app.mail_client, "mark_read", _timeout)
        original = app.notify
        app.notify = lambda msg, **kw: (seen.append(msg), original(msg, **kw))
        await pilot.press("space")
        await pilot.pause()
        assert app.is_running
    assert any("Marking read/unread failed" in m for m in seen)


async def test_failed_fetch_clears_loading_and_reports(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    seen = []
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        original_notify = app.notify
        app.notify = lambda msg, **kw: (seen.append(msg), original_notify(msg, **kw))

        def dead(folder_id, message_id):
            raise ErrorTimeoutExpired("Reraised from ReadTimeout(...)")

        monkeypatch.setattr(app.mail_client, "get_message", dead)
        await pilot.press("j")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "Loading" not in preview_text(app)
    assert any("Failed to load message" in m for m in seen)
