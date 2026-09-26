"""
Fake in-memory stand-ins for MailClient / CalendarClient, used by
`--demo`. Same method signatures, same dataclasses, no network at
all — lets you build and test the Textual UI without an Exchange
server to point at.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .ews_client import (
    AttachmentSummary,
    EventSummary,
    FolderSummary,
    MeetingInfo,
    MessageDetail,
    MessageSummary,
    BLOCKING_BUSY_TYPES,
    BusySlot,
    MovedMessage,
    Room,
    RoomAvailability,
    RoomMatch,
    RoomsDay,
    _unique_path,
)

DEMO_ROOMS = [
    Room("Room 4B (8 pers)", "room-4b@corp.example"),
    Room("Aquarium (4 pers)", "aquarium@corp.example"),
    Room("Boardroom (16 pers)", "boardroom@corp.example"),
]
# What / search can find beyond DEMO_ROOMS: the org's room lists, plus a
# person the directory search also turns up (as ResolveNames would).
DEMO_ROOM_LISTS = DEMO_ROOMS + [
    Room("Havgus (8 pers)", "havgus@corp.example"),
    Room("Stormvejr (12 pers)", "stormvejr@corp.example"),
]
DEMO_PEOPLE = [Room("Havgus Hansen", "hh@corp.example")]

# Conversation indexes for the demo "EWS bridge project" thread: a 22-byte
# header, plus 5 bytes per reply level (your reply s1, then their m2).
_EWS_THREAD = bytes(range(22))
_EWS_S1 = _EWS_THREAD + b"\x01" * 5
_EWS_M2 = _EWS_S1 + b"\x02" * 5

# A demo body with links, for trying U (links in an email).
_MAINTENANCE = (
    "Hi all,\n\n"
    "Email will be unavailable Saturday 02:00-06:00. Details on the status page <https://status.corp.example/maintenance>.\n"
    "Questions? Join the drop-in call: https://meet.corp.example/j/123456 or write to mailto:it-support@corp.example.\n"
    "Background reading (see www.corp.example/it/faq).\n\n"
    "Unsubscribe <https://lists.corp.example/unsubscribe?id=42>\n"
)

# Display names, and a long recipient list (for the folded To/Cc line, e).
_STAFF = [
    "Anna Berg", "Bo Christensen", "Carla Dahl", "David Eriksen", "Emma Frost", "Frederik Gram",
    "Gitte Holm", "Henrik Iversen", "Ida Jensen", "Jonas Krogh", "Karen Lund", "Lars Madsen",
    "Maja Nielsen", "Niels Olsen", "Olivia Poulsen", "Peter Quist", "Rikke Rasmussen", "Søren Svendsen",
    "Tina Thomsen", "Ulrik Vang", "Vibeke Winther", "William Yde", "Zara Østergaard", "Åse Aagaard",
]
_STAFF_ADDRESSES = [f"{n.split()[0].lower()}.{n.split()[1].lower()}@corp.example" for n in _STAFF]
_NAMES = {
    "finance@corp.example": "Finance Team",
    "colleague@corp.example": "Casey Colleague",
    "it-notifications@corp.example": "IT Notifications",
    "friend@corp.example": "Freddie Friend",
    "you@corp.example": "You",
    "team@corp.example": "Project Team",
    **dict(zip(_STAFF_ADDRESSES, _STAFF)),
}

_INVITE = "Let's plan the next sprint. Bring your estimates.\n\nJoin: https://meet.corp.example/j/424242\n"


def _today(now: datetime, hour: int, days: int = 0) -> datetime:
    return (now + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


def _demo_invites(now: datetime) -> list[MessageDetail]:
    """An invite clashing with the demo 1:1 at 14:00, and a cancellation
    of the demo calendar's "Friday retro" (e4)."""
    return [
        MessageDetail(
            id="inv1", changekey="c1", subject="Sprint planning",
            sender="colleague@corp.example", received=now - timedelta(days=3),
            is_read=True, has_attachments=False,
            to=["you@corp.example", "team@corp.example"], cc=[], body_text=_INVITE, kind="invite",
            meeting=MeetingInfo(
                kind="invite", start=_today(now, 14), end=_today(now, 15), location="Room 4B",
                organizer="colleague@corp.example",
            ),
        ),
        MessageDetail(
            id="can1", changekey="c1", subject="Canceled: Friday retro",
            sender="colleague@corp.example", received=now - timedelta(days=4),
            is_read=True, has_attachments=False,
            to=["you@corp.example", "team@corp.example"], cc=[], body_text="This meeting has been cancelled.",
            kind="cancellation",
            meeting=MeetingInfo(
                kind="cancellation", start=_today(now, 15, days=1), end=_today(now, 16, days=1),
                location="Aquarium", organizer="colleague@corp.example", calendar_item_id="e4",
            ),
        ),
    ]


_LOREM = (
    "This is a demo message body. Run without --demo and with --email "
    "(plus auth flags) to talk to a real Exchange mailbox over EWS.\n\n"
    "-- \nSent from ewstui"
)


class DemoMailClient:
    def __init__(self, page_size: int = 50, calendar: DemoCalendarClient | None = None, with_invites: bool = False):
        """`with_invites` adds a meeting invite and a cancellation to the
        Inbox (`--demo` does; most tests keep the plain four emails).
        Answering the invite / removing the cancelled meeting changes
        `calendar` (if given), like Exchange does."""
        self.page_size = page_size
        self.sent: list[dict] = []  # demo "outbox": what send_mail/reply would have sent
        self.calendar = calendar
        self.invite_responses: list[dict] = []  # what respond_to_invite would have done
        self._folders = [
            FolderSummary(id="inbox", name="Inbox", total_count=6 if with_invites else 4, unread_count=2, depth=0),
            FolderSummary(id="sent", name="Sent Items", total_count=2, unread_count=0, depth=0),
            FolderSummary(id="drafts", name="Drafts", total_count=1, unread_count=0, depth=0),
            FolderSummary(id="archive", name="Archive", total_count=1, unread_count=0, depth=0),
            FolderSummary(id="trash", name="Deleted Items", total_count=0, unread_count=0, depth=0),
            # Outlook housekeeping: not listed unless unhidden (. shows it, H unhides it).
            FolderSummary(id="sync", name="Sync Issues", total_count=0, unread_count=0, depth=0, hidden_by_default=True),
        ]
        now = datetime.now()
        self._attachments: dict[str, list[tuple[str, str, bytes]]] = {
            "m1": [
                ("Q3-budget.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 b"PK\x03\x04 fake xlsx bytes for demo purposes only"),
            ],
            "s2": [
                ("weekly-report.pdf", "application/pdf", b"%PDF-1.4 fake pdf bytes for demo purposes only"),
            ],
        }
        self._messages: dict[str, list[MessageDetail]] = {
            "inbox": [
                MessageDetail(
                    id="m1", changekey="c1", subject="Q3 budget review",
                    sender="finance@corp.example", received=now - timedelta(hours=1),
                    is_read=False, has_attachments=True,
                    to=["you@corp.example"], cc=[], body_text=_LOREM,
                ),
                MessageDetail(
                    id="m2", changekey="c2", subject="Re: EWS bridge project",
                    sender="colleague@corp.example", received=now - timedelta(hours=3),
                    is_read=False, has_attachments=False,
                    to=["you@corp.example"], cc=["team@corp.example"], body_text=_LOREM,
                    conversation_id="conv-ews", depth=2, conversation_index=_EWS_M2,  # answers your s1
                ),
                MessageDetail(
                    id="m3", changekey="c3", subject="IT maintenance window this weekend",
                    sender="it-notifications@corp.example", received=now - timedelta(days=1),
                    is_read=True, has_attachments=False,
                    to=_STAFF_ADDRESSES[:20], cc=_STAFF_ADDRESSES[20:] + ["team@corp.example"],
                    body_text=_MAINTENANCE,
                ),
                MessageDetail(
                    id="m4", changekey="c4", subject="Lunch Friday?",
                    sender="friend@corp.example", received=now - timedelta(days=2),
                    is_read=True, has_attachments=False,
                    to=["you@corp.example"], cc=[], body_text=_LOREM,
                ),
                *(_demo_invites(now) if with_invites else []),
            ],
            "sent": [
                MessageDetail(
                    id="s1", changekey="c1", subject="Re: EWS bridge project",
                    sender="you@corp.example", received=now - timedelta(days=1, hours=2),
                    is_read=True, has_attachments=False, to=["colleague@corp.example"], cc=[], body_text=_LOREM,
                    conversation_id="conv-ews", depth=1, conversation_index=_EWS_S1,
                ),
                MessageDetail(
                    id="s2", changekey="c2", subject="Weekly report",
                    sender="you@corp.example", received=now - timedelta(days=6),
                    is_read=True, has_attachments=True, to=["team@corp.example"], cc=[], body_text=_LOREM,
                ),
            ],
            "drafts": [
                MessageDetail(
                    id="d1", changekey="c1", subject="(untitled)",
                    sender="you@corp.example", received=None,
                    is_read=True, has_attachments=False, to=[], cc=[], body_text="",
                ),
            ],
            "archive": [
                MessageDetail(
                    id="a1", changekey="c1", subject="Old thread",
                    sender="someone@corp.example", received=now - timedelta(days=90),
                    is_read=True, has_attachments=False, to=["you@corp.example"], cc=[], body_text=_LOREM,
                ),
            ],
        }
        for msgs in self._messages.values():  # every demo message gets an Internet Message-ID
            for m in msgs:
                m.internet_message_id = m.internet_message_id or f"<{m.id}@demo.corp.example>"
                m.names = {a: _NAMES[a] for a in [m.sender, *m.to, *m.cc] if a in _NAMES}

    def list_folders(self) -> list[FolderSummary]:
        return list(self._folders)

    def find_message(self, internet_message_id: str, hint_folder_id: str | None = None) -> MovedMessage | None:
        order = ([hint_folder_id] if hint_folder_id else []) + [f for f in self._messages if f != hint_folder_id]
        for folder_id in order:
            for m in self._messages.get(folder_id, []):
                if m.internet_message_id == internet_message_id:
                    return MovedMessage(folder_id=folder_id, message_id=m.id)
        return None

    def default_folder_id(self) -> str:
        return "inbox"

    def list_messages(self, folder_id: str, offset: int = 0, limit: int | None = None) -> list[MessageSummary]:
        msgs = self._messages.get(folder_id, [])
        limit = limit or self.page_size
        return [
            MessageSummary(
                m.id, m.changekey, m.subject, m.sender, m.received, m.is_read, m.has_attachments,
                conversation_id=m.conversation_id, depth=m.depth, folder_id=folder_id,
                conversation_index=m.conversation_index, kind=m.kind,
            )
            for m in msgs[offset : offset + limit]
        ]

    def sent_folder_id(self) -> str:
        return "sent"

    def get_message(self, folder_id: str, message_id: str) -> MessageDetail:
        for m in self._messages.get(folder_id, []):
            if m.id == message_id:
                return m
        raise KeyError(message_id)

    def mark_read(self, folder_id: str, message_id: str, read: bool = True) -> None:
        m = self.get_message(folder_id, message_id)
        m.is_read = read

    def move_message(self, folder_id: str, message_id: str, dest_folder_id: str) -> MovedMessage:
        moving = self.get_message(folder_id, message_id)
        self._messages[folder_id] = [m for m in self._messages[folder_id] if m.id != message_id]
        dest = self._messages.setdefault(dest_folder_id, [])
        dest.append(moving)
        dest.sort(key=lambda m: m.received or datetime.min, reverse=True)
        return MovedMessage(folder_id=dest_folder_id, message_id=message_id)

    def delete_message(self, folder_id: str, message_id: str) -> MovedMessage | None:
        if folder_id == "trash":
            self.get_message(folder_id, message_id)  # KeyError if missing, like the live client
            self._messages["trash"] = [m for m in self._messages["trash"] if m.id != message_id]
            return None
        return self.move_message(folder_id, message_id, "trash")

    def archive_message(self, folder_id: str, message_id: str) -> MovedMessage:
        return self.move_message(folder_id, message_id, "archive")

    def respond_to_invite(self, folder_id: str, message_id: str, response: str, note: str = "", send: bool = True) -> None:
        m = self.get_message(folder_id, message_id)
        if m.meeting is None or m.meeting.kind != "invite":
            raise ValueError("not a meeting invite")
        self.invite_responses.append({"id": message_id, "response": response, "note": note, "send": send})
        m.meeting.my_response = {"accept": "Accept", "tentative": "Tentative", "decline": "Decline"}[response]
        m.changekey += "+"
        if self.calendar is not None:
            self.calendar.answer_invite(m.id, m.subject, m.meeting, response)

    def remove_cancelled_meeting(self, folder_id: str, message_id: str) -> bool:
        m = self.get_message(folder_id, message_id)
        if self.calendar is None or m.meeting is None or not m.meeting.calendar_item_id:
            return False
        return self.calendar.remove_event(m.meeting.calendar_item_id)

    def send_mail(self, to, subject, body, cc=None) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body, "cc": cc or []})

    def reply(
        self,
        folder_id: str,
        message_id: str,
        subject: str,
        body: str,
        to: list[str] | None = None,
        reply_all: bool = False,
    ) -> None:
        self.get_message(folder_id, message_id)  # KeyError if missing, like the live client
        self.sent.append(
            {"in_reply_to": message_id, "to": to, "subject": subject, "body": body, "reply_all": reply_all}
        )

    def list_attachments(self, folder_id: str, message_id: str) -> list[AttachmentSummary]:
        out = []
        for name, content_type, content in self._attachments.get(message_id, []):
            out.append(AttachmentSummary(id=f"{message_id}:{name}", name=name, content_type=content_type, size=len(content), is_file=True))
        return out

    def save_attachment(self, folder_id: str, message_id: str, attachment_id: str, dest_dir) -> "Path":
        from pathlib import Path

        for name, _content_type, content in self._attachments.get(message_id, []):
            if f"{message_id}:{name}" == attachment_id or name == attachment_id:
                dest_dir = Path(dest_dir)
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = _unique_path(dest_dir / name)
                dest_path.write_bytes(content)
                return dest_path
        raise LookupError(f"No attachment with id {attachment_id!r} on this message")


class DemoCalendarClient:
    def __init__(self):
        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self._events = [
            EventSummary(
                id="e1", changekey="c1", subject="Standup",
                start=today.replace(hour=9), end=today.replace(hour=9, minute=15),
                location="Zoom", organizer="you@corp.example", is_all_day=False,
            ),
            EventSummary(
                id="e2", changekey="c2", subject="1:1 with manager",
                start=today.replace(hour=14), end=today.replace(hour=14, minute=30),
                location="Room 4B", organizer="boss@corp.example", is_all_day=False,
            ),
            EventSummary(
                id="e3", changekey="c3", subject="Company holiday",
                start=today + timedelta(days=3), end=today + timedelta(days=4),
                location="", organizer="hr@corp.example", is_all_day=True,
            ),
            EventSummary(  # cancelled by the demo cancellation "can1"
                id="e4", changekey="c4", subject="Friday retro",
                start=today + timedelta(days=1, hours=15), end=today + timedelta(days=1, hours=16),
                location="Aquarium", organizer="colleague@corp.example", is_all_day=False,
            ),
        ]

        # Room bookings, keyed by room address. Room 4B is taken during the
        # 1:1 above; like real Exchange rooms, the subject shown is the
        # organizer's name.
        self.room_bookings: dict[str, list[BusySlot]] = {
            "room-4b@corp.example": [
                BusySlot(today.replace(hour=14), today.replace(hour=14, minute=30), "Busy", "Boss Person")
            ],
        }

    def list_events(self, start: datetime, end: datetime) -> list[EventSummary]:
        return [e for e in self._events if e.start < end and e.end > start]

    def answer_invite(self, invite_id: str, subject: str, meeting: MeetingInfo, response: str) -> None:
        """Accepted / tentative: the meeting is in the calendar; declined: it isn't."""
        event_id = f"e-{invite_id}"
        self._events = [e for e in self._events if e.id != event_id]
        if response != "decline":
            self._events.append(
                EventSummary(
                    id=event_id, changekey="c1", subject=subject, start=meeting.start, end=meeting.end,
                    location=meeting.location, organizer=meeting.organizer, is_all_day=meeting.is_all_day,
                )
            )

    def remove_event(self, event_id: str) -> bool:
        before = len(self._events)
        self._events = [e for e in self._events if e.id != event_id]
        return len(self._events) < before

    def search_rooms(self, query: str) -> list[RoomMatch]:
        needle = query.strip().casefold()
        if not needle:
            return []
        hits = [RoomMatch(r, "room list") for r in DEMO_ROOM_LISTS if needle in r.name.casefold()]
        hits += [RoomMatch(r, "directory") for r in DEMO_PEOPLE if needle in r.name.casefold()]
        return hits

    def rooms_day(self, rooms: list[Room], day) -> RoomsDay:
        start = datetime.combine(day, datetime.min.time())
        end = start + timedelta(days=1)
        out = []
        for room in rooms:
            busy = [
                b for b in self.room_bookings.get(room.email, [])
                if b.busy_type in BLOCKING_BUSY_TYPES and b.start < end and b.end > start
            ]
            out.append(RoomAvailability(room=room, free=not busy, busy=busy))
        return RoomsDay(day=day, work_hours=None, rooms=out)  # None: UI falls back to 08:00-17:00

    def create_event(self, subject, start, end, location="", body="", resources=None) -> None:
        self._events.append(
            EventSummary(
                id=f"e{len(self._events) + 1}", changekey="c1", subject=subject,
                start=start, end=end, location=location, organizer="you@corp.example", is_all_day=False,
            )
        )
        for email in resources or []:  # the demo rooms always accept
            self.room_bookings.setdefault(email, []).append(BusySlot(start, end, "Busy", "Demo User"))

    def delete_event(self, event_id: str) -> None:
        self._events = [e for e in self._events if e.id != event_id]
