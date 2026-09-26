"""Meeting invites in the mail view: the card (when / where / your response /
clashes / day strip), answering with i, and cancelled meetings."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from exchangelib import EWSDateTime, EWSTimeZone, Mailbox
from exchangelib.errors import ErrorItemNotFound
from exchangelib.items import SAVE_ONLY, SEND_AND_SAVE_COPY, MeetingCancellation, MeetingRequest, Message

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import EventSummary, MailClient, MeetingInfo
from ewstui.invites import conflicts, day_strip, render_invite_card, strip_hours, when_text
from ewstui.screens import ConfirmScreen, InviteResponseScreen
from ewstui.widgets.message_table import MessageTable
from ewstui.widgets.preview import PreviewPane

DAY = datetime(2026, 9, 29)


def at(hour: int, minute: int = 0) -> datetime:
    return DAY.replace(hour=hour, minute=minute)


def event(eid: str, subject: str, start: datetime, end: datetime, all_day: bool = False) -> EventSummary:
    return EventSummary(eid, "c", subject, start, end, "", "x@corp.example", all_day)


def invite(start=at(10), end=at(11), **kw) -> MeetingInfo:
    return MeetingInfo(kind=kw.pop("kind", "invite"), start=start, end=end, **kw)


# -- pure functions -------------------------------------------------------

def test_conflicts_skip_the_meetings_own_entry_and_all_day_events():
    events = [
        event("e2", "1:1", at(10, 30), at(11)),
        event("e1", "Standup", at(10), at(10, 15)),
        event("placeholder", "Sprint planning", at(10), at(11)),
        event("hol", "Holiday", DAY, DAY + timedelta(days=1), all_day=True),
        event("e3", "Later", at(11), at(12)),  # starts as it ends: no clash
    ]
    clashing = conflicts(events, invite(calendar_item_id="placeholder"))
    assert [e.subject for e in clashing] == ["Standup", "1:1"]


def test_when_text():
    assert when_text(invite()) == "Tue 29 Sep 2026 10:00–11:00 (1 h)"
    assert when_text(invite(end=at(11, 30))) == "Tue 29 Sep 2026 10:00–11:30 (1 h 30 min)"
    assert when_text(invite(start=DAY, end=DAY + timedelta(days=1), is_all_day=True)) == "Tue 29 Sep 2026, all day"
    assert when_text(invite(start=None, end=None)) == "(no time given)"


def test_strip_covers_the_working_day_widened_to_the_meeting():
    assert strip_hours(invite()) == (at(8).time(), at(17).time())
    assert strip_hours(invite(start=at(7, 30), end=at(18, 15))) == (at(7).time(), at(19).time())


def test_day_strip_marks_free_busy_the_meeting_and_clashes():
    labels, cells = day_strip([event("e1", "Standup", at(10), at(10, 30)), event("e2", "Lunch", at(12), at(13))],
                              invite(end=at(11)), width=60)
    cells, labels = cells.plain, labels.plain
    assert len(cells) == 18 * 3  # 08:00–17:00, three columns per half hour
    slot = lambda h, m=0: cells[((h - 8) * 2 + m // 30) * 3:][:3]  # noqa: E731
    assert slot(9) == " · "
    assert slot(10) == "×××"  # the meeting, clashing with Standup
    assert slot(10, 30) == "▒▒▒"  # the meeting, free
    assert slot(12) == slot(12, 30) == "███"  # busy
    assert labels.startswith("08    09    10")


def test_day_strip_narrows_its_cells_to_fit():
    labels, cells = day_strip([], invite(), width=40)
    assert len(cells.plain) == 36 and len(labels.plain) == 36


def test_card_for_an_unanswered_invite():
    card = render_invite_card(invite(location="Room 4B", is_recurring=True), [event("e1", "Standup", at(10), at(10, 15))], 80)
    lines = card.plain.splitlines()
    assert lines[0] == "When  Tue 29 Sep 2026 10:00–11:00 (1 h) · recurring"
    assert lines[1] == "Where Room 4B"
    assert lines[2] == "You   Not answered yet · i to respond"
    assert lines[3] == "Clash Standup 10:00–10:15"
    assert "×" in lines[5] and set(lines[6]) == {"─"}


def test_card_while_the_calendar_is_being_checked_and_when_free():
    assert "checking your calendar…" in render_invite_card(invite(), None, 80).plain
    assert "none — you're free" in render_invite_card(invite(), [], 80).plain


def test_card_shows_your_answer_and_out_of_date():
    plain = render_invite_card(invite(my_response="Accept", is_out_of_date=True), [], 80).plain
    assert "Accepted ✓ · i to respond" in plain and "out of date" in plain
    assert "You're the organizer" in render_invite_card(invite(my_response="Organizer"), [], 80).plain


def test_card_for_a_cancellation():
    plain = render_invite_card(invite(kind="cancellation", calendar_item_id="cal-2"), None, 80).plain
    assert "When  Tue 29 Sep 2026 10:00–11:00 (1 h)" in plain
    assert "Cancelled · i removes it from your calendar" in plain
    assert "Clash" not in plain  # nothing to check


def test_card_for_a_cancellation_already_gone_from_the_calendar():
    plain = render_invite_card(invite(kind="cancellation", start=None, end=None), None, 80).plain
    assert "When" not in plain
    assert "Cancelled · already gone from your calendar" in plain


# -- ews_client -----------------------------------------------------------

class FakeQuerySet:
    def __init__(self, items):
        self.items = items

    def order_by(self, *a):
        return self

    def only(self, *a):
        return self

    def __getitem__(self, s):
        return self.items[s]


def with_id(item, item_id):
    item._id = SimpleNamespace(id=item_id, changekey="ck")
    return item


def client_with(items, calendar=None) -> MailClient:
    by_id = {i.id: i for i in items}
    folder = SimpleNamespace(id="inbox", children=[], all=lambda: FakeQuerySet(items), get=lambda id: by_id[id])
    client = MailClient(SimpleNamespace(msg_folder_root=folder, calendar=calendar))
    client.tz = EWSTimeZone("UTC")
    return client


UTC = EWSTimeZone("UTC")
WHEN = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def meeting_items():
    request = with_id(
        MeetingRequest(
            subject="Sprint planning", sender=Mailbox(email_address="casey@corp.example", name="Casey"),
            organizer=Mailbox(email_address="casey@corp.example"), datetime_received=WHEN,
            start=EWSDateTime(2026, 9, 29, 10, tzinfo=UTC), end=EWSDateTime(2026, 9, 29, 11, tzinfo=UTC),
            location="Room 4B", my_response_type="NoResponseReceived", is_out_of_date=False,
            associated_calendar_item_id=SimpleNamespace(id="cal-1"),
        ),
        "req1",
    )
    cancel = with_id(
        MeetingCancellation(
            subject="Canceled: Retro", sender=Mailbox(email_address="casey@corp.example"), datetime_received=WHEN,
            associated_calendar_item_id=SimpleNamespace(id="cal-2"),
        ),
        "can1",
    )
    mail = with_id(Message(subject="Hi", sender=Mailbox(email_address="a@corp.example"), datetime_received=WHEN), "m1")
    return [request, cancel, mail]


def test_list_messages_tells_invites_and_cancellations_apart():
    kinds = [(m.id, m.kind) for m in client_with(meeting_items()).list_messages("inbox")]
    assert kinds == [("req1", "invite"), ("can1", "cancellation"), ("m1", "mail")]


def test_get_message_on_an_invite_has_the_meeting():
    meeting = client_with(meeting_items()).get_message("inbox", "req1").meeting
    assert meeting == MeetingInfo(
        kind="invite", start=datetime(2026, 9, 29, 10), end=datetime(2026, 9, 29, 11), location="Room 4B",
        organizer="casey@corp.example", my_response="NoResponseReceived", calendar_item_id="cal-1",
    )
    assert client_with(meeting_items()).get_message("inbox", "m1").meeting is None


def test_a_cancellation_takes_time_and_place_from_the_meeting_in_your_calendar():
    def get(id):
        if id != "cal-2":
            raise ErrorItemNotFound("gone")
        return SimpleNamespace(start=EWSDateTime(2026, 9, 30, 15, tzinfo=UTC), end=EWSDateTime(2026, 9, 30, 16, tzinfo=UTC),
                               location="Aquarium", organizer=Mailbox(email_address="casey@corp.example"))

    items = meeting_items()
    meeting = client_with(items, calendar=SimpleNamespace(get=get)).get_message("inbox", "can1").meeting
    assert (meeting.kind, meeting.start, meeting.location, meeting.calendar_item_id) == (
        "cancellation", datetime(2026, 9, 30, 15), "Aquarium", "cal-2")
    items[1].associated_calendar_item_id = SimpleNamespace(id="gone")
    meeting = client_with(items, calendar=SimpleNamespace(get=get)).get_message("inbox", "can1").meeting
    assert meeting.start is None and meeting.calendar_item_id is None


class FakeInvite:
    id = "req1"

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda **kw: self.calls.append((name, kw))


@pytest.mark.parametrize(("response", "method"), [("accept", "accept"), ("tentative", "tentatively_accept"), ("decline", "decline")])
def test_respond_to_invite_sends_the_response(response, method):
    item = FakeInvite()
    client_with([item]).respond_to_invite("inbox", "req1", response, note="see you")
    assert item.calls == [(method, {"message_disposition": SEND_AND_SAVE_COPY, "body": "see you"})]


def test_respond_without_sending_only_updates_the_calendar():
    item = FakeInvite()
    client_with([item]).respond_to_invite("inbox", "req1", "accept", send=False)
    assert item.calls == [("accept", {"message_disposition": SAVE_ONLY, "body": None})]


def test_remove_cancelled_meeting_trashes_the_calendar_entry():
    trashed = []

    def get(id):
        if id == "gone":
            raise ErrorItemNotFound("gone")
        return SimpleNamespace(move_to_trash=lambda: trashed.append(id))

    items = meeting_items()
    assert client_with(items, calendar=SimpleNamespace(get=get)).remove_cancelled_meeting("inbox", "can1")
    assert trashed == ["cal-2"]
    items[1].associated_calendar_item_id = SimpleNamespace(id="gone")
    assert not client_with(items, calendar=SimpleNamespace(get=get)).remove_cancelled_meeting("inbox", "can1")


# -- in the app (demo) -------------------------------------------------------

def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    calendar = DemoCalendarClient()
    return EwstuiApp(DemoMailClient(calendar=calendar, with_invites=True), calendar, cfg)


def body(app) -> str:
    return str(app.query_one("#preview-body").render())


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def go_to(app, pilot, message_id):
    await pilot.pause()
    table = app.query_one("#messages", MessageTable)
    await pilot.press(*["j"] * table.get_row_index(message_id))
    await settle(app, pilot)


async def test_invite_card_with_clash_and_tagged_in_the_list(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await go_to(app, pilot, "inv1")
        table = app.query_one("#messages", MessageTable)
        assert str(table.get_row("inv1")[-1]) == "invite · Sprint planning"
        assert str(table.get_row("can1")[-1]) == "cancelled · Canceled: Friday retro"
        text = body(app)
        assert "Where Room 4B" in text and "Not answered yet · i to respond" in text
        assert "Clash 1:1 with manager 14:00–14:30" in text  # the demo calendar's 1:1


async def test_i_then_a_accepts_archives_and_u_brings_the_email_back(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await go_to(app, pilot, "inv1")
        await pilot.press("i")
        await settle(app, pilot)
        assert isinstance(app.screen, InviteResponseScreen)
        assert "1:1 with manager" in str(app.screen.query_one("#invite-meeting").render())
        await pilot.press("a")
        await settle(app, pilot)
        assert app.mail_client.invite_responses == [{"id": "inv1", "response": "accept", "note": "", "send": True}]
        assert any(e.id == "e-inv1" for e in app.calendar_client._events)
        inbox = [m.id for m in app.mail_client.list_messages("inbox")]
        assert "inv1" not in inbox and "inv1" in [m.id for m in app.mail_client.list_messages("archive")]
        await pilot.press("u")
        await settle(app, pilot)
        assert "inv1" in [m.id for m in app.mail_client.list_messages("inbox")]


async def test_decline_without_sending_and_a_note_is_dropped(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await go_to(app, pilot, "inv1")
        await pilot.press("i")
        await settle(app, pilot)
        await pilot.press("n", *"sorry", "enter", "s", "d")
        await settle(app, pilot)
        assert app.mail_client.invite_responses == [{"id": "inv1", "response": "decline", "note": "", "send": False}]
        assert not any(e.id == "e-inv1" for e in app.calendar_client._events)


async def test_note_is_sent_with_the_response(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await go_to(app, pilot, "inv1")
        await pilot.press("i")
        await settle(app, pilot)
        await pilot.press("n", *"late by 5", "enter", "t")
        await settle(app, pilot)
        assert app.mail_client.invite_responses[0]["note"] == "late by 5"
        assert app.mail_client.invite_responses[0]["response"] == "tentative"


async def test_escape_answers_nothing(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await go_to(app, pilot, "inv1")
        await pilot.press("i")
        await settle(app, pilot)
        await pilot.press("escape")
        await settle(app, pilot)
        assert app.mail_client.invite_responses == []


async def test_cancelled_meeting_is_removed_from_the_calendar(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await go_to(app, pilot, "can1")
        assert "Cancelled · i removes it from your calendar" in body(app)
        await pilot.press("i")
        await settle(app, pilot)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await settle(app, pilot)
        assert not any(e.id == "e4" for e in app.calendar_client._events)
        assert "can1" in [m.id for m in app.mail_client.list_messages("archive")]


async def test_i_on_a_normal_email_does_nothing(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await go_to(app, pilot, "m1")
        await pilot.press("i")
        await settle(app, pilot)
        assert not isinstance(app.screen, (InviteResponseScreen, ConfirmScreen))
        assert isinstance(app.query_one("#preview"), PreviewPane)
