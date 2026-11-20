from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from ewstui import __main__ as entry
from ewstui import onepassword
from ewstui.config import config_from_args
from test_keychain import ARGS, KEY, answer, login, typed  # noqa: F401 - shared fixtures

REF = "op://Work/Exchange/password"
OP_ARGS = [*ARGS, "--password-op-ref", REF]


@pytest.fixture
def op(monkeypatch):
    """Fake `op read`: returns `secret`, or None to simulate a failed read."""
    state = {"secret": "from-1password", "refs": []}

    def fake_read(ref):
        state["refs"].append(ref)
        return state["secret"]

    monkeypatch.setattr(onepassword, "read", fake_read)
    return state


def test_password_read_from_1password(login, typed, answer, op, memory_keyring, touch_id):
    memory_keyring.items[KEY] = "keychain-password"
    assert entry.main(OP_ARGS) == 0
    assert op["refs"] == [REF]
    assert login.passwords == ["from-1password"]  # 1Password wins over the Keychain
    assert typed["asked"] == 0
    assert touch_id.prompts == []


def test_failed_op_read_falls_back_to_prompt(login, typed, answer, op, memory_keyring):
    op["secret"] = None
    typed["password"] = "typed"
    assert entry.main(OP_ARGS) == 0
    assert login.passwords == ["typed"]
    assert answer["asked"] == 0  # 1Password chosen for this account: no Keychain offer
    assert memory_keyring.items == {}


def test_failed_op_read_without_terminal_fails_clearly(login, typed, answer, op, monkeypatch, capsys):
    op["secret"] = None
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert entry.main([*OP_ARGS, "--no-keychain"]) == 1
    assert "reading it from 1Password failed" in capsys.readouterr().err


def test_env_password_wins_over_1password(login, typed, answer, op, monkeypatch):
    monkeypatch.setenv("EWSTUI_PASSWORD", "from-env")
    assert entry.main(OP_ARGS) == 0
    assert login.passwords == ["from-env"]
    assert op["refs"] == []


def test_rejected_1password_password_reported_not_deleted(login, typed, answer, op, memory_keyring, capsys):
    memory_keyring.items[KEY] = "keychain-password"
    login.reject = True
    assert entry.main(OP_ARGS) == 1
    err = capsys.readouterr().err
    assert REF in err and "Update the item in 1Password" in err
    assert memory_keyring.items[KEY] == "keychain-password"  # Keychain untouched


def test_op_ref_stored_in_account_profile(tmp_path, login, typed, answer, op):
    cfg_path = tmp_path / "config.toml"
    assert entry.main(["--config", str(cfg_path), "--account", "work", *OP_ARGS]) == 0
    assert f'password_op_ref = "{REF}"' in cfg_path.read_text()
    assert "from-1password" not in cfg_path.read_text()
    cfg = config_from_args(["--config", str(cfg_path)])
    assert cfg.password_op_ref == REF


def test_read_runs_op(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="s3cret", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert onepassword.read(REF) == "s3cret"
    assert calls == [["op", "read", "--no-newline", REF]]


@pytest.mark.parametrize(
    "outcome",
    [
        SimpleNamespace(returncode=1, stdout="", stderr="[ERROR] not signed in"),
        FileNotFoundError("op"),
        subprocess.TimeoutExpired("op", 120),
    ],
)
def test_read_failures_return_none(monkeypatch, outcome):
    def fake_run(argv, **kw):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert onepassword.read(REF) is None
