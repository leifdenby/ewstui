from __future__ import annotations

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Static

from ..ews_client import EventSummary, MessageDetail


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

    def show_message(self, msg: MessageDetail) -> None:
        body = self.query_one("#preview-body", Static)
        when = msg.received.strftime("%Y-%m-%d %H:%M") if msg.received else "(no date)"
        header = (
            f"[b]{msg.subject}[/b]\n"
            f"From: {msg.sender}\n"
            f"To: {', '.join(msg.to) or '(none)'}\n"
            + (f"Cc: {', '.join(msg.cc)}\n" if msg.cc else "")
            + f"Date: {when}\n"
            + ("[dim]has attachments[/dim]\n" if msg.has_attachments else "")
            + "\n" + "-" * 40 + "\n\n"
        )
        body.update(header + (msg.body_text or "(empty message)"))
        self.scroll_home(animate=False)

    def show_event(self, ev: EventSummary) -> None:
        body = self.query_one("#preview-body", Static)
        when = (
            "All day" if ev.is_all_day
            else f"{ev.start.strftime('%Y-%m-%d %H:%M')} \u2013 {ev.end.strftime('%H:%M')}"
        )
        text = (
            f"[b]{ev.subject}[/b]\n"
            f"When: {when}\n"
            f"Organizer: {ev.organizer}\n"
            + (f"Location: {ev.location}\n" if ev.location else "")
        )
        body.update(text)
        self.scroll_home(animate=False)

    def clear(self) -> None:
        self.query_one("#preview-body", Static).update("")
