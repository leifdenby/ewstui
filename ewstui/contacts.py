"""
Your own address book: ~/.config/ewstui/contacts.toml (next to config.toml).

    [[contact]]
    name = "Heleen ter Pelkwijk"
    email = "heleen@knmi.nl"

    [[contact]]
    name = "Chris Romick"
    email = "chris.romick@knmi.nl"
    org = "KNMI"
    source = "~/Notes/people/Chris Romick.md"

To / Cc lookups (Tab) search it alongside the Exchange directory. Entries
without `source` are yours and never changed by ewstui. Entries with one
are kept in sync from folders of Markdown notes, one person per note, set
in config.toml:

    [contacts]
    sync_from = ["~/Notes/people"]

A note gives its `# Heading` as the name (else the file name), every
address on its `**Email:**` line (the first one first) and its `**Org:**`.
The sync runs when ewstui starts if a note changed since the last one,
and on `ewstui contacts sync`.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit
from tomlkit import TOMLDocument

log = logging.getLogger(__name__)

HEADER = """\
ewstui address book: To / Cc lookups (Tab) search it as well as the directory.
Add people as [[contact]] entries with name, email and optionally org.
Entries with a `source` are kept in sync from the notes folders set in
config.toml ([contacts] sync_from); ewstui rewrites those, never yours.
"""

_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_FIELD = re.compile(r"^\s*[-*]\s*\*\*(?P<key>[^*:]+):\*\*\s*(?P<value>.*)$")


class ContactsError(ValueError):
    """The address book file can't be read or has a bad entry: shown as-is."""


@dataclass
class Contact:
    """One address in the address book."""

    name: str
    email: str
    org: str = ""
    source: str = ""  # the note it was synced from; empty for your own entries


@dataclass
class SyncResult:
    """What a sync from notes changed."""

    notes: int = 0  # notes read
    contacts: int = 0  # synced entries now in the file
    added: list[str] = field(default_factory=list)  # addresses new to the file
    removed: list[str] = field(default_factory=list)  # addresses dropped from it
    skipped: bool = False  # nothing changed since the last sync: not run


def build_default_contacts_path(config_path: Path) -> Path:
    """The address book's place: next to the config file.

    Parameters
    ----------
    config_path : Path
        The config.toml in use.

    Returns
    -------
    Path
        ``contacts.toml`` in the same directory.
    """
    return config_path.parent / "contacts.toml"


def load_contacts_document(path: Path) -> TOMLDocument:
    """The address book as a tomlkit document (an empty one with the
    explaining header if the file doesn't exist yet).

    Parameters
    ----------
    path : Path
        The contacts.toml.

    Returns
    -------
    TOMLDocument
        Its contents, comments and layout kept.

    Raises
    ------
    ContactsError
        If the file can't be parsed.
    """
    if not path.exists():
        doc = tomlkit.document()
        for line in HEADER.splitlines():
            doc.add(tomlkit.comment(line))
        return doc
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except tomlkit.exceptions.ParseError as e:
        raise ContactsError(f"Can't parse {path}: {e}") from e


def read_contacts(doc: TOMLDocument) -> list[Contact]:
    """The [[contact]] entries of a document, in file order.

    Parameters
    ----------
    doc : TOMLDocument
        A loaded address book.

    Returns
    -------
    list of Contact
        Entries without an email are skipped.
    """
    out = []
    for entry in doc.get("contact", []):
        email = str(entry.get("email", "")).strip()
        if not email:
            continue
        out.append(Contact(
            name=str(entry.get("name", "")).strip() or email,
            email=email,
            org=str(entry.get("org", "")).strip(),
            source=str(entry.get("source", "")).strip(),
        ))
    return out


def load_contacts(path: Path) -> list[Contact]:
    """The address book's entries (none if the file doesn't exist).

    Parameters
    ----------
    path : Path
        The contacts.toml.

    Returns
    -------
    list of Contact
        In file order.

    Raises
    ------
    ContactsError
        If the file can't be parsed.
    """
    if not path.exists():
        return []
    return read_contacts(load_contacts_document(path))


def fold_text(text: str) -> str:
    """Lower case without accents, for matching: "Körnich" -> "kornich".

    Parameters
    ----------
    text : str
        Any text.

    Returns
    -------
    str
        Casefolded, with combining marks removed.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def find_contacts(contacts: list[Contact], query: str) -> list[tuple[str, str]]:
    """The entries matching `query`: every word of it in the name, address
    or org (ignoring case and accents).

    Parameters
    ----------
    contacts : list of Contact
        The address book.
    query : str
        Part of a name, address or org.

    Returns
    -------
    list of tuple of (str, str)
        (name, address) pairs in file order, each address once.
    """
    words = fold_text(query).split()
    if not words:
        return []
    out, seen = [], set()
    for c in contacts:
        haystack = fold_text(f"{c.name} {c.email} {c.org}")
        if all(w in haystack for w in words) and c.email.casefold() not in seen:
            seen.add(c.email.casefold())
            out.append((c.name, c.email))
    return out


def build_contact_finder(path: Path) -> Callable[[str], list[tuple[str, str]]]:
    """A lookup function over the address book, re-reading the file on
    each call so edits show up without restarting.

    Parameters
    ----------
    path : Path
        The contacts.toml.

    Returns
    -------
    callable
        ``find(query) -> [(name, address)]``; empty if the file is missing
        or unreadable (logged).
    """

    def find(query: str) -> list[tuple[str, str]]:
        try:
            contacts = load_contacts(path)
        except (OSError, ContactsError):
            log.warning("can't read the address book %s", path, exc_info=True)
            return []
        return find_contacts(contacts, query)

    return find


def parse_person_note(path: Path) -> list[Contact]:
    """The contacts a Markdown person note gives: one per address on its
    `**Email:**` line, named by its `# Heading` (else the file name), with
    its `**Org:**` (up to any parenthesis).

    Parameters
    ----------
    path : Path
        A note such as ``people/Chris Romick.md``.

    Returns
    -------
    list of Contact
        Empty if the note has no address.
    """
    text = path.read_text(encoding="utf-8")
    name, org, emails = path.stem, "", []
    for line in text.splitlines():
        if line.startswith("# ") and name == path.stem:
            name = line[2:].strip() or path.stem
            continue
        m = _FIELD.match(line)
        if not m:
            continue
        key = m["key"].strip().casefold()
        if key == "email" and not emails:
            emails = list(dict.fromkeys(_EMAIL.findall(m["value"])))
        elif key == "org" and not org:
            org = m["value"].split("(")[0].strip().rstrip(".;,")
    source = shorten_home(path)
    return [Contact(name=name, email=email, org=org, source=source) for email in emails]


def shorten_home(path: Path) -> str:
    """`path` with the home directory written as ``~``.

    Parameters
    ----------
    path : Path
        An absolute path.

    Returns
    -------
    str
        E.g. ``~/Notes/people/Chris Romick.md``.
    """
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


def list_note_files(folders: list[Path]) -> list[Path]:
    """The Markdown notes directly in `folders` (not in subfolders), sorted.

    Parameters
    ----------
    folders : list of Path
        Folders of person notes; missing ones are skipped (logged).

    Returns
    -------
    list of Path
        The ``*.md`` files.
    """
    notes = []
    for folder in folders:
        if not folder.is_dir():
            log.warning("contacts sync_from folder %s doesn't exist", folder)
            continue
        notes.extend(sorted(p for p in folder.glob("*.md") if p.is_file()))
    return notes


def is_sync_needed(path: Path, folders: list[Path]) -> bool:
    """Whether a note (or a folder, for deleted and renamed notes) changed
    since the address book was last written.

    Parameters
    ----------
    path : Path
        The contacts.toml.
    folders : list of Path
        The sync_from folders.

    Returns
    -------
    bool
        True if the file is missing or older than any note or folder.
    """
    if not folders:
        return False
    if not path.exists():
        return True
    written = path.stat().st_mtime
    candidates = [f for f in folders if f.is_dir()] + list_note_files(folders)
    return any(p.stat().st_mtime > written for p in candidates)


def build_contact_table(contact: Contact) -> tomlkit.items.Table:
    """A [[contact]] entry for `contact`.

    Parameters
    ----------
    contact : Contact
        The entry's values.

    Returns
    -------
    tomlkit.items.Table
        name, email, then org and source when set, with a blank line
        before its header.
    """
    table = tomlkit.table()
    table.trivia.indent = "\n"  # (synced entries are recreated each time, so this never piles up)
    table["name"] = contact.name
    table["email"] = contact.email
    if contact.org:
        table["org"] = contact.org
    if contact.source:
        table["source"] = contact.source
    return table


def strip_trailing_blank_lines(table: tomlkit.items.Table) -> None:
    """Remove the blank lines at the end of a parsed [[contact]] entry
    (comments stay).

    Parameters
    ----------
    table : tomlkit.items.Table
        The entry; changed in place.

    Returns
    -------
    None
    """
    body = table.value.body
    while body and body[-1][0] is None and isinstance(body[-1][1], tomlkit.items.Whitespace):
        body.pop()


def sync_contacts_from_notes(path: Path, folders: list[Path], force: bool = False) -> SyncResult:
    """Rebuild the synced entries of the address book from the notes in
    `folders`, keeping your own entries (no `source`) as they are.

    Parameters
    ----------
    path : Path
        The contacts.toml (created if missing).
    folders : list of Path
        Folders of person notes.
    force : bool, optional
        Sync even if no note changed since the last sync.

    Returns
    -------
    SyncResult
        Counts and the addresses added and removed.

    Raises
    ------
    ContactsError
        If the existing file can't be parsed.
    OSError
        If a note can't be read or the file can't be written.
    """
    if not force and not is_sync_needed(path, folders):
        return SyncResult(skipped=True)
    doc = load_contacts_document(path)
    old = {c.email.casefold() for c in read_contacts(doc) if c.source}
    notes = list_note_files(folders)
    synced = [contact for note in notes for contact in parse_person_note(note)]

    # Edit the [[contact]] array in place: your entries are never touched,
    # so they keep their comments and blank lines (rebuilding the array
    # added a blank line after each of them on every sync).
    if "contact" not in doc:
        doc["contact"] = tomlkit.aot()
    entries = doc["contact"]
    for i in reversed(range(len(entries))):
        if str(entries[i].get("source", "")).strip():
            del entries[i]
    if len(entries) and synced:
        # Reading the file back puts the blank line before the first synced
        # entry at the end of your last one; drop it (the synced entry
        # brings its own), else each sync would add one more there.
        strip_trailing_blank_lines(entries[len(entries) - 1])
    for contact in synced:
        entries.append(build_contact_table(contact))
    if not len(entries):
        del doc["contact"]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    new = {c.email.casefold(): c.email for c in synced}
    return SyncResult(
        notes=len(notes),
        contacts=len(synced),
        added=sorted(email for key, email in new.items() if key not in old),
        removed=sorted(key for key in old if key not in new),
    )


def sync_contacts_quietly(path: Path, folders: list[Path]) -> SyncResult | None:
    """The sync at startup: run it if needed, never fail (problems are
    logged, the address book is left as it was).

    Parameters
    ----------
    path : Path
        The contacts.toml.
    folders : list of Path
        The sync_from folders.

    Returns
    -------
    SyncResult or None
        None if the sync failed.
    """
    try:
        return sync_contacts_from_notes(path, folders)
    except (OSError, ContactsError, UnicodeDecodeError):
        log.warning("address book sync into %s failed", path, exc_info=True)
        return None
