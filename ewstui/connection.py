"""
Keep EWS connections healthy across idle periods.

The problem: exchangelib pools HTTPS connections and reuses them. After a
while without traffic, something between us and Exchange (an F5 gateway,
a firewall/NAT, or the Mac sleeping) drops the connection *silently* — no
FIN/RST reaches us — so the next request is written into the void and
waits out the whole read timeout (exchangelib's default: 120 s). When that
request runs on the UI thread, the app freezes.

The fix, in layers:
  1. TCP keepalive on every connection: the OS pings an idle connection
     every KEEPALIVE_IDLE seconds, so middleboxes don't consider it idle,
     and a connection that did die is detected and closed by the OS
     (urllib3 then reconnects instead of hanging).
  2. Sessions are replaced before use only if the Mac *slept* since they
     were last used (keepalive can't survive that). Otherwise they're kept:
     with NTLM every new connection needs a fresh login handshake, and the
     F5 in front of Exchange intermittently stalls on that handshake's last
     leg — so fewer new connections means fewer stalls. (An earlier version
     replaced any session idle for 2 minutes, which forced exactly those
     handshakes.)
  3. A shorter request timeout (REQUEST_TIMEOUT) so a stall costs seconds,
     not minutes; read-only client calls retry once on a fresh connection
     (see ews_client.retry_on_dead_connection).
"""
from __future__ import annotations

import logging
import socket
import threading
import time

import requests
from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter
from urllib3.connection import HTTPConnection

log = logging.getLogger(__name__)

KEEPALIVE_IDLE = 30  # seconds of silence before the first keepalive probe
KEEPALIVE_INTERVAL = 10  # seconds between probes
KEEPALIVE_COUNT = 3  # unanswered probes before the OS declares the connection dead
# If wall-clock time since a session was last used exceeds monotonic time
# (which stops while the Mac sleeps) by more than this, the Mac slept.
SLEEP_DETECT = 30  # seconds
REQUEST_TIMEOUT = 30  # seconds per EWS request (exchangelib default: 120)
# Parallel connections. exchangelib's default is 1, so every request queued
# behind whichever one was running: one stalled request (say, the background
# refresh) left the preview stuck on "Loading…" too.
POOL_SIZE = 3
SLOW_AFTER = 3  # seconds before a running request shows as "waiting for server"


class ConnectionMonitor:
    """What the connection is doing, for the status bar. Fed by the session
    pool hooks in harden(): a request starts when a session is handed out,
    succeeds when it's given back, fails when exchangelib retires the
    session (timeouts, resets). Thread-safe: requests run in workers."""

    def __init__(self, mono=time.monotonic):
        self._mono = mono
        self._lock = threading.Lock()
        self._running: dict[int, float] = {}  # id(session) -> start time
        self.last_ok: float | None = None
        self.last_error: float | None = None
        self.active = False  # set by harden(); stays False in --demo

    def started(self, session) -> None:
        with self._lock:
            self._running[id(session)] = self._mono()

    def succeeded(self, session) -> None:
        with self._lock:
            if self._running.pop(id(session), None) is not None:
                self.last_ok = self._mono()

    def failed(self, session) -> None:
        with self._lock:
            self._running.pop(id(session), None)
            self.last_error = self._mono()

    def state(self) -> tuple[str, str]:
        """(text, role) — role is "ok", "busy", "slow" or "error"."""
        now = self._mono()
        with self._lock:
            oldest = min(self._running.values(), default=None)
        if oldest is not None:
            waited = now - oldest
            if waited >= SLOW_AFTER:
                return f"◐ waiting for server {waited:.0f}s", "slow"
            return "● working", "busy"
        if self.last_error is not None and (self.last_ok is None or self.last_error > self.last_ok):
            ago = now - self.last_error
            when = f"{ago:.0f}s ago" if ago < 60 else f"{ago / 60:.0f}m ago"
            return f"✕ connection problem ({when})", "error"
        return "● connected", "ok"


MONITOR = ConnectionMonitor()  # one mailbox per process


def keepalive_socket_options() -> list[tuple[int, int, int]]:
    options = list(HTTPConnection.default_socket_options)  # keeps TCP_NODELAY
    options.append((socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1))
    # Idle time before probing: TCP_KEEPIDLE on Linux, TCP_KEEPALIVE on macOS.
    idle_opt = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
    if idle_opt is not None:
        options.append((socket.IPPROTO_TCP, idle_opt, KEEPALIVE_IDLE))
    if hasattr(socket, "TCP_KEEPINTVL"):
        options.append((socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, KEEPALIVE_INTERVAL))
    if hasattr(socket, "TCP_KEEPCNT"):
        options.append((socket.IPPROTO_TCP, socket.TCP_KEEPCNT, KEEPALIVE_COUNT))
    return options


class KeepAliveMixin:
    def init_poolmanager(self, *args, **kwargs):
        kwargs["socket_options"] = keepalive_socket_options()
        super().init_poolmanager(*args, **kwargs)


class KeepAliveHTTPAdapter(KeepAliveMixin, requests.adapters.HTTPAdapter):
    pass


class KeepAliveNoVerifyHTTPAdapter(KeepAliveMixin, NoVerifyHTTPAdapter):
    pass


def install_keepalive_adapter(verify_ssl: bool = True) -> None:
    """Make every new exchangelib session use keepalive sockets. Call
    before the first request (sessions are created lazily)."""
    BaseProtocol.HTTP_ADAPTER_CLS = KeepAliveHTTPAdapter if verify_ssl else KeepAliveNoVerifyHTTPAdapter


def harden(protocol, clock=time.time, mono=time.monotonic, monitor: ConnectionMonitor | None = None) -> None:
    """Shorter timeout, and replace pooled sessions after the Mac slept.

    Wraps this protocol instance's get_session/release_session: each
    session is stamped (wall clock and monotonic clock) when released. When
    it's handed out again, wall-clock time elapsed minus monotonic time
    elapsed is the time spent asleep (on macOS time.monotonic stops during
    sleep); if that's over SLEEP_DETECT the session's connection can't have
    survived, so it's closed and replaced. Idle-but-awake sessions are kept:
    keepalive holds their connection (and its NTLM login) open.
    """
    if getattr(protocol, "_ewstui_hardened", False):
        return
    monitor = monitor or MONITOR
    protocol.TIMEOUT = REQUEST_TIMEOUT
    # No public setter; Configuration(max_connections=...) sets the same.
    protocol._session_pool_maxsize = max(getattr(protocol, "_session_pool_maxsize", 1) or 1, POOL_SIZE)
    get_session, release_session = protocol.get_session, protocol.release_session
    retire_session = protocol.retire_session

    def fresh_get_session():
        session = get_session()
        stamp = getattr(session, "ewstui_last_used", None)
        if stamp is not None:
            asleep = (clock() - stamp[0]) - (mono() - stamp[1])
            if asleep > SLEEP_DETECT:
                log.info("slept for %.0fs since this session was used; reconnecting", asleep)
                protocol.close_session(session)
                session = protocol.create_session()
                session.usage_count = 1
        monitor.started(session)
        return session

    def stamped_release_session(session):
        monitor.succeeded(session)  # no-op for a fresh session put in by retire
        session.ewstui_last_used = (clock(), mono())
        release_session(session)

    def monitored_retire_session(session):
        monitor.failed(session)
        retire_session(session)

    protocol.get_session = fresh_get_session
    protocol.release_session = stamped_release_session
    protocol.retire_session = monitored_retire_session
    protocol._ewstui_hardened = True
    monitor.active = True
