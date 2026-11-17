from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ewstui import __main__ as entry
from ewstui.config import config_from_args
from ewstui.log_file import build_default_log_path, ensure_log_dir, resolve_log_path


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake home directory, with no log-related env vars set."""
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    for var in ("EWSTUI_LOG_FILE", "XDG_STATE_HOME", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    return fake_home


def test_macos_uses_library_logs(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert build_default_log_path() == home / "Library" / "Logs" / "ewstui" / "ewstui.log"


def test_linux_uses_xdg_state_home(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert build_default_log_path() == tmp_path / "state" / "ewstui" / "ewstui.log"


def test_linux_falls_back_to_local_state(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert build_default_log_path() == home / ".local" / "state" / "ewstui" / "ewstui.log"


def test_empty_xdg_state_home_is_ignored(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", "")
    assert build_default_log_path() == home / ".local" / "state" / "ewstui" / "ewstui.log"


def test_windows_uses_localappdata(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    assert build_default_log_path() == tmp_path / "AppData" / "Local" / "ewstui" / "Logs" / "ewstui.log"


def test_windows_without_localappdata(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert build_default_log_path() == home / "AppData" / "Local" / "ewstui" / "Logs" / "ewstui.log"


def test_default_path_creates_nothing(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    build_default_log_path()
    assert not home.exists()


def test_env_var_overrides_default(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EWSTUI_LOG_FILE", str(tmp_path / "env.log"))
    assert resolve_log_path() == tmp_path / "env.log"


def test_cli_overrides_env_var(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EWSTUI_LOG_FILE", str(tmp_path / "env.log"))
    assert resolve_log_path(tmp_path / "cli.log") == tmp_path / "cli.log"


def test_override_expands_tilde(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EWSTUI_LOG_FILE", "~/x.log")
    assert resolve_log_path() == home / "x.log"


def test_no_override_gives_default(home: Path) -> None:
    assert resolve_log_path() == build_default_log_path()


def test_ensure_log_dir_creates_parents(tmp_path: Path) -> None:
    log_path = tmp_path / "a" / "b" / "ewstui.log"
    ensure_log_dir(log_path)
    ensure_log_dir(log_path)  # already there: fine
    assert log_path.parent.is_dir()


def test_config_takes_log_file_option(tmp_path: Path) -> None:
    cfg = config_from_args(["--demo", "--log-file", str(tmp_path / "cli.log")])
    assert cfg.log_file == tmp_path / "cli.log"


def test_config_defaults_to_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EWSTUI_LOG_FILE", str(tmp_path / "env.log"))
    assert config_from_args(["--demo"]).log_file == tmp_path / "env.log"


def test_setup_logging_creates_log_dir(tmp_path: Path) -> None:
    cfg = config_from_args(["--demo", "--log-file", str(tmp_path / "new" / "dir" / "ewstui.log")])
    entry._setup_logging(cfg)
    assert (tmp_path / "new" / "dir").is_dir()
