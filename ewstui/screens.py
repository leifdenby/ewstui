from __future__ import annotations

from datetime import datetime, timedelta

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static, TextArea

from . import keymap


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
                 fetch_events=None) -> None:
        super().__init__()
        self._to = to
        self._subject = subject
        self._body = body
        self.context = context  # for a reply: who sent the email and when
        # Opened with text that isn't saved anywhere (reopened after a failed
        # send): Esc saves it even if it's unchanged.
        self.unsaved = unsaved
        # fetch_events(start, end) -> [EventSummary], for the calendar (Ctrl+O);
        # called in a thread. None: no calendar.
        self.fetch_events = fetch_events

    def compose(self) -> ComposeResult:
        from .widgets.week_calendar import WeekCalendar

        with Horizontal(id="compose-box"):
            with Vertical(id="compose-fields"):
                yield Label("Compose  (Ctrl+S or Cmd+Enter to send, Esc to save to Drafts and close"
                            + (", Ctrl+O your calendar)" if self.fetch_events else ")"))
                if self.context:
                    yield Label(self.context, id="compose-context", markup=False)
                yield Input(value=self._to, placeholder="To", id="compose-to")
                yield Input(value=self._subject, placeholder="Subject", id="compose-subject")
                yield TextArea(self._body, id="compose-body")
            yield WeekCalendar(id="compose-calendar")

    # -- the calendar beside the email (Ctrl+O) ------------------------------------

    @property
    def calendar_shown(self) -> bool:
        return self.query_one("#compose-box").has_class("with-calendar")

    def action_toggle_calendar(self) -> None:
        if self.fetch_events is None:
            return
        box = self.query_one("#compose-box")
        box.toggle_class("with-calendar")
        if self.calendar_shown:
            self._load_calendar()

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
            "subject": self.query_one("#compose-subject", Input).value,
            "body": self.query_one("#compose-body", TextArea).text,
        }

    def action_cancel(self) -> None:
        fields = self._fields()
        if not self.unsaved and fields == {"to": self._to, "subject": self._subject, "body": self._body}:
            self.dismiss(None)  # nothing written: just close
        else:
            self.dismiss({**fields, "draft": True})

    def action_send(self) -> None:
        self.dismiss(self._fields())


class NewEventScreen(ModalScreen[dict | None]):
    """New calendar event. Dismisses with a dict of field values, or None."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    DEFAULT_CSS = """
    NewEventScreen {
        align: center middle;
    }
    #event-box {
        width: 60%;
        height: auto;
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }
    #event-box Input {
        margin-bottom: 1;
    }
    """

    def __init__(
        self,
        default_start: datetime | None = None,
        default_end: datetime | None = None,
        default_location: str = "",
        title: str = "New event",
    ) -> None:
        super().__init__()
        start = default_start or datetime.now()
        end = default_end or start + timedelta(hours=1)
        self._start_default = start.strftime("%Y-%m-%d %H:%M")
        self._end_default = end.strftime("%Y-%m-%d %H:%M")
        self._location_default = default_location
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="event-box"):
            yield Label(f"{self._title}  (Ctrl+S to save, Esc to cancel)", markup=False)
            yield Input(placeholder="Subject", id="event-subject")
            yield Input(value=self._start_default, placeholder="Start (YYYY-MM-DD HH:MM)", id="event-start")
            yield Input(value=self._end_default, placeholder="End (YYYY-MM-DD HH:MM)", id="event-end")
            yield Input(value=self._location_default, placeholder="Location", id="event-location")

    def on_mount(self) -> None:
        self.query_one("#event-subject", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        fmt = "%Y-%m-%d %H:%M"
        try:
            start = datetime.strptime(self.query_one("#event-start", Input).value, fmt)
            end = datetime.strptime(self.query_one("#event-end", Input).value, fmt)
        except ValueError:
            self.app.bell()
            return
        self.dismiss(
            {
                "subject": self.query_one("#event-subject", Input).value or "(no subject)",
                "start": start,
                "end": end,
                "location": self.query_one("#event-location", Input).value,
            }
        )


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
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
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
    """

    def __init__(self, label: str = "Note (Ctrl+S to save, Esc to cancel)", value: str = "") -> None:
        super().__init__()
        self._label = label
        self._value = value  # an existing note, to edit

    def compose(self) -> ComposeResult:
        with Vertical(id="note-box"):
            yield Label(self._label, markup=False)
            yield Input(value=self._value, placeholder="Note", id="note-text")

    def on_mount(self) -> None:
        self.query_one("#note-text", Input).focus()

    def action_cancel(self) -> None:
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


class AttachmentListScreen(ModalScreen[str | None]):
    """Lists a message's attachments; Enter/l saves + opens the selected
    one, Esc cancels. Dismisses with the chosen attachment id, or None.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("l", "select_cursor", "Open", show=False),
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
    """

    def __init__(self, subject: str, attachments: list) -> None:
        super().__init__()
        self._subject = subject
        self._attachments = attachments

    def compose(self) -> ComposeResult:
        from textual.widgets import ListItem, ListView

        with Vertical(id="attachment-box"):
            yield Label(f"Attachments — {self._subject}  (Enter/l to save + open, Esc to cancel)")
            with ListView(id="attachment-items"):
                for att in self._attachments:
                    note = "" if att.is_file else "  [dim](embedded item, not downloadable)[/dim]"
                    yield ListItem(Label(f"{att.name}  ({_human_size(att.size)}){note}"), name=att.id)

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

    def action_cursor_up(self) -> None:
        lv = self._list()
        lv.index = 0 if lv.index is None else max(lv.index - 1, 0)

    def action_select_cursor(self) -> None:
        self._select_current()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event) -> None:
        self._select_current()

    def _select_current(self) -> None:
        lv = self._list()
        if lv.index is None:
            return
        att = self._attachments[lv.index]
        if not att.is_file:
            self.app.bell()
            return
        self.dismiss(att.id)


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
