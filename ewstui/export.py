"""
`ewstui export`: copy one mail folder to plain files, for reading and
searching outside ewstui (grep, scripts, an AI assistant).

    <out>/index.json                       where it came from + one record per email
    <out>/<date>_<subject>_<hash>.md       the email as text
    <out>/attachments/<same stem>/<file>   its file attachments

Works on the MailClient interface only (list_folders, list_messages,
get_message, list_attachments, save_attachment, sent_folder_id), so the
demo backend stands in for tests. Re-running adds what's new and rewrites
what changed (by change key); emails already exported stay as they are.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .ews_client import FolderSummary, MessageDetail, MessageSummary, safe_filename

FORMAT = "ewstui-export/1"
INDEX = "index.json"
PAGE = 100
# --with-sent: how far before the folder's oldest email to look for your
# replies in Sent Items (a conversation can start before what was filed).
SENT_LOOKBACK = timedelta(days=30)


class ExportError(RuntimeError):
    pass


@dataclass
class Stats:
    folder_total: int = 0   # emails in the folder now
    exported: int = 0       # records in index.json still in the folder (or Sent Items)
    new: int = 0
    updated: int = 0        # changed on the server since the last run, rewritten
    unchanged: int = 0
    from_sent: int = 0      # your replies from Sent Items (--with-sent)
    attachments: int = 0
    skipped_embedded: int = 0  # forwarded emails etc. attached as items, not files


def folder_paths(folders: list[FolderSummary]) -> dict[str, FolderSummary]:
    """"Projects/0 - Proposals/SBEP" -> folder, from list_folders()'s
    depth-first order (each folder follows its parent, one level deeper)."""
    out, stack = {}, []
    for f in folders:
        del stack[f.depth:]
        stack.append(f.name)
        out["/".join(stack)] = f
    return out


def find_folder(folders: list[FolderSummary], path: str) -> tuple[str, FolderSummary]:
    """The folder at `path` (case-insensitive, surrounding slashes ignored)
    with its path as the mailbox spells it."""
    paths = folder_paths(folders)
    wanted = "/".join(p.strip() for p in path.strip("/").split("/")).casefold()
    for p, f in paths.items():
        if p.casefold() == wanted:
            return p, f
    close = difflib.get_close_matches(wanted, [p.casefold() for p in paths], n=5, cutoff=0.4)
    spelled = {p.casefold(): p for p in paths}
    hint = "".join(f"\n  {spelled[c]}" for c in close) or "\n  (no similar folder paths)"
    raise ExportError(f"no folder {path!r}. Closest:{hint}")


def iter_all(client, folder_id: str):
    """Every message in the folder, newest first, a page at a time."""
    offset = 0
    while True:
        page = client.list_messages(folder_id, offset=offset, limit=PAGE)
        yield from page
        if len(page) < PAGE:
            return
        offset += len(page)


def _local(dt: datetime | None) -> datetime | None:
    """Exchange times are tz-aware (UTC); show them in local time."""
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone()


def _iso(dt: datetime | None) -> str | None:
    dt = _local(dt)
    return dt.isoformat(timespec="seconds") if dt else None


def _slug(subject: str, width: int = 40) -> str:
    s = re.sub(r"^((re|sv|fw|fwd|vs|aw|wg)\s*:\s*)+", "", subject.strip(), flags=re.I)
    s = re.sub(r"[^\w]+", "-", s.casefold()).strip("-_")
    return s[:width].rstrip("-_") or "no-subject"


def message_key(detail: MessageDetail) -> str:
    """What identifies an email across runs: its Internet Message-ID,
    which survives moves (the EWS item id doesn't)."""
    return detail.internet_message_id or f"ews:{detail.id}"


def filename(detail: MessageDetail) -> str:
    when = _local(detail.received)
    stamp = when.strftime("%Y-%m-%d_%H%M") if when else "undated"
    digest = hashlib.sha1(message_key(detail).encode()).hexdigest()[:8]
    return f"{stamp}_{_slug(detail.subject)}_{digest}.md"


def _person(address: str, names: dict[str, str]) -> str:
    name = names.get(address)
    return f"{name} <{address}>" if name else address


def message_text(detail: MessageDetail, attachments: list[str]) -> str:
    """The email as a small text file: headers, then Exchange's plain text body."""
    lines = [
        f"Subject: {detail.subject}",
        f"From: {_person(detail.sender, detail.names)}",
        f"To: {', '.join(_person(a, detail.names) for a in detail.to)}",
    ]
    if detail.cc:
        lines.append(f"Cc: {', '.join(_person(a, detail.names) for a in detail.cc)}")
    lines.append(f"Date: {_iso(detail.received) or '(none)'}")
    lines.append(f"Message-ID: {detail.internet_message_id or '(none)'}")
    if attachments:
        lines.append(f"Attachments: {', '.join(attachments)}")
    return "\n".join(lines) + "\n\n" + (detail.body_text or "").rstrip() + "\n"


def email_record(summary: MessageSummary, detail: MessageDetail, folder_id: str, source_folder: str,
                 file: str, attachments: list[str]) -> dict:
    def person(address):
        return {"name": detail.names.get(address), "address": address}

    return {
        "file": file,
        "date": _iso(detail.received),
        "subject": detail.subject,
        "from": person(detail.sender),
        "to": [person(a) for a in detail.to],
        "cc": [person(a) for a in detail.cc],
        "source_folder": source_folder,
        "present": True,
        "message_id": detail.internet_message_id,
        "ews_item_id": summary.id,
        "ews_folder_id": folder_id,
        "changekey": summary.changekey,
        "conversation_id": summary.conversation_id,
        "attachments": attachments,
    }


def load_index(out_dir: Path) -> dict | None:
    path = Path(out_dir) / INDEX
    if not path.exists():
        return None
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ExportError(f"can't read {path}: {e}") from e
    if index.get("format") != FORMAT:
        raise ExportError(f"{path} isn't an {FORMAT} index (format: {index.get('format')!r})")
    return index


def write_index(out_dir: Path, index: dict) -> None:
    """Via a temporary file and a rename: an interrupted run leaves the
    previous index, never half of one."""
    path = Path(out_dir) / INDEX
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def check_source(existing: dict | None, source: dict, out_dir: Path) -> None:
    """One folder of one mailbox per directory: refuse to mix another in."""
    if not existing:
        return
    old = existing.get("source", {})
    for key in ("mailbox", "folder_id"):
        if old.get(key) != source.get(key):
            raise ExportError(
                f"{Path(out_dir) / INDEX} is an export of {old.get('folder_path')!r} in {old.get('mailbox')}, "
                f"not {source.get('folder_path')!r} in {source.get('mailbox')}. Use another --out directory."
            )


def git_commit() -> str | None:
    """The ewstui checkout's commit, if it's run from one ("-dirty" with
    uncommitted changes)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent), "describe", "--always", "--dirty"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _save_attachments(client, folder_id: str, summary: MessageSummary, out_dir: Path, stem: str,
                      stats: Stats) -> list[str]:
    """Download the email's file attachments (an existing copy of the same
    name is kept, not downloaded again). Paths relative to `out_dir`."""
    dest = out_dir / "attachments" / stem
    saved = []
    for att in client.list_attachments(folder_id, summary.id):
        if not att.is_file:
            stats.skipped_embedded += 1
            continue
        path = dest / safe_filename(att.name)
        if not path.exists():
            path = client.save_attachment(folder_id, summary.id, att.id, dest)
        saved.append(path.relative_to(out_dir).as_posix())
    return saved


def _sent_replies(client, folder_messages: list[MessageSummary]) -> tuple[str, list[MessageSummary]]:
    """Your replies in Sent Items to the folder's conversations, as the
    thread view finds them: matched on conversation id, going back to
    SENT_LOOKBACK before the folder's oldest email."""
    sent_id = client.sent_folder_id()
    conversations = {m.conversation_id for m in folder_messages if m.conversation_id}
    dates = [m.received for m in folder_messages if m.received]
    if not conversations or not dates:
        return sent_id, []
    cutoff = min(dates) - SENT_LOOKBACK
    replies = []
    for m in iter_all(client, sent_id):
        if m.received and m.received < cutoff:
            break
        if m.conversation_id in conversations:
            replies.append(m)
    return sent_id, replies


def export_folder(client, folder: FolderSummary, out_dir: Path, *, source: dict, exported_by: dict,
                  with_sent: bool = False, attachments: bool = True, now=None, progress=None) -> Stats:
    """Export `folder` into `out_dir` (see the module docstring); returns
    what happened. `source` and `exported_by` go into index.json as they
    are; `progress(done, total)` is called after each email."""
    out_dir = Path(out_dir)
    existing = load_index(out_dir)
    check_source(existing, source, out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    records = {r["message_id"] or f"ews:{r['ews_item_id']}": r for r in (existing or {}).get("emails", [])}
    by_item = {r["ews_item_id"]: r for r in records.values()}
    for r in records.values():
        r["present"] = False  # until seen in this run

    stats = Stats()
    in_folder = list(iter_all(client, folder.id))
    stats.folder_total = len(in_folder)
    todo = [(m, folder.id, folder.name) for m in in_folder]
    if with_sent:
        sent_id, replies = _sent_replies(client, in_folder)
        todo += [(m, sent_id, "Sent Items") for m in replies]

    for done, (summary, folder_id, folder_name) in enumerate(todo, 1):
        known = by_item.get(summary.id)
        if known and known["changekey"] == summary.changekey and (out_dir / known["file"]).exists():
            known["present"] = True
            stats.unchanged += 1
        else:
            detail = client.get_message(folder_id, summary.id)
            previous = records.get(message_key(detail))
            file = previous["file"] if previous else filename(detail)
            saved = []
            if attachments and summary.has_attachments:
                saved = _save_attachments(client, folder_id, summary, out_dir, Path(file).stem, stats)
            (out_dir / file).write_text(message_text(detail, saved), encoding="utf-8")
            records[message_key(detail)] = email_record(summary, detail, folder_id, folder_name, file, saved)
            if previous:
                stats.updated += 1
            else:
                stats.new += 1
        if folder_id != folder.id:
            stats.from_sent += 1
        if progress:
            progress(done, len(todo))

    stats.attachments = sum(len(r["attachments"]) for r in records.values() if r["present"])
    stats.exported = sum(1 for r in records.values() if r["present"])
    synced = _iso(now or datetime.now().astimezone())
    write_index(out_dir, {
        "format": FORMAT,
        "description": (
            f"Copy of the Exchange folder {source.get('folder_path')!r} in {source.get('mailbox')}, made with "
            "`ewstui export`. A read-only snapshot: changes here don't reach Exchange. Emails no longer in the "
            "folder are kept with present: false. Refresh with: ewstui export --update <this directory>"
        ),
        "source": source,
        "exported_by": exported_by,
        "first_synced": (existing or {}).get("first_synced") or synced,
        "last_synced": synced,
        "counts": {k: v for k, v in asdict(stats).items() if k not in ("new", "updated", "unchanged")},
        "emails": sorted(records.values(), key=lambda r: r["date"] or "", reverse=True),
    })
    return stats
