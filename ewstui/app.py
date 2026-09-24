from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import datetime, timedelta

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header

from .config import Config
from .ews_client import CalendarClient, MailClient
from .priority_store import PriorityStore
from .screens import AddNoteScreen, AttachmentListScreen, ComposeScreen, HelpScreen, NewEventScreen
from .widgets.calendar_view import CalendarView
from .widgets.folder_list import FolderList
from .widgets.message_table import MessageTable
from .widgets.preview import PreviewPane
from .widgets.priority_view import PriorityView

log = logging.getLogger(__name__)


class EwstuiApp(App):
    """Terminal Exchange mail + calendar client, vim-ish bindings."""

    TITLE = "ewstui"

    CSS = """
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
        Binding("2", "show_calendar", "Calendar"),
        Binding("3", "show_priority", "Priority"),
        Binding("w", "compose_new", "Compose"),
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

    # -- layout -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
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

    # -- data loading ---------------------------------------------------

    def load_folders(self) -> None:
        try:
            folders = self.mail_client.list_folders()
        except Exception as e:  # noqa: BLE001 - surface any EWS error to the user
            self.notify(f"Failed to load folders: {e}", severity="error", timeout=10)
            return
        self.query_one("#folders", FolderList).set_folders(folders)
        if folders:
            self.select_folder(folders[0].id)

    def select_folder(self, folder_id: str) -> None:
        self.current_folder_id = folder_id
        try:
            messages = self.mail_client.list_messages(folder_id, limit=self.config.page_size)
        except Exception as e:  # noqa: BLE001
            self.notify(f"Failed to load messages: {e}", severity="error", timeout=10)
            return
        self.query_one("#messages", MessageTable).set_messages(messages)
        self.query_one("#preview", PreviewPane).clear()
        self.current_message_id = None

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
        detail = self.mail_client.get_message(self.current_folder_id, event.message_id)
        quoted = "\n".join(f"> {line}" for line in detail.body_text.splitlines())
        prefill_body = f"\n\n-- original message --\n{quoted}"

        def _on_result(result: dict | None) -> None:
            if result is None:
                return
            self.mail_client.reply(
                self.current_folder_id, event.message_id, result["body"], reply_all=event.reply_all
            )
            self.notify("Reply sent")

        self.push_screen(
            ComposeScreen(to=detail.sender, subject=f"Re: {detail.subject}", body=prefill_body),
            _on_result,
        )

    def on_message_table_delete_requested(self, event: MessageTable.DeleteRequested) -> None:
        if not self.current_folder_id:
            return
        self.mail_client.delete_message(self.current_folder_id, event.message_id)
        self.notify("Message deleted")
        self.select_folder(self.current_folder_id)  # refresh list

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
        try:
            self.mail_client.archive_message(self.current_folder_id, event.message_id)
        except Exception as e:  # noqa: BLE001 - e.g. no Archive folder found
            self.notify(f"Archive failed: {e}", severity="error", timeout=10)
            return
        self.notify("Message archived")
        self.select_folder(self.current_folder_id)  # refresh list

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
        opener = shutil.which("xdg-open")
        if opener is None:
            self.notify(f"Saved to {path} (xdg-open not found — open it manually)", timeout=10)
            return
        try:
            subprocess.Popen(
                [opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
            )
            self.notify(f"Saved to {path} and opening it")
        except OSError as e:
            self.notify(f"Saved to {path}, but couldn't open it automatically: {e}", severity="warning", timeout=10)

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

    def on_calendar_view_delete_event_requested(self, event: CalendarView.DeleteEventRequested) -> None:
        self.calendar_client.delete_event(event.event_id)
        self.notify("Event deleted")
        self.load_calendar_range()

    def on_calendar_view_range_shift_requested(self, event: CalendarView.RangeShiftRequested) -> None:
        self.calendar_range_start += timedelta(days=event.direction * self.calendar_range_days)
        self.load_calendar_range()

    # -- global actions -----------------------------------------------

    def action_show_calendar(self) -> None:
        self.query_one("#main").add_class("hidden")
        self.query_one("#priority").add_class("hidden")
        self.query_one("#calendar").remove_class("hidden")
        self.load_calendar_range()
        self.query_one("#calendar", CalendarView).focus()

    def action_show_mail(self) -> None:
        self.query_one("#calendar").add_class("hidden")
        self.query_one("#priority").add_class("hidden")
        self.query_one("#main").remove_class("hidden")
        self.query_one("#messages", MessageTable).focus()

    def action_show_priority(self) -> None:
        self.query_one("#main").add_class("hidden")
        self.query_one("#calendar").add_class("hidden")
        self.query_one("#priority").remove_class("hidden")
        self.load_priority_list()
        self.query_one("#priority", PriorityView).focus()

    def action_compose_new(self) -> None:
        def _on_result(result: dict | None) -> None:
            if result is None:
                return
            self.mail_client.send_mail(to=[result["to"]], subject=result["subject"], body=result["body"])
            self.notify("Message sent")

        self.push_screen(ComposeScreen(), _on_result)

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())
