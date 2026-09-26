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

import functools
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import NamedTuple

from exchangelib import Account, EWSDateTime, EWSTimeZone, Message

log = logging.getLogger(__name__)


def _is_dead_connection(e: Exception) -> bool:
    import requests
    from exchangelib.errors import ErrorTimeoutExpired

    return isinstance(e, (ErrorTimeoutExpired, requests.exceptions.ConnectionError, requests.exceptions.Timeout))


def retry_on_dead_connection(func):
    """Retry a *read-only* call once if the connection turned out to be
    dead (timeout / reset). exchangelib has already retired the broken
    session by then, so the retry goes out on a fresh connection. Not for
    writes (send, move, delete, ...): if the first attempt did reach the
    server, repeating it would do it twice.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if not _is_dead_connection(e):
                raise
            log.warning("%s: connection problem (%s), retrying once on a fresh connection", func.__name__, e)
            return func(*args, **kwargs)

    return wrapper


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
    # For the thread view: which conversation the message belongs to, how
    # deep a reply it is (0 = starts the thread), its raw conversation index
    # (a reply's index is its parent's plus 5 bytes, which is how the tree
    # is built) and the folder it lives in (threads can include your replies
    # from Sent Items).
    conversation_id: str | None = None
    depth: int = 0
    folder_id: str | None = None
    conversation_index: bytes | None = None


@dataclass
class MessageDetail(MessageSummary):
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    body_text: str = ""
    # The Internet Message-ID header (<...@host>): unlike the EWS item id it
    # stays the same when the message is moved, so the priority list uses
    # it to find a message again.
    internet_message_id: str | None = None
    # Display names of the sender and recipients, by address ("Jane Doe"),
    # for the reading pane; sender/to/cc stay plain addresses for replying.
    names: dict[str, str] = field(default_factory=dict)


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
class Room:
    """A bookable meeting room: a display name and its mailbox address."""
    name: str
    email: str


class RoomMatch(NamedTuple):
    """A room search result; `source` is "room list" or "directory" (the
    latter can also be a person, so the UI shows where it came from)."""
    room: Room
    source: str


class BusySlot(NamedTuple):
    start: datetime
    end: datetime
    busy_type: str
    # What the free/busy "Detailed" view says about the booking, if the
    # room shares details. Exchange rooms by default replace the subject
    # with the organizer's name (AddOrganizerToSubject/DeleteSubject), so
    # this is often who booked it; None when the room only shares busy/free.
    subject: str | None = None


@dataclass
class RoomAvailability:
    room: Room
    free: bool
    busy: list[BusySlot] = field(default_factory=list)
    error: str | None = None  # e.g. unknown address; the room is then neither free nor busy


# Free/busy states that block a booking. "Free" doesn't; "NoData" means
# the server has no information, which we don't treat as busy either.
BLOCKING_BUSY_TYPES = {"Busy", "Tentative", "OOF", "WorkingElsewhere"}


@dataclass
class RoomsDay:
    """All rooms' free/busy for one day. `busy` times are naive local.
    `work_hours` is your Outlook working day for that weekday, or None
    if the server didn't say (the UI then uses DEFAULT_WORK_HOURS).
    """
    day: date
    work_hours: tuple[time, time] | None
    rooms: list[RoomAvailability]


DEFAULT_WORK_HOURS = (time(8, 0), time(17, 0))


def _working_hours(view, day: date) -> tuple[time, time] | None:
    """Working hours from a FreeBusyView for `day`'s weekday. Assumes the
    mailbox's working-hours time zone is the local one (the usual case).
    """
    if view is None or isinstance(view, Exception):
        return None
    for period in getattr(view, "working_hours", None) or []:
        if day.isoweekday() in (period.weekdays or []) and period.start and period.end and period.start < period.end:
            return period.start, period.end
    return None


def availability_from_view(room: Room, view, start: datetime, end: datetime) -> RoomAvailability:
    """Turn one exchangelib FreeBusyView (or the exception EWS returned
    for that mailbox) into a RoomAvailability for [start, end).
    """
    if isinstance(view, Exception):
        return RoomAvailability(room=room, free=False, error=f"{type(view).__name__}: {view}")
    busy = [
        BusySlot(ev.start, ev.end, ev.busy_type, _booking_subject(ev.details))
        for ev in (view.calendar_events or [])
        if ev.busy_type in BLOCKING_BUSY_TYPES and ev.start < end and ev.end > start
    ]
    return RoomAvailability(room=room, free=not busy, busy=busy)


def _booking_subject(details) -> str | None:
    """CalendarEventDetails -> text to show, or None if the room shares none."""
    if details is None:
        return None
    if details.is_private:
        return "private"
    return (details.subject or "").strip() or None


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


def _is_mail_folder(folder) -> bool:
    folder_class = getattr(folder, "folder_class", None)
    return folder_class is None or folder_class.startswith("IPF.Note")


def _reply_depth(conversation_index: bytes | None) -> int:
    """PidTagConversationIndex is a 22-byte header plus 5 bytes per reply
    level, so its length says how deep in the thread a message is."""
    if not conversation_index:
        return 0
    return max(0, (len(conversation_index) - 22) // 5)


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


def _names(item) -> dict[str, str]:
    """Address -> display name for the sender and all recipients."""
    names = {}
    people = [getattr(item, a, None) for a in ("sender", "author", "organizer")]
    for attr in ("to_recipients", "cc_recipients", "required_attendees", "optional_attendees"):
        people += getattr(item, attr, None) or []
    for person in people:
        mailbox = getattr(person, "mailbox", person)
        address, name = getattr(mailbox, "email_address", None), getattr(mailbox, "name", None)
        if address and name and name != address:
            names.setdefault(str(address), str(name))
    return names


def _to_local(value, tz) -> datetime:
    """EWS returns timed events as tz-aware EWSDateTime (usually UTC) and
    all-day events as EWSDate. The UI works in naive local time, so
    convert both: datetimes to local wall-clock time, dates to midnight.
    """
    if isinstance(value, datetime):  # check first: datetime is a subclass of date
        if value.tzinfo is not None:
            value = value.astimezone(tz)
        return datetime.combine(value.date(), value.time())  # plain, naive datetime
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    raise TypeError(f"expected a date or datetime, got {value!r}")


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

    @retry_on_dead_connection
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

    @retry_on_dead_connection
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

    @retry_on_dead_connection
    def list_messages(self, folder_id: str, offset: int = 0, limit: int | None = None) -> list[MessageSummary]:
        folder = self._folder_by_id(folder_id)
        limit = limit or self.page_size
        qs = folder.all().order_by("-datetime_received").only(
            "subject", "sender", "datetime_received", "is_read", "has_attachments",
            "conversation_id", "conversation_index",
        )
        out = []
        for item in qs[offset : offset + limit]:
            conversation = getattr(item, "conversation_id", None)
            index = getattr(item, "conversation_index", None)
            out.append(
                MessageSummary(
                    id=item.id,
                    changekey=item.changekey,
                    subject=item.subject or "(no subject)",
                    sender=_sender(item),
                    received=item.datetime_received,
                    is_read=_is_read(item),
                    has_attachments=bool(getattr(item, "has_attachments", False)),
                    conversation_id=getattr(conversation, "id", None),
                    depth=_reply_depth(index),
                    folder_id=folder_id,
                    conversation_index=bytes(index) if index else None,
                )
            )
        return out

    @retry_on_dead_connection
    def sent_folder_id(self) -> str:
        """Sent Items, via its well-known id (works with localized names)."""
        return self.account.sent.id

    @retry_on_dead_connection
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
            internet_message_id=getattr(item, "message_id", None),
            names=_names(item),
        )

    @retry_on_dead_connection
    def find_message(self, internet_message_id: str, hint_folder_id: str | None = None) -> MovedMessage | None:
        """Where a message is now, by its Internet Message-ID (which, unlike
        the EWS id, survives moves). Looks in `hint_folder_id` first, then
        all mail folders in one FindItem request. None if it's nowhere.
        """
        from exchangelib.folders import FolderCollection

        def first(queryset):
            item = next(iter(queryset.only("parent_folder_id")[:1]), None)
            return MovedMessage(folder_id=item.parent_folder_id.id, message_id=item.id) if item else None

        if hint_folder_id:
            try:
                found = first(self._folder_by_id(hint_folder_id).filter(message_id=internet_message_id))
            except KeyError:
                found = None
            if found:
                return found
        folders = [f for f in self._all_folders() if _is_mail_folder(f)]
        return first(FolderCollection(account=self.account, folders=folders).filter(message_id=internet_message_id))

    def _all_folders(self) -> list:
        out, stack = [], list(self.account.msg_folder_root.children)
        while stack:
            f = stack.pop()
            out.append(f)
            stack.extend(f.children)
        return out

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

    @retry_on_dead_connection
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

    @retry_on_dead_connection
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
        self._room_directory: list[Room] | None = None  # all rooms in the org's room lists, fetched once

    @retry_on_dead_connection
    def search_rooms(self, query: str) -> list[RoomMatch]:
        """Rooms matching `query` (case-insensitive, name or address):
        first from the organisation's room lists (GetRoomLists/GetRooms,
        if the admins set any up), then a directory name search
        (ResolveNames), which can also return people.
        """
        needle = query.strip().casefold()
        if not needle:
            return []
        matches: dict[str, RoomMatch] = {}
        for room in self._room_list_rooms():
            if needle in room.name.casefold() or needle in room.email.casefold():
                matches.setdefault(room.email.casefold(), RoomMatch(room, "room list"))
        try:
            found = list(self.account.protocol.resolve_names(names=[query.strip()], search_scope="ActiveDirectory"))
        except Exception:  # noqa: BLE001 - no directory hits is not an error for the user
            log.info("directory search for %r failed", query, exc_info=True)
            found = []
        for mailbox in found:
            email = getattr(mailbox, "email_address", None)  # errors (no results) come back as exceptions
            if email:
                room = Room(name=mailbox.name or email, email=email)
                matches.setdefault(email.casefold(), RoomMatch(room, "directory"))
        return list(matches.values())

    def _room_list_rooms(self) -> list[Room]:
        if self._room_directory is None:
            rooms: list[Room] = []
            try:
                for room_list in self.account.protocol.get_roomlists():
                    for r in self.account.protocol.get_rooms(room_list.email_address):
                        rooms.append(Room(name=r.name or r.email_address, email=r.email_address))
            except Exception:  # noqa: BLE001 - many orgs have no room lists; fall back to the directory
                log.info("room lists unavailable", exc_info=True)
            self._room_directory = rooms
        return self._room_directory

    @retry_on_dead_connection
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
                    start=_to_local(item.start, self.tz),
                    end=_to_local(item.end, self.tz),
                    location=item.location or "",
                    organizer=str(item.organizer.email_address) if item.organizer else "",
                    is_all_day=bool(item.is_all_day),
                )
            )
        return out

    @retry_on_dead_connection
    def rooms_day(self, rooms: list[Room], day: date) -> RoomsDay:
        """Free/busy for every room over the whole of `day`, plus your own
        Outlook working hours for that weekday, in one EWS
        GetUserAvailability call. Needs no access to the rooms' calendars.
        """
        start = datetime.combine(day, time.min)
        start_ews = EWSDateTime.from_datetime(start).astimezone(self.tz)
        end_ews = EWSDateTime.from_datetime(start + timedelta(days=1)).astimezone(self.tz)
        # Your own mailbox goes first: only for its working hours.
        views = list(
            self.account.protocol.get_free_busy_info(
                accounts=[(self.account.primary_smtp_address, "Required", False)]
                + [(room.email, "Room", False) for room in rooms],
                start=start_ews,
                end=end_ews,
                requested_view="Detailed",
            )
        )
        own, room_views = views[0], views[1:]
        # EWS answers in request order, with an exception in place of a
        # view for a mailbox it couldn't look up.
        results = []
        for room, view in zip(rooms, room_views):
            r = availability_from_view(room, view, start_ews, end_ews)
            r.busy = [b._replace(start=_to_local(b.start, self.tz), end=_to_local(b.end, self.tz)) for b in r.busy]
            results.append(r)
        return RoomsDay(day=day, work_hours=_working_hours(own, day), rooms=results)

    def create_event(
        self,
        subject: str,
        start: datetime,
        end: datetime,
        location: str = "",
        body: str = "",
        resources: list[str] | None = None,
    ) -> None:
        """With `resources` (room addresses), the rooms are invited as
        resource attendees and invitations are sent, which is what
        actually books them (the room's booking assistant accepts or
        declines). Without, it's a plain appointment in your calendar.
        """
        from exchangelib import Attendee, CalendarItem, Mailbox
        from exchangelib.items import SEND_TO_ALL_AND_SAVE_COPY

        item = CalendarItem(
            account=self.account,
            folder=self.account.calendar,
            subject=subject,
            start=EWSDateTime.from_datetime(start).astimezone(self.tz),
            end=EWSDateTime.from_datetime(end).astimezone(self.tz),
            location=location,
            body=body,
        )
        if resources:
            item.resources = [
                Attendee(mailbox=Mailbox(email_address=email), response_type="Unknown") for email in resources
            ]
            item.save(send_meeting_invitations=SEND_TO_ALL_AND_SAVE_COPY)
        else:
            item.save()

    def delete_event(self, event_id: str) -> None:
        item = self.account.calendar.get(id=event_id)
        item.delete()
