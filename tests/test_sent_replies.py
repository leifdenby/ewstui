"""`export --with-sent`: your replies found per conversation, not by paging through Sent Items."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ewstui import export
from ewstui.demo_backend import DemoMailClient
from ewstui.ews_client import CONVERSATION_BATCH, MailClient, MessageDetail


def scan_sent_replies(client, folder_messages):
    """The old --with-sent: page through Sent Items, newest first, until
    SENT_LOOKBACK before the folder's oldest email. The reference the new
    lookup must agree with."""
    sent_id = client.sent_folder_id()
    conversations = {m.conversation_id for m in folder_messages if m.conversation_id}
    dates = [m.received for m in folder_messages if m.received]
    if not conversations or not dates:
        return []
    cutoff = min(dates) - export.SENT_LOOKBACK
    replies = []
    for m in export.iter_all(client, sent_id):
        if m.received and m.received < cutoff:
            break
        if m.conversation_id in conversations:
            replies.append(m)
    return replies


def busy_mailbox() -> DemoMailClient:
    """A demo mailbox with 300 sent emails over a year and a filed folder
    whose emails belong to some of their conversations.

    The folder's oldest email is 200 days old, so replies within 230 days
    count; conversations conv-0 .. conv-9 are filed, conv-10 .. are not.
    """
    client = DemoMailClient()
    now = datetime(2026, 11, 25, 12)
    sent = []
    for i in range(300):
        sent.append(MessageDetail(
            id=f"x{i}", changekey="c", subject=f"Re: thread {i % 15}", sender="you@corp.example",
            received=now - timedelta(days=i * 365 / 300), is_read=True, has_attachments=False,
            to=["someone@corp.example"], cc=[], body_text="", conversation_id=f"conv-{i % 15}",
            internet_message_id=f"<x{i}@demo.corp.example>",
        ))
    client._messages["sent"] = sent
    client._messages["filed"] = [
        MessageDetail(id=f"f{c}", changekey="c", subject=f"thread {c}", sender="someone@corp.example",
                      received=now - timedelta(days=20 * c), is_read=True, has_attachments=False,
                      to=["you@corp.example"], cc=[], body_text="", conversation_id=f"conv-{c}",
                      internet_message_id=f"<f{c}@demo.corp.example>")
        for c in range(11) if c != 10  # conv-0 .. conv-9, oldest 180 days
    ]
    client._messages["filed"].append(MessageDetail(
        id="f-old", changekey="c", subject="thread 3 again", sender="someone@corp.example",
        received=now - timedelta(days=200), is_read=True, has_attachments=False, to=[], cc=[], body_text="",
        conversation_id="conv-3", internet_message_id="<f-old@demo.corp.example>",
    ))
    client._messages["filed"].append(MessageDetail(
        id="f-none", changekey="c", subject="no conversation", sender="someone@corp.example",
        received=now - timedelta(days=1), is_read=True, has_attachments=False, to=[], cc=[], body_text="",
        internet_message_id="<f-none@demo.corp.example>",
    ))
    return client


def test_replies_are_the_same_as_scanning_sent_items():
    client = busy_mailbox()
    in_folder = list(export.iter_all(client, "filed"))
    old = scan_sent_replies(client, in_folder)
    _, new = export._sent_replies(client, in_folder)
    assert [m.id for m in new] == [m.id for m in old]
    # The case is a real one: Sent Items is longer than a page, the
    # lookback cuts some replies off, and other conversations are skipped.
    assert len(client._messages["sent"]) > export.PAGE
    assert 0 < len(new) < sum(1 for m in client._messages["sent"] if int(m.conversation_id[5:]) < 10)


def test_replies_are_the_same_for_the_plain_demo_folders():
    client = DemoMailClient()
    for folder in ("inbox", "archive", "sent"):
        in_folder = list(export.iter_all(client, folder))
        assert [m.id for m in export._sent_replies(client, in_folder)[1]] == [
            m.id for m in scan_sent_replies(client, in_folder)
        ]


def test_sent_items_is_not_paged_through(monkeypatch):
    client = busy_mailbox()
    in_folder = list(export.iter_all(client, "filed"))
    listed = client.list_messages

    def list_messages(folder_id, offset=0, limit=None):
        assert folder_id != "sent", "Sent Items paged through"
        return listed(folder_id, offset, limit)

    monkeypatch.setattr(client, "list_messages", list_messages)
    assert export._sent_replies(client, in_folder)[1]


def test_folder_without_conversations_looks_nothing_up(monkeypatch):
    client = DemoMailClient()
    monkeypatch.setattr(client, "find_in_conversations", lambda *a, **k: pytest.fail("looked up"))
    assert export._sent_replies(client, list(export.iter_all(client, "archive"))) == ("sent", [])


def live_sent_folder(items, requests):
    """A stand-in exchangelib Sent Items folder recording each FindItem's restrictions."""

    class QS:
        def __init__(self, restrictions):
            self.restrictions = restrictions

        def filter(self, *args, **kwargs):
            return QS([*self.restrictions, *args, *kwargs.items()])

        def only(self, *fields):
            requests.append(self.restrictions)
            return iter(items)

    return SimpleNamespace(id="sent", name="Sent Items", folder_class="IPF.Note", children=[],
                           filter=lambda *a, **k: QS([*a, *k.items()]))


def live_item(iid, conversation):
    return SimpleNamespace(
        id=iid, changekey="c", subject="Re: x", sender=None,
        datetime_received=datetime(2026, 9, 1, tzinfo=timezone.utc), is_read=True, has_attachments=False,
        conversation_id=SimpleNamespace(id=conversation), conversation_index=None, message_id=f"<{iid}@x>",
    )


def test_live_client_restricts_on_conversations_in_batches():
    from exchangelib import EWSDateTime, Q

    requests = []
    sent = live_sent_folder([live_item("s1", "c1")], requests)
    client = MailClient(SimpleNamespace(msg_folder_root=SimpleNamespace(id="root", children=[sent])))
    ids = [f"c{i}" for i in range(CONVERSATION_BATCH + 5)]
    found = client.find_in_conversations("sent", ids + ["c1"], since=datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert [m.id for m in found] == ["s1"]  # the same item from both requests, once
    assert len(requests) == 2
    first, since = requests[0]
    assert isinstance(first, Q) and first.conn_type == Q.OR and len(first.children) == CONVERSATION_BATCH
    assert since[0] == "datetime_received__gte" and isinstance(since[1], EWSDateTime)
    assert len(requests[1][0].children) == 5
