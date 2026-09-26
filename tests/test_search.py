"""Fuzzy matching of emails for the search bar."""
from __future__ import annotations

from datetime import datetime

from ewstui.ews_client import MessageSummary
from ewstui.search import message_score, rank


def msg(mid, subject, sender="someone@corp.example"):
    return MessageSummary(mid, "c", subject, sender, datetime(2026, 9, 1), True, False)


MESSAGES = [
    msg("m1", "Q3 budget review", "finance@corp.example"),
    msg("m2", "Re: EWS bridge project", "colleague@corp.example"),
    msg("m3", "IT maintenance window this weekend", "it-notifications@corp.example"),
    msg("m4", "Lunch Friday?", "friend@corp.example"),
]


def ids(messages):
    return [m.id for m in messages]


def test_partial_and_fuzzy_words_match():
    assert ids(rank(MESSAGES, "budg")) == ["m1"]
    assert ids(rank(MESSAGES, "maint wknd")) == ["m3"]  # letters of "weekend", in order
    assert ids(rank(MESSAGES, "EWS")) == ["m2"]


def test_sender_and_display_names_match():
    assert ids(rank(MESSAGES, "finance")) == ["m1"]
    assert ids(rank(MESSAGES, "casey", names={"colleague@corp.example": "Casey Colleague"})) == ["m2"]


def test_scattered_letters_do_not_match_everything():
    assert rank(MESSAGES, "xqz") == []
    assert message_score("eiw", MESSAGES[3]) is None  # e..i..w are in "Lunch Friday? friend@..." but far apart


def test_empty_query_keeps_everything_in_order():
    assert ids(rank(MESSAGES, "  ")) == ["m1", "m2", "m3", "m4"]


def test_demo_search_in_a_folder_and_everywhere():
    from ewstui.demo_backend import DemoMailClient

    mail = DemoMailClient()
    assert [(m.id, m.folder_id) for m in mail.search("budg", "inbox")] == [("m1", "inbox")]
    everywhere = mail.search("ews bridge")
    assert {(m.id, m.folder_id) for m in everywhere} == {("m2", "inbox"), ("s1", "sent")}
    assert mail.search("maintenance", "sent") == []


def live_folder(fid, items, calls, fail=False):
    from types import SimpleNamespace

    class QS:
        def __init__(self, query):
            calls.append((fid, query))
            if fail:
                raise RuntimeError("no index here")

        def order_by(self, *a):
            return self

        def only(self, *fields):
            return self

        def __getitem__(self, s):
            return items[s]

    return SimpleNamespace(id=fid, name=fid.title(), folder_class="IPF.Note", children=[], filter=QS)


def live_item(iid, day):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=iid, changekey="c", subject=f"Budget {iid}", sender=None, datetime_received=datetime(2026, 9, day),
        is_read=True, has_attachments=False, conversation_id=None, conversation_index=None,
    )


def test_live_search_in_one_folder_uses_exchange_search():
    from types import SimpleNamespace

    from ewstui.ews_client import MailClient

    calls = []
    inbox = live_folder("inbox", [live_item("x1", 1)], calls)
    client = MailClient(SimpleNamespace(msg_folder_root=SimpleNamespace(id="root", children=[inbox])))
    found = client.search("budget q3", "inbox")
    assert calls == [("inbox", "budget q3")]
    assert [(m.id, m.folder_id) for m in found] == [("x1", "inbox")]


def test_live_search_everywhere_is_one_request_per_folder():
    """Exchange refuses one text search over several folders ("Shared folder
    search cannot be performed on multiple folders")."""
    from types import SimpleNamespace

    from ewstui.ews_client import MailClient

    calls = []
    inbox = live_folder("inbox", [live_item("x1", 1)], calls)
    archive = live_folder("archive", [live_item("x2", 5)], calls)
    broken = live_folder("broken", [], calls, fail=True)
    calendar = SimpleNamespace(id="cal", name="Calendar", folder_class="IPF.Appointment", children=[])
    account = SimpleNamespace(msg_folder_root=SimpleNamespace(id="root", children=[archive, broken, calendar, inbox]),
                              inbox=inbox)
    client = MailClient(account)
    progress = list(client.search_everywhere("budget"))
    assert sorted(fid for fid, _ in calls) == ["archive", "broken", "inbox"]  # mail folders only, each on its own
    assert [(done, total) for done, total, _ in progress] == [(1, 3), (2, 3), (3, 3)]
    assert sorted(m.id for _, _, batch in progress for m in batch) == ["x1", "x2"]  # the broken folder skipped
    assert [m.id for m in client.search("budget")] == ["x2", "x1"]  # merged, newest first


def test_best_match_first():
    messages = [msg("a", "Notes from the budget meeting"), msg("b", "Budget")]
    assert ids(rank(messages, "budget")) == ["b", "a"]
