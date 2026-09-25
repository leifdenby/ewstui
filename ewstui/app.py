from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.worker import get_current_worker
from textual.widgets.data_table import RowDoesNotExist
from textual.widgets import Footer, Header, Tab, Tabs

from .config import Config
from .ews_client import CalendarClient, MailClient, MovedMessage
from .opener import OpenError, open_with_default_app
from .priority_store import PriorityStore
from .screens import (
    AddNoteScreen,
    AttachmentListScreen,
    ComposeScreen,
    FindRoomScreen,
    HelpScreen,
    NewEventScreen,
)
from .widgets.calendar_view import CalendarView
from .widgets.folder_list import FolderList
from .widgets.message_table import MessageTable
from .widgets.preview import PreviewPane
from .widgets.priority_view import PriorityView

log = logging.getLogger(__name__)


def _split_addresses(field: str) -> list[str]:
    """'a@x, b@y; c@z' -> ['a@x', 'b@y', 'c@z'] (the To field is free text)."""
    return [a.strip() for a in field.replace(";", ",").split(",") if a.strip()]


@dataclass
class UndoEntry:
    """A reversible mail action: `moved` is where the message is now,
    `original_folder_id` is where `u` puts it back.
    """
    verb: str  # past tense, for notifications: "deleted", "archived"
    subject: str
    original_folder_id: str
    moved: MovedMessage


class EwstuiApp(App):
    """Terminal Exchange mail + calendar client, vim-ish bindings."""

    TITLE = "ewstui"

    CSS = """
    #modes {
        background: $panel;
    }
    #modes Tab {
        padding: 0 2;
    }
    #modes Tab.-active {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    #main {
        height: 1fr;
    }
    #folders {
        width: 22%;
        border-right: solid $accent;
    }
    #messages {
        width: 40%;
        border-right: solid $accent;
    }
    #preview {
        width: 1fr;
        padding: 0 1;
    }
    #calendar {
        height: 1fr;
    }
    #priority {
        height: 1fr;
    }
    .hidden {
        display: none;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "show_help", "Help"),
        Binding("1", "show_mail", "Mail"),
        Binding("2", "show_priority", "Priority"),
        Binding("3", "show_calendar", "Calendar"),
        Binding("w", "compose_new", "Compose"),
        Binding("u", "undo", "Undo"),
        Binding("ctrl+l", "refresh", "Refresh"),
        Binding("tab", "focus_next", "Next pane", show=False),
        Binding("shift+tab", "focus_previous", "Prev pane", show=False),
    ]

    def __init__(self, mail_client: MailClient, calendar_client: CalendarClient, config: Config):
        super().__init__()
        self.mail_client = mail_client
        self.calendar_client = calendar_client
        self.config = config
        self.current_folder_id: str | None = None
        self.current_message_id: str | None = None
        self.calendar_range_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.calendar_range_days = 7
        self.priority_store = PriorityStore(config.priority_file)
        # Session-only; most recent last. Not persisted across restarts.
        self.undo_stack: list[UndoEntry] = []
        # What's on screen, so a refresh can tell what changed.
        self._folder_unread: dict[str, int] = {}
        self._message_snapshot: list[tuple[str, bool]] = []
        self._last_refresh_failed = False

    # -- layout -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        modes = Tabs(
            Tab("1 Mail", id="mode-mail"),
            Tab("2 Priority", id="mode-priority"),
            Tab("3 Calendar", id="mode-calendar"),
            id="modes",
        )
        # Mode bar is click/hotkey driven only; keep it out of the
        # Tab/Shift+Tab pane cycle.
        modes.can_focus = False
        yield modes
        with Horizontal(id="main"):
            yield FolderList(id="folders")
            yield MessageTable(id="messages")
            yield PreviewPane(id="preview")
        yield CalendarView(id="calendar", classes="hidden")
        yield PriorityView(id="priority", classes="hidden")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.config.email or "(demo)"
        self.load_folders()
        if self.config.refresh_interval > 0:
            self.set_interval(self.config.refresh_interval * 60, self._background_refresh)

    # -- data loading ---------------------------------------------------

    def load_folders(self) -> None:
        try:
            folders = self.mail_client.list_folders()
        except Exception as e:  # noqa: BLE001 - surface any EWS error to the user
            self.notify(f"Failed to load folders: {e}", severity="error", timeout=10)
            return
        if not folders:
            return
        folder_list = self.query_one("#folders", FolderList)
        folder_list.set_folders(folders)
        self._folder_unread = {f.id: f.unread_count for f in folders}
        # Open the Inbox, not the first row: live mailboxes list "Top of
        # Information Store" first. Fall back to the first folder if the
        # Inbox can't be resolved or isn't in the list.
        try:
            default_id = self.mail_client.default_folder_id()
        except Exception:  # noqa: BLE001
            log.warning("could not resolve the Inbox folder", exc_info=True)
            default_id = None
        if default_id not in {f.id for f in folders}:
            default_id = folders[0].id
        folder_list.highlight_folder(default_id)
        self.select_folder(default_id)

    def select_folder(self, folder_id: str) -> None:
        self.current_folder_id = folder_id
        try:
            messages = self.mail_client.list_messages(folder_id, limit=self.config.page_size)
        except Exception as e:  # noqa: BLE001
            self.notify(f"Failed to load messages: {e}", severity="error", timeout=10)
            return
        self.query_one("#messages", MessageTable).set_messages(messages)
        self._message_snapshot = [(m.id, m.is_read) for m in messages]
        self.query_one("#preview", PreviewPane).clear()
        self.current_message_id = None

    # -- refresh (Ctrl+l, and every --refresh-interval minutes) ------------

    def action_refresh(self) -> None:
        mode = self.query_one("#modes", Tabs).active
        if mode == "mode-calendar":
            self.load_calendar_range()
        elif mode == "mode-priority":
            self.load_priority_list()
        else:
            self._refresh_mail(manual=True)

    # Not `_auto_refresh`: Textual's DOMNode already uses that attribute.
    def _background_refresh(self) -> None:
        self._refresh_mail(manual=False)

    @work(thread=True, exclusive=True, group="refresh")
    def _refresh_mail(self, manual: bool) -> None:
        """Fetch in a thread so a slow server never freezes the UI; the
        result is applied on the UI thread by _apply_refresh.
        """
        worker = get_current_worker()
        folder_id = self.current_folder_id
        try:
            folders = self.mail_client.list_folders()
            messages = self.mail_client.list_messages(folder_id, limit=self.config.page_size) if folder_id else None
        except Exception as e:  # noqa: BLE001 - surface any EWS error to the user
            log.warning("refresh failed", exc_info=True)
            if not worker.is_cancelled:
                self.call_from_thread(self._refresh_failed, e, manual)
            return
        if not worker.is_cancelled:
            self.call_from_thread(self._apply_refresh, folder_id, folders, messages, manual)

    def _refresh_failed(self, error: Exception, manual: bool) -> None:
        # Auto-refresh keeps failing while offline; say so once, not every tick.
        if manual or not self._last_refresh_failed:
            self.notify(f"Refresh failed: {error}", severity="error", timeout=10)
        self._last_refresh_failed = True

    def _apply_refresh(self, folder_id, folders, messages, manual: bool) -> None:
        self._last_refresh_failed = False
        if self.screen is not self.screen_stack[0]:
            return  # a modal (compose, help, ...) is open; the next refresh catches up

        gained = [
            (f.name, f.unread_count - self._folder_unread[f.id])
            for f in folders
            if f.id in self._folder_unread and f.unread_count > self._folder_unread[f.id]
        ]
        self._folder_unread = {f.id: f.unread_count for f in folders}
        if gained:
            self.notify("New mail: " + ", ".join(f"{name} (+{n})" for name, n in gained))
        elif manual:
            self.notify("No new mail")

        folder_list = self.query_one("#folders", FolderList)
        highlighted = folder_list.highlighted_folder_id()
        folder_list.set_folders(folders)
        if highlighted:
            folder_list.highlight_folder(highlighted)

        if messages is None or folder_id != self.current_folder_id:
            return  # user switched folders while we were fetching
        snapshot = [(m.id, m.is_read) for m in messages]
        if snapshot == self._message_snapshot:
            return
        table = self.query_one("#messages", MessageTable)
        # Nothing selected yet (e.g. folder was empty): a normal fill is fine.
        table.set_messages(messages, keep_cursor_on=table._current_message_id())
        self._message_snapshot = snapshot

    def open_message(self, message_id: str) -> None:
        if not self.current_folder_id:
            return
        self.current_message_id = message_id
        try:
            detail = self.mail_client.get_message(self.current_folder_id, message_id)
        except Exception as e:  # noqa: BLE001
            self.notify(f"Failed to load message: {e}", severity="error", timeout=10)
            return
        self.query_one("#preview", PreviewPane).show_message(detail)

    def load_priority_list(self) -> None:
        self.query_one("#priority", PriorityView).set_entries(self.priority_store.list_entries())

    def load_calendar_range(self) -> None:
        start = self.calendar_range_start
        end = start + timedelta(days=self.calendar_range_days)
        try:
            events = self.calendar_client.list_events(start, end)
        except Exception as e:  # noqa: BLE001
            self.notify(f"Failed to load calendar: {e}", severity="error", timeout=10)
            return
        self.query_one("#calendar", CalendarView).set_events(events)

    # -- mail pane events -------------------------------------------------

    def on_folder_list_folder_selected(self, event: FolderList.FolderSelected) -> None:
        self.select_folder(event.folder.id)
        self.query_one("#messages", MessageTable).focus()

    def on_message_table_message_opened(self, event: MessageTable.MessageOpened) -> None:
        self.open_message(event.message_id)
        if event.explicit:
            self.query_one("#preview", PreviewPane).focus()

    def on_message_table_focus_folders_requested(self, event: MessageTable.FocusFoldersRequested) -> None:
        self.query_one("#folders", FolderList).focus()

    def on_preview_pane_focus_messages_requested(self, event: PreviewPane.FocusMessagesRequested) -> None:
        self.query_one("#messages", MessageTable).focus()

    def on_message_table_reply_requested(self, event: MessageTable.ReplyRequested) -> None:
        if not self.current_folder_id:
            return
        folder_id = self.current_folder_id
        detail = self.mail_client.get_message(folder_id, event.message_id)
        quoted = "\n".join(f"> {line}" for line in detail.body_text.splitlines())
        prefill_body = f"\n\n-- original message --\n{quoted}"

        def _send(result: dict) -> None:
            self.mail_client.reply(
                folder_id,
                event.message_id,
                subject=result["subject"],
                body=result["body"],
                to=_split_addresses(result["to"]),
                reply_all=event.reply_all,
            )

        self._compose_and_send(
            ComposeScreen(to=detail.sender, subject=f"Re: {detail.subject}", body=prefill_body),
            _send,
            sent_message="Reply sent",
        )

    def _compose_and_send(self, screen: ComposeScreen, send, sent_message: str) -> None:
        """Show `screen`; on Ctrl+S call `send(result)`. If sending fails,
        say why and reopen the compose screen with the draft intact, so
        a server error never throws away what the user wrote.
        """

        def _on_result(result: dict | None) -> None:
            if result is None:
                return
            try:
                send(result)
            except Exception as e:  # noqa: BLE001 - surface any EWS error, keep the draft
                log.exception("send failed")
                self.notify(f"Send failed: {e} — draft kept", severity="error", timeout=10)
                self._compose_and_send(
                    ComposeScreen(to=result["to"], subject=result["subject"], body=result["body"]),
                    send,
                    sent_message,
                )
                return
            self.notify(sent_message)

        self.push_screen(screen, _on_result)

    def on_message_table_delete_requested(self, event: MessageTable.DeleteRequested) -> None:
        if not self.current_folder_id:
            return
        folder_id = self.current_folder_id
        try:
            subject = self.mail_client.get_message(folder_id, event.message_id).subject
            moved = self.mail_client.delete_message(folder_id, event.message_id)
        except Exception as e:  # noqa: BLE001
            self.notify(f"Delete failed: {e}", severity="error", timeout=10)
            return
        if moved is None:
            self.notify("Message permanently deleted (can't be undone)", severity="warning")
        else:
            self.undo_stack.append(UndoEntry("deleted", subject, folder_id, moved))
            self.notify("Moved to Deleted Items — press u to undo")
        self.select_folder(folder_id)  # refresh list

    def on_message_table_toggle_read_requested(self, event: MessageTable.ToggleReadRequested) -> None:
        if not self.current_folder_id:
            return
        detail = self.mail_client.get_message(self.current_folder_id, event.message_id)
        self.mail_client.mark_read(self.current_folder_id, event.message_id, read=not detail.is_read)
        self.select_folder(self.current_folder_id)  # refresh list + unread counts

    def on_message_table_add_to_priority_requested(self, event: MessageTable.AddToPriorityRequested) -> None:
        if not self.current_folder_id:
            return
        detail = self.mail_client.get_message(self.current_folder_id, event.message_id)
        self.priority_store.add_email(
            message_id=event.message_id,
            folder_id=self.current_folder_id,
            subject=detail.subject,
            sender=detail.sender,
            priority=None,
        )
        self.notify("Added to priority list (no priority set)")

    def on_message_table_add_to_priority_with_note_requested(
        self, event: MessageTable.AddToPriorityWithNoteRequested
    ) -> None:
        if not self.current_folder_id:
            return
        detail = self.mail_client.get_message(self.current_folder_id, event.message_id)

        def _on_result(note: str | None) -> None:
            if note is None:
                return
            self.priority_store.add_email(
                message_id=event.message_id,
                folder_id=self.current_folder_id,
                subject=detail.subject,
                sender=detail.sender,
                priority=None,
                note=note,
            )
            self.notify("Added to priority list with note")

        self.push_screen(AddNoteScreen(label=f"Note for: {detail.subject}"), _on_result)

    def on_message_table_archive_requested(self, event: MessageTable.ArchiveRequested) -> None:
        if not self.current_folder_id:
            return
        folder_id = self.current_folder_id
        try:
            subject = self.mail_client.get_message(folder_id, event.message_id).subject
            moved = self.mail_client.archive_message(folder_id, event.message_id)
        except Exception as e:  # noqa: BLE001 - e.g. no Archive folder found
            self.notify(f"Archive failed: {e}", severity="error", timeout=10)
            return
        self.undo_stack.append(UndoEntry("archived", subject, folder_id, moved))
        self.notify("Message archived — press u to undo")
        self.select_folder(folder_id)  # refresh list

    def on_message_table_view_attachments_requested(self, event: MessageTable.ViewAttachmentsRequested) -> None:
        if not self.current_folder_id:
            return
        try:
            attachments = self.mail_client.list_attachments(self.current_folder_id, event.message_id)
        except Exception as e:  # noqa: BLE001
            self.notify(f"Failed to load attachments: {e}", severity="error", timeout=10)
            return
        if not attachments:
            self.notify("No attachments on this message")
            return
        detail = self.mail_client.get_message(self.current_folder_id, event.message_id)

        def _on_result(attachment_id: str | None) -> None:
            if attachment_id is None:
                return
            try:
                path = self.mail_client.save_attachment(
                    self.current_folder_id, event.message_id, attachment_id, self.config.attachment_dir
                )
            except Exception as e:  # noqa: BLE001
                self.notify(f"Failed to save attachment: {e}", severity="error", timeout=10)
                return
            self._open_with_system_default(path)

        self.push_screen(AttachmentListScreen(detail.subject, attachments), _on_result)

    def _open_with_system_default(self, path) -> None:
        try:
            open_with_default_app(path)
        except OpenError as e:
            self.notify(f"Saved to {path}, but couldn't open it automatically: {e}", severity="warning", timeout=10)
            return
        self.notify(f"Saved to {path} and opening it")

    # -- priority pane events ---------------------------------------------

    def on_priority_view_priority_changed(self, event: PriorityView.PriorityChanged) -> None:
        self.priority_store.set_priority_many(event.entry_keys, event.priority)
        n = len(event.entry_keys)
        self.notify(f"Set {n} item{'s' if n != 1 else ''} to ({event.priority})")
        self.load_priority_list()

    def on_priority_view_complete_toggled(self, event: PriorityView.CompleteToggled) -> None:
        self.priority_store.toggle_complete(event.entry_key)
        self.load_priority_list()

    def on_priority_view_remove_requested(self, event: PriorityView.RemoveRequested) -> None:
        self.priority_store.remove(event.entry_key)
        self.notify("Removed from priority list")
        self.load_priority_list()

    def on_priority_view_entry_opened(self, event: PriorityView.EntryOpened) -> None:
        entry = next((e for e in self.priority_store.list_entries(include_completed=True) if e.key == event.entry_key), None)
        if entry is None or not entry.message_id or not entry.folder_id:
            return
        self.action_show_mail()
        self.select_folder(entry.folder_id)
        self.open_message(entry.message_id)

    # -- calendar pane events ---------------------------------------------

    def on_calendar_view_event_opened(self, event: CalendarView.EventOpened) -> None:
        events = self.calendar_client.list_events(
            self.calendar_range_start, self.calendar_range_start + timedelta(days=self.calendar_range_days)
        )
        match = next((e for e in events if e.id == event.event_id), None)
        if match:
            self.query_one("#preview", PreviewPane).show_event(match)

    def on_calendar_view_new_event_requested(self, event: CalendarView.NewEventRequested) -> None:
        def _on_result(result: dict | None) -> None:
            if result is None:
                return
            self.calendar_client.create_event(
                subject=result["subject"], start=result["start"], end=result["end"], location=result["location"]
            )
            self.notify("Event created")
            self.load_calendar_range()

        self.push_screen(NewEventScreen(default_start=self.calendar_range_start), _on_result)

    def on_calendar_view_find_room_requested(self, event: CalendarView.FindRoomRequested) -> None:
        if not self.config.rooms:
            where = f"[accounts.{self.config.account or 'NAME'}.rooms] in {self.config.config_path or 'the config file'}"
            self.notify(f"No meeting rooms configured — add them under {where}", severity="warning", timeout=10)
            return

        def _book(result: dict | None) -> None:
            if result is None:
                return
            room = result["room"]

            def _on_event(details: dict | None) -> None:
                if details is None:
                    return
                try:
                    self.calendar_client.create_event(
                        subject=details["subject"],
                        start=details["start"],
                        end=details["end"],
                        location=details["location"],
                        resources=[room.email],
                    )
                except Exception as e:  # noqa: BLE001 - surface any EWS error
                    self.notify(f"Booking failed: {e}", severity="error", timeout=10)
                    return
                self.notify(f"Invite sent to {room.name} — it will accept or decline shortly")
                self.load_calendar_range()

            self.push_screen(
                NewEventScreen(
                    default_start=result["start"],
                    default_end=result["end"],
                    default_location=room.name,
                    title=f"New event in {room.name}",
                ),
                _on_event,
            )

        self.push_screen(FindRoomScreen(self.config.rooms, self.calendar_client.room_availability), _book)

    def on_calendar_view_delete_event_requested(self, event: CalendarView.DeleteEventRequested) -> None:
        self.calendar_client.delete_event(event.event_id)
        self.notify("Event deleted")
        self.load_calendar_range()

    def on_calendar_view_range_shift_requested(self, event: CalendarView.RangeShiftRequested) -> None:
        self.calendar_range_start += timedelta(days=event.direction * self.calendar_range_days)
        self.load_calendar_range()

    # -- global actions -----------------------------------------------

    # Hotkeys just move the mode bar; the actual view switch happens in
    # on_tabs_tab_activated, so clicking a tab and pressing 1/2/3 share
    # one code path and the highlighted tab can't drift from the view.
    def action_show_mail(self) -> None:
        self.query_one("#modes", Tabs).active = "mode-mail"

    def action_show_priority(self) -> None:
        self.query_one("#modes", Tabs).active = "mode-priority"

    def action_show_calendar(self) -> None:
        self.query_one("#modes", Tabs).active = "mode-calendar"

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        mode = event.tab.id.removeprefix("mode-")
        views = {"mail": "#main", "priority": "#priority", "calendar": "#calendar"}
        for name, selector in views.items():
            self.query_one(selector).set_class(name != mode, "hidden")
        if mode == "mail":
            self.query_one("#messages", MessageTable).focus()
        elif mode == "priority":
            self.load_priority_list()
            self.query_one("#priority", PriorityView).focus()
        else:
            self.load_calendar_range()
            self.query_one("#calendar", CalendarView).focus()

    def action_undo(self) -> None:
        if not self.undo_stack:
            self.notify("Nothing to undo")
            return
        entry = self.undo_stack.pop()
        try:
            restored = self.mail_client.move_message(
                entry.moved.folder_id, entry.moved.message_id, entry.original_folder_id
            )
        except Exception as e:  # noqa: BLE001 - e.g. already purged from Deleted Items
            self.notify(f"Undo failed: {e}", severity="error", timeout=10)
            return
        self.notify(f"Restored {entry.verb} message: {entry.subject}")
        if self.current_folder_id in (entry.original_folder_id, entry.moved.folder_id):
            self.select_folder(self.current_folder_id)
            if self.current_folder_id == entry.original_folder_id:
                table = self.query_one("#messages", MessageTable)
                try:
                    table.move_cursor(row=table.get_row_index(restored.message_id))
                except RowDoesNotExist:
                    pass  # older than the loaded page; restored but not on screen

    def action_compose_new(self) -> None:
        def _send(result: dict) -> None:
            self.mail_client.send_mail(
                to=_split_addresses(result["to"]), subject=result["subject"], body=result["body"]
            )

        self._compose_and_send(ComposeScreen(), _send, sent_message="Message sent")

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())
