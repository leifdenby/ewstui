from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests
from exchangelib.errors import ErrorTimeoutExpired, UnauthorizedError

from ewstui import __main__ as entry
from ewstui import auth

URL = "https://mail.example.test/EWS/Exchange.asmx"


class FakeResponse:
    def __init__(self, status_code=401, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.is_redirect = status_code in (301, 302, 303, 307, 308)


@pytest.fixture(autouse=True)
def no_app(monkeypatch):
    """Fail loudly if a test would actually start the TUI."""
    def boom(self):
        raise AssertionError("TUI started despite connection failure")
    monkeypatch.setattr(entry.EwstuiApp, "run", boom)


@pytest.fixture
def probe_ok(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(401, {"WWW-Authenticate": "Negotiate, NTLM"})
    )


def run_main(capsys, *extra):
    rc = entry.main(["--email", "me@example.test", "--ews-url", URL, *extra])
    return rc, capsys.readouterr().err


# -- probe ---------------------------------------------------------------


def test_probe_reports_offered_auth_schemes(probe_ok, capsys):
    auth.probe_endpoint(URL)
    err = capsys.readouterr().err
    assert "HTTP 401" in err
    assert "NTLM, Negotiate" in err


def test_probe_flags_oauth_only_servers(monkeypatch, capsys):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(401, {"WWW-Authenticate": 'Bearer realm="x"'}))
    auth.probe_endpoint(URL)
    assert "--auth oauth2" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("exc", "hint"),
    [
        (requests.exceptions.SSLError("bad cert"), "--no-verify-ssl"),
        (requests.exceptions.ConnectTimeout("slow"), "VPN"),
        (requests.exceptions.ConnectionError("no route"), "hostname"),
    ],
)
def test_probe_network_failures_raise_with_hint(monkeypatch, exc, hint):
    def raise_(*a, **kw):
        raise exc
    monkeypatch.setattr(requests, "get", raise_)
    with pytest.raises(auth.AuthError, match=hint):
        auth.probe_endpoint(URL)


# -- main: no blank TUI on failure -------------------------------------


def fake_account(inbox_getter):
    """Minimal stand-in for exchangelib.Account: .protocol.TIMEOUT and .inbox."""
    class FakeAccount:
        protocol = SimpleNamespace(TIMEOUT=120)
        inbox = property(lambda self: inbox_getter())
    return FakeAccount()


def test_verify_times_out_with_hint_and_restores_timeout(capsys):
    seen = {}

    def hang():
        seen["timeout"] = account.protocol.TIMEOUT
        # what exchangelib raises when the POST times out
        raise ErrorTimeoutExpired("Reraised from ReadTimeout(read timed out)")

    account = fake_account(hang)
    with pytest.raises(auth.AuthError, match="--auth basic"):
        auth.verify_account(account, timeout=7)
    assert seen["timeout"] == 7
    assert account.protocol.TIMEOUT == 120


def test_verify_prints_still_waiting_hint_for_slow_server(capsys):
    import time

    def slow():
        time.sleep(0.3)
        return SimpleNamespace(total_count=1, unread_count=0)

    auth.verify_account(fake_account(slow), timeout=0.2)  # hint fires at timeout/2
    assert "Still waiting" in capsys.readouterr().err


def test_401_hint_suggests_the_other_auth_method():
    err = UnauthorizedError("Invalid credentials")
    assert "--auth ntlm" in auth.explain_error(err, "basic")
    assert "--auth basic" in auth.explain_error(err, "NTLM")


def test_ntlm_timeout_hint_suggests_disabling_cbt():
    err = ErrorTimeoutExpired("Reraised from ReadTimeout()")
    assert "--ntlm-no-cbt" in auth.explain_error(err, "NTLM")
    assert "--ntlm-no-cbt" not in auth.explain_error(err, "basic")


def test_ntlm_no_cbt_flag_disables_channel_binding(monkeypatch):
    from exchangelib import NTLM, transport

    monkeypatch.setitem(transport.AUTH_TYPE_MAP, NTLM, transport.AUTH_TYPE_MAP[NTLM])
    monkeypatch.setattr(auth, "_password_account", lambda cfg, auth_type: None)
    cfg = entry.config_from_args(["--email", "me@example.test", "--auth", "ntlm", "--ntlm-no-cbt"])
    auth.get_account(cfg)
    ntlm_auth = transport.get_auth_instance(NTLM, username="u", password="p")
    assert ntlm_auth.send_cbt is False


def test_bad_credentials_exit_with_explanation(monkeypatch, probe_ok, capsys):
    class RejectingAccount:
        protocol = SimpleNamespace(TIMEOUT=120)

        @property
        def inbox(self):
            raise UnauthorizedError("Invalid credentials for https://mail.example.test/EWS/Exchange.asmx")

    monkeypatch.setattr(auth, "get_account", lambda cfg: RejectingAccount())
    rc, err = run_main(capsys)
    assert rc == 1
    assert "Connection failed: UnauthorizedError" in err
    assert "DOMAIN\\user" in err
    assert "--debug" in err


def test_unreachable_server_exits_before_password_prompt(monkeypatch, capsys):
    def raise_(*a, **kw):
        raise requests.exceptions.ConnectionError("no route")
    monkeypatch.setattr(requests, "get", raise_)
    monkeypatch.setattr(auth, "get_account", lambda cfg: pytest.fail("should not prompt for password"))
    rc, err = run_main(capsys)
    assert rc == 1
    assert "Could not connect" in err


def test_unexpected_error_is_reported_not_raised(monkeypatch, probe_ok, capsys):
    def explode(cfg):
        raise ValueError("something odd")
    monkeypatch.setattr(auth, "get_account", explode)
    rc, err = run_main(capsys, "--debug")
    assert rc == 1
    assert "ValueError: something odd" in err
    assert "Traceback" in err


def test_successful_check_starts_app(monkeypatch, probe_ok, capsys):
    inbox = SimpleNamespace(total_count=12, unread_count=3)
    monkeypatch.setattr(auth, "get_account", lambda cfg: fake_account(lambda: inbox))
    started = []
    monkeypatch.setattr(entry.EwstuiApp, "run", lambda self: started.append(True))
    rc, err = run_main(capsys)
    assert rc == 0
    assert started == [True]
    assert "Inbox has 12 messages (3 unread)" in err
