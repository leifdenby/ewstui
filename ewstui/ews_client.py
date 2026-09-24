"""
Thin wrappers around an authenticated `exchangelib.Account`.

Every method here does exactly one live EWS round trip (or a small,
obvious number of them) and returns plain dataclasses that the UI
layer can render — nothing is cached between calls. If you re-open a
folder, it's fetched again.

`MessageSummary` / `MessageDetail` / `EventSummary` are the only
shapes the UI layer (app.py, widgets/*) should ever touch — this
keeps `exchangelib` objects out of the widget code, so the demo
backend in `demo_backend.py` can stand in for this module exactly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from exchangelib import Account, EWSDateTime, EWSTimeZone, Message


@dataclass
class FolderSummary:
    id: str
    name: str
    total_count: int
    unread_count: int
    depth: int = 0


@dataclass
class MessageSummary:
    id: str
    changekey: str
    subject: str
    sender: str
    received: datetime | None
    is_read: bool
    has_attachments: bool


@dataclass
class MessageDetail(MessageSummary):
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    body_text: str = ""


@dataclass
class AttachmentSummary:
    id: str
    name: str
    content_type: str
    size: int              # bytes
    is_file: bool           # False for embedded-item attachments (forwarded emails, etc.) — not downloadable in v1


@dataclass
class MovedMessage:
    """Where a message ended up after a move. EWS gives a moved item a
    new id, so this is what's needed to find it again (e.g. to undo).
    """
    folder_id: str
    message_id: str


@dataclass
class EventSummary:
    id: str
    changekey: str
    subject: str
    start: datetime
    end: datetime
    location: str
    organizer: str
    is_all_day: bool


# Mail folders don't only hold Messages: Drafts can hold unsent meeting
# invites (CalendarItem), and other folders contacts, tasks, etc. Those
# lack sender/is_read/to_recipients, so read fields defensively.

def _email(mailbox) -> str:
    """Mailbox or Attendee (which wraps a Mailbox) -> address or name."""
    mailbox = getattr(mailbox, "mailbox", mailbox)
    return str(getattr(mailbox, "email_address", None) or getattr(mailbox, "name", None) or "")


def _sender(item) -> str:
    # Messages have `sender`; calendar items have `organizer` instead.
    for attr in ("sender", "author", "organizer"):
        address = _email(getattr(item, attr, None))
        if address:
            return address
    return "(unknown)"


def _is_read(item) -> bool:
    # Items without a read flag (calendar items, contacts, ...) count as read.
    return bool(getattr(item, "is_read", True))


def _addresses(item, *attrs: str) -> list[str]:
    """Addresses from the first of `attrs` the item has (e.g. to_recipients
    for a Message, required_attendees for a CalendarItem).
    """
    for attr in attrs:
        if hasattr(item, attr):
            return [a for a in (_email(r) for r in (getattr(item, attr) or [])) if a]
    return []


def _unique_path(path: Path) -> Path:
    """If `path` already exists, append " (1)", " (2)", ... before the
    extension until it doesn't — never silently overwrite a previous
    download.
    """
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 1
    while True:
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


class MailClient:
    """Live mail operations. `account.inbox` etc. are exchangelib Folder
    objects — traversing `.walk()` hits EWS each time, by design.
    """

    def __init__(self, account: Account, page_size: int = 50):
        self.account = account
        self.page_size = page_size

    def list_folders(self) -> list[FolderSummary]:
        folders = []

        def walk(folder, depth=0):
            folders.append(
                FolderSummary(
                    id=folder.id,
                    name=folder.name,
                    total_count=folder.total_count or 0,
                    unread_count=folder.unread_count or 0,
                    depth=depth,
                )
            )
            for child in folder.children:
                walk(child, depth + 1)

        # exchangelib caches the whole folder tree (and its counts) on the
        # root after the first walk; drop it so every listing is current.
        self.account.root.clear_cache()
        walk(self.account.root / "Top of Information Store" if False else self.account.msg_folder_root)
        return folders

    def default_folder_id(self) -> str:
        """The folder to open on startup: the Inbox, via its EWS
        well-known id so it's found regardless of display language.
        """
        return self.account.inbox.id

    def _folder_by_id(self, folder_id: str):
        # exchangelib folders are usually navigated by object, but the UI
        # only knows ids (strings) — look the folder up by walking from
        # msg_folder_root. For a hot path you'd want to cache this
        # per-session (folder ids don't change often); left as a TODO
        # since correctness > cleverness for a v1.
        stack = [self.account.msg_folder_root]
        while stack:
            f = stack.pop()
            if f.id == folder_id:
                return f
            stack.extend(f.children)
        raise KeyError(f"no such folder: {folder_id}")

    def list_messages(self, folder_id: str, offset: int = 0, limit: int | None = None) -> list[MessageSummary]:
        folder = self._folder_by_id(folder_id)
        limit = limit or self.page_size
        qs = folder.all().order_by("-datetime_received").only(
            "subject", "sender", "datetime_received", "is_read", "has_attachments"
        )
        out = []
        for item in qs[offset : offset + limit]:
            out.append(
                MessageSummary(
                    id=item.id,
                    changekey=item.changekey,
                    subject=item.subject or "(no subject)",
                    sender=_sender(item),
                    received=item.datetime_received,
                    is_read=_is_read(item),
                    has_attachments=bool(getattr(item, "has_attachments", False)),
                )
            )
        return out

    def get_message(self, folder_id: str, message_id: str) -> MessageDetail:
        folder = self._folder_by_id(folder_id)
        item = folder.get(id=message_id)
        return MessageDetail(
            id=item.id,
            changekey=item.changekey,
            subject=item.subject or "(no subject)",
            sender=_sender(item),
            received=item.datetime_received,
            is_read=_is_read(item),
            has_attachments=bool(getattr(item, "has_attachments", False)),
            to=_addresses(item, "to_recipients", "required_attendees"),
            cc=_addresses(item, "cc_recipients", "optional_attendees"),
            body_text=getattr(item, "text_body", None) or "",
        )

    def mark_read(self, folder_id: str, message_id: str, read: bool = True) -> None:
        folder = self._folder_by_id(folder_id)
        item = folder.get(id=message_id)
        item.is_read = read
        item.save(update_fields=["is_read"])

    def move_message(self, folder_id: str, message_id: str, dest_folder_id: str) -> MovedMessage:
        item = self._folder_by_id(folder_id).get(id=message_id)
        dest = self._folder_by_id(dest_folder_id)
        item.move(dest)
        return MovedMessage(folder_id=dest.id, message_id=item.id)

    def delete_message(self, folder_id: str, message_id: str) -> MovedMessage | None:
        """Move to Deleted Items and return its new location there, so
        the delete can be undone. Deleting from Deleted Items itself
        soft-deletes (Recoverable Items, only reachable via Outlook/OWA
        "Recover deleted items") and returns None: not undoable here.
        """
        folder = self._folder_by_id(folder_id)
        item = folder.get(id=message_id)
        trash = self.account.trash
        if folder.id == trash.id:
            item.soft_delete()
            return None
        # Explicit move rather than item.move_to_trash(): same result,
        # but move() gives us the item's new id in Deleted Items.
        item.move(trash)
        return MovedMessage(folder_id=trash.id, message_id=item.id)

    def _find_archive_folder(self):
        # "Archive" is typically just a regular folder under the mailbox
        # (created by Outlook's own Archive/Move actions), not a special
        # EWS well-known folder — so we look it up by name rather than
        # via account.archive_msg_folder_root, which only exists for
        # mailboxes with a separate Online Archive mailbox enabled.
        stack = [self.account.msg_folder_root]
        while stack:
            f = stack.pop()
            if (f.name or "").strip().lower() == "archive":
                return f
            stack.extend(f.children)
        raise LookupError(
            "No folder named 'Archive' was found in this mailbox. "
            "Create one (or move a message into one manually once) and try again."
        )

    def archive_message(self, folder_id: str, message_id: str) -> MovedMessage:
        folder = self._folder_by_id(folder_id)
        item = folder.get(id=message_id)
        archive_folder = self._find_archive_folder()
        item.move(archive_folder)
        return MovedMessage(folder_id=archive_folder.id, message_id=item.id)

    def list_attachments(self, folder_id: str, message_id: str) -> list[AttachmentSummary]:
        from exchangelib import FileAttachment

        folder = self._folder_by_id(folder_id)
        item = folder.get(id=message_id)
        out = []
        for att in item.attachments or []:
            is_file = isinstance(att, FileAttachment)
            out.append(
                AttachmentSummary(
                    id=att.attachment_id.id if att.attachment_id else att.name,
                    name=att.name or "(unnamed)",
                    content_type=getattr(att, "content_type", "") or "",
                    size=getattr(att, "size", 0) or 0,
                    is_file=is_file,
                )
            )
        return out

    def save_attachment(self, folder_id: str, message_id: str, attachment_id: str, dest_dir: Path) -> Path:
        from exchangelib import FileAttachment

        folder = self._folder_by_id(folder_id)
        item = folder.get(id=message_id)
        match = next(
            (a for a in (item.attachments or []) if (a.attachment_id and a.attachment_id.id == attachment_id) or a.name == attachment_id),
            None,
        )
        if match is None:
            raise LookupError(f"No attachment with id {attachment_id!r} on this message")
        if not isinstance(match, FileAttachment):
            raise NotImplementedError("This is an embedded item attachment (e.g. a forwarded email), not a file — not downloadable yet")

        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = _unique_path(dest_dir / (match.name or "attachment"))
        dest_path.write_bytes(match.content)
        return dest_path

    def send_mail(self, to: list[str], subject: str, body: str, cc: list[str] | None = None) -> None:
        msg = Message(
            account=self.account,
            folder=self.account.sent,
            subject=subject,
            body=body,
            to_recipients=to,
            cc_recipients=cc or None,
        )
        msg.send_and_save()  # sends and keeps a copy in Sent Items

    def reply(
        self,
        folder_id: str,
        message_id: str,
        subject: str,
        body: str,
        to: list[str] | None = None,
        reply_all: bool = False,
    ) -> None:
        """`to` overrides the reply recipients (plain reply only —
        exchangelib's reply_all always goes to the original recipients).
        """
        folder = self._folder_by_id(folder_id)
        item = folder.get(id=message_id)
        if reply_all:
            item.reply_all(subject=subject, body=body)
        else:
            item.reply(subject=subject, body=body, to_recipients=to or None)


class CalendarClient:
    """Live calendar operations against account.calendar."""

    def __init__(self, account: Account):
        self.account = account
        self.tz = EWSTimeZone.localzone()

    def list_events(self, start: datetime, end: datetime) -> list[EventSummary]:
        start_ews = EWSDateTime.from_datetime(start).astimezone(self.tz)
        end_ews = EWSDateTime.from_datetime(end).astimezone(self.tz)
        qs = self.account.calendar.view(start=start_ews, end=end_ews).only(
            "subject", "start", "end", "location", "organizer", "is_all_day"
        )
        out = []
        for item in qs:
            out.append(
                EventSummary(
                    id=item.id,
                    changekey=item.changekey,
                    subject=item.subject or "(no subject)",
                    start=item.start,
                    end=item.end,
                    location=item.location or "",
                    organizer=str(item.organizer.email_address) if item.organizer else "",
                    is_all_day=bool(item.is_all_day),
                )
            )
        return out

    def create_event(self, subject: str, start: datetime, end: datetime, location: str = "", body: str = "") -> None:
        from exchangelib import CalendarItem

        item = CalendarItem(
            account=self.account,
            folder=self.account.calendar,
            subject=subject,
            start=EWSDateTime.from_datetime(start).astimezone(self.tz),
            end=EWSDateTime.from_datetime(end).astimezone(self.tz),
            location=location,
            body=body,
        )
        item.save()

    def delete_event(self, event_id: str) -> None:
        item = self.account.calendar.get(id=event_id)
        item.delete()
