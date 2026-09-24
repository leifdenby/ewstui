"""
Fake in-memory stand-ins for MailClient / CalendarClient, used by
`--demo`. Same method signatures, same dataclasses, no network at
all — lets you build and test the Textual UI without an Exchange
server to point at.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .ews_client import AttachmentSummary, EventSummary, FolderSummary, MessageDetail, MessageSummary, _unique_path

_LOREM = (
    "This is a demo message body. Run without --demo and with --email "
    "(plus auth flags) to talk to a real Exchange mailbox over EWS.\n\n"
    "-- \nSent from ewstui"
)


class DemoMailClient:
    def __init__(self, page_size: int = 50):
        self.page_size = page_size
        self._folders = [
            FolderSummary(id="inbox", name="Inbox", total_count=4, unread_count=2, depth=0),
            FolderSummary(id="sent", name="Sent Items", total_count=2, unread_count=0, depth=0),
            FolderSummary(id="drafts", name="Drafts", total_count=1, unread_count=0, depth=0),
            FolderSummary(id="archive", name="Archive", total_count=1, unread_count=0, depth=0),
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
                ),
                MessageDetail(
                    id="m3", changekey="c3", subject="IT maintenance window this weekend",
                    sender="it-notifications@corp.example", received=now - timedelta(days=1),
                    is_read=True, has_attachments=False,
                    to=["all-staff@corp.example"], cc=[], body_text=_LOREM,
                ),
                MessageDetail(
                    id="m4", changekey="c4", subject="Lunch Friday?",
                    sender="friend@corp.example", received=now - timedelta(days=2),
                    is_read=True, has_attachments=False,
                    to=["you@corp.example"], cc=[], body_text=_LOREM,
                ),
            ],
            "sent": [
                MessageDetail(
                    id="s1", changekey="c1", subject="Re: Project status",
                    sender="you@corp.example", received=now - timedelta(days=1, hours=2),
                    is_read=True, has_attachments=False, to=["boss@corp.example"], cc=[], body_text=_LOREM,
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

    def list_folders(self) -> list[FolderSummary]:
        return list(self._folders)

    def list_messages(self, folder_id: str, offset: int = 0, limit: int | None = None) -> list[MessageSummary]:
        msgs = self._messages.get(folder_id, [])
        limit = limit or self.page_size
        return [
            MessageSummary(m.id, m.changekey, m.subject, m.sender, m.received, m.is_read, m.has_attachments)
            for m in msgs[offset : offset + limit]
        ]

    def get_message(self, folder_id: str, message_id: str) -> MessageDetail:
        for m in self._messages.get(folder_id, []):
            if m.id == message_id:
                return m
        raise KeyError(message_id)

    def mark_read(self, folder_id: str, message_id: str, read: bool = True) -> None:
        m = self.get_message(folder_id, message_id)
        m.is_read = read

    def delete_message(self, folder_id: str, message_id: str) -> None:
        self._messages[folder_id] = [m for m in self._messages.get(folder_id, []) if m.id != message_id]

    def archive_message(self, folder_id: str, message_id: str) -> None:
        msgs = self._messages.get(folder_id, [])
        moving = next((m for m in msgs if m.id == message_id), None)
        if moving is None:
            raise KeyError(message_id)
        self._messages[folder_id] = [m for m in msgs if m.id != message_id]
        self._messages.setdefault("archive", []).append(moving)

    def send_mail(self, to, subject, body, cc=None) -> None:
        pass  # demo: no-op

    def reply(self, folder_id: str, message_id: str, body: str, reply_all: bool = False) -> None:
        pass  # demo: no-op

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
        ]

    def list_events(self, start: datetime, end: datetime) -> list[EventSummary]:
        return [e for e in self._events if e.start < end and e.end > start]

    def create_event(self, subject, start, end, location="", body="") -> None:
        self._events.append(
            EventSummary(
                id=f"e{len(self._events) + 1}", changekey="c1", subject=subject,
                start=start, end=end, location=location, organizer="you@corp.example", is_all_day=False,
            )
        )

    def delete_event(self, event_id: str) -> None:
        self._events = [e for e in self._events if e.id != event_id]
