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
