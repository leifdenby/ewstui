"""The email header in the reading pane: names, aligned labels, long To/Cc
lists folded to "+N more" that e (or a click) unfolds."""
from __future__ import annotations

from datetime import datetime

from exchangelib import Mailbox, Message

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import MessageDetail, _names
from ewstui.widgets.message_header import fold, render_header
from ewstui.widgets.preview import PreviewPane

PEOPLE = [f"person{i}@corp.example" for i in range(30)]
NAMES = {a: f"Person Number{i}" for i, a in enumerate(PEOPLE)}


def detail(**kw) -> MessageDetail:
    base = dict(
        id="x", changekey="c", subject="Hello", sender="boss@corp.example", received=datetime(2026, 9, 25, 13, 22),
        is_read=True, has_attachments=False, to=list(PEOPLE), cc=[], body_text="body",
        names={**NAMES, "boss@corp.example": "The Boss"},
    )
    return MessageDetail(**{**base, **kw})


def lines(msg, width=80, expanded=False) -> list[str]:
    return render_header(msg, width, expanded).plain.splitlines()


def test_fold_keeps_what_fits_and_counts_the_rest():
    shown, hidden = fold(PEOPLE, NAMES, 60)
    assert shown == ["Person Number0", "Person Number1", "Person Number2"]
    assert hidden == 27
    assert fold(PEOPLE[:2], NAMES, 60) == (["Person Number0", "Person Number1"], 0)
    assert fold(PEOPLE, NAMES, 5) == (["Person Number0"], 29)  # always at least one


def test_fold_falls_back_to_the_address_without_a_name():
    assert fold(["a@x.test"], {}, 40) == (["a@x.test"], 0)


def test_folded_header():
    out = lines(detail(cc=["cc@corp.example"]))
    assert out[0] == "Hello"
    assert out[2] == "From  The Boss <boss@corp.example>"
    assert out[3].startswith("To    Person Number0, Person Number1, ")
    assert out[3].endswith("more · e") and len(out[3]) <= 80
    assert out[4] == "Cc    cc@corp.example"  # a single recipient is shown in full
    assert out[5] == "Date  Fri 25 Sep 2026 13:22"
    assert set(out[6]) == {"─"}


def test_expanded_header_lists_everyone_with_their_address():
    out = lines(detail(), expanded=True)
    assert out[3] == "To    Person Number0 <person0@corp.example>  30 people"
    assert out[4] == "      Person Number1 <person1@corp.example>"
    assert out[3 + 29] == "      Person Number29 <person29@corp.example>"
    assert out[3 + 30].startswith("Date")


def test_no_recipients_and_attachments():
    out = lines(detail(to=[], has_attachments=True, received=None))
    assert "To    (none)" in out
    assert "Date  (no date)" in out
    assert any("has attachments" in line for line in out)


def test_brackets_in_the_email_are_text_not_markup():
    out = lines(detail(subject="[EXT] Re: [b]budget[/b] [/]"))
    assert out[0] == "[EXT] Re: [b]budget[/b] [/]"


def test_names_come_from_the_exchange_mailboxes():
    item = Message(
        sender=Mailbox(email_address="boss@corp.example", name="The Boss"),
        to_recipients=[Mailbox(email_address="a@corp.example", name="Anna"), Mailbox(email_address="b@corp.example")],
        cc_recipients=[Mailbox(email_address="c@corp.example", name="c@corp.example")],
    )
    assert _names(item) == {"boss@corp.example": "The Boss", "a@corp.example": "Anna"}


# -- in the app -----------------------------------------------------------

def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


def body_text(app) -> str:
    return str(app.query_one("#preview-body").render())


async def open_maintenance_email(app, pilot):
    await pilot.pause()
    await pilot.press("j", "j")  # m3: 20 To + 5 Cc recipients
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_e_unfolds_and_folds_the_recipients(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_maintenance_email(app, pilot)
        assert "more · e" in body_text(app) and "anna.berg@corp.example" not in body_text(app)
        await pilot.press("e")
        await pilot.pause()
        assert "Anna Berg <anna.berg@corp.example>  20 people" in body_text(app)
        assert "Project Team <team@corp.example>" in body_text(app)
        await pilot.press("e")
        await pilot.pause()
        assert "more · e" in body_text(app)


async def test_e_works_from_the_reading_pane_and_resets_per_email(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_maintenance_email(app, pilot)
        await pilot.press("o", "e")  # focus in the reading pane
        await pilot.pause()
        assert app.query_one("#preview", PreviewPane).recipients_expanded
        await pilot.press("h", "k")  # another email: folded again
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not app.query_one("#preview", PreviewPane).recipients_expanded


async def test_rule_and_folded_line_fit_when_a_scrollbar_appears(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 30)) as pilot:
        await open_maintenance_email(app, pilot)
        pane = app.query_one("#preview", PreviewPane)
        assert not pane.show_vertical_scrollbar
        await pilot.press("e")  # 25 recipient lines: now it scrolls
        await pilot.pause()
        await pilot.pause()
        assert pane.show_vertical_scrollbar
        width = app.query_one("#preview-body").size.width
        rule = next(line for line in body_text(app).splitlines() if line.startswith("──"))
        assert len(rule) <= width


async def test_clicking_more_unfolds(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_maintenance_email(app, pilot)
        to_line = body_text(app).splitlines()[3]
        await pilot.click("#preview-body", offset=(1, 3))  # on "To": nothing
        await pilot.pause()
        assert not app.query_one("#preview", PreviewPane).recipients_expanded
        await pilot.click("#preview-body", offset=(to_line.index("+") + 1, 3))
        await pilot.pause()
        assert app.query_one("#preview", PreviewPane).recipients_expanded
