"""Mail folders can hold non-Message items (e.g. an unsent meeting invite
in Drafts is a CalendarItem). Use exchangelib's real item classes so the
missing attributes (sender, is_read, to_recipients) are the real ones.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from exchangelib import Attendee, CalendarItem, Contact, Mailbox, Message

from ewstui.ews_client import MailClient

WHEN = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


class FakeQuerySet:
    def __init__(self, items):
        self.items = items

    def order_by(self, *a):
        return self

    def only(self, *a):
        return self

    def __getitem__(self, s):
        return self.items[s]


def client_with(items) -> MailClient:
    by_id = {i.id: i for i in items}
    folder = SimpleNamespace(
        id="drafts", children=[], all=lambda: FakeQuerySet(items), get=lambda id: by_id[id]
    )
    return MailClient(SimpleNamespace(msg_folder_root=folder))


def with_id(item, item_id):
    item._id = SimpleNamespace(id=item_id, changekey="ck")
    return item


@pytest.fixture
def items():
    invite = with_id(
        CalendarItem(
            subject="Planning meeting",
            organizer=Mailbox(email_address="me@corp.example"),
            required_attendees=[Attendee(mailbox=Mailbox(email_address="a@corp.example"), response_type="Unknown")],
            optional_attendees=[Attendee(mailbox=Mailbox(name="Room 4B"), response_type="Unknown")],
            text_body="Agenda: ...",
            datetime_received=WHEN,
        ),
        "cal1",
    )
    draft = with_id(
        Message(subject="Unsent draft", sender=None, is_read=True, to_recipients=[Mailbox(email_address="b@corp.example")]),
        "msg1",
    )
    contact = with_id(Contact(subject="Some contact"), "con1")
    return [invite, draft, contact]


def test_list_messages_handles_calendar_items_and_contacts(items):
    summaries = client_with(items).list_messages("drafts")
    assert [(s.id, s.subject, s.sender, s.is_read) for s in summaries] == [
        ("cal1", "Planning meeting", "me@corp.example", True),
        ("msg1", "Unsent draft", "(unknown)", True),
        ("con1", "Some contact", "(unknown)", True),
    ]


def test_get_message_on_calendar_item_uses_attendees(items):
    detail = client_with(items).get_message("drafts", "cal1")
    assert detail.sender == "me@corp.example"
    assert detail.to == ["a@corp.example"]
    assert detail.cc == ["Room 4B"]
    assert detail.body_text == "Agenda: ..."


def test_get_message_on_draft_message(items):
    detail = client_with(items).get_message("drafts", "msg1")
    assert detail.to == ["b@corp.example"]
    assert detail.cc == []
