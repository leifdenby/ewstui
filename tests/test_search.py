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


def test_live_search_uses_exchange_search_and_says_the_folder():
    from types import SimpleNamespace

    from ewstui.ews_client import MailClient

    calls = []
    item = SimpleNamespace(
        id="x1", changekey="c", subject="Budget", sender=None, datetime_received=datetime(2026, 9, 1),
        is_read=True, has_attachments=False, conversation_id=None, conversation_index=None,
        parent_folder_id=SimpleNamespace(id="archive"),
    )

    class QS:
        def __init__(self, query):
            calls.append(query)

        def order_by(self, *a):
            return self

        def only(self, *fields):
            calls.append(fields)
            return self

        def __getitem__(self, s):
            return [item][s]

    inbox = SimpleNamespace(id="inbox", children=[], filter=QS)
    client = MailClient(SimpleNamespace(msg_folder_root=SimpleNamespace(id="root", children=[inbox])))
    found = client.search("budget q3", "inbox")
    assert calls[0] == "budget q3" and "parent_folder_id" in calls[1]
    assert [(m.id, m.folder_id, m.subject) for m in found] == [("x1", "archive", "Budget")]


def test_best_match_first():
    messages = [msg("a", "Notes from the budget meeting"), msg("b", "Budget")]
    assert ids(rank(messages, "budget")) == ["b", "a"]
