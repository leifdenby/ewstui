from __future__ import annotations

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.widgets import Static

from ..ews_client import EventSummary, MessageDetail
from ..html_render import render_html
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
        # The email actions, on the email being read (as in the message list;
        # Space pages here, so toggling read stays a list key).
        Binding("r", "email('reply')", "Reply", show=False),
        Binding("R", "email('reply_all')", "Reply all", show=False),
        Binding("f", "email('forward')", "Forward", show=False),
        Binding("d", "email('delete')", "Delete", show=False),
        Binding("A", "email('archive')", "Archive", show=False),
        Binding("m", "email('move')", "Move", show=False),
        Binding("P", "email('priority')", "Add to priority", show=False),
        Binding("p", "email('priority_note')", "Add to priority + note", show=False),
        Binding("v", "email('attachments')", "Attachments", show=False),
        Binding("C", "email('agent')", "Ask agent", show=False),
        Binding("H", "toggle_html", "Formatted / plain text", show=False),
        # Copying: drag over the text with the mouse (double-click: all of
        # it), then y (or Ctrl+C, as in other apps); Y copies the whole body.
        Binding("y,ctrl+c", "copy_selection", "Copy selected text", show=False),
        Binding("Y", "copy_body", "Copy the email's text", show=False),
    ]

    def action_email(self, action: str) -> None:
        """Post the same request the message list would, for the email shown."""
        from .message_table import MessageTable

        message_id = self.app.current_message_id
        if not message_id:
            return
        request = {
            "reply": lambda: MessageTable.ReplyRequested(message_id, reply_all=False),
            "reply_all": lambda: MessageTable.ReplyRequested(message_id, reply_all=True),
            "forward": lambda: MessageTable.ForwardRequested(message_id),
            "delete": lambda: MessageTable.DeleteRequested(message_id),
            "archive": lambda: MessageTable.ArchiveRequested(message_id),
            "move": lambda: MessageTable.MoveRequested(message_id),
            "priority": lambda: MessageTable.AddToPriorityRequested(message_id),
            "priority_note": lambda: MessageTable.AddToPriorityWithNoteRequested(message_id),
            "attachments": lambda: MessageTable.ViewAttachmentsRequested(message_id),
            "agent": lambda: MessageTable.AskAgentRequested(message_id),
        }[action]
        self.post_message(request())

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
        # H: show HTML emails as their plain-text version instead (sticks
        # across emails until H again).
        self.show_plain_text = False
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
        """(Re)draw the email shown: header, invite card if any, body."""
        msg = self._message
        width = self.scrollable_content_region.width or 80  # 0 before the first layout
        colors = self.app.theme_variables
        header = render_header(msg, width, self.recipients_expanded, colors)
        if getattr(msg, "meeting", None) is not None:
            header += render_invite_card(msg.meeting, self._invite_events, width, colors)
        self._set_body(header + self._render_body(msg, colors))

    def _set_body(self, content: Content | str) -> None:
        """Show `content` in the pane, dropping any mouse selection in it
        (it was made on the text that was there before).

        Parameters
        ----------
        content : Content or str
            What to show (a str is Textual markup).
        """
        body = self.query_one("#preview-body", Static)
        if body in self.screen.selections:
            self.screen.selections = {w: s for w, s in self.screen.selections.items() if w is not body}
        body.update(content)

    def _render_body(self, msg: MessageDetail, colors: dict[str, str]) -> Content:
        """Return the email's body: formatted from its HTML when it has one
        (and H hasn't switched to plain text), otherwise its plain text.

        Parameters
        ----------
        msg : MessageDetail
            The email shown.
        colors : dict[str, str]
            The theme's colour variables.

        Returns
        -------
        Content
            The body, ready to put under the header.
        """
        html = getattr(msg, "body_html", "")
        if html and not self.show_plain_text:
            body = render_html(html, background=colors.get("background", "#000000"),
                               link_color=colors.get("accent"), dim_color=colors.get("tux-dim"))
            if body.plain.strip():
                return body
        # Plain text: brackets in an email are never markup.
        return Content(msg.body_text or "(empty message)")

    def action_toggle_html(self) -> None:
        """H: switch between the formatted (HTML) and plain-text body."""
        self.show_plain_text = not self.show_plain_text
        if self._message is not None:
            self._render_message()
            self.notify("Plain text" if self.show_plain_text else "Formatted (HTML)", timeout=1.5)

    def action_copy_selection(self) -> None:
        """y / Ctrl+C: copy the text selected with the mouse to the clipboard
        (a bell and a hint when nothing is selected)."""
        text = self.screen.get_selected_text()
        if not text:
            self.app.bell()
            self.notify("Nothing selected: drag over the text with the mouse "
                        "(double-click: all of it), or Y for the whole email",
                        severity="warning", timeout=3)
            return
        self.copy_text_to_clipboard(text)

    def action_copy_body(self) -> None:
        """Y: copy the body of the email shown, as displayed (formatted or
        plain text, per H), to the clipboard."""
        if self._message is None:
            self.app.bell()
            self.notify("No email shown", severity="warning", timeout=2)
            return
        self.copy_text_to_clipboard(self._render_body(self._message, self.app.theme_variables).plain)

    def copy_text_to_clipboard(self, text: str) -> None:
        """Put `text` on the clipboard and say how much was copied.

        Parameters
        ----------
        text : str
            The text to copy.
        """
        self.app.copy_to_clipboard(text)
        self.notify(f"Copied {len(text)} character{'' if len(text) == 1 else 's'}", timeout=1.5)

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
        """Show a calendar event's summary (subject, when, organizer, where).

        Parameters
        ----------
        ev : EventSummary
            The event to show.
        """
        self._message = None
        when = (
            "All day" if ev.is_all_day
            else f"{ev.start.strftime('%Y-%m-%d %H:%M')} \u2013 {ev.end.strftime('%H:%M')}"
        )
        text = Content.from_markup("[b]$subject[/b]\n", subject=ev.subject) + Content(
            f"When: {when}\n"
            f"Organizer: {ev.organizer}\n"
            + (f"Location: {ev.location}\n" if ev.location else "")
        )
        self._set_body(text)
        self.scroll_home(animate=False)

    def show_loading(self) -> None:
        """Show "Loading…" while an email is fetched."""
        self._message = None
        self._set_body("[dim]Loading…[/dim]")
        self.scroll_home(animate=False)

    def clear(self) -> None:
        """Empty the pane (no email shown)."""
        self._message = None
        self._set_body("")
