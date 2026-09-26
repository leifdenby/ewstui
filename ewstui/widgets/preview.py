from __future__ import annotations

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.widgets import Static

from ..ews_client import EventSummary, MessageDetail
from ..invites import render_invite_card
from .message_header import render_header


class PreviewPane(VerticalScroll):
    """Right pane: full message or event detail. Read-only, scrollable."""

    BINDINGS = [
        Binding("h", "focus_messages", "Focus list", show=False),
        Binding("j", "scroll_down", "Scroll down", show=False),
        Binding("k", "scroll_up", "Scroll up", show=False),
        Binding("g", "scroll_home", "Top", show=False),
        Binding("G", "scroll_end", "Bottom", show=False),
        Binding("ctrl+d", "half_page_down", "Half page down", show=False),
        Binding("ctrl+u", "half_page_up", "Half page up", show=False),
        Binding("ctrl+f", "page_down", "Page down", show=False),
        Binding("ctrl+b", "page_up", "Page up", show=False),
        Binding("space", "page_down", "Page down", show=False),
    ]

    DEFAULT_CSS = """
    PreviewPane #preview-body {
        /* "+N more" in the header (click to show all recipients) */
        link-color: $accent;
        link-style: bold;
        link-color-hover: $accent;
        link-background-hover: $panel;
        link-style-hover: bold underline;
    }
    """

    class FocusMessagesRequested(Message):
        """h: move keyboard focus back to the message list."""

    def action_focus_messages(self) -> None:
        self.post_message(self.FocusMessagesRequested())

    def action_half_page_down(self) -> None:
        self.scroll_relative(y=self.scrollable_content_region.height // 2, animate=False)

    def action_half_page_up(self) -> None:
        self.scroll_relative(y=-(self.scrollable_content_region.height // 2), animate=False)

    def compose(self):
        yield Static(id="preview-body")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._message: MessageDetail | None = None  # the email shown, if any
        self.recipients_expanded = False
        # For an invite: your calendar on the meeting's day, once looked up.
        self._invite_events: list[EventSummary] | None = None

    def show_message(self, msg: MessageDetail, invite_events: list[EventSummary] | None = None) -> None:
        self._message = msg
        self._invite_events = invite_events
        self.recipients_expanded = False  # every email starts with long To/Cc lists folded
        self._render_message()
        self.scroll_home(animate=False)

    def set_invite_events(self, message_id: str, events: list[EventSummary]) -> None:
        """The calendar lookup for an invite is back: fill in its clashes
        and day strip (unless you've moved on to another email)."""
        if self._message is not None and self._message.id == message_id:
            self._invite_events = events
            self._render_message()

    def toggle_recipients(self) -> None:
        """e: unfold / fold the To and Cc lists of the email shown."""
        if self._message is None:
            return
        self.recipients_expanded = not self.recipients_expanded
        self._render_message()

    def _render_message(self) -> None:
        msg = self._message
        width = self.scrollable_content_region.width or 80  # 0 before the first layout
        colors = self.app.theme_variables
        header = render_header(msg, width, self.recipients_expanded, colors)
        if getattr(msg, "meeting", None) is not None:
            header += render_invite_card(msg.meeting, self._invite_events, width, colors)
        # The body is plain text: brackets in an email are never markup.
        self.query_one("#preview-body", Static).update(header + Content(msg.body_text or "(empty message)"))

    def on_mount(self) -> None:
        self.watch(self.app, "theme", self._rerender, init=False)  # colours come from the theme

    def on_resize(self) -> None:
        self._rerender()  # the folded To/Cc line and the rule follow the width

    def watch_show_vertical_scrollbar(self, shown: bool) -> None:
        # A scrollbar coming or going changes the width without a resize
        # event; redraw once the layout has it.
        self.call_after_refresh(self._rerender)

    def _rerender(self, *_) -> None:
        if self._message is not None:
            self._render_message()

    def show_event(self, ev: EventSummary) -> None:
        self._message = None
        body = self.query_one("#preview-body", Static)
        when = (
            "All day" if ev.is_all_day
            else f"{ev.start.strftime('%Y-%m-%d %H:%M')} \u2013 {ev.end.strftime('%H:%M')}"
        )
        text = Content.from_markup("[b]$subject[/b]\n", subject=ev.subject) + Content(
            f"When: {when}\n"
            f"Organizer: {ev.organizer}\n"
            + (f"Location: {ev.location}\n" if ev.location else "")
        )
        body.update(text)
        self.scroll_home(animate=False)

    def show_loading(self) -> None:
        self._message = None
        self.query_one("#preview-body", Static).update("[dim]Loading…[/dim]")
        self.scroll_home(animate=False)

    def clear(self) -> None:
        self._message = None
        self.query_one("#preview-body", Static).update("")
