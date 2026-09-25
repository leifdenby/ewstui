from __future__ import annotations

from datetime import datetime, timedelta

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static, TextArea
from textual.worker import get_current_worker

from . import keymap


class ComposeScreen(ModalScreen[dict | None]):
    """New message / reply. Dismisses with a dict of field values, or
    None if the user cancelled.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "send", "Send"),
        # Cmd+Enter only arrives in terminals that report the Cmd key
        # (kitty keyboard protocol: Ghostty, kitty, WezTerm, iTerm2 with
        # "Report keys using CSI u"); Terminal.app swallows it.
        Binding("super+enter", "send", "Send", show=False),
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
    #compose-body {
        height: 1fr;
        border: round $primary;
        margin-top: 1;
    }
    """

    def __init__(self, to: str = "", subject: str = "", body: str = "") -> None:
        super().__init__()
        self._to = to
        self._subject = subject
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="compose-box"):
            yield Label("Compose  (Ctrl+S or Cmd+Enter to send, Esc to cancel)")
            yield Input(value=self._to, placeholder="To", id="compose-to")
            yield Input(value=self._subject, placeholder="Subject", id="compose-subject")
            yield TextArea(self._body, id="compose-body")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_send(self) -> None:
        to = self.query_one("#compose-to", Input).value
        subject = self.query_one("#compose-subject", Input).value
        body = self.query_one("#compose-body", TextArea).text
        self.dismiss({"to": to, "subject": subject, "body": body})


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


class _RoomList(ListView):
    # j/k live on the list (not the screen) so they don't swallow
    # typing in the time/duration inputs.
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]


def _next_half_hour(now: datetime) -> datetime:
    base = now.replace(second=0, microsecond=0)
    return base + timedelta(minutes=30 - base.minute % 30)


class FindRoomScreen(ModalScreen[dict | None]):
    """Check which configured rooms are free for a time slot. Enter on a
    free room dismisses with {"room", "start", "end"}; Esc with None.

    `check(rooms, start, end) -> list[RoomAvailability]` does the EWS
    call; it runs in a thread so a slow server doesn't freeze the UI.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "check", "Check"),
    ]

    DEFAULT_CSS = """
    FindRoomScreen {
        align: center middle;
    }
    #room-box {
        width: 70%;
        height: auto;
        max-height: 85%;
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }
    #room-box Input {
        margin-bottom: 1;
    }
    #room-results {
        height: auto;
        max-height: 20;
    }
    """

    def __init__(self, rooms: list, check, now: datetime | None = None) -> None:
        super().__init__()
        self._rooms = rooms
        self._check = check
        self._start_default = _next_half_hour(now or datetime.now()).strftime("%Y-%m-%d %H:%M")
        self._results: list = []
        self._slot: tuple[datetime, datetime] | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="room-box"):
            yield Label("Find a free room  (Enter to check, then Enter on a free room to book; Esc to cancel)")
            yield Input(value=self._start_default, placeholder="Start (YYYY-MM-DD HH:MM)", id="room-start")
            yield Input(value="60", placeholder="Duration (minutes)", id="room-duration")
            yield Label("", id="room-status")
            yield _RoomList(id="room-results")

    def on_mount(self) -> None:
        self.query_one("#room-start", Input).focus()

    def _parse_slot(self) -> tuple[datetime, datetime] | None:
        try:
            start = datetime.strptime(self.query_one("#room-start", Input).value.strip(), "%Y-%m-%d %H:%M")
            minutes = int(self.query_one("#room-duration", Input).value.strip())
        except ValueError:
            return None
        if minutes <= 0:
            return None
        return start, start + timedelta(minutes=minutes)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_check()

    def action_check(self) -> None:
        slot = self._parse_slot()
        if slot is None:
            self._set_status("Start must be YYYY-MM-DD HH:MM and duration a positive number of minutes")
            self.app.bell()
            return
        self._slot = slot
        self._set_status(f"Checking {len(self._rooms)} room{'s' if len(self._rooms) != 1 else ''} …")
        self.query_one("#room-results", ListView).clear()
        self._run_check(slot)

    @work(thread=True, exclusive=True, group="room-check")
    def _run_check(self, slot: tuple[datetime, datetime]) -> None:
        worker = get_current_worker()
        try:
            results = self._check(self._rooms, *slot)
        except Exception as e:  # noqa: BLE001 - show any EWS error in the screen
            if not worker.is_cancelled:
                self.app.call_from_thread(self._set_status, f"Check failed: {e}")
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._show_results, slot, results)

    def _set_status(self, text: str) -> None:
        self.query_one("#room-status", Label).update(text)

    def _show_results(self, slot: tuple[datetime, datetime], results: list) -> None:
        if slot != self._slot:
            return  # a newer check was started meanwhile
        self._results = results
        start, end = slot
        free = sum(r.free for r in results)
        self._set_status(f"{start:%a %d %b %H:%M}–{end:%H:%M}: {free} of {len(results)} free")
        results_list = self.query_one("#room-results", ListView)
        for r in results:
            results_list.append(ListItem(Label(_room_line(r), markup=False)))
        if results:
            results_list.index = next((i for i, r in enumerate(results) if r.free), 0)
            results_list.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = self.query_one("#room-results", ListView).index
        if index is None or not self._results or self._slot is None:
            return
        result = self._results[index]
        if not result.free:
            self.app.bell()
            return
        start, end = self._slot
        self.dismiss({"room": result.room, "start": start, "end": end})

    def action_cancel(self) -> None:
        self.dismiss(None)


def _room_line(result) -> str:
    if result.error:
        return f"?  {result.room.name}  — couldn't check: {result.error}"
    if result.free:
        return f"✓  {result.room.name}  — free"
    first_start, first_end, _ = result.busy[0]
    more = f" (+{len(result.busy) - 1} more)" if len(result.busy) > 1 else ""
    return f"✗  {result.room.name}  — busy {first_start:%H:%M}–{first_end:%H:%M}{more}"


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

    def __init__(self, label: str = "Note (Ctrl+S to save, Esc to cancel)") -> None:
        super().__init__()
        self._label = label

    def compose(self) -> ComposeResult:
        with Vertical(id="note-box"):
            yield Label(self._label)
            yield Input(placeholder="Note", id="note-text")

    def on_mount(self) -> None:
        self.query_one("#note-text", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        self.dismiss(self.query_one("#note-text", Input).value.strip())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_save()


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


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss_help", "Close"), Binding("question_mark", "dismiss_help", "Close")]

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-box {
        width: auto;
        height: auto;
        border: round $accent;
        padding: 1 3;
        background: $surface;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static(keymap.HELP_TEXT)

    def action_dismiss_help(self) -> None:
        self.dismiss(None)

    def on_key(self, event) -> None:
        # Any key closes the help overlay, not just Esc/?.
        self.dismiss(None)
