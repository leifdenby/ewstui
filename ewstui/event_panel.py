"""`c`: make a calendar event from the email you're reading — for a
save-the-date that came as plain text, without an invite.

A panel docked on the right, so the email stays readable next to it (Tab
goes between the panel's fields and the reading pane). It starts from the
email: its subject (minus "Save the date:" and the like), the first
upcoming date the text mentions (all of them are marked in the calendar;
`n` in the calendar jumps to the next), and a time range if the text has
one (otherwise all day). Below, your calendar that day, as on invites.
Ctrl+S creates the event (in your calendar only, no invitations).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.message import Message
from textual.widgets import Checkbox, Input, Label, Static

from .dates import find_dates, find_time_range, parse_date
from .ews_client import MeetingInfo
from .invites import availability_lines
from .widgets.date_picker import DatePicker

_REPLY_PREFIXES = r"(?:re|fw|fwd|sv|vs|aw|wg)\s*:\s*|\[[^\]]*\]\s*"
_PREFIXES = re.compile(rf"^\s*(?:{_REPLY_PREFIXES}|save[\s-]+the[\s-]+date\s*[:!–—-]*\s*)+", re.I)


def event_subject(email_subject: str) -> str:
    """"Fwd: SAVE THE DATE: Autumn workshop" -> "Autumn workshop" (and a
    bare "Re: Save the date" -> "Save the date")."""
    cleaned = _PREFIXES.sub("", email_subject).strip(" -–—:!")
    return cleaned or re.sub(rf"^\s*(?:{_REPLY_PREFIXES})+", "", email_subject, flags=re.I).strip() or email_subject.strip()


def parse_time(text: str) -> time | None:
    """9 / 9:30 / 09.30 / 0930 -> a time."""
    s = text.strip().replace(".", ":")
    m = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?", s)
    if not m:
        return None
    try:
        return time(int(m[1]), int(m[2] or 0))
    except ValueError:
        return None


class EventFromEmailPanel(Vertical):
    BINDINGS = [
        Binding("ctrl+s", "create", "Create event"),
        Binding("escape", "close", "Close"),
    ]

    DEFAULT_CSS = """
    EventFromEmailPanel {
        dock: right;
        width: 54;
        height: 1fr;
        border: round $accent;
        border-title-align: left;
        border-subtitle-align: right;
        border-subtitle-color: $tux-dim;
        background: $surface;
        padding: 0 1;
    }
    EventFromEmailPanel Input {
        margin: 0;
        border-title-color: $tux-dim;
    }
    #ev-when {
        height: auto;
        margin-top: 1;
    }
    #ev-date-side {
        width: 1fr;
        height: auto;
        padding-left: 1;
    }
    #ev-keys {
        color: $text-muted;
        margin-top: 1;
    }
    #ev-found {
        color: $tux-key;
        margin-bottom: 1;
    }
    #ev-times {
        height: auto;
    }
    #ev-times Input {
        width: 11;
    }
    #ev-days {
        width: 8;
    }
    #ev-day {
        margin-top: 1;
        height: auto;
    }
    """

    class Submitted(Message):
        def __init__(self, values: dict) -> None:
            self.values = values
            super().__init__()

    class Closed(Message):
        pass

    class DayChanged(Message):
        """The chosen day changed: the app looks up your calendar for it."""

        def __init__(self, day: date) -> None:
            self.day = day
            super().__init__()

    def __init__(self, detail, today: date | None = None, **kwargs) -> None:
        super().__init__(id="event-panel", **kwargs)
        self.detail = detail
        self.today = today or date.today()
        text = f"{detail.subject}\n{detail.body_text or ''}"
        self.found = find_dates(text, self.today)
        upcoming = [d for d in self.found if d >= self.today]
        self._start_day = (upcoming or self.found or [self.today])[0]
        self._times = find_time_range(text)
        self._events: dict[date, list] = {}  # your calendar, by day, once looked up

    def compose(self) -> ComposeResult:
        self.border_title = "New event from this email"
        self.border_subtitle = "Ctrl+S create · Esc close"
        yield self._titled(Input(value=event_subject(self.detail.subject), placeholder="Title", id="ev-subject"), "title")
        with Horizontal(id="ev-when"):
            yield DatePicker(value=self._start_day, marked=self.found, today=self.today, id="ev-date")
            with Vertical(id="ev-date-side"):
                yield self._titled(Input(placeholder="fri, +3d, 14 oct", id="ev-date-text"), "type a date")
                yield Label("h/l j/k move · [ ] month\nt today" + (" · n next date" if self.found else ""),
                            id="ev-keys", markup=False)
        yield Label(self._found_text(), id="ev-found", markup=False)
        with Horizontal(id="ev-times"):
            start, end = self._times or (time(9), time(10))
            yield self._titled(Input(value=f"{start:%H:%M}", id="ev-start"), "start")
            yield self._titled(Input(value=f"{end:%H:%M}", id="ev-end"), "end")
            yield Checkbox("All day", value=self._times is None, id="ev-allday")
            yield self._titled(Input(value="1", id="ev-days"), "days")
        yield self._titled(Input(placeholder="optional", id="ev-location"), "location")
        yield Static(id="ev-day")

    @staticmethod
    def _titled(widget, title: str):
        widget.border_title = title
        return widget

    def _found_text(self) -> str:
        if not self.found:
            return "No dates found in the email."
        days = ", ".join(f"{d:%a %d %b}" for d in self.found[:4]) + (" …" if len(self.found) > 4 else "")
        return f"In the email: {days}"

    def on_mount(self) -> None:
        self._update_times_enabled()
        self.query_one(DatePicker).focus()
        self.post_message(self.DayChanged(self._start_day))
        self._draw_day()

    # -- keeping the fields in step --------------------------------------------

    def on_date_picker_changed(self, event: DatePicker.Changed) -> None:
        event.stop()
        self.post_message(self.DayChanged(event.value))
        self._draw_day()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "ev-date-text":
            day = parse_date(event.value, self.today)
            if day is None:
                self.app.bell()
                self.app.notify(f"Couldn't read {event.value!r} as a date", severity="warning")
                return
            event.input.value = ""
            picker = self.query_one(DatePicker)
            picker.value = day
            picker.focus()
        else:
            self.screen.focus_next()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in ("ev-start", "ev-end", "ev-days"):
            self._draw_day()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        event.stop()
        self._update_times_enabled()
        self._draw_day()

    def _update_times_enabled(self) -> None:
        all_day = self.query_one("#ev-allday", Checkbox).value
        self.query_one("#ev-start").disabled = all_day
        self.query_one("#ev-end").disabled = all_day
        self.query_one("#ev-days").display = all_day

    def set_day_events(self, day: date, events: list) -> None:
        """Your calendar for `day`, looked up by the app."""
        self._events[day] = events
        self._draw_day()

    def _draw_day(self) -> None:
        try:
            values = self.values()
        except ValueError:
            values = None
        day = self.query_one(DatePicker).value
        start = values["start"] if values else datetime.combine(day, time(9))
        end = values["end"] if values else start + timedelta(hours=1)
        info = MeetingInfo(kind="invite", start=start, end=end, is_all_day=bool(values and values["is_all_day"]))
        lines = availability_lines(info, self._events.get(day), self.query_one("#ev-day").size.width or 50,
                                   colors=self.app.theme_variables)
        header = Content.from_markup("[b]$d[/]", d=f"{day:%A %d %B %Y}")
        self.query_one("#ev-day", Static).update(Content("\n").join([header, *lines]))

    # -- the result --------------------------------------------------------------

    def values(self) -> dict:
        """The event as entered; ValueError (with what's wrong) if it can't be made."""
        day = self.query_one(DatePicker).value
        subject = self.query_one("#ev-subject", Input).value.strip() or event_subject(self.detail.subject)
        location = self.query_one("#ev-location", Input).value.strip()
        if self.query_one("#ev-allday", Checkbox).value:
            try:
                days = int(self.query_one("#ev-days", Input).value or "1")
            except ValueError:
                raise ValueError("days must be a number") from None
            if days < 1:
                raise ValueError("days must be at least 1")
            start = datetime.combine(day, time.min)
            return {"subject": subject, "start": start, "end": start + timedelta(days=days),
                    "location": location, "is_all_day": True}
        start_t = parse_time(self.query_one("#ev-start", Input).value)
        end_t = parse_time(self.query_one("#ev-end", Input).value)
        if start_t is None or end_t is None:
            raise ValueError("times are like 9:30 or 14")
        start, end = datetime.combine(day, start_t), datetime.combine(day, end_t)
        if end <= start:
            raise ValueError("the end must be after the start")
        return {"subject": subject, "start": start, "end": end, "location": location, "is_all_day": False}

    def action_create(self) -> None:
        try:
            values = self.values()
        except ValueError as e:
            self.app.bell()
            self.app.notify(f"Can't create the event: {e}", severity="warning")
            return
        self.post_message(self.Submitted(values))

    def action_close(self) -> None:
        self.post_message(self.Closed())
