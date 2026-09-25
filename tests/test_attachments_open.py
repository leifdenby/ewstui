from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ewstui import opener
from ewstui.config import config_from_args

FILE = Path("/tmp/report.pdf")


@pytest.fixture
def popen(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    return calls


# -- opening ------------------------------------------------------------------


def test_macos_uses_open(monkeypatch, popen):
    monkeypatch.setattr(opener.sys, "platform", "darwin")
    opener.open_with_default_app(FILE)
    cmd, kw = popen[0]
    assert cmd == ["open", str(FILE)]
    assert kw["start_new_session"] is True


def test_linux_uses_xdg_open(monkeypatch, popen):
    monkeypatch.setattr(opener.sys, "platform", "linux")
    monkeypatch.setattr(opener.shutil, "which", lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None)
    opener.open_with_default_app(FILE)
    assert popen[0][0] == ["/usr/bin/xdg-open", str(FILE)]


def test_linux_without_xdg_open_raises(monkeypatch, popen):
    monkeypatch.setattr(opener.sys, "platform", "linux")
    monkeypatch.setattr(opener.shutil, "which", lambda name: None)
    with pytest.raises(opener.OpenError, match="xdg-open not found"):
        opener.open_with_default_app(FILE)
    assert popen == []


def test_windows_uses_startfile(monkeypatch, popen):
    started = []
    monkeypatch.setattr(opener.sys, "platform", "win32")
    monkeypatch.setattr(opener.os, "startfile", started.append, raising=False)
    opener.open_with_default_app(FILE)
    assert started == [str(FILE)]
    assert popen == []


def test_launch_failure_becomes_open_error(monkeypatch):
    monkeypatch.setattr(opener.sys, "platform", "darwin")

    def fail(*a, **kw):
        raise FileNotFoundError("open")

    monkeypatch.setattr(subprocess, "Popen", fail)
    with pytest.raises(opener.OpenError):
        opener.open_with_default_app(FILE)


# -- where attachments go ----------------------------------------------------


def test_macos_default_dir_is_per_account(monkeypatch):
    monkeypatch.setattr(opener.sys, "platform", "darwin")
    downloads = Path.home() / "Downloads"
    assert opener.default_attachment_dir("dmi", "lcd@dmi.dk") == downloads / "Attachments" / "dmi"
    assert opener.default_attachment_dir(None, "lcd@dmi.dk") == downloads / "Attachments" / "lcd@dmi.dk"
    assert opener.default_attachment_dir(None, None) == downloads / "Attachments" / "default"


def test_account_name_cannot_escape_the_attachments_dir(monkeypatch):
    monkeypatch.setattr(opener.sys, "platform", "darwin")
    d = opener.default_attachment_dir("../../etc", None)
    assert d.parent == Path.home() / "Downloads" / "Attachments"


def test_other_platforms_keep_old_default(monkeypatch):
    monkeypatch.setattr(opener.sys, "platform", "linux")
    assert opener.default_attachment_dir("dmi", "x@y") == Path.home() / "Downloads" / "ewstui-attachments"


def test_config_uses_account_name_and_explicit_dir_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(opener.sys, "platform", "darwin")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('default_account = "dmi"\n[accounts.dmi]\nemail = "lcd@dmi.dk"\n')

    cfg = config_from_args(["--config", str(cfg_path)])
    assert cfg.attachment_dir == Path.home() / "Downloads" / "Attachments" / "dmi"

    cfg = config_from_args(["--email", "a@b.test", "--config", str(tmp_path / "none.toml")])
    assert cfg.attachment_dir == Path.home() / "Downloads" / "Attachments" / "a@b.test"

    cfg = config_from_args(["--config", str(cfg_path), "--attachment-dir", str(tmp_path / "att")])
    assert cfg.attachment_dir == tmp_path / "att"
