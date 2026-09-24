"""
A local, todo.txt-formatted priority list for emails.

This is deliberately *not* an Exchange folder — it's a plain text file
on disk (~/.local/share/ewstui/priorities.todo.txt by default),
compatible with any other todo.txt tool (topydo, Todo.txt CLI,
mobile apps that sync the file via Dropbox/Syncthing/etc). Every
entry ewstui creates carries the `+email` project tag plus a couple
of informal key:value fields (`id:`, `folder:`) that let ewstui jump
back to the original message; other todo.txt tools will just show
those as literal text, which is fine — the file stays valid todo.txt
either way.

Format reminder (https://github.com/todotxt/todo.txt):
    pending:  (A) 2026-09-24 Subject text +email id:AAMk... folder:inbox from:x@y.z
    done:     x 2026-09-25 2026-09-24 Subject text +email pri:A id:AAMk... folder:inbox from:x@y.z

`pri:A` is added on completion to remember the original priority,
following the same convention several todo.txt clients (e.g. topydo)
use, since the spec itself drops `(A)` once a task is marked done.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DEFAULT_PATH = Path.home() / ".local" / "share" / "ewstui" / "priorities.todo.txt"

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

    @property
    def is_email(self) -> bool:
        return "email" in self.projects

    @property
    def message_id(self) -> str | None:
        return self.kv.get("id")

    @property
    def folder_id(self) -> str | None:
        return self.kv.get("folder")

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
        tokens.extend(f"+{p}" for p in self.projects)
        tokens.extend(f"@{c}" for c in self.contexts)

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
    )


class PriorityStore:
    """Loads/saves the todo.txt file and offers the operations the UI needs.
    Every mutating method writes the file back out immediately — this is
    a small, local, single-user file, not something worth batching.
    """

    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        self._entries: list[PriorityEntry] = []
        self.load()

    def load(self) -> None:
        self._entries = []
        if not self.path.exists():
            return
        for line in self.path.read_text().splitlines():
            entry = parse_line(line)
            if entry is not None:
                self._entries.append(entry)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text("\n".join(e.to_line() for e in self._entries) + "\n" if self._entries else "")
        tmp.replace(self.path)

    # -- queries --------------------------------------------------------

    def list_entries(self, include_completed: bool = False) -> list[PriorityEntry]:
        entries = self._entries if include_completed else [e for e in self._entries if not e.completed]

        def sort_key(e: PriorityEntry):
            # Priority A..Z first (in order), then unprioritized, then by creation date.
            pri_rank = ord(e.priority) if e.priority else ord("Z") + 1
            return (pri_rank, e.creation_date or "")

        return sorted(entries, key=sort_key)

    def find_by_message_id(self, message_id: str) -> PriorityEntry | None:
        for e in self._entries:
            if e.message_id == message_id and not e.completed:
                return e
        return None

    # -- mutations --------------------------------------------------------

    def add_email(
        self,
        message_id: str,
        folder_id: str,
        subject: str,
        sender: str,
        priority: str | None,
        note: str | None = None,
    ) -> PriorityEntry:
        existing = self.find_by_message_id(message_id)
        if existing is not None:
            existing.priority = priority
            self.save()
            return existing

        description = subject if not note else f"{subject} — {note}"
        entry = PriorityEntry(
            key=str(uuid.uuid4()),
            priority=priority,
            completed=False,
            completion_date=None,
            creation_date=date.today().isoformat(),
            description=_sanitize_description(description),
            projects=["email"],
            contexts=[],
            kv={"id": message_id, "folder": folder_id, "from": sender},
        )
        self._entries.append(entry)
        self.save()
        return entry

    def add_task(self, description: str, priority: str | None = None) -> PriorityEntry:
        """A plain, manually-typed todo.txt entry — not tied to any
        email (no +email tag, no id:/folder: fields).
        """
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
        self._entries.append(entry)
        self.save()
        return entry

    def set_priority(self, key: str, priority: str | None) -> None:
        for e in self._entries:
            if e.key == key:
                e.priority = priority
                self.save()
                return

    def set_priority_many(self, keys: set[str], priority: str) -> None:
        for e in self._entries:
            if e.key in keys:
                e.priority = priority
        self.save()

    def toggle_complete(self, key: str) -> None:
        for e in self._entries:
            if e.key == key:
                e.completed = not e.completed
                e.completion_date = date.today().isoformat() if e.completed else None
                self.save()
                return

    def remove(self, key: str) -> None:
        self._entries = [e for e in self._entries if e.key != key]
        self.save()


def _sanitize_description(text: str) -> str:
    # todo.txt is one entry per line; collapse newlines/whitespace so a
    # multi-line subject (rare, but possible) can't corrupt the file.
    return " ".join(text.split())
