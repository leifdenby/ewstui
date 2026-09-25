"""Thread view (t): conversations as nested trees, placed by their newest message."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from exchangelib import Message
from exchangelib.properties import ConversationId

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import MailClient, MessageSummary, _reply_depth
from ewstui.threads import build_threads, topic, tree_rows
from ewstui.widgets.message_table import MessageTable

T0 = datetime(2026, 9, 25, 12, 0)
HDR = bytes(range(22))  # conversation index header; each reply level adds 5 bytes


def idx(*levels: str) -> bytes:
    return HDR + b"".join(level.encode() * 5 for level in levels)


def msg(mid, conv=None, hours_ago=0, sender="a@x", read=True, index=None, subject="Re: Plan") -> MessageSummary:
    return MessageSummary(
        id=mid, changekey="c", subject=subject, sender=sender, received=T0 - timedelta(hours=hours_ago),
        is_read=read, has_attachments=False, conversation_id=conv, conversation_index=index,
        depth=_reply_depth(index),
    )


# -- pure helpers -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Re: SV: Fwd: Budget", "Budget"),
        ("AW: WG: Treffen", "Treffen"),
        ("RE[2]: status", "status"),
        ("VS: Lokale til Classiq x DMI", "Lokale til Classiq x DMI"),
        ("Regarding the plan", "Regarding the plan"),  # "Re" only as a prefix with a colon
        ("Re:", "Re:"),
    ],
)
def test_topic_strips_reply_and_forward_prefixes(subject, expected):
    assert topic(subject) == expected


def test_threads_are_placed_by_their_newest_message():
    threads = build_threads([
        msg("old-root", "c1", hours_ago=10, index=idx()),
        msg("lone", None, hours_ago=3, subject="Lunch"),
        msg("new-reply", "c1", hours_ago=1, index=idx("a")),
    ])
    assert [t.key for t in threads] == ["c1", "msg:lone"]  # c1's newest (1h) beats lone (3h)
    assert [m.id for m in threads[0].messages] == ["old-root", "new-reply"]  # oldest first inside


def tree(messages) -> list[str]:
    (thread,) = build_threads(messages)
    return [prefix + m.id for m, prefix in tree_rows(thread)]


def test_tree_nests_replies_under_the_message_they_answer():
    assert tree([
        msg("root", "c", 10, index=idx()),
        msg("a", "c", 8, index=idx("a")),
        msg("b", "c", 6, index=idx("a", "b")),  # reply to a
        msg("c", "c", 5, index=idx("a", "c")),  # another reply to a
        msg("d", "c", 1, index=idx("a", "b", "d")),  # reply to b
    ]) == [
        "root",
        "└─ a",
        "   ├─ b",
        "   │  └─ d",
        "   └─ c",
    ]


def test_missing_parent_attaches_to_nearest_listed_ancestor_or_first_message():
    # The original isn't in this folder: replies hang under the oldest listed one,
    # and a grandchild whose parent is missing goes to its grandparent.
    assert tree([
        msg("x", "c", 9, index=idx("x")),
        msg("y", "c", 7, index=idx("y")),
        msg("x2", "c", 4, index=idx("x", "q", "z")),  # parent idx("x","q") missing -> x
    ]) == ["x", "├─ y", "└─ x2"]


def test_messages_without_index_still_form_a_thread():
    assert tree([msg("a", "c", 3), msg("b", "c", 2), msg("c", "c", 1)]) == ["a", "├─ b", "└─ c"]


def test_reply_depth_from_conversation_index():
    assert _reply_depth(None) == 0
    assert _reply_depth(bytes(22)) == 0
    assert _reply_depth(bytes(27)) == 1
    assert _reply_depth(bytes(32)) == 2


def test_live_list_messages_reads_conversation_fields():
    item = Message(subject="Re: Hi", is_read=True, conversation_id=ConversationId(id="conv-1"),
                   conversation_index=idx("a"), datetime_received=None)
    item._id = SimpleNamespace(id="i1", changekey="ck")

    class QS:
        def order_by(self, *a):
            return self

        def only(self, *fields):
            assert "conversation_id" in fields and "conversation_index" in fields
            return self

        def __getitem__(self, s):
            return [item][s]

    folder = SimpleNamespace(id="inbox", children=[], all=lambda: QS())
    (m,) = MailClient(SimpleNamespace(msg_folder_root=folder)).list_messages("inbox")
    assert (m.conversation_id, m.depth, m.folder_id, m.conversation_index) == ("conv-1", 1, "inbox", idx("a"))


# -- the message list (demo: m2 in Inbox answers your s1 in Sent Items) ----------------


def make_app(tmp_path, *extra) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt"), *extra])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


def rows(app) -> list[tuple]:
    table = app.query_one("#messages", MessageTable)
    return [tuple(table.get_row_at(i)) for i in range(table.row_count)]


def subjects(app) -> list[str]:
    return [r[4] for r in rows(app)]  # columns: flag, P, received, from, subject


def ids(app) -> list[str]:
    table = app.query_one("#messages", MessageTable)
    return [table.coordinate_to_cell_key((i, 0)).row_key.value for i in range(table.row_count)]


def preview_text(app) -> str:
    return str(app.query_one("#preview-body").render())


async def test_thread_is_a_tree_placed_by_its_newest_message(tmp_path):
    app = make_app(tmp_path, "--threads")
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        # m1 (1h) is newest; the EWS thread's newest message m2 (3h) places
        # it next, before m3 (1 day) — even though its root s1 is 26h old.
        assert ids(app) == ["m1", "s1", "m2", "m3", "m4"]
        assert subjects(app)[1:3] == [
            "Re: EWS bridge project (sent)",  # your reply, from Sent Items, starts the tree here
            "└─ ",  # their answer: same topic, so just the tree guide (mutt-style)
        ]
        assert rows(app)[2][0] == "●" and rows(app)[2][3] == "colleague@corp.example"


async def test_every_row_is_a_message_you_can_open_and_act_on(tmp_path):
    app = make_app(tmp_path, "--threads")
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        await pilot.press("j")  # s1, which lives in Sent Items
        await pilot.pause()
        assert table._current_message_id() == "s1"
        assert "colleague@corp.example" in preview_text(app)  # loaded from the sent folder
        await pilot.press("j", "d")  # delete m2 itself
        await pilot.pause()
        assert "m2" not in [m.id for m in app.mail_client.list_messages("inbox")]
        assert "s1" in [m.id for m in app.mail_client.list_messages("sent")]


async def test_t_toggles_and_keeps_the_cursor_on_the_message(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#messages", MessageTable)
        assert ids(app) == ["m1", "m2", "m3", "m4"] and not table.threaded  # flat by default
        await pilot.press("j", "t")  # on m2, threads on
        await pilot.pause()
        assert table.threaded and table._current_message_id() == "m2"
        assert ids(app) == ["m1", "s1", "m2", "m3", "m4"]
        await pilot.press("t")
        await pilot.pause()
        assert not table.threaded and table._current_message_id() == "m2"
        assert ids(app) == ["m1", "m2", "m3", "m4"]  # flat view: only this folder


async def test_h_still_moves_to_folders_and_l_opens(tmp_path):
    from ewstui.widgets.folder_list import FolderList
    from ewstui.widgets.preview import PreviewPane

    app = make_app(tmp_path, "--threads")
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("j", "j", "l")  # open m2 (a reply row)
        await pilot.pause()
        assert isinstance(app.focused, PreviewPane)
        await pilot.press("h", "h")
        await pilot.pause()
        assert isinstance(app.focused, FolderList)


async def test_deleting_a_sent_reply_uses_sent_items_and_undo_brings_it_back(tmp_path):
    app = make_app(tmp_path, "--threads")
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("j", "d")  # s1
        await pilot.pause()
        assert "s1" in [m.id for m in app.mail_client.list_messages("trash")]
        assert ids(app) == ["m1", "m2", "m3", "m4"]  # m2 now stands alone
        await pilot.press("u")
        await pilot.pause()
        assert ids(app) == ["m1", "s1", "m2", "m3", "m4"]
        assert app.query_one("#messages", MessageTable)._current_message_id() == "s1"


async def test_refresh_keeps_the_cursor_on_the_same_message(tmp_path):
    app = make_app(tmp_path, "--threads")
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("j", "j")  # m2
        await pilot.pause()
        app.mail_client.get_message("inbox", "m1").is_read = True  # something changes
        await pilot.press("ctrl+l")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one("#messages", MessageTable)._current_message_id() == "m2"


async def test_sent_items_failure_still_shows_the_folder(tmp_path, monkeypatch):
    app = make_app(tmp_path, "--threads")

    def broken():
        raise RuntimeError("no sent folder")

    monkeypatch.setattr(app.mail_client, "sent_folder_id", broken)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        assert ids(app) == ["m1", "m2", "m3", "m4"]  # m2 alone now, no crash
        assert subjects(app)[1] == "Re: EWS bridge project"


async def test_sent_folder_itself_has_no_extras(tmp_path):
    app = make_app(tmp_path, "--threads")
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        app.select_folder("sent")
        await pilot.pause()
        assert "(sent)" not in " ".join(subjects(app))


def test_threads_setting_from_config_and_cli(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[accounts.work]\nemail = "me@corp.example"\nthreads = true\n')
    assert config_from_args(["--config", str(path), "--account", "work"]).threads is True
    assert config_from_args(["--config", str(path), "--account", "work", "--no-threads"]).threads is False
    assert config_from_args(["--demo"]).threads is False
