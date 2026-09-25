"""Connection indicator in the status bar, and a pool of several
connections so one stalled request doesn't block the rest."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from exchangelib import NTLM, Configuration, Credentials
from exchangelib.protocol import Protocol

from conftest import fake_protocol
from ewstui import connection
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.connection import ConnectionMonitor
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.widgets.chrome import StatusBar

# -- the monitor ----------------------------------------------------------------------


def test_monitor_states():
    now = [100.0]
    m = ConnectionMonitor(mono=lambda: now[0])
    assert m.state() == ("● connected", "ok")
    session = object()
    m.started(session)
    assert m.state() == ("● working", "busy")
    now[0] += 12
    assert m.state() == ("◐ waiting for server 12s", "slow")
    m.failed(session)
    assert m.state() == ("✕ connection problem (0s ago)", "error")
    now[0] += 125
    assert m.state() == ("✕ connection problem (2m ago)", "error")
    other = object()
    m.started(other)
    m.succeeded(other)
    assert m.state() == ("● connected", "ok")  # a success clears the problem


def test_hooks_feed_the_monitor_and_retire_is_a_failure():
    m = ConnectionMonitor()
    sessions = iter([SimpleNamespace(usage_count=0) for _ in range(3)])
    protocol = fake_protocol(get_session=lambda: next(sessions), create_session=lambda: SimpleNamespace(usage_count=0))
    released = []
    protocol.release_session = released.append
    retired = []

    def retire(session):  # like exchangelib: close it, put a fresh one in the pool
        retired.append(session)
        protocol.release_session(SimpleNamespace(usage_count=0))

    protocol.retire_session = retire
    connection.harden(protocol, monitor=m)
    assert m.active

    s = protocol.get_session()
    assert m.state()[1] == "busy"
    protocol.release_session(s)
    assert m.state()[1] == "ok"

    s = protocol.get_session()
    protocol.retire_session(s)  # timeout: exchangelib retires the session
    assert retired == [s]
    assert m.state()[1] == "error"  # the fresh replacement session doesn't count as a success


# -- several connections --------------------------------------------------------------


def test_pool_allows_parallel_requests_on_a_real_exchangelib_protocol():
    """With exchangelib's default pool of 1, a second get_session() blocks
    until the first is released — a stalled request froze everything."""
    config = Configuration(
        service_endpoint="https://mail.example.test/EWS/Exchange.asmx",
        credentials=Credentials("PROD\\user", "pw"),
        auth_type=NTLM,
    )
    protocol = Protocol(config=config)
    connection.harden(protocol, monitor=ConnectionMonitor())
    assert protocol._session_pool_maxsize == connection.POOL_SIZE

    got = []
    first = protocol.get_session()  # e.g. the background refresh, stalled on the server

    def second():
        got.append(protocol.get_session())  # e.g. the preview of the next email

    t = threading.Thread(target=second, daemon=True)
    t.start()
    t.join(timeout=2)
    assert got, "second request had to wait for the first"
    assert got[0] is not first
    protocol.release_session(first)
    protocol.release_session(got[0])


# -- status bar -----------------------------------------------------------------------


def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt")])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


async def test_indicator_hidden_in_demo(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(1.2)
        text = app.query_one(StatusBar).render().plain
        assert "connected" not in text and "waiting" not in text


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        (lambda m, s: None, "● connected"),
        (lambda m, s: m.started(s), "● working"),
        (lambda m, s: (m.started(s), m.failed(s)), "✕ connection problem"),
    ],
)
async def test_indicator_follows_the_connection(tmp_path, setup, expected):
    app = make_app(tmp_path)
    connection.MONITOR.active = True  # as after logging in to a real mailbox
    setup(connection.MONITOR, object())
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(1.2)  # the once-a-second update
        assert expected in app.query_one(StatusBar).render().plain


async def test_slow_request_shows_seconds_waiting(tmp_path):
    now = [0.0]
    connection.MONITOR = ConnectionMonitor(mono=lambda: now[0])
    connection.MONITOR.active = True
    connection.MONITOR.started(object())
    now[0] = 17
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(1.2)
        assert "◐ waiting for server 17s" in app.query_one(StatusBar).render().plain
