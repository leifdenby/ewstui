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

from .ews_client import Room
from .folder_tree import FolderPrefs

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
    "layout",
    "threads",
    "theme",
    "priority_file",
    "attachment_dir",
)
PATH_KEYS = {"token_cache", "priority_file", "attachment_dir"}
# Profile keys that aren't CLI options: set by editing the file or from the
# app, and read separately (see account_rooms, account_folder_prefs).
FOLDER_KEYS = ("folder_order", "hidden_folders", "shown_folders")
FILE_ONLY_KEYS = ("rooms", *FOLDER_KEYS)


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
    unknown = set(table) - set(STORED_KEYS) - set(FILE_ONLY_KEYS)
    if unknown:
        raise ConfigFileError(
            f"Unknown setting(s) in [accounts.{name}]: {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(STORED_KEYS + FILE_ONLY_KEYS)}"
        )
    out = {}
    for key, value in table.unwrap().items():
        if key in FILE_ONLY_KEYS:
            continue
        out[key] = Path(value).expanduser() if key in PATH_KEYS else value
    return out


def account_rooms(doc: TOMLDocument, name: str) -> list[Room]:
    """Meeting rooms for the account, in file order. Either

        [accounts.NAME.rooms]
        "Room 4B (8 pers)" = "room-4b@corp.example"

    or a plain list of addresses: rooms = ["room-4b@corp.example", ...].
    """
    table = doc.get("accounts", {}).get(name)
    if table is None or "rooms" not in table:
        return []
    rooms = table["rooms"].unwrap()
    if isinstance(rooms, dict) and all(isinstance(v, str) for v in rooms.values()):
        return [Room(name=display, email=email) for display, email in rooms.items()]
    if isinstance(rooms, list) and all(isinstance(v, str) for v in rooms):
        return [Room(name=email, email=email) for email in rooms]
    raise ConfigFileError(
        f"[accounts.{name}] rooms must be a table of \"Room name\" = \"address\" "
        "or a list of addresses"
    )


def add_room(path: Path, name: str, room: Room) -> bool:
    """Append `room` to [accounts.NAME] rooms, in whichever of the two
    shapes the file already uses. False if it's already listed.
    """
    doc = load(path)
    table = doc.get("accounts", {}).get(name)
    if table is None:
        raise ConfigFileError(f"no [accounts.{name}] in {path}")
    rooms = table.get("rooms")
    if rooms is None:
        rooms = table["rooms"] = tomlkit.table()
    if isinstance(rooms, list):
        if any(str(v).casefold() == room.email.casefold() for v in rooms):
            return False
        rooms.append(room.email)
    else:
        if any(str(v).casefold() == room.email.casefold() for v in rooms.values()):
            return False
        key = room.name if room.name not in rooms else f"{room.name} ({room.email})"
        rooms[key] = room.email
    path.write_text(tomlkit.dumps(doc))
    path.chmod(0o600)
    return True


def account_folder_prefs(doc: TOMLDocument, name: str) -> FolderPrefs:
    """The folder pane arrangement saved for the account: folder_order,
    hidden_folders and shown_folders, each a list of EWS folder ids."""
    table = doc.get("accounts", {}).get(name) or {}
    lists = {}
    for key in FOLDER_KEYS:
        value = table.get(key, [])
        value = value.unwrap() if hasattr(value, "unwrap") else value
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigFileError(f"[accounts.{name}] {key} must be a list of folder ids")
        lists[key] = value
    return FolderPrefs(order=lists["folder_order"], hidden=lists["hidden_folders"], shown=lists["shown_folders"])


def save_folder_prefs(path: Path, name: str, prefs: FolderPrefs, names: dict[str, str]) -> None:
    """Write the folder arrangement into [accounts.NAME], one id per line
    with the folder's name as a comment (ids alone are unreadable)."""
    doc = load(path)
    table = doc.get("accounts", {}).get(name)
    if table is None:
        raise ConfigFileError(f"no [accounts.{name}] in {path}")
    for key, ids in zip(FOLDER_KEYS, (prefs.order, prefs.hidden, prefs.shown)):
        if not ids:
            table.pop(key, None)
            continue
        array = tomlkit.array()
        for fid in ids:
            array.add_line(fid, comment=names.get(fid) or None)
        array.add_line(indent="")
        table[key] = array
    path.write_text(tomlkit.dumps(doc))
    path.chmod(0o600)


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
