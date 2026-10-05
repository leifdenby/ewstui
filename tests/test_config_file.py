from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
import tomlkit

from ewstui import __main__ as entry
from ewstui import auth, config_file
from ewstui.config import config_from_args

URL = "https://mail.example.test/EWS/Exchange.asmx"
CREATE = ["--email", "me@example.test", "--ews-url", URL, "--domain", "PROD", "--ntlm-no-cbt"]


@pytest.fixture
def cfg_path(tmp_path) -> Path:
    return tmp_path / "config.toml"


def stored(path: Path) -> dict:
    return tomlkit.parse(path.read_text()).unwrap()


@pytest.fixture
def connect(monkeypatch):
    """Fake a live connection; flip .ok to make it fail."""
    state = SimpleNamespace(ok=True, cfgs=[])

    def get_account(cfg):
        state.cfgs.append(cfg)
        if not state.ok:
            raise auth.AuthError("nope")
        return SimpleNamespace(protocol=SimpleNamespace(TIMEOUT=120, credentials=None))

    monkeypatch.setattr(auth, "probe_endpoint", lambda *a, **kw: None)
    monkeypatch.setattr(auth, "get_account", get_account)
    monkeypatch.setattr(auth, "verify_account", lambda account, **kw: None)
    monkeypatch.setattr(entry.EwstuiApp, "run", lambda self: None)
    return state


def run(cfg_path, *args) -> int:
    return entry.main(["--config", str(cfg_path), *args])


# -- creating / reading profiles ------------------------------------------


def test_account_with_args_is_saved_after_successful_login(cfg_path, connect, capsys):
    assert run(cfg_path, "--account", "work", *CREATE) == 0
    assert stored(cfg_path) == {
        "default_account": "work",
        "accounts": {
            "work": {"email": "me@example.test", "ews_url": URL, "domain": "PROD", "ntlm_no_cbt": True}
        },
    }
    assert "Saved email, ews_url, domain, ntlm_no_cbt to account 'work'" in capsys.readouterr().err
    assert stat.S_IMODE(cfg_path.stat().st_mode) == 0o600


def test_saved_account_is_loaded_by_name_and_as_default(cfg_path, connect):
    run(cfg_path, "--account", "work", *CREATE)
    for args in (["--account", "work"], []):
        cfg = config_from_args(["--config", str(cfg_path), *args])
        assert (cfg.email, cfg.ews_url, cfg.domain, cfg.ntlm_send_cbt) == ("me@example.test", URL, "PROD", False)
        assert cfg.account == "work"
        assert cfg.pending_account_updates == {}  # nothing new typed, nothing to save


def test_failed_login_saves_nothing(cfg_path, connect):
    connect.ok = False
    assert run(cfg_path, "--account", "work", *CREATE) == 1
    assert not cfg_path.exists()


def test_cli_overrides_profile_and_updates_it(cfg_path, connect):
    run(cfg_path, "--account", "work", *CREATE)
    run(cfg_path, "--account", "work", "--page-size", "25")
    assert connect.cfgs[-1].page_size == 25
    assert connect.cfgs[-1].email == "me@example.test"  # rest still from the profile
    assert stored(cfg_path)["accounts"]["work"]["page_size"] == 25


def test_without_explicit_account_cli_args_are_one_off(cfg_path, connect):
    run(cfg_path, "--account", "work", *CREATE)
    run(cfg_path, "--page-size", "25")  # default_account in use, not --account
    assert connect.cfgs[-1].page_size == 25
    assert "page_size" not in stored(cfg_path)["accounts"]["work"]


def test_second_account_does_not_replace_default(cfg_path, connect):
    run(cfg_path, "--account", "work", *CREATE)
    run(cfg_path, "--account", "home", "--email", "me@home.test", "--ews-url", URL)
    data = stored(cfg_path)
    assert data["default_account"] == "work"
    assert set(data["accounts"]) == {"work", "home"}


def test_builtin_defaults_apply_when_not_in_profile(cfg_path, connect):
    run(cfg_path, "--account", "work", *CREATE)
    cfg = config_from_args(["--config", str(cfg_path)])
    assert cfg.page_size == 50 and cfg.refresh_interval == 5.0 and cfg.use_keychain


# -- things that must never be stored ---------------------------------------


def test_password_debug_demo_never_stored(cfg_path, connect, monkeypatch):
    monkeypatch.setenv("EWSTUI_PASSWORD", "s3cret")
    run(cfg_path, "--account", "work", *CREATE, "--debug")
    text = cfg_path.read_text()
    assert "s3cret" not in text and "debug" not in text
    run(cfg_path, "--account", "work", "--demo")  # demo path; nothing storable given
    assert "demo" not in cfg_path.read_text()


# -- errors -------------------------------------------------------------------


def test_unknown_account_without_args_explains_how_to_create(cfg_path):
    with pytest.raises(SystemExit, match="Create it by passing its settings once"):
        config_from_args(["--config", str(cfg_path), "--account", "nope"])


def test_dangling_default_account(cfg_path):
    cfg_path.write_text('default_account = "gone"\n')
    with pytest.raises(SystemExit, match="default_account 'gone'"):
        config_from_args(["--config", str(cfg_path)])


def test_unknown_key_in_profile_is_rejected(cfg_path):
    cfg_path.write_text('[accounts.work]\nemail = "a@b.test"\npasword = "typo"\n')
    with pytest.raises(SystemExit, match="Unknown setting.*pasword"):
        config_from_args(["--config", str(cfg_path), "--account", "work"])


def test_invalid_auth_value(cfg_path):
    cfg_path.write_text('[accounts.work]\nemail = "a@b.test"\nauth = "ntml"\n')
    with pytest.raises(SystemExit, match="invalid auth 'ntml'"):
        config_from_args(["--config", str(cfg_path), "--account", "work"])


def test_unparsable_file(cfg_path):
    cfg_path.write_text("this is = = not toml\n")
    with pytest.raises(SystemExit, match="Can't parse"):
        config_from_args(["--config", str(cfg_path), "--demo"])


# -- file handling ------------------------------------------------------------


def test_comments_survive_updates(cfg_path, connect):
    cfg_path.write_text(
        '# my ewstui settings\ndefault_account = "work"\n\n[accounts.work]\n'
        'email = "me@example.test"  # work mailbox\n'
    )
    run(cfg_path, "--account", "work", "--ews-url", URL)
    text = cfg_path.read_text()
    assert "# my ewstui settings" in text and "# work mailbox" in text
    assert stored(cfg_path)["accounts"]["work"]["ews_url"] == URL


def test_paths_stored_as_strings_and_expanded_on_load(cfg_path, connect):
    cfg_path.write_text('[accounts.work]\nemail = "a@b.test"\npriority_file = "~/todo.txt"\n')
    cfg = config_from_args(["--config", str(cfg_path), "--account", "work"])
    assert cfg.priority_file == Path.home() / "todo.txt"
    run(cfg_path, "--account", "work", "--attachment-dir", "/tmp/att")
    assert stored(cfg_path)["accounts"]["work"]["attachment_dir"] == "/tmp/att"


def test_default_path_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_file.default_path() == tmp_path / "ewstui" / "config.toml"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert config_file.default_path() == Path.home() / ".config" / "ewstui" / "config.toml"


def test_no_config_file_behaves_as_before():
    cfg = config_from_args(["--email", "a@b.test"])
    assert cfg.account is None and cfg.pending_account_updates == {}
    with pytest.raises(SystemExit, match="--email is required"):
        config_from_args([])
