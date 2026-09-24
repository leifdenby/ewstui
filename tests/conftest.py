from __future__ import annotations

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from ewstui import keychain


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        if self.items.pop((service, username), None) is None:
            raise PasswordDeleteError(username)


class FakePresence:
    """Stands in for the Touch ID dialog; records each prompt."""

    def __init__(self):
        self.allow = True
        self.prompts: list[str] = []

    def __call__(self, reason: str) -> bool:
        self.prompts.append(reason)
        return self.allow


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path, monkeypatch):
    """Never read or write the user's real ~/.config/ewstui/config.toml."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch):
    """Never touch the real Keychain or pop a real Touch ID dialog in tests."""
    backend = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    monkeypatch.setattr(keychain, "available", lambda: True)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def touch_id(monkeypatch) -> FakePresence:
    presence = FakePresence()
    monkeypatch.setattr(keychain, "require_user_presence", presence)
    return presence
