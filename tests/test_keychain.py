from __future__ import annotations

import getpass
from types import SimpleNamespace

import pytest
from exchangelib.errors import UnauthorizedError

from ewstui import __main__ as entry
from ewstui import auth, keychain
from conftest import fake_protocol

URL = "https://mail.example.test/EWS/Exchange.asmx"
USER = "PROD\\jdoe"
KEY = ("ewstui", f"{USER} @ {URL}")
ARGS = ["--email", "me@example.test", "--ews-url", URL, "--domain", "PROD", "--username", "jdoe"]


class Recorder:
    """Captures what the fake login saw, instead of talking to EWS."""

    def __init__(self, reject=False):
        self.reject = reject
        self.passwords: list[str] = []

    def account(self, cfg, auth_type):
        username = auth.login_username(cfg, auth_type)
        self.passwords.append(auth._resolve_password(cfg, username))
        reject = self.reject

        class FakeAccount:
            protocol = fake_protocol(credentials=SimpleNamespace(username=username, password=self.passwords[-1]))

            @property
            def inbox(self):
                if reject:
                    raise UnauthorizedError("Invalid credentials")
                return SimpleNamespace(total_count=1, unread_count=0)

        return FakeAccount()


@pytest.fixture
def login(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(auth, "_password_account", rec.account)
    monkeypatch.setattr(auth, "probe_endpoint", lambda *a, **kw: None)
    monkeypatch.setattr(entry.EwstuiApp, "run", lambda self: None)
    monkeypatch.delenv("EWSTUI_PASSWORD", raising=False)
    return rec


@pytest.fixture
def typed(monkeypatch):
    """Simulate the getpass prompt; fails the test if it's unexpectedly shown."""
    state = {"password": None, "asked": 0}

    def fake_getpass(prompt=""):
        state["asked"] += 1
        if state["password"] is None:
            pytest.fail("password prompt shown unexpectedly")
        return state["password"]

    monkeypatch.setattr(getpass, "getpass", fake_getpass)
    return state


@pytest.fixture
def answer(monkeypatch):
    """Simulate a TTY and the y/N save prompt."""
    state = {"reply": "n", "asked": 0}

    def fake_input(prompt=""):
        state["asked"] += 1
        return state["reply"]

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    return state


def test_typed_password_saved_after_successful_login(login, typed, answer, memory_keyring):
    typed["password"] = "s3cret"
    answer["reply"] = "y"
    assert entry.main(ARGS) == 0
    assert memory_keyring.items[KEY] == "s3cret"


def test_declining_save_stores_nothing(login, typed, answer, memory_keyring):
    typed["password"] = "s3cret"
    assert entry.main(ARGS) == 0
    assert answer["asked"] == 1
    assert memory_keyring.items == {}


def test_stored_password_used_after_touch_id(login, typed, answer, memory_keyring, touch_id):
    memory_keyring.items[KEY] = "s3cret"
    assert entry.main(ARGS) == 0
    assert login.passwords == ["s3cret"]
    assert typed["asked"] == 0
    assert answer["asked"] == 0  # already stored, don't offer again
    assert len(touch_id.prompts) == 1 and USER in touch_id.prompts[0]


def test_cancelled_touch_id_falls_back_to_prompt(login, typed, answer, memory_keyring, touch_id):
    memory_keyring.items[KEY] = "s3cret"
    touch_id.allow = False
    typed["password"] = "typed-instead"
    assert entry.main(ARGS) == 0
    assert login.passwords == ["typed-instead"]


def test_no_touch_id_prompt_when_nothing_stored(login, typed, answer, touch_id):
    typed["password"] = "s3cret"
    entry.main(ARGS)
    assert touch_id.prompts == []


def test_rejected_stored_password_is_removed(login, typed, answer, memory_keyring, capsys):
    memory_keyring.items[KEY] = "old-password"
    login.reject = True
    assert entry.main(ARGS) == 1
    assert KEY not in memory_keyring.items
    assert "rejected and has been removed" in capsys.readouterr().err


@pytest.mark.parametrize(("offered", "kept"), [(["NTLM", "Negotiate"], True), (["Basic", "NTLM"], False)])
def test_stored_password_kept_if_server_does_not_offer_the_method(
    login, typed, answer, memory_keyring, monkeypatch, capsys, offered, kept
):
    """On VPN the internal server may offer only NTLM: a Basic 401 there
    says nothing about the password, so it stays in the Keychain."""
    key = ("ewstui", f"jdoe @ {URL}")
    memory_keyring.items[key] = "good-password"
    login.reject = True
    monkeypatch.setattr(auth, "probe_endpoint", lambda *a, **kw: offered)
    assert entry.main([*ARGS, "--auth", "basic"]) == 1
    assert (key in memory_keyring.items) is kept
    assert ("has been removed" in capsys.readouterr().err) is not kept


def test_rejected_typed_password_is_not_offered_for_saving(login, typed, answer, memory_keyring):
    typed["password"] = "wrong"
    login.reject = True
    assert entry.main(ARGS) == 1
    assert answer["asked"] == 0
    assert memory_keyring.items == {}


def test_no_keychain_flag_skips_keychain(login, typed, answer, memory_keyring, touch_id):
    memory_keyring.items[KEY] = "s3cret"
    typed["password"] = "typed"
    assert entry.main([*ARGS, "--no-keychain"]) == 0
    assert login.passwords == ["typed"]
    assert touch_id.prompts == []
    assert answer["asked"] == 0


def test_env_password_bypasses_keychain(login, typed, answer, memory_keyring, monkeypatch):
    monkeypatch.setenv("EWSTUI_PASSWORD", "from-env")
    assert entry.main(ARGS) == 0
    assert login.passwords == ["from-env"]
    assert memory_keyring.items == {}


def test_forget_password(memory_keyring, capsys):
    memory_keyring.items[KEY] = "s3cret"
    assert entry.main([*ARGS, "--forget-password"]) == 0
    assert KEY not in memory_keyring.items
    assert entry.main([*ARGS, "--forget-password"]) == 0
    assert "No Keychain password stored" in capsys.readouterr().err


def test_keychain_key_matches_login_username():
    cfg = entry.config_from_args(ARGS)
    assert auth.login_username(cfg) == USER
    cfg = entry.config_from_args([*ARGS, "--auth", "basic"])
    assert auth.login_username(cfg) == "jdoe"  # --domain only applies to NTLM
    assert keychain._item_key("jdoe", None) == "jdoe @ autodiscover"
