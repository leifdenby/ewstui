"""
Optional TOML config file with named account profiles.

    ~/.config/ewstui/config.toml   (or $XDG_CONFIG_HOME/ewstui/config.toml)

    default_account = "work"

    [accounts.work]
    email = "you@corp.example"
    ews_url = "https://mail.corp.example/EWS/Exchange.asmx"
    ntlm_no_cbt = true

Keys are the CLI option names with dashes as underscores. Values are
merged as: built-in defaults < [accounts.NAME] < explicit CLI args
(see config.config_from_args). tomlkit is used so that saving back
into a hand-edited file keeps its comments and layout.
"""
from __future__ import annotations

import os
from pathlib import Path

import tomlkit
from tomlkit import TOMLDocument

# CLI options that can be stored in a profile (argparse dest names).
# Never: password (Keychain/env/prompt only), debug, demo,
# forget_password, account, config.
STORED_KEYS = (
    "email",
    "ews_url",
    "autodiscover",
    "no_verify_ssl",
    "auth",
    "username",
    "domain",
    "ntlm_no_cbt",
    "no_keychain",
    "tenant_id",
    "client_id",
    "oauth_authority",
    "token_cache",
    "page_size",
    "refresh_interval",
    "priority_file",
    "attachment_dir",
)
PATH_KEYS = {"token_cache", "priority_file", "attachment_dir"}


class ConfigFileError(ValueError):
    """Unknown account, bad value, unparsable file — shown to the user as-is."""


def default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "ewstui" / "config.toml"


def load(path: Path) -> TOMLDocument:
    if not path.exists():
        return tomlkit.document()
    try:
        return tomlkit.parse(path.read_text())
    except tomlkit.exceptions.ParseError as e:
        raise ConfigFileError(f"Can't parse {path}: {e}") from e


def resolve_account_name(doc: TOMLDocument, cli_name: str | None) -> str | None:
    """--account wins; otherwise the file's default_account; else None."""
    return cli_name or doc.get("default_account")


def account_settings(doc: TOMLDocument, name: str) -> dict:
    """The stored values for `name`, as plain Python values (paths
    expanded). Empty dict if the account doesn't exist.
    """
    table = doc.get("accounts", {}).get(name)
    if table is None:
        return {}
    unknown = set(table) - set(STORED_KEYS)
    if unknown:
        raise ConfigFileError(
            f"Unknown setting(s) in [accounts.{name}]: {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(STORED_KEYS)}"
        )
    out = {}
    for key, value in table.unwrap().items():
        out[key] = Path(value).expanduser() if key in PATH_KEYS else value
    return out


def has_account(doc: TOMLDocument, name: str) -> bool:
    return name in doc.get("accounts", {})


def save_account(path: Path, name: str, updates: dict) -> None:
    """Merge `updates` into [accounts.NAME] (creating it), make it the
    default_account if there isn't one yet, and write the file (0600).
    """
    doc = load(path)
    if "default_account" not in doc:
        doc["default_account"] = name  # tomlkit places it above the tables
    accounts = doc.setdefault("accounts", tomlkit.table(is_super_table=True))
    table = accounts.setdefault(name, tomlkit.table())
    for key, value in updates.items():
        if key not in STORED_KEYS:
            raise ConfigFileError(f"{key} can't be stored in a profile")
        table[key] = str(value) if isinstance(value, Path) else value

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc))
    path.chmod(0o600)
