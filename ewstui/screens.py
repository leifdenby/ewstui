from __future__ import annotations

from datetime import datetime, timedelta

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static, TextArea

from . import keymap
from .ews_client import Room, RoomAvailability


class ComposeScreen(ModalScreen[dict | None]):
    """New message / reply. Ctrl+S dismisses with the field values to
    send; Esc with the same plus "draft": True (saved to Drafts), or None
    if nothing was changed from how the window opened.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close (save to Drafts)"),
        Binding("ctrl+s", "send", "Send"),
        # Cmd+Enter only arrives in terminals that report the Cmd key
        # (kitty keyboard protocol: Ghostty, kitty, WezTerm, iTerm2 with
        # "Report keys using CSI u"); Terminal.app swallows it.
        Binding("super+enter", "send", "Send", show=False),
        # Your calendar beside the email, to check when you're free.
        Binding("ctrl+o", "toggle_calendar", "Calendar", show=False),
        Binding("ctrl+b", "calendar_weeks(-1)", "Earlier weeks", show=False),
        Binding("ctrl+f", "calendar_weeks(1)", "Later weeks", show=False),
    ]

    DEFAULT_CSS = """
    ComposeScreen {
        align: center middle;
    }
    #compose-box {
        width: 80%;
        height: 80%;
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }
    #compose-box.with-calendar {
        width: 98%;
        height: 90%;
    }
    #compose-fields {
        width: 1fr;
    }
    #compose-box.with-calendar #compose-fields {
        width: 2fr;
    }
    #compose-calendar {
        display: none;
        width: 3fr;
        padding-left: 2;
    }
    #compose-box.with-calendar #compose-calendar {
        display: block;
    }
    #compose-title {
        text-style: bold;
    }
    #compose-context {
        color: $text-muted;
    }
    #compose-body {
        height: 1fr;
        border: round $primary;
        margin-top: 1;
    }
    """

    def __init__(self, to: str = "", subject: str = "", body: str = "", context: str = "", unsaved: bool = False,
                 fetch_events=None, cc: str = "", title: str | None = None) -> None:
        super().__init__()
        self.title_text = title or ("Reply" if context else "New email")
        self._to = to
        self._cc = cc
        self._subject = subject
        self._body = body
        self.context = context  # for a reply: who sent the email and when
        # Opened with text that isn't saved anywhere (reopened after a failed
        # send): Esc saves it even if it's unchanged.
        self.unsaved = unsaved
        # fetch_events(start, end) -> [EventSummary], for the calendar (Ctrl+O);
        # called in a thread. None: no calendar.
        self.fetch_events = fetch_events

    def _hints(self) -> str:
        from .widgets.week_calendar import WeekCalendar

        from .widgets.vim_text_area import INSERT, VimTextArea

        if isinstance(self.focused, WeekCalendar):
            return ("h/l day · j/k week · Space pick day · v select days · Enter free times into the email · "
                    "Ctrl+B/F weeks · Esc/Ctrl+O back to writing")
        if isinstance(self.focused, VimTextArea):
            if self.focused.mode == INSERT:
                hints = ["Ctrl+S send", "Esc normal mode (vim)", "Tab next field"]
            elif self.focused.mode == "NORMAL":
                hints = ["Ctrl+S send", "i/a/o insert · v/V visual · dd yy p u", "Esc save to Drafts & close"]
            else:
                hints = ["move to select · d delete · y yank · c change", "Esc/v back to normal"]
        else:
            hints = ["Ctrl+S send", "Esc save to Drafts & close", "Tab next field"]
        if self.fetch_events:
            hints.append("Ctrl+O to the calendar (free times) · Ctrl+B/F weeks" if self.calendar_shown
                         else "Ctrl+O your calendar")
        return " · ".join(hints)

    def _update_hints(self) -> None:
        from .widgets.chrome import HintBar

        self.query_one(HintBar).set_hints(self._hints())

    def on_descendant_focus(self, event) -> None:
        self._update_hints()

    def on_vim_text_area_mode_changed(self, event) -> None:
        self._update_hints()

    def compose(self) -> ComposeResult:
        from .widgets.chrome import HintBar
        from .widgets.vim_text_area import VimTextArea
        from .widgets.week_calendar import WeekCalendar

        yield HintBar("WRITE", "", id="compose-hints")  # the keys, where the status bar is in the other views
        with Horizontal(id="compose-box"):
            with Vertical(id="compose-fields"):
                yield Label(self.title_text, id="compose-title")
                if self.context:
                    yield Label(self.context, id="compose-context", markup=False)
                yield Input(value=self._to, placeholder="To", id="compose-to")
                yield Input(value=self._cc, placeholder="Cc", id="compose-cc")
                yield Input(value=self._subject, placeholder="Subject", id="compose-subject")
                yield VimTextArea(self._body, id="compose-body")  # vim modes, shown bottom right
            yield WeekCalendar(id="compose-calendar")

    # -- the calendar beside the email (Ctrl+O) ------------------------------------

    def on_mount(self) -> None:
        self._update_hints()

    @property
    def calendar_shown(self) -> bool:
        return self.query_one("#compose-box").has_class("with-calendar")

    def action_toggle_calendar(self) -> None:
        """Ctrl+O: show the calendar and go to it; from the calendar, back
        to writing (and hide it)."""
        from .widgets.week_calendar import WeekCalendar

        if self.fetch_events is None:
            return
        box = self.query_one("#compose-box")
        calendar = self.query_one(WeekCalendar)
        if not self.calendar_shown:
            box.add_class("with-calendar")
            self._load_calendar()
            calendar.focus()
        elif calendar.has_focus:
            box.remove_class("with-calendar")
            self.query_one("#compose-body", TextArea).focus()
        else:
            calendar.focus()
        self._update_hints()

    def on_week_calendar_weeks_changed(self, event) -> None:
        self._load_calendar()

    def on_week_calendar_left(self, event) -> None:
        self.query_one("#compose-body", TextArea).focus()

    def on_week_calendar_availability_requested(self, event) -> None:
        """Enter on the chosen days: your free times, as text, into the email
        where its cursor was."""
        from .availability import availability_on

        days = event.days
        start = datetime.combine(min(days), datetime.min.time())
        end = datetime.combine(max(days) + timedelta(days=1), datetime.min.time())

        def work() -> None:
            try:
                events = self.fetch_events(start, end)
            except Exception as e:  # noqa: BLE001
                self.app.call_from_thread(self.app.notify, f"Couldn't check your calendar: {e}", severity="error")
                return
            self.app.call_from_thread(self._insert_into_body, availability_on(events, days))

        self.run_worker(work, thread=True, group="compose-availability")

    def _insert_into_body(self, text: str) -> None:
        body = self.query_one("#compose-body", TextArea)
        body.focus()
        body.insert(text)  # at the cursor, which stayed where you left it

    def action_calendar_weeks(self, weeks: int) -> None:
        if not self.calendar_shown:
            return
        from .widgets.week_calendar import WeekCalendar

        self.query_one(WeekCalendar).shift(weeks * 3)
        self._load_calendar()

    def _load_calendar(self) -> None:
        from .widgets.week_calendar import WeekCalendar

        calendar = self.query_one(WeekCalendar)
        first, last = calendar.first, calendar.last
        start = datetime.combine(first, datetime.min.time())
        end = datetime.combine(last + timedelta(days=1), datetime.min.time())

        def fetch() -> None:
            try:
                events = self.fetch_events(start, end)
            except Exception as e:  # noqa: BLE001 - writing goes on without it
                self.app.call_from_thread(self.app.notify, f"Couldn't load your calendar: {e}", severity="warning")
                events = []
            self.app.call_from_thread(calendar.set_events, first, events)

        self.run_worker(fetch, thread=True, exclusive=True, group="compose-calendar")

    def _fields(self) -> dict:
        return {
            "to": self.query_one("#compose-to", Input).value,
            "cc": self.query_one("#compose-cc", Input).value,
            "subject": self.query_one("#compose-subject", Input).value,
            "body": self.query_one("#compose-body", TextArea).text,
        }

    def action_cancel(self) -> None:
        fields = self._fields()
        if not self.unsaved and fields == {"to": self._to, "cc": self._cc, "subject": self._subject, "body": self._body}:
            self.dismiss(None)  # nothing written: just close
        else:
            self.dismiss({**fields, "draft": True})

    def action_send(self) -> None:
        self.dismiss(self._fields())


CHECKING = "checking"  # an attendee's free/busy is being looked up
FREE_STYLE, BUSY_STYLE, UNSURE_STYLE = "#7aa67a", "#e07a7a", "#d4b06a"


def split_addresses(text: str) -> list[str]:
    """"a@x, b@x; c@x" -> ["a@x", "b@x", "c@x"]."""
    return [a.strip() for a in text.replace(";", ",").split(",") if a.strip()]


def attendee_line(email: str, status, start: datetime | None = None) -> Text:
    """One attendee in the new-event form: the address, then whether
    they're free for the meeting — `status` is a RoomAvailability,
    CHECKING, or None when free/busy isn't looked up (no calendar)."""
    line = Text("  ")
    if status is None:
        return line.append(f"· {email}")
    if status == CHECKING:
        return line.append(f"… {email}  ").append("checking…", style="dim")
    if status.error:
        return line.append(f"? {email}  ", style=UNSURE_STYLE).append("couldn't check — is the address right?",
                                                                      style="dim")
    if status.free:
        return line.append(f"✓ {email}  ", style=FREE_STYLE).append("free", style=FREE_STYLE)
    slots = []
    for b in sorted(status.busy, key=lambda b: b.start):
        when = f"{b.start:%H:%M}–{b.end:%H:%M}"
        if start is not None and b.start.date() != start.date():
            when = f"{b.start:%a} {when}"
        kind = {"Tentative": "tentative", "OOF": "out of office", "WorkingElsewhere": "working elsewhere"}.get(
            b.busy_type, "busy")
        slots.append(f"{kind} {when}" + (f" ({b.subject})" if b.subject else ""))
    style = UNSURE_STYLE if all(b.busy_type == "Tentative" for b in status.busy) else BUSY_STYLE
    return line.append(f"✗ {email}  ", style=style).append(", ".join(slots), style=style)


class AttendeeInput(Input):
    """The attendee field: Backspace with nothing typed asks to take the
    last added attendee off the list."""

    class RemoveLast(Message):
        pass

    def action_delete_left(self) -> None:
        if not self.value:
            self.post_message(self.RemoveLast())
            return
        super().action_delete_left()


class NewEventScreen(ModalScreen[dict | None]):
    """New calendar event. Dismisses with a dict of field values, or None.
    Ctrl+T instead: the same plus "teams": True, to schedule it in Teams
    (a Teams meeting, with its join link) rather than as a plain event.

    Attendees: type an address and press Enter to add it to the list
    below the field (Backspace in the empty field takes the last one off
    again). With `free_busy(emails, start, end)` each one is looked up and
    shown as free or busy for the meeting's time, and looked up again
    when the time changes."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+t", "teams", "Schedule in Teams"),
    ]

    DEFAULT_CSS = """
    NewEventScreen {
        align: center middle;
    }
    #event-box {
        width: 60%;
        height: auto;
        max-height: 100%;  /* a short terminal scrolls the form rather than cutting off its end */
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }
    #event-box Input {
        margin-bottom: 1;
    }
    #event-notes {
        height: 6;
    }
    #event-attendee-list {
        height: auto;
        max-height: 8;
        margin-bottom: 1;
    }
    """

    def __init__(
        self,
        default_start: datetime | None = None,
        default_end: datetime | None = None,
        default_location: str = "",
        title: str = "New event",
        free_busy=None,
        attendees: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.free_busy = free_busy  # (emails, start, end) -> [RoomAvailability], or None
        self.attendees: list[str] = list(attendees or [])
        self._status: dict[str, object] = {}  # address -> RoomAvailability / CHECKING
        start = default_start or datetime.now()
        end = default_end or start + timedelta(hours=1)
        self._start_default = start.strftime("%Y-%m-%d %H:%M")
        self._end_default = end.strftime("%Y-%m-%d %H:%M")
        self._location_default = default_location
        self._title = title

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="event-box"):
            yield Label(f"{self._title}  (Ctrl+S save · Ctrl+T Teams meeting instead · Esc cancel)", markup=False)
            yield Input(placeholder="Subject", id="event-subject")
            yield Input(value=self._start_default, placeholder="Start (YYYY-MM-DD HH:MM)", id="event-start")
            yield Input(value=self._end_default, placeholder="End (YYYY-MM-DD HH:MM)", id="event-end")
            yield Input(value=self._location_default, placeholder="Location", id="event-location")
            yield AttendeeInput(placeholder="Attendee address, then Enter to add (Backspace when empty removes the last)",
                                id="event-attendees")
            yield Static(id="event-attendee-list")
            yield TextArea(id="event-notes", placeholder="Notes")

    def on_mount(self) -> None:
        self.query_one("#event-subject", Input).focus()
        self._check(list(self.attendees))  # ones brought along from the room finder

    def _times(self) -> tuple[datetime, datetime] | None:
        fmt = "%Y-%m-%d %H:%M"
        try:
            start = datetime.strptime(self.query_one("#event-start", Input).value, fmt)
            end = datetime.strptime(self.query_one("#event-end", Input).value, fmt)
        except ValueError:
            return None
        return start, end

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "event-attendees":
            return
        event.stop()
        new = [a for a in split_addresses(event.value) if a.casefold() not in {x.casefold() for x in self.attendees}]
        event.input.value = ""
        if new:
            self.attendees += new
            self._check(new)

    def on_attendee_input_remove_last(self, event: AttendeeInput.RemoveLast) -> None:
        if self.attendees:
            self._status.pop(self.attendees.pop(), None)
            self._show_attendees()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in ("event-start", "event-end") and self.attendees and self._times():
            self._check(list(self.attendees))  # a new time: are they free then?

    def _check(self, emails: list[str]) -> None:
        times = self._times()
        if not emails or self.free_busy is None or times is None or times[1] <= times[0]:
            self._show_attendees()
            return
        for email in emails:
            self._status[email] = CHECKING
        self._show_attendees()

        def work() -> None:
            try:
                results = self.free_busy(emails, *times)
            except Exception as e:  # noqa: BLE001 - say so, don't crash
                results = [RoomAvailability(Room(email, email), free=False, error=str(e)) for email in emails]
            self.app.call_from_thread(self._checked, times, emails, results)

        self.run_worker(work, thread=True, group="event-free-busy")

    def _checked(self, times, emails: list[str], results: list) -> None:
        if times != self._times():
            return  # the time changed meanwhile; a newer check is on its way
        for email, result in zip(emails, results):
            if email in self.attendees:
                self._status[email] = result
        self._show_attendees()

    def _show_attendees(self) -> None:
        box = self.query_one("#event-attendee-list", Static)
        box.display = bool(self.attendees)
        times = self._times()
        text = Text("\n").join(
            attendee_line(email, self._status.get(email) if self.free_busy else None, times[0] if times else None)
            for email in self.attendees
        )
        box.update(text)
        if self.attendees:
            self.call_after_refresh(box.scroll_visible, animate=False)  # the newest one, on a short terminal

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self, teams: bool = False) -> None:
        times = self._times()
        if times is None:
            self.app.bell()
            return
        start, end = times
        typed = [a for a in split_addresses(self.query_one("#event-attendees", Input).value)
                 if a.casefold() not in {x.casefold() for x in self.attendees}]  # typed but not Enter'd
        self.dismiss(
            {
                "subject": self.query_one("#event-subject", Input).value or "(no subject)",
                "start": start,
                "end": end,
                "location": self.query_one("#event-location", Input).value,
                "attendees": [*self.attendees, *typed],
                "notes": self.query_one("#event-notes", TextArea).text.strip(),
                "teams": teams,
            }
        )

    def action_teams(self) -> None:
        self.action_save(teams=True)


class PickPriorityScreen(ModalScreen[str | None]):
    """Tiny prompt: press a single A-Z key to pick a todo.txt priority
    letter, or Esc to cancel. Dismisses with that letter, or None.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        *[Binding(chr(65 + i), f"pick('{chr(65 + i)}')", show=False) for i in range(26)],
    ]

    DEFAULT_CSS = """
    PickPriorityScreen {
        align: center middle;
    }
    #pick-box {
        width: auto;
        height: auto;
        border: round $accent;
        padding: 1 3;
        background: $surface;
    }
    """

    def __init__(self, subject: str) -> None:
        super().__init__()
        self._subject = subject

    def compose(self) -> ComposeResult:
        with Vertical(id="pick-box"):
            yield Label(f"Add to priority list: {self._subject}")
            yield Label("Press a letter A-Z for its priority, or Esc to cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_pick(self, letter: str) -> None:
        self.dismiss(letter)


class AddNoteScreen(ModalScreen[str | None]):
    """A short text prompt. Dismisses with the entered text (empty string
    if left blank but saved), or None if cancelled.

    Typing `due:` or `t:` (todo.txt's due and threshold dates) opens a
    month calendar, tuxedo-style: h/l j/k [ ] t pick the day, Enter puts it
    in (due:2026-10-01), Esc closes it and you go on typing. `dur:` (how
    long answering will take) opens a row of durations the same way: h/l
    move, 1-7 pick one straight away (dur:30m).
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
        Binding("enter", "pick_date", "Put the date in", show=False),  # (in the note field, Enter saves)
    ]

    DEFAULT_CSS = """
    AddNoteScreen {
        align: center middle;
    }
    #note-box {
        width: 60%;
        height: auto;
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }
    #note-date, #note-duration {
        display: none;
        height: auto;
        margin-top: 1;
    }
    #note-date.open, #note-duration.open {
        display: block;
    }
    #note-date-help, #note-duration-help {
        color: $text-muted;
    }
    """

    def __init__(self, label: str = "Note (Ctrl+S to save, Esc to cancel)", value: str = "") -> None:
        super().__init__()
        self._label = label
        self._value = value  # an existing note, to edit

    def compose(self) -> ComposeResult:
        from .widgets.date_picker import DatePicker
        from .widgets.duration_picker import DurationPicker

        with Vertical(id="note-box"):
            yield Label(self._label, markup=False)
            # (no select-on-focus: coming back from a picker, typing goes on after what it put in)
            yield Input(value=self._value, placeholder="Note (due: or t: picks a date, dur: how long)", id="note-text",
                        select_on_focus=False)
            with Vertical(id="note-date"):
                yield DatePicker(id="note-date-picker")
                yield Label("h/l day · j/k week · [ ] month · t today\nEnter put it in · Esc back to typing",
                            id="note-date-help", markup=False)
            with Vertical(id="note-duration"):
                yield Label("How long will it take?", markup=False)
                yield DurationPicker(id="note-duration-picker")
                yield Label("h/l choose · 1-7 or Enter put it in · Esc back to typing",
                            id="note-duration-help", markup=False)

    def on_mount(self) -> None:
        self.query_one("#note-text", Input).focus()

    # -- the pickers: calendar (due: / t:), durations (dur:) --------------------------

    PICKERS = {"due:": "#note-date", "t:": "#note-date", "dur:": "#note-duration"}

    @property
    def picking_date(self) -> bool:
        return self.query_one("#note-date").has_class("open")

    @property
    def picking_duration(self) -> bool:
        return self.query_one("#note-duration").has_class("open")

    def on_input_changed(self, event: Input.Changed) -> None:
        field = event.input
        before = field.value[: field.cursor_position]
        for key, picker in self.PICKERS.items():
            if before.endswith(key) and (len(before) == len(key) or before[-len(key) - 1] == " "):
                self._open_picker(picker)
                return

    def _open_picker(self, box_id: str) -> None:
        from datetime import date

        from .widgets.date_picker import DatePicker
        from .widgets.duration_picker import DurationPicker

        box = self.query_one(box_id)
        box.add_class("open")
        if box_id == "#note-date":
            picker = self.query_one(DatePicker)
            picker.value = date.today()
        else:
            picker = self.query_one(DurationPicker)
        picker.focus()

    def _close_pickers(self) -> None:
        for box_id in ("#note-date", "#note-duration"):
            self.query_one(box_id).remove_class("open")
        self.query_one("#note-text", Input).focus()

    def _insert(self, text: str) -> None:
        """Put `text` in the note where the cursor is, close the picker and
        go on typing after it."""
        field = self.query_one("#note-text", Input)
        at = field.cursor_position
        field.value = field.value[:at] + text + field.value[at:]
        self._close_pickers()
        field.cursor_position = at + len(text)

    def action_pick_date(self) -> None:  # Enter (in the note field itself, Enter saves)
        from .widgets.date_picker import DatePicker
        from .widgets.duration_picker import DurationPicker

        if self.picking_date:
            self._insert(self.query_one(DatePicker).value.isoformat())
        elif self.picking_duration:
            self._insert(self.query_one(DurationPicker).value)

    def on_duration_picker_picked(self, event) -> None:  # 1-7
        self._insert(event.value)

    def action_cancel(self) -> None:
        if self.picking_date or self.picking_duration:  # Esc first closes the picker
            self._close_pickers()
            return
        self.dismiss(None)

    def action_save(self) -> None:
        self.dismiss(self.query_one("#note-text", Input).value.strip())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_save()


class InviteResponseScreen(ModalScreen[dict | None]):
    """`i` on an invite: the meeting and your availability that day, then
    a / t / d to accept, tentatively accept or decline. `s` toggles whether
    the organizer is sent your response; `n` (or Tab) goes to an optional
    note sent with it, Enter comes back. Dismisses with
    {"response", "note", "send"}, or None.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("a", "respond('accept')", "Accept"),
        Binding("t", "respond('tentative')", "Tentative"),
        Binding("d", "respond('decline')", "Decline"),
        Binding("s", "toggle_send", "Send response"),
        Binding("n,tab", "note", "Note", show=False),
    ]
    AUTO_FOCUS = ""  # focus nothing: letters are answers, not typing, until n/Tab

    DEFAULT_CSS = """
    InviteResponseScreen {
        align: center middle;
    }
    #invite-box {
        width: 90%;
        max-width: 110;
        height: auto;
        border: round $accent;
        border-title-align: left;
        padding: 1 2;
        background: $surface;
    }
    #invite-choices {
        margin-top: 1;
    }
    #invite-note {
        margin-top: 1;
    }
    """

    def __init__(self, subject: str, meeting, events=None, organizer_name: str = "") -> None:
        super().__init__()
        self._subject = subject
        self._meeting = meeting
        self._events = events  # your calendar that day; None while it's being looked up
        self._organizer = organizer_name or meeting.organizer
        self.send = True

    def compose(self) -> ComposeResult:
        with Vertical(id="invite-box") as box:
            box.border_title = f"Respond to “{self._subject}”"
            yield Static(id="invite-meeting")
            yield Static(id="invite-choices")
            yield Static(id="invite-send")
            yield Input(placeholder="Note to send with your response (optional) — n or Tab to type, Enter when done",
                        id="invite-note")

    def on_mount(self) -> None:
        self._draw()

    def on_resize(self) -> None:
        self._draw()

    def set_events(self, events) -> None:
        """The calendar lookup is back: show clashes and the day strip."""
        self._events = events
        self._draw()

    def _draw(self) -> None:
        from textual.content import Content

        from .invites import availability_lines, response_line, when_text
        from .widgets.message_header import LABEL_WIDTH

        c = self.app.theme_variables
        if not self.query("#invite-meeting"):  # a resize before the widgets are there
            return
        width = self.query_one("#invite-meeting").size.width or 80
        label = lambda text: Content.from_markup(f"[{c['tux-dim']}]$t[/]", t=f"{text:<{LABEL_WIDTH}}")  # noqa: E731
        m = self._meeting
        lines = [label("When") + Content.from_markup("[b]$w[/]", w=when_text(m))]
        if m.location:
            lines.append(label("Where") + Content(m.location))
        if m.my_response not in ("Unknown", "NoResponseReceived"):
            lines.append(response_line(m, c))
        lines += availability_lines(m, self._events, width, colors=c)
        self.query_one("#invite-meeting", Static).update(Content("\n").join(lines))
        key = lambda k, text: Content.from_markup(f"[b {c['tux-key']}]$k[/] $t   ", k=k, t=text)  # noqa: E731
        self.query_one("#invite-choices", Static).update(
            key("a", "accept") + key("t", "tentative") + key("d", "decline") + Content.from_markup(f"[{c['tux-dim']}]Esc cancel[/]")
        )
        box = "[x]" if self.send else "[ ]"
        self.query_one("#invite-send", Static).update(
            Content.from_markup(f"[b {c['tux-key']}]s[/] $box send your response to $who", box=box, who=self._organizer)
            if self.send else
            Content.from_markup(f"[b {c['tux-key']}]s[/] $box send your response (off: only your calendar changes)", box=box)
        )

    def action_toggle_send(self) -> None:
        self.send = not self.send
        self._draw()

    def action_note(self) -> None:
        self.query_one("#invite-note", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.set_focus(None)  # back to answering with a / t / d

    def action_respond(self, response: str) -> None:
        note = self.query_one("#invite-note", Input).value.strip()
        self.dismiss({"response": response, "note": note if self.send else "", "send": self.send})

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no question: y confirms; n or Esc says no."""

    BINDINGS = [
        Binding("y", "answer(True)", "Yes"),
        Binding("n,escape", "answer(False)", "No"),
    ]

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #confirm-box {
        width: auto;
        max-width: 90%;
        height: auto;
        border: round $accent;
        padding: 1 3;
        background: $surface;
    }
    """

    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._question, markup=False)
            yield Label("y yes · n / Esc no")

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)


class AttachmentListScreen(ModalScreen[dict | None]):
    """Lists a message's attachments; Enter/l saves + opens the selected
    one, s saves it to a folder you pick, Esc cancels. V selects several
    (j/k extend it) for Enter / s to do all at once; Esc first ends the
    selection. Dismisses with {"ids": [attachment ids], "action": "open" |
    "save_to", "skipped": n not downloadable}, or None.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("l", "select_cursor", "Open", show=False),
        Binding("s", "save_to", "Save to…", show=False),
        Binding("V", "toggle_visual", "Select several", show=False),
    ]

    DEFAULT_CSS = """
    AttachmentListScreen {
        align: center middle;
    }
    #attachment-box {
        width: 70%;
        height: auto;
        max-height: 80%;
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }
    #attachment-items ListItem.-selected-attachment {
        background: $tux-mode-bg 40%;
        text-style: bold;
    }
    #attachment-help {
        color: $text-muted;
    }
    """

    def __init__(self, subject: str, attachments: list) -> None:
        super().__init__()
        self._subject = subject
        self._attachments = attachments
        self._anchor: int | None = None  # where V started a selection

    def compose(self) -> ComposeResult:
        from textual.widgets import ListItem, ListView

        with Vertical(id="attachment-box"):
            yield Label(f"Attachments — {self._subject}", markup=False)
            with ListView(id="attachment-items"):
                for att in self._attachments:
                    note = "" if att.is_file else "  [dim](embedded item, not downloadable)[/dim]"
                    yield ListItem(Label(f"{att.name}  ({_human_size(att.size)}){note}"), name=att.id)
            yield Label("", id="attachment-help", markup=False)

    def on_mount(self) -> None:
        self._show_selection()

    # -- selecting several (V) ----------------------------------------------------

    def selected(self) -> list:
        """The attachments chosen: the selection, or the one under the cursor."""
        index = self._list().index
        if index is None:
            return []
        if self._anchor is None:
            return [self._attachments[index]]
        lo, hi = sorted((self._anchor, index))
        return self._attachments[lo:hi + 1]

    def action_toggle_visual(self) -> None:
        index = self._list().index
        self._anchor = None if self._anchor is not None or index is None else index
        self._show_selection()

    def _show_selection(self) -> None:
        from textual.widgets import ListItem

        chosen = {id(a) for a in self.selected()} if self._anchor is not None else set()
        for item, att in zip(self._list().query(ListItem), self._attachments):
            item.set_class(id(att) in chosen, "-selected-attachment")
        if self._anchor is None:
            text = "Enter/l save + open · s save to… · V select several · Esc cancel"
        else:
            text = f"{len(self.selected())} selected · j/k extend · Enter save + open all · s save all to… · Esc end selection"
        self.query_one("#attachment-help", Label).update(text)

    def _list(self):
        from textual.widgets import ListView

        return self.query_one("#attachment-items", ListView)

    # These are defined directly on the screen (not left to bubble into
    # the child ListView's own actions) because a screen-level Binding
    # only resolves against methods the *screen* itself defines — see
    # the same gotcha with pane-switching h/l in widgets/message_table.py.
    def action_cursor_down(self) -> None:
        lv = self._list()
        lv.index = 0 if lv.index is None else min(lv.index + 1, len(self._attachments) - 1)
        self._show_selection()

    def action_cursor_up(self) -> None:
        lv = self._list()
        lv.index = 0 if lv.index is None else max(lv.index - 1, 0)
        self._show_selection()

    def on_list_view_highlighted(self, event) -> None:
        if self.is_mounted:
            self._show_selection()

    def action_select_cursor(self) -> None:
        self._select_current()

    def action_save_to(self) -> None:
        self._select_current("save_to")

    def action_cancel(self) -> None:
        if self._anchor is not None:  # Esc first ends a selection
            self._anchor = None
            self._show_selection()
            return
        self.dismiss(None)

    def on_list_view_selected(self, event) -> None:
        self._select_current()

    def _select_current(self, action: str = "open") -> None:
        chosen = self.selected()
        files = [a for a in chosen if a.is_file]
        if not files:  # only embedded items: nothing to download
            self.app.bell()
            return
        self.dismiss({"ids": [a.id for a in files], "action": action, "skipped": len(chosen) - len(files)})


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


KEY_WIDTH = 14  # key column, like tuxedo's padded key chips
TWO_COLUMN_MIN_WIDTH = 100  # below this the sections stack in one column


def help_column(sections, v: dict[str, str]) -> Text:
    """One column of the help overlay: bold accent section titles, keys
    padded in the key colour, descriptions in the normal text colour."""
    out = Text()
    for i, (title, keys) in enumerate(sections):
        if i:
            out.append("\n")
        out.append(f"{title}\n", style=f"bold {v['accent']}")
        for key, desc in keys:
            out.append(f"  {key:<{KEY_WIDTH}}", style=f"bold {v['tux-key']}")
            out.append(f"{desc}\n", style=v["foreground"])
    return out


class HelpScreen(ModalScreen[None]):
    """`?`: the keybindings for the current view (mail, priority or
    calendar) next to the ones that work everywhere, tuxedo-style — a
    titled panel with sections in two columns (one column on narrow
    terminals), scrollable if it doesn't fit. j/k, Ctrl+d/u and arrows
    scroll; Esc, ? or q close.
    """

    BINDINGS = [
        Binding("escape,question_mark,q", "dismiss_help", "Close"),
        Binding("j,down", "scroll(1)", show=False),
        Binding("k,up", "scroll(-1)", show=False),
        Binding("ctrl+d,space", "scroll(10)", show=False),
        Binding("ctrl+u", "scroll(-10)", show=False),
    ]

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-box {
        width: 90%;
        max-width: 120;
        height: auto;
        max-height: 90%;
        border: round $border;
        border-title-align: left;
        border-subtitle-align: right;
        border-subtitle-color: $tux-dim;
        background: $panel;
        padding: 0 1;
    }
    #help-scroll {
        height: auto;
        max-height: 100%;
    }
    #help-columns {
        height: auto;
    }
    #help-columns Static {
        width: 1fr;
        height: auto;
    }
    #help-columns.narrow {
        layout: vertical;
    }
    """

    def __init__(self, mode: str = "mail") -> None:
        super().__init__()
        self._mode = mode if mode in keymap.HELP_COLUMNS else "mail"

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            with VerticalScroll(id="help-scroll"):
                with Horizontal(id="help-columns"):
                    yield Static(id="help-left")
                    yield Static(id="help-right")

    def on_mount(self) -> None:
        v = self.app.get_css_variables()
        box = self.query_one("#help-box")
        box.border_title = Text.assemble(
            (" ewstui", f"bold {v['accent']}"), (" · help · ", v["tux-dim"]), (f"{self._mode.title()} ", v["foreground"])
        )
        box.border_subtitle = " Esc close · j/k scroll "
        left, right = keymap.HELP_COLUMNS[self._mode]
        self.query_one("#help-left", Static).update(help_column(left, v))
        self.query_one("#help-right", Static).update(help_column(right, v))
        self._fit(self.app.size.width)

    def on_resize(self, event) -> None:
        self._fit(event.size.width)

    def _fit(self, width: int) -> None:
        self.query_one("#help-columns").set_class(width < TWO_COLUMN_MIN_WIDTH, "narrow")

    def action_scroll(self, lines: int) -> None:
        self.query_one("#help-scroll", VerticalScroll).scroll_relative(y=lines, animate=False)

    def action_dismiss_help(self) -> None:
        self.dismiss(None)
