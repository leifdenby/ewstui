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
  2. Sessions idle for longer than IDLE_RESET seconds (wall clock, so time
     asleep counts) are replaced with fresh ones before use.
  3. A shorter request timeout (REQUEST_TIMEOUT) so a dead connection costs
     seconds, not minutes; read-only client calls retry once on a fresh
     connection (see ews_client.retry_on_dead_connection).
"""
from __future__ import annotations

import logging
import socket
import time

import requests
from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter
from urllib3.connection import HTTPConnection

log = logging.getLogger(__name__)

KEEPALIVE_IDLE = 30  # seconds of silence before the first keepalive probe
KEEPALIVE_INTERVAL = 10  # seconds between probes
KEEPALIVE_COUNT = 3  # unanswered probes before the OS declares the connection dead
IDLE_RESET = 120  # seconds; a pooled session unused for longer is replaced before use
REQUEST_TIMEOUT = 30  # seconds per EWS request (exchangelib default: 120)


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


def harden(protocol, clock=time.time) -> None:
    """Shorter timeout, and replace pooled sessions that sat idle too long.

    Wraps this protocol instance's get_session/release_session: each
    session is stamped when released; one handed out after more than
    IDLE_RESET seconds is closed and replaced with a fresh one (a new TCP +
    TLS connection and NTLM handshake, well under a second) instead of
    being trusted. `clock` is wall-clock time so a sleeping Mac counts as
    idle (time.monotonic doesn't advance during sleep on macOS).
    """
    if getattr(protocol, "_ewstui_hardened", False):
        return
    protocol.TIMEOUT = REQUEST_TIMEOUT
    get_session, release_session = protocol.get_session, protocol.release_session

    def fresh_get_session():
        session = get_session()
        last = getattr(session, "ewstui_last_used", None)
        if last is not None and clock() - last > IDLE_RESET:
            log.info("session idle for %.0fs; reconnecting", clock() - last)
            protocol.close_session(session)
            session = protocol.create_session()
            session.usage_count = 1
        return session

    def stamped_release_session(session):
        session.ewstui_last_used = clock()
        release_session(session)

    protocol.get_session = fresh_get_session
    protocol.release_session = stamped_release_session
    protocol._ewstui_hardened = True
