"""
A local, todo.txt-formatted priority list for emails.

This is deliberately *not* an Exchange folder — it's a plain text file
on disk (~/.local/share/ewstui/priorities.todo.txt by default),
compatible with any other todo.txt tool (topydo, Todo.txt CLI,
mobile apps that sync the file via Dropbox/Syncthing/etc). Every
entry ewstui creates carries the `@email` context plus a couple of
informal key:value fields (`id:`, `folder:`) that let ewstui jump back
to the original message; other todo.txt tools will just show those as
literal text, which is fine — the file stays valid todo.txt either way.
Entries tagged with the older `+email` project are still recognised,
and switch to `@email` the next time ewstui changes them.

Format reminder (https://github.com/todotxt/todo.txt):
    pending:  (A) 2026-09-24 Subject text @email id:AAMk... folder:inbox from:x@y.z
    done:     x 2026-09-25 2026-09-24 Subject text @email pri:A id:AAMk... folder:inbox from:x@y.z

`pri:A` is added on completion to remember the original priority,
following the same convention several todo.txt clients (e.g. topydo)
use, since the spec itself drops `(A)` once a task is marked done.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DEFAULT_PATH = Path.home() / ".local" / "share" / "ewstui" / "priorities.todo.txt"
EMAIL_TAG = "email"  # written as the @email context
NOTE_SEPARATOR = " — "  # an email entry's text: "Subject — note"

_PRIORITY_RE = re.compile(r"^\(([A-Z])\)\s+")
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+")
_DONE_RE = re.compile(r"^x\s+")


@dataclass
class PriorityEntry:
    """One line of the todo.txt file, parsed. `key` is an in-memory
    identifier (not stored in the file) so the UI has something stable
    to key table rows on across a single run.
    """

    key: str
    priority: str | None       # 'A'..'Z', or None (unprioritized)
    completed: bool
    completion_date: str | None
    creation_date: str | None
    description: str           # human-readable text, tags stripped out
    projects: list[str] = field(default_factory=list)   # e.g. ["email"]
    contexts: list[str] = field(default_factory=list)
    kv: dict[str, str] = field(default_factory=dict)     # e.g. {"id": "...", "folder": "inbox"}
    # The line exactly as it was in the file; written back unchanged unless
    # ewstui modifies the entry (dirty), so other tools' formatting survives.
    raw: str | None = None
    dirty: bool = False

    @property
    def is_email(self) -> bool:
        # @email (context) is what ewstui writes; +email (project) is the
        # older tag, still recognised.
        return EMAIL_TAG in self.contexts or EMAIL_TAG in self.projects

    @property
    def message_id(self) -> str | None:
        return self.kv.get("id")

    @property
    def folder_id(self) -> str | None:
        return self.kv.get("folder")

    @property
    def internet_id(self) -> str | None:
        """Internet Message-ID (`msgid:<...>`): the message's lasting
        identity; `id:`/`folder:` are just where it was last seen."""
        return self.kv.get("msgid")

    def to_line(self) -> str:
        tokens: list[str] = []
        if self.completed:
            tokens.append("x")
            if self.completion_date:
                tokens.append(self.completion_date)
            if self.creation_date:
                tokens.append(self.creation_date)
        else:
            if self.priority:
                tokens.append(f"({self.priority})")
            if self.creation_date:
                tokens.append(self.creation_date)

        tokens.append(self.description)
        projects, contexts = self.projects, self.contexts
        if self.is_email:  # written as the @email context (migrates old +email)
            projects = [p for p in projects if p != EMAIL_TAG]
            contexts = [c for c in contexts if c != EMAIL_TAG] + [EMAIL_TAG]
        tokens.extend(f"+{p}" for p in projects)
        tokens.extend(f"@{c}" for c in contexts)

        kv = dict(self.kv)
        if self.completed and self.priority:
            kv.setdefault("pri", self.priority)
        tokens.extend(f"{k}:{v}" for k, v in kv.items())

        return " ".join(t for t in tokens if t)


def parse_line(line: str) -> PriorityEntry | None:
    raw = line.rstrip("\n")
    if not raw.strip():
        return None

    rest = raw
    completed = False
    completion_date = None
    creation_date = None
    priority = None

    m = _DONE_RE.match(rest)
    if m:
        completed = True
        rest = rest[m.end():]
        m2 = _DATE_RE.match(rest)
        if m2:
            completion_date = m2.group(1)
            rest = rest[m2.end():]
            m3 = _DATE_RE.match(rest)
            if m3:
                creation_date = m3.group(1)
                rest = rest[m3.end():]
    else:
        m = _PRIORITY_RE.match(rest)
        if m:
            priority = m.group(1)
            rest = rest[m.end():]
        m2 = _DATE_RE.match(rest)
        if m2:
            creation_date = m2.group(1)
            rest = rest[m2.end():]

    projects: list[str] = []
    contexts: list[str] = []
    kv: dict[str, str] = {}
    desc_words: list[str] = []

    for token in rest.split():
        if len(token) > 1 and token[0] == "+" and token[1] not in "-+":
            projects.append(token[1:])
        elif len(token) > 1 and token[0] == "@" and token[1] not in "-@":
            contexts.append(token[1:])
        elif ":" in token and not token.startswith(("http:", "https:")) and "//" not in token:
            key, _, value = token.partition(":")
            if key.isidentifier() or key.replace("-", "_").isidentifier():
                kv[key] = value
                if key == "pri" and completed and priority is None:
                    priority = value
                continue
            desc_words.append(token)
        else:
            desc_words.append(token)

    return PriorityEntry(
        key=str(uuid.uuid4()),
        priority=priority,
        completed=completed,
        completion_date=completion_date,
        creation_date=creation_date,
        description=" ".join(desc_words),
        projects=projects,
        contexts=contexts,
        kv=kv,
        raw=raw,
    )


class PriorityStore:
    """The todo.txt file and the operations the UI needs.

    Always in step with the file on disk: every query and mutation first
    re-reads it if it changed (mtime/size), and mutations are
    read-modify-write, so edits made by other todo.txt tools or a sync
    client are seen and never overwritten. Lines ewstui doesn't change —
    other tasks, blank lines — are written back byte-for-byte; only
    entries it adds or edits are re-serialized.

    Entry keys are `line-number:hash-of-line`, so they survive a reload of
    an unchanged file but won't match a line that moved or changed on disk
    in the meantime: mutations then return False instead of touching the
    wrong task.
    """

    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        self._lines: list[str | PriorityEntry] = []  # blank lines kept as str
        self._stat: tuple[int, int] | None = None
        self.load()

    @property
    def _entries(self) -> list[PriorityEntry]:
        return [item for item in self._lines if isinstance(item, PriorityEntry)]

    def _file_stat(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def load(self) -> None:
        self._lines = []
        self._stat = self._file_stat()
        if self._stat is None:
            return
        for n, line in enumerate(self.path.read_text().splitlines()):
            entry = parse_line(line)
            if entry is None:
                self._lines.append(line)
                continue
            entry.key = f"{n}:{hashlib.sha1(line.encode()).hexdigest()[:10]}"
            self._lines.append(entry)

    def _refresh(self) -> None:
        """Re-read the file if it changed on disk since we last saw it."""
        if self.changed_on_disk():
            self.load()

    def changed_on_disk(self) -> bool:
        """Has the file changed (edited elsewhere) since we last read or
        wrote it? Just a stat — cheap enough to poll every second."""
        return self._file_stat() != self._stat

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        out = []
        for item in self._lines:
            if isinstance(item, str):
                out.append(item)
            else:
                out.append(item.to_line() if item.dirty or item.raw is None else item.raw)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text("\n".join(out) + "\n" if out else "")
        tmp.replace(self.path)
        self.load()  # fresh keys and stat for what's now on disk

    def _find(self, key: str) -> PriorityEntry | None:
        return next((e for e in self._entries if e.key == key), None)

    # -- queries --------------------------------------------------------

    def list_entries(self, include_completed: bool = False, email_only: bool = False) -> list[PriorityEntry]:
        self._refresh()
        entries = [e for e in self._entries if (include_completed or not e.completed) and (e.is_email or not email_only)]

        def sort_key(e: PriorityEntry):
            # Priority A..Z first (in order), then unprioritized, then by creation date.
            pri_rank = ord(e.priority) if e.priority else ord("Z") + 1
            return (pri_rank, e.creation_date or "")

        return sorted(entries, key=sort_key)

    def find_by_message_id(self, message_id: str) -> PriorityEntry | None:
        self._refresh()
        for e in self._entries:
            if e.message_id == message_id and not e.completed:
                return e
        return None

    def priorities_by_message(self) -> dict[str, str | None]:
        """message id -> priority letter (None: on the list, no priority)
        for open email entries — for the mail list's P column."""
        self._refresh()
        return {e.message_id: e.priority for e in self._entries if e.is_email and e.message_id and not e.completed}

    # -- mutations (all read-modify-write; False = key no longer matches) --

    def add_email(
        self,
        message_id: str,
        folder_id: str,
        subject: str,
        sender: str,
        priority: str | None,
        note: str | None = None,
        internet_id: str | None = None,
    ) -> PriorityEntry:
        """Add the email, or if it's on the list already, leave that entry as
        it is (its priority and note too) except for setting `priority` if
        one is given and filling in a missing Message-ID."""
        existing = self.find_by_message_id(message_id)  # also refreshes from disk
        if existing is not None:
            changed = False
            if priority is not None and existing.priority != priority:
                existing.priority, changed = priority, True
            if internet_id and not existing.internet_id:
                existing.kv["msgid"], changed = internet_id, True
            if changed:
                existing.dirty = True
                self.save()
            return self.find_by_message_id(message_id) or existing

        description = subject if not note else f"{subject}{NOTE_SEPARATOR}{note}"
        entry = PriorityEntry(
            key=str(uuid.uuid4()),
            priority=priority,
            completed=False,
            completion_date=None,
            creation_date=date.today().isoformat(),
            description=_sanitize_description(description),
            projects=[],
            contexts=[EMAIL_TAG],
            kv={"id": message_id, "folder": folder_id, "from": sender, **({"msgid": internet_id} if internet_id else {})},
        )
        self._lines.append(entry)
        self.save()
        return self.find_by_message_id(message_id) or entry

    def set_note(self, message_id: str, note: str, subject: str | None = None) -> bool:
        """Replace the note on the email's open entry (empty: no note),
        keeping the subject part of its text. False if it has no entry."""
        entry = self.find_by_message_id(message_id)  # also refreshes from disk
        if entry is None:
            return False
        text, _ = split_note(entry.description, subject)
        note = _sanitize_description(note)
        entry.description = _sanitize_description(f"{text}{NOTE_SEPARATOR}{note}" if note else text)
        entry.dirty = True
        self.save()
        return True

    def add_task(self, description: str, priority: str | None = None) -> PriorityEntry:
        """A plain, manually-typed todo.txt entry — not tied to any
        email (no @email tag, no id:/folder: fields).
        """
        self._refresh()
        entry = PriorityEntry(
            key=str(uuid.uuid4()),
            priority=priority,
            completed=False,
            completion_date=None,
            creation_date=date.today().isoformat(),
            description=_sanitize_description(description),
            projects=[],
            contexts=[],
            kv={},
        )
        self._lines.append(entry)
        self.save()
        return entry

    def relocate(self, old_message_id: str, folder_id: str, message_id: str) -> bool:
        """The message moved (EWS gave it a new id): point its open entry at
        the new location. False if it has no entry."""
        entry = self.find_by_message_id(old_message_id)
        if entry is None:
            return False
        entry.kv["id"], entry.kv["folder"] = message_id, folder_id
        entry.dirty = True
        self.save()
        return True

    def relocate_key(self, key: str, folder_id: str, message_id: str) -> bool:
        """Same, for the entry with this key (after finding it by Message-ID)."""
        self._refresh()
        entry = self._find(key)
        if entry is None:
            return False
        entry.kv["id"], entry.kv["folder"] = message_id, folder_id
        entry.dirty = True
        self.save()
        return True

    def remember_internet_id(self, message_id: str, internet_id: str) -> bool:
        """Fill in a missing Message-ID for an entry added before ewstui
        stored them (done when the message is opened). False if no change."""
        entry = self.find_by_message_id(message_id)
        if entry is None or entry.internet_id:
            return False
        entry.kv["msgid"] = internet_id
        entry.dirty = True
        self.save()
        return True

    def set_priority(self, key: str, priority: str | None) -> bool:
        return self.set_priority_many({key}, priority)

    def set_priority_many(self, keys: set[str], priority: str | None) -> bool:
        """All-or-nothing: if any key no longer matches (file changed on
        disk), nothing is changed and False is returned."""
        self._refresh()
        entries = [self._find(k) for k in keys]
        if any(e is None for e in entries):
            return False
        for e in entries:
            e.priority = priority
            e.dirty = True
        self.save()
        return True

    def toggle_complete(self, key: str) -> bool:
        self._refresh()
        e = self._find(key)
        if e is None:
            return False
        e.completed = not e.completed
        e.completion_date = date.today().isoformat() if e.completed else None
        e.dirty = True
        self.save()
        return True

    def remove(self, key: str) -> bool:
        self._refresh()
        e = self._find(key)
        if e is None:
            return False
        self._lines.remove(e)
        self.save()
        return True


def split_note(description: str, subject: str | None = None) -> tuple[str, str]:
    """An email entry's text as (subject part, note): "Subject — note".
    With the email's `subject`, a subject that itself contains " — " is
    still split in the right place."""
    if subject:
        subject = _sanitize_description(subject)
        if description == subject:
            return description, ""
        if description.startswith(subject + NOTE_SEPARATOR):
            return subject, description[len(subject) + len(NOTE_SEPARATOR):]
    text, sep, note = description.partition(NOTE_SEPARATOR)
    return (text, note) if sep else (description, "")


def _sanitize_description(text: str) -> str:
    # todo.txt is one entry per line; collapse newlines/whitespace so a
    # multi-line subject (rare, but possible) can't corrupt the file.
    return " ".join(text.split())
