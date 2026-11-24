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
# Never: password (1Password/Keychain/env/prompt only; the 1Password
# reference `password_op_ref` is stored, the secret never is), debug, demo,
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
    "password_op_ref",
    "no_keychain",
    "tenant_id",
    "client_id",
    "oauth_authority",
    "token_cache",
    "page_size",
    "refresh_interval",
    "layout",
    "threads",
    "date_groups",
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


def expand_path(value: str | Path) -> Path:
    """A path from the config file with `~` and environment variables
    (`$HOME`, `${XDG_DATA_HOME}`, ...) expanded. Unset variables are left
    as written, as os.path.expandvars does.
    """
    return Path(os.path.expandvars(str(value))).expanduser()


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
        out[key] = expand_path(value) if key in PATH_KEYS else value
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


def read_contacts_settings(doc: TOMLDocument, config_path: Path) -> tuple[Path, list[Path]]:
    """The address book settings, from the top-level [contacts] table:

        [contacts]
        file = "~/.config/ewstui/contacts.toml"   # optional; this is the default
        sync_from = ["~/Notes/people"]            # folders of person notes

    Parameters
    ----------
    doc : TOMLDocument
        The loaded config file.
    config_path : Path
        Where it lives (the address book defaults to its directory).

    Returns
    -------
    tuple of (Path, list of Path)
        The address book file and the folders to sync it from (paths
        expanded).

    Raises
    ------
    ConfigFileError
        On unknown keys or values of the wrong type.
    """
    from .contacts import build_default_contacts_path

    table = doc.get("contacts", {})
    unknown = set(table) - {"file", "sync_from"}
    if unknown:
        raise ConfigFileError(f"Unknown setting(s) in [contacts]: {', '.join(sorted(unknown))}. Allowed: file, sync_from")
    file = table.get("file")
    if file is not None and not isinstance(file, str):
        raise ConfigFileError("[contacts] file must be a path")
    folders = table.get("sync_from", [])
    folders = folders.unwrap() if hasattr(folders, "unwrap") else folders
    if isinstance(folders, str):
        folders = [folders]
    if not isinstance(folders, list) or not all(isinstance(f, str) for f in folders):
        raise ConfigFileError("[contacts] sync_from must be a list of folder paths")
    path = expand_path(file) if file else build_default_contacts_path(config_path)
    return path, [expand_path(f) for f in folders]


def read_agent_settings(doc: TOMLDocument, config_path: Path):
    """The [llm-agent] table (handing an email to an LLM agent, see
    ask_agent.py) as AgentSettings; the defaults if it's not there.

    Raises
    ------
    ConfigFileError
        On unknown keys or bad values.
    """
    from .ask_agent import AgentConfigError, parse_settings

    table = doc.get("llm-agent", {})
    try:
        return parse_settings(table.unwrap() if hasattr(table, "unwrap") else dict(table))
    except AgentConfigError as e:
        raise ConfigFileError(f"{e} (in {config_path})") from e


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
