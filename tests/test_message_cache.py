"""In-memory message cache and prefetching of the next messages."""
from __future__ import annotations

import dataclasses
import threading
from types import SimpleNamespace

import pytest

from ewstui import app as app_module
from ewstui import connection
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.connection import ConnectionMonitor
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.message_cache import MessageCache
from ewstui.widgets.chrome import StatusBar

# -- the cache ----------------------------------------------------------------------


def detail(changekey="c1"):
    return SimpleNamespace(changekey=changekey)


def test_cache_returns_only_up_to_date_entries():
    cache = MessageCache()
    cache.put("m1", detail("c1"))
    assert cache.get("m1", "c1") is not None
    assert cache.get("m1") is not None  # no change key to compare: trust it
    assert cache.get("m1", "c2") is None  # changed on the server
    assert "m1" not in cache  # and dropped


def test_cache_evicts_the_least_recently_used():
    cache = MessageCache(size=2)
    cache.put("a", detail())
    cache.put("b", detail())
    cache.get("a")  # a is now more recent than b
    cache.put("c", detail())
    assert "a" in cache and "c" in cache and "b" not in cache


# -- in the app ------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def quick_prefetch(monkeypatch):
    monkeypatch.setattr(app_module, "PREFETCH_DELAY", 0.05)


def make_app(tmp_path):
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt")])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


def count_fetches(app, monkeypatch):
    """Call before run_test, so the first message's fetch is counted too."""
    calls = []
    original = app.mail_client.get_message

    def counting(folder_id, message_id):
        calls.append(message_id)
        return original(folder_id, message_id)

    monkeypatch.setattr(app.mail_client, "get_message", counting)
    return calls


async def settle(app, pilot, wait=0.2):
    await pilot.pause(wait)
    await app.workers.wait_for_complete()
    await pilot.pause()


def preview_text(app) -> str:
    return str(app.query_one("#preview-body").render())


async def test_revisiting_a_message_needs_no_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "PREFETCH_COUNT", 0)  # isolate the cache from prefetching
    app = make_app(tmp_path)
    calls = count_fetches(app, monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("j")
        await settle(app, pilot)
        await pilot.press("k", "j", "k")
        await settle(app, pilot)
        assert sorted(calls) == ["m1", "m2"]  # each fetched once


async def test_next_messages_are_prefetched_and_shown_at_once(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    calls = count_fetches(app, monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(app, pilot)  # m1 shown, then m2 + m3 prefetched
        assert calls == ["m1", "m2", "m3"]
        await pilot.press("j")  # m2: already cached
        await pilot.pause()
        assert "Loading" not in preview_text(app) and "Re: EWS bridge project" in preview_text(app)
        await settle(app, pilot)
        assert calls == ["m1", "m2", "m3", "m4"]  # only m4 was new (m3 already cached)


async def test_a_changed_message_is_fetched_again(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "PREFETCH_COUNT", 0)
    app = make_app(tmp_path)
    calls = count_fetches(app, monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(app, pilot)
        # A new version on the server (a new object, as a real fetch would return).
        inbox = app.mail_client._messages["inbox"]
        inbox[0] = dataclasses.replace(inbox[0], changekey="edited-elsewhere")
        app.select_folder("inbox")  # the list now carries the new change key
        await settle(app, pilot)
        assert calls == ["m1", "m1"]


async def test_prefetching_shows_in_the_status_bar_not_as_waiting(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    release = threading.Event()
    original = app.mail_client.get_message

    def slow_prefetch(folder_id, message_id):
        if message_id != "m1":
            release.wait(5)
        return original(folder_id, message_id)

    connection.MONITOR.active = True  # as with a real mailbox
    async with app.run_test(size=(160, 40)) as pilot:
        monkeypatch.setattr(app.mail_client, "get_message", slow_prefetch)
        await pilot.pause(1.3)  # m1 shown, prefetch of m2 stuck; status bar updated
        text = app.query_one(StatusBar).render().plain
        assert "⇣ prefetching 2" in text
        assert "waiting for server" not in text
        release.set()
        await settle(app, pilot)
        assert "prefetching" not in app.query_one(StatusBar).render().plain


# -- background requests and the connection indicator ----------------------------------------


def test_background_requests_are_not_waiting_or_problems():
    now = [0.0]
    m = ConnectionMonitor(mono=lambda: now[0])
    session = object()
    with connection.background():
        m.started(session)
    now[0] = 20
    assert m.state() == ("● connected", "ok")  # a slow prefetch isn't "waiting for server"
    assert m.background_count() == 1
    m.failed(session)
    assert m.state()[1] == "ok"  # nor is a failed one a connection problem
    assert m.background_count() == 0


def test_foreground_requests_still_count():
    now = [0.0]
    m = ConnectionMonitor(mono=lambda: now[0])
    m.started(object())
    now[0] = 20
    assert m.state()[1] == "slow"
