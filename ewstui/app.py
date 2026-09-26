from __future__ import annotations

import functools
import logging
from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.css.query import NoMatches
from textual.worker import get_current_worker
from textual.widgets.data_table import RowDoesNotExist
from textual import events
from textual.widgets import Tab, Tabs

from . import config_file, connection
from .config import Config
from .ews_client import CalendarClient, MailClient, MovedMessage
from .opener import OpenError, open_with_default_app
from .priority_store import PriorityStore, split_note
from .event_panel import EventFromEmailPanel
from .invites import when_text
from .widgets.date_picker import DatePicker
from .screens import (
    AddNoteScreen,
    AttachmentListScreen,
    ComposeScreen,
    ConfirmScreen,
    HelpScreen,
    InviteResponseScreen,
    NewEventScreen,
)
from .folder_picker import MoveToFolderScreen
from .links import LinkPickerScreen, extract_links, open_link
from .message_cache import MessageCache
from .room_grid import FindRoomScreen
from .theme import MUTED_SLATE, THEMES
from .keymap import INVITE_HINTS, STATUS_HINTS
from .widgets.calendar_view import CalendarView
from .widgets.chrome import StatusBar, TopBar
from .widgets.folder_list import FolderList
from .widgets.message_table import MessageTable
from .widgets.preview import PreviewPane
from .widgets.priority_view import PriorityView

log = logging.getLogger(__name__)

# How often the open priority view checks todo.txt for outside edits (a stat).
PRIORITY_WATCH_SECONDS = 1.0
# Prefetch the next few messages once the cursor has rested on one this long.
PREFETCH_DELAY = 0.5  # seconds
PREFETCH_COUNT = 2
# What answering an invite did, for the notice afterwards.
INVITE_DONE = {"accept": "Accepted", "tentative": "Tentatively accepted", "decline": "Declined"}


def ews_guard(what: str):
    """For handlers that talk to Exchange on the UI thread: any error (a
    timeout through the gateway, a rejected request, ...) becomes a notice
    instead of an unhandled exception, which would crash the app."""

    def decorate(handler):
        @functools.wraps(handler)
        def wrapper(self, *args, **kwargs):
            try:
                return handler(self, *args, **kwargs)
            except Exception as e:  # noqa: BLE001 - surface anything, never crash
                log.warning("%s failed", what, exc_info=True)
                self.notify(f"{what} failed: {e}", severity="error", timeout=10)
                return None

        return wrapper

    return decorate


def _local(dt: datetime) -> datetime:
    """A time from Exchange (UTC-aware) as this machine's local time."""
    return dt.astimezone() if dt.tzinfo is not None else dt


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
    # Textual's command palette isn't used, and its app-level Ctrl+p would
    # beat Ctrl+p (previous) in pickers like the move-to-folder screen.
    ENABLE_COMMAND_PALETTE = False

    # Colours all come from the theme (ewstui/theme.py: tuxedo's Muted
    # Slate / Nord): subdued $border lines between panes, panes on
    # $background, bars on $panel / $tux-statusbar.
    CSS = """
    Screen {
        background: $background;
    }
    ModalScreen {
        /* popups dim the view behind them instead of hiding it */
        background: $background 60%;
    }
    #modes {
        background: $panel;
        height: 2;
    }
    #modes Tab {
        padding: 0 2;
        color: $tux-dim;
    }
    #modes Tab.-active {
        background: $tux-mode-bg;
        color: $tux-mode-fg;
        text-style: bold;
    }
    #main {
        height: 1fr;
    }
    #folders {
        width: 22%;
        border-right: solid $border;
        background: $background;
    }
    #messages, #preview, #calendar, #priority {
        background: $background;
    }
    /* Message list + reading pane, right of the folders. "columns": side
       by side; "stacked": list on top, email below (config `layout`). */
    #reading {
        width: 1fr;
        layout: horizontal;
    }
    #reading.stacked {
        layout: vertical;
    }
    #reading.columns #messages {
        width: 45%;
        border-right: solid $border;
    }
    #reading.stacked #messages {
        height: 40%;
        border-bottom: solid $border;
    }
    #preview {
        width: 1fr;
        height: 1fr;
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
        Binding("U", "show_links", "Links", show=False),
        Binding("e", "toggle_recipients", "All recipients", show=False),
        Binding("i", "invite", "Answer invite", show=False),
        Binding("c", "event_from_email", "Calendar event from email", show=False),
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
        # Session-only; most recent last. Not persisted across restarts. A
        # list entry is a visual-selection batch, undone as one.
        self.undo_stack: list[UndoEntry | list[UndoEntry]] = []
        # Folders mail was moved to with `m` this session, most recent first
        # (shown first in the picker).
        self._recent_move_targets: list[str] = []
        # (entry key, folder id, message id, Message-ID) to show once the Mail
        # tab is active; see on_priority_view_entry_opened.
        self._pending_jump: tuple | None = None
        self._message_cache = MessageCache()  # fully loaded messages, this session (see message_cache.py)
        # Your calendar on the days invites are for (the card's clashes and
        # day strip), by day; dropped on Ctrl+l and after answering an invite.
        self._invite_days: dict[date, list] = {}
        self._prefetch_timer = None
        # What's on screen, so a refresh can tell what changed.
        self._folder_unread: dict[str, int] = {}
        self._message_snapshot: list[tuple[str, bool]] = []
        self._last_refresh_failed = False

    # -- layout -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
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
            with Container(id="reading", classes=self.config.layout):
                yield MessageTable(id="messages", threaded=self.config.threads)
                yield PreviewPane(id="preview")
        yield CalendarView(id="calendar", classes="hidden")
        yield PriorityView(id="priority", classes="hidden")
        yield StatusBar(id="statusbar")

    def get_theme_variable_defaults(self) -> dict[str, str]:
        # Declare the custom $tux-* variables so the CSS parses under any theme.
        return {k: v for k, v in MUTED_SLATE.variables.items() if k.startswith("tux-")}

    def on_mount(self) -> None:
        for theme in THEMES.values():
            self.register_theme(theme)
        self.theme = self.config.theme
        self.query_one(TopBar).account = self.config.email or "demo"
        self._set_mode_status("mail")
        self.load_folders()
        if self.config.refresh_interval > 0:
            self.set_interval(self.config.refresh_interval * 60, self._background_refresh)
        self.set_interval(PRIORITY_WATCH_SECONDS, self._watch_priority_file)
        self.set_interval(1.0, self._show_connection_state)

    def _show_connection_state(self) -> None:
        """Status bar connection indicator (live mailbox only)."""
        if not connection.MONITOR.active:
            return
        try:
            self.query_one(StatusBar).conn = connection.MONITOR.state()
        except NoMatches:  # shutting down
            pass

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
        folder_list.set_folders(folders, self.config.folder_prefs)
        folders = folder_list.folders or folders  # as listed: your order, hidden ones left out
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
            messages = self._fetch_messages(folder_id)
        except Exception as e:  # noqa: BLE001
            self.notify(f"Failed to load messages: {e}", severity="error", timeout=10)
            return
        self.query_one("#messages", MessageTable).set_messages(messages, home_folder_id=folder_id, priorities=self.priority_store.priorities_by_message())
        self._message_snapshot = [(m.id, m.is_read) for m in messages]
        self._update_status()
        self.query_one("#preview", PreviewPane).clear()
        self.current_message_id = None

    def _fetch_messages(self, folder_id: str) -> list:
        """The folder's messages; in thread view also your replies from
        Sent Items that belong to those conversations (Outlook-style).
        Safe to call from the refresh worker thread.
        """
        messages = self.mail_client.list_messages(folder_id, limit=self.config.page_size)
        if not self.query_one("#messages", MessageTable).threaded:
            return messages
        try:
            sent_id = self.mail_client.sent_folder_id()
            if sent_id == folder_id:
                return messages
            conversations = {m.conversation_id for m in messages if m.conversation_id}
            listed = {m.id for m in messages}
            # One page of recent Sent Items, matched on conversation here —
            # no server-side conversation filter needed.
            replies = [
                m
                for m in self.mail_client.list_messages(sent_id, limit=self.config.page_size)
                if m.conversation_id in conversations and m.id not in listed
            ]
        except Exception:  # noqa: BLE001 - threads still work without your replies
            log.warning("couldn't load Sent Items for threads", exc_info=True)
            return messages
        return messages + replies

    def _folder_of(self, message_id: str) -> str | None:
        """Where a listed message lives: usually the current folder, but a
        thread's replies from Sent Items live there."""
        return self.query_one("#messages", MessageTable).folder_of(message_id) or self.current_folder_id

    def on_message_table_threads_toggled(self, event: MessageTable.ThreadsToggled) -> None:
        table = self.query_one("#messages", MessageTable)
        table.threaded = event.threaded
        self.config.threads = event.threaded
        if not self.current_folder_id:
            return
        try:
            messages = self._fetch_messages(self.current_folder_id)
        except Exception as e:  # noqa: BLE001
            self.notify(f"Failed to load messages: {e}", severity="error", timeout=10)
            return
        table.set_messages(messages, keep_cursor_on=event.message_id, home_folder_id=self.current_folder_id, priorities=self.priority_store.priorities_by_message())
        self._message_snapshot = [(m.id, m.is_read) for m in messages]
        self._update_status()
        self.notify("Thread view on (t to switch off)" if event.threaded else "Thread view off (t to switch on)")

    # -- refresh (Ctrl+l, and every --refresh-interval minutes) ------------

    def action_refresh(self) -> None:
        mode = self.query_one("#modes", Tabs).active
        if mode == "mode-calendar":
            self.load_calendar_range()
        elif mode == "mode-priority":
            self.load_priority_list()
        else:
            self._invite_days.clear()  # invites' clashes are looked up again
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
            messages = self._fetch_messages(folder_id) if folder_id else None
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

        hidden = self.query_one("#folders", FolderList).hidden_folder_ids
        gained = [
            (f.name, f.unread_count - self._folder_unread[f.id])
            for f in folders
            if f.id in self._folder_unread and f.unread_count > self._folder_unread[f.id] and f.id not in hidden
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
        table.set_messages(messages, keep_cursor_on=table._current_message_id(), home_folder_id=folder_id, priorities=self.priority_store.priorities_by_message())
        self._message_snapshot = snapshot
        self._update_status()

    def open_message(self, message_id: str) -> None:
        """Show a message in the reading pane: straight from the in-memory
        cache when it's there and up to date, otherwise fetched in a thread
        (so a slow server never freezes the UI; moving on cancels a fetch
        still in flight). Either way, the next few are then prefetched."""
        if not self.current_folder_id:
            return
        self.current_message_id = message_id
        self._update_status()  # e.g. "i answer invite" for an invite
        cached = self._previewed(message_id)
        if cached is not None:
            self._show_preview(message_id, cached)
            return
        self.query_one("#preview", PreviewPane).show_loading()
        self._load_preview(self._folder_of(message_id), message_id)

    @work(thread=True, exclusive=True, group="preview")
    def _load_preview(self, folder_id: str, message_id: str) -> None:
        worker = get_current_worker()
        try:
            detail = self.mail_client.get_message(folder_id, message_id)
        except Exception as e:  # noqa: BLE001
            if not worker.is_cancelled:
                self.call_from_thread(self._preview_failed, message_id, e)
            return
        if not worker.is_cancelled:
            self.call_from_thread(self._show_preview, message_id, detail)

    def _show_preview(self, message_id: str, detail) -> None:
        # Cached for revisits, reply, links and add-to-priority.
        self._message_cache.put(message_id, detail)
        if message_id == self.current_message_id:  # ignore a message the cursor already left
            self.query_one("#preview", PreviewPane).show_message(detail, self._invite_day(detail))
            self._schedule_prefetch()
        # Priority entries added before ewstui stored Message-IDs get theirs
        # the first time the message is opened (no extra request needed).
        if getattr(detail, "internet_message_id", None):
            self.priority_store.remember_internet_id(message_id, detail.internet_message_id)

    # -- invites: your calendar on the meeting's day ----------------------------------

    def _invite_day(self, detail) -> list | None:
        """Your events on an invite's day if already looked up; otherwise
        None, and the lookup starts (the card fills in when it's back)."""
        meeting = getattr(detail, "meeting", None)
        if meeting is None or meeting.kind != "invite" or meeting.start is None:
            return None
        day = meeting.start.date()
        if day in self._invite_days:
            return self._invite_days[day]
        self._load_invite_day(detail.id, day)
        return None

    @work(thread=True, exclusive=True, group="invite-day")
    def _load_invite_day(self, message_id: str, day: date) -> None:
        start = datetime.combine(day, time.min)
        try:
            events = self.calendar_client.list_events(start, start + timedelta(days=1))
        except Exception as e:  # noqa: BLE001 - the invite still shows, just without clashes
            log.warning("looking up the calendar for an invite failed", exc_info=True)
            self.call_from_thread(self.notify, f"Couldn't check your calendar: {e}", severity="warning", timeout=8)
            return
        self.call_from_thread(self._invite_day_loaded, message_id, day, events)

    def _invite_day_loaded(self, message_id: str, day: date, events: list) -> None:
        self._invite_days[day] = events
        try:
            self.query_one("#preview", PreviewPane).set_invite_events(message_id, events)
        except NoMatches:  # shutting down
            return
        if isinstance(self.screen, InviteResponseScreen):
            self.screen.set_events(events)

    # -- c: a calendar event from the email (save-the-dates without an invite) ---------

    def action_event_from_email(self) -> None:
        if self.query_one(StatusBar).mode != "MAIL":
            return
        existing = self.screen.query(EventFromEmailPanel)
        if existing:  # already open: back into it
            existing.first().query_one(DatePicker).focus()
            return
        detail = self._previewed(self.current_message_id) if self.current_message_id else None
        if detail is None:
            self.notify("Open an email first (or wait for it to load)")
            return
        # Right of the email, in the main area (header and status bar stay).
        self.query_one("#main").mount(EventFromEmailPanel(detail))

    def on_event_from_email_panel_day_changed(self, event: EventFromEmailPanel.DayChanged) -> None:
        if event.day in self._invite_days:
            self._event_panel_day(event.day, self._invite_days[event.day])
        else:
            self._load_event_day(event.day)

    @work(thread=True, exclusive=True, group="event-day")
    def _load_event_day(self, day: date) -> None:
        start = datetime.combine(day, time.min)
        try:
            events = self.calendar_client.list_events(start, start + timedelta(days=1))
        except Exception:  # noqa: BLE001 - the panel works without it
            log.warning("looking up the calendar for a new event failed", exc_info=True)
            return
        self.call_from_thread(self._event_panel_day, day, events)

    def _event_panel_day(self, day: date, events: list) -> None:
        self._invite_days[day] = events
        for panel in self.screen.query(EventFromEmailPanel):
            panel.set_day_events(day, events)

    def on_event_from_email_panel_submitted(self, event: EventFromEmailPanel.Submitted) -> None:
        panel = event.control if isinstance(event.control, EventFromEmailPanel) else self.screen.query_one(EventFromEmailPanel)
        detail = panel.detail
        received = f", {_local(detail.received):%a %d %b %Y %H:%M}" if detail.received else ""
        body = f"From the email “{detail.subject}” ({detail.sender}{received}):\n\n{detail.body_text or ''}"
        self._create_event_from_email({**event.values, "body": body})

    @work(thread=True, group="event-create")
    def _create_event_from_email(self, values: dict) -> None:
        try:
            self.calendar_client.create_event(
                subject=values["subject"], start=values["start"], end=values["end"],
                location=values["location"], body=values["body"], is_all_day=values["is_all_day"],
            )
        except Exception as e:  # noqa: BLE001 - keep the panel, say why
            log.warning("creating an event from an email failed", exc_info=True)
            self.call_from_thread(self.notify, f"Creating the event failed: {e}", severity="error", timeout=10)
            return
        self.call_from_thread(self._event_created, values)

    def _event_created(self, values: dict) -> None:
        self._invite_days.clear()  # your calendar changed
        start, end = values["start"], values["end"]
        if values["is_all_day"]:
            days = (end - start).days
            when = f"{start:%a %d %b %Y}, all day" + (f" ({days} days)" if days > 1 else "")
        else:
            when = f"{start:%a %d %b %Y %H:%M}–{end:%H:%M}"
        self.notify(f"Added “{values['subject']}” to your calendar: {when}")
        self._close_event_panel()

    def on_event_from_email_panel_closed(self, event: EventFromEmailPanel.Closed) -> None:
        self._close_event_panel()

    def _close_event_panel(self) -> None:
        for panel in self.screen.query(EventFromEmailPanel):
            panel.remove()
        if self.query_one(StatusBar).mode == "MAIL":
            self.query_one("#preview", PreviewPane).focus()

    def action_invite(self) -> None:
        """i: answer the invite in the reading pane (or remove a cancelled
        meeting from your calendar)."""
        if self.query_one(StatusBar).mode != "MAIL" or not self.current_message_id:
            return
        message_id = self.current_message_id
        detail = self._previewed(message_id)
        if detail is None:
            self.notify("Still loading the email — try again in a moment")
            return
        meeting = getattr(detail, "meeting", None)
        if meeting is None:
            self.notify("Not a meeting invite")
            return
        folder_id = self._folder_of(message_id)
        if meeting.kind == "cancellation":
            if not meeting.calendar_item_id:
                self.notify("That meeting is already gone from your calendar")
                return
            when = when_text(meeting)

            def _on_confirm(yes: bool | None) -> None:
                if yes:
                    self._remove_cancelled(folder_id, detail)

            self.push_screen(ConfirmScreen(f"Remove “{detail.subject}” ({when}) from your calendar?"), _on_confirm)
            return

        def _on_result(result: dict | None) -> None:
            if result is not None:
                self.notify("Sending your response…" if result["send"] else "Updating your calendar…", timeout=3)
                self._respond_to_invite(folder_id, detail, result["response"], result["note"], result["send"])

        organizer = detail.names.get(meeting.organizer, meeting.organizer) if getattr(detail, "names", None) else meeting.organizer
        self.push_screen(
            InviteResponseScreen(detail.subject, meeting, self._invite_day(detail), organizer_name=organizer), _on_result
        )

    @work(thread=True, group="invite-respond")
    def _respond_to_invite(self, folder_id: str, detail, response: str, note: str, send: bool) -> None:
        try:
            self.mail_client.respond_to_invite(folder_id, detail.id, response, note=note, send=send)
        except Exception as e:  # noqa: BLE001
            log.warning("answering an invite failed", exc_info=True)
            self.call_from_thread(self.notify, f"Answering the invite failed: {e}", severity="error", timeout=10)
            return
        moved, error = self._archive_wherever(folder_id, detail)
        self.call_from_thread(self._invite_done, detail, folder_id, INVITE_DONE[response], send, moved, error)

    @work(thread=True, group="invite-respond")
    def _remove_cancelled(self, folder_id: str, detail) -> None:
        try:
            removed = self.mail_client.remove_cancelled_meeting(folder_id, detail.id)
        except Exception as e:  # noqa: BLE001
            log.warning("removing a cancelled meeting failed", exc_info=True)
            self.call_from_thread(self.notify, f"Removing the meeting failed: {e}", severity="error", timeout=10)
            return
        moved, error = self._archive_wherever(folder_id, detail)
        done = "Removed from your calendar" if removed else "It was already gone from your calendar"
        self.call_from_thread(self._invite_done, detail, folder_id, done, False, moved, error)

    def _archive_wherever(self, folder_id: str, detail):
        """Archive an answered invite / handled cancellation. Exchange may
        already have moved it (e.g. to Deleted Items), so find it by its
        Message-ID first. (worker thread) -> (MovedMessage | None, error)"""
        try:
            where = MovedMessage(folder_id, detail.id)
            if detail.internet_message_id:
                where = self.mail_client.find_message(detail.internet_message_id, folder_id) or where
            return self.mail_client.archive_message(where.folder_id, where.message_id), None
        except Exception as e:  # noqa: BLE001
            log.warning("archiving an answered invite failed", exc_info=True)
            return None, e

    def _invite_done(self, detail, folder_id: str, done: str, sent: bool, moved, error) -> None:
        self._invite_days.clear()  # your calendar changed
        self._message_cache.forget(detail.id)
        if sent:
            organizer = detail.meeting.organizer
            done += f" — response sent to {detail.names.get(organizer, organizer)}"
        if moved is not None:
            self.undo_stack.append(UndoEntry("archived", detail.subject, folder_id, moved))
            self.priority_store.relocate(detail.id, moved.folder_id, moved.message_id)
            self.notify(f"{done}. Email archived (u brings it back; the calendar change stays).", timeout=8)
        else:
            self.notify(f"{done}, but archiving the email failed: {error}", severity="warning", timeout=10)
        self._refresh_keeping_row()

    def _previewed(self, message_id: str):
        """The full message from the in-memory cache, if it's there and its
        change key still matches what the message list says (so an email
        changed elsewhere is fetched again rather than shown stale)."""
        summary = self.query_one("#messages", MessageTable)._by_id.get(message_id)
        return self._message_cache.get(message_id, summary.changekey if summary else None)

    # -- prefetching the next messages ----------------------------------------------

    def _schedule_prefetch(self) -> None:
        """Once the cursor has rested on a message for PREFETCH_DELAY, fetch
        the next PREFETCH_COUNT in the background so j shows them at once."""
        if self._prefetch_timer is not None:
            self._prefetch_timer.stop()
        self._prefetch_timer = self.set_timer(PREFETCH_DELAY, self._start_prefetch)

    def _start_prefetch(self) -> None:
        try:
            table = self.query_one("#messages", MessageTable)
        except NoMatches:  # the timer fired while the app is shutting down
            return
        current = self.current_message_id
        if current is None:
            return
        try:
            row = table.get_row_index(current)
        except RowDoesNotExist:
            return
        todo = []
        for r in range(row + 1, min(row + 1 + PREFETCH_COUNT, table.row_count)):  # the next few rows
            message_id = table.coordinate_to_cell_key((r, 0)).row_key.value
            if self._previewed(message_id) is None:
                todo.append((self._folder_of(message_id), message_id))
        if todo:
            self._set_prefetching(len(todo))
            self._prefetch(todo)

    @work(thread=True, exclusive=True, group="prefetch")
    def _prefetch(self, todo: list) -> None:
        worker = get_current_worker()
        with connection.background():  # not shown as waiting/problems in the status bar
            for i, (folder_id, message_id) in enumerate(todo):
                if worker.is_cancelled:  # the cursor moved on: a newer prefetch replaces this one
                    return
                try:
                    detail = self.mail_client.get_message(folder_id, message_id)
                except Exception:  # noqa: BLE001 - just a prefetch; fetched on demand later
                    log.info("prefetch failed", exc_info=True)
                    continue
                self.call_from_thread(self._message_cache.put, message_id, detail)
                self.call_from_thread(self._set_prefetching, len(todo) - i - 1)
        self.call_from_thread(self._set_prefetching, 0)

    def _set_prefetching(self, n: int) -> None:
        try:
            self.query_one(StatusBar).prefetching = n
        except NoMatches:  # shutting down
            pass

    def _preview_failed(self, message_id: str, error: Exception) -> None:
        if message_id == self.current_message_id:
            self.query_one("#preview", PreviewPane).clear()
            self.notify(f"Failed to load message: {error}", severity="error", timeout=10)

    def load_priority_list(self) -> None:
        view = self.query_one("#priority", PriorityView)
        current = view.current_entry()
        view.set_entries(self.priority_store.list_entries(email_only=True))
        if current is not None:
            view.select_matching(current)  # keep the cursor on the same item
        self._update_status()

    def _watch_priority_file(self) -> None:
        """Every PRIORITY_WATCH_SECONDS while the priority view is open:
        pick up edits made to todo.txt in another application."""
        try:
            mode = self.query_one(StatusBar).mode
        except NoMatches:  # the timer can fire while the app is shutting down
            return
        if mode != "PRIORITY" or not self.priority_store.changed_on_disk():
            return
        if self.query_one("#priority", PriorityView).in_visual_mode:
            return  # don't cancel a selection in progress; picked up on a later tick
        self._priorities_changed()  # reload the list, and the mail list's P column

    def load_calendar_range(self) -> None:
        start = self.calendar_range_start
        end = start + timedelta(days=self.calendar_range_days)
        try:
            events = self.calendar_client.list_events(start, end)
        except Exception as e:  # noqa: BLE001
            self.notify(f"Failed to load calendar: {e}", severity="error", timeout=10)
            return
        self.query_one("#calendar", CalendarView).set_events(events)
        self._update_status()

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
        folder_id = self._folder_of(event.message_id)
        cached = self._previewed(event.message_id)
        if cached is not None:
            # Already loaded in the reading pane: no server round trip.
            self._open_reply(folder_id, event.message_id, event.reply_all, cached)
            return
        self.notify("Loading the message…", timeout=3)
        self._fetch_for_reply(folder_id, event.message_id, event.reply_all)

    @work(thread=True, exclusive=True, group="reply")
    def _fetch_for_reply(self, folder_id: str, message_id: str, reply_all: bool) -> None:
        try:
            detail = self.mail_client.get_message(folder_id, message_id)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self.notify, f"Reply failed: couldn't load the message: {e}", severity="error", timeout=10)
            return
        self.call_from_thread(self._open_reply, folder_id, message_id, reply_all, detail)

    def _open_reply(self, folder_id: str, message_id: str, reply_all: bool, detail) -> None:
        event = SimpleNamespace(message_id=message_id, reply_all=reply_all)
        quoted = "\n".join(f"> {line}" for line in detail.body_text.splitlines())
        # Who wrote it and when: shown while writing, and as the usual
        # attribution line above the quote (the recipients see that one).
        name = (getattr(detail, "names", None) or {}).get(detail.sender)
        who = f"{name} <{detail.sender}>" if name else detail.sender
        when = _local(detail.received).strftime("%a %d %b %Y %H:%M") if detail.received else None
        attribution = f"On {when}, {who} wrote:" if when else f"{who} wrote:"
        prefill_body = f"\n\n{attribution}\n{quoted}"
        context = f"Replying to {who}" + (f" · sent {when}" if when else "")

        def _send(result: dict) -> None:
            self.mail_client.reply(
                folder_id,
                event.message_id,
                subject=result["subject"],
                body=result["body"],
                to=_split_addresses(result["to"]),
                reply_all=event.reply_all,
            )

        def _save_draft(result: dict) -> None:
            self.mail_client.save_reply_draft(
                folder_id,
                event.message_id,
                subject=result["subject"],
                body=result["body"],
                to=_split_addresses(result["to"]),
                reply_all=event.reply_all,
            )

        self._compose_and_send(
            ComposeScreen(to=detail.sender, subject=f"Re: {detail.subject}", body=prefill_body, context=context),
            _send,
            sent_message="Reply sent",
            save_draft=_save_draft,
        )

    def _compose_and_send(self, screen: ComposeScreen, send, sent_message: str, save_draft) -> None:
        """Show `screen`; on Ctrl+S call `send(result)`, on Esc (with
        something written) `save_draft(result)`. If either fails, say why
        and reopen the compose screen with the text intact, so a server
        error never throws away what the user wrote.
        """

        if screen.fetch_events is None:  # Ctrl+O: your calendar beside the email
            screen.fetch_events = self.calendar_client.list_events

        def _reopen(result: dict) -> None:
            self._compose_and_send(
                ComposeScreen(to=result["to"], subject=result["subject"], body=result["body"],
                              context=screen.context, unsaved=True),
                send, sent_message, save_draft,
            )

        def _on_result(result: dict | None) -> None:
            if result is None:
                return
            if result.get("draft"):
                try:
                    save_draft(result)
                except Exception as e:  # noqa: BLE001 - keep the text
                    log.exception("saving a draft failed")
                    self.notify(f"Saving to Drafts failed: {e} — still here", severity="error", timeout=10)
                    _reopen(result)
                    return
                self.notify("Saved to Drafts")
                self._drafts_changed()
                return
            try:
                send(result)
            except Exception as e:  # noqa: BLE001 - surface any EWS error, keep the draft
                log.exception("send failed")
                self.notify(f"Send failed: {e} — draft kept", severity="error", timeout=10)
                _reopen(result)
                return
            self.notify(sent_message)

        self.push_screen(screen, _on_result)

    def _drafts_changed(self) -> None:
        """A draft was saved: show it if Drafts is the folder on screen."""
        try:
            drafts_id = self.mail_client.drafts_folder_id()
        except Exception:  # noqa: BLE001 - only a refresh
            return
        if self.current_folder_id == drafts_id:
            self._refresh_keeping_row()

    def on_message_table_delete_requested(self, event: MessageTable.DeleteRequested) -> None:
        if not self.current_folder_id:
            return
        folder_id = self._folder_of(event.message_id)
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
            self.priority_store.relocate(event.message_id, moved.folder_id, moved.message_id)
            self.notify("Moved to Deleted Items — press u to undo")
        self._refresh_keeping_row()

    @ews_guard("Marking read/unread")
    def on_message_table_toggle_read_requested(self, event: MessageTable.ToggleReadRequested) -> None:
        if not self.current_folder_id:
            return
        folder_id = self._folder_of(event.message_id)
        summary = self.query_one("#messages", MessageTable)._by_id.get(event.message_id)
        is_read = summary.is_read if summary else self.mail_client.get_message(folder_id, event.message_id).is_read
        self.mail_client.mark_read(folder_id, event.message_id, read=not is_read)
        self._refresh_keeping_row()  # list + unread counts, cursor stays put

    def _priority_fields(self, folder_id: str, message_id: str) -> tuple[str, str, str | None]:
        """(subject, sender, Message-ID) for a new priority entry, from what's
        already loaded when possible: the reading pane has the Message-ID, the
        list has subject and sender. Fetches only if neither has it."""
        detail = self._previewed(message_id)
        if detail is not None:
            return detail.subject, detail.sender, detail.internet_message_id
        summary = self.query_one("#messages", MessageTable)._by_id.get(message_id)
        if summary is not None:
            return summary.subject, summary.sender, None  # Message-ID filled in when it's opened
        detail = self.mail_client.get_message(folder_id, message_id)
        return detail.subject, detail.sender, detail.internet_message_id

    @ews_guard("Adding to the priority list")
    def on_message_table_add_to_priority_requested(self, event: MessageTable.AddToPriorityRequested) -> None:
        if not self.current_folder_id:
            return
        existing = self.priority_store.find_by_message_id(event.message_id)
        if existing is not None:  # leave its priority and note alone
            pri = f"priority {existing.priority}" if existing.priority else "no priority"
            self.notify(f"Already on the priority list ({pri}) — p edits its note, 2 sets the priority")
            return
        folder_id = self._folder_of(event.message_id)
        subject, sender, internet_id = self._priority_fields(folder_id, event.message_id)
        self.priority_store.add_email(
            message_id=event.message_id,
            folder_id=folder_id,
            subject=subject,
            sender=sender,
            priority=None,
            internet_id=internet_id,
        )
        self.notify(f"Added to priority list, no priority set ({self._priority_path()})")
        self._priorities_changed()

    @ews_guard("Adding to the priority list")
    def on_message_table_add_to_priority_with_note_requested(
        self, event: MessageTable.AddToPriorityWithNoteRequested
    ) -> None:
        if not self.current_folder_id:
            return
        folder_id = self._folder_of(event.message_id)
        subject, sender, internet_id = self._priority_fields(folder_id, event.message_id)
        existing = self.priority_store.find_by_message_id(event.message_id)
        if existing is not None:  # on the list already: edit its note
            _, current = split_note(existing.description, subject)

            def _on_edit(note: str | None) -> None:
                if note is None or note == current:
                    return
                if not self.priority_store.set_note(event.message_id, note, subject):
                    self.notify("That entry changed in todo.txt meanwhile — try again", severity="warning")
                    return
                self.notify("Note removed" if not note else "Note updated")
                self._priorities_changed()

            self.push_screen(AddNoteScreen(label=f"Edit note for: {subject}  (Ctrl+S / Enter to save, Esc to cancel)",
                                           value=current), _on_edit)
            return

        def _on_result(note: str | None) -> None:
            if note is None:
                return
            self.priority_store.add_email(
                message_id=event.message_id,
                folder_id=folder_id,
                subject=subject,
                sender=sender,
                priority=None,
                note=note,
                internet_id=internet_id,
            )
            self.notify(f"Added to priority list with note ({self._priority_path()})")
            self._priorities_changed()

        self.push_screen(AddNoteScreen(label=f"Note for: {subject}"), _on_result)

    def on_message_table_archive_requested(self, event: MessageTable.ArchiveRequested) -> None:
        if not self.current_folder_id:
            return
        folder_id = self._folder_of(event.message_id)
        try:
            subject = self.mail_client.get_message(folder_id, event.message_id).subject
            moved = self.mail_client.archive_message(folder_id, event.message_id)
        except Exception as e:  # noqa: BLE001 - e.g. no Archive folder found
            self.notify(f"Archive failed: {e}", severity="error", timeout=10)
            return
        self.undo_stack.append(UndoEntry("archived", subject, folder_id, moved))
        self.priority_store.relocate(event.message_id, moved.folder_id, moved.message_id)
        self.notify("Message archived — press u to undo")
        self._refresh_keeping_row()

    def on_message_table_move_requested(self, event: MessageTable.MoveRequested) -> None:
        if not self.current_folder_id:
            return
        folder_id = self._folder_of(event.message_id)
        try:
            subject = self.mail_client.get_message(folder_id, event.message_id).subject
        except Exception as e:  # noqa: BLE001
            self.notify(f"Couldn't load the message: {e}", severity="error", timeout=10)
            return
        folders = self.query_one("#folders", FolderList).folders

        def _move(dest_id: str | None) -> None:
            if dest_id is None:
                return
            dest_name = next((f.name for f in folders if f.id == dest_id), "folder")
            try:
                moved = self.mail_client.move_message(folder_id, event.message_id, dest_id)
            except Exception as e:  # noqa: BLE001 - surface any EWS error
                self.notify(f"Move failed: {e}", severity="error", timeout=10)
                return
            self.undo_stack.append(UndoEntry("moved", subject, folder_id, moved))
            self.priority_store.relocate(event.message_id, moved.folder_id, moved.message_id)
            self._recent_move_targets = [dest_id] + [f for f in self._recent_move_targets if f != dest_id][:4]
            self.notify(f"Moved to {dest_name} — press u to undo")
            self._refresh_keeping_row()

        self.push_screen(MoveToFolderScreen(subject, folders, self._recent_move_targets, folder_id), _move)

    def _refresh_keeping_row(self, row: int | None = None) -> None:
        """Reload the current folder after a message left it (delete/
        archive/move), leaving the cursor on the same row — i.e. the next
        message — rather than jumping back to the top."""
        table = self.query_one("#messages", MessageTable)
        row = table.cursor_row if row is None else row
        scroll_y = table.scroll_y  # and the list stays where it was on screen
        self.select_folder(self.current_folder_id)
        if table.row_count:
            table.move_cursor(row=min(row, table.row_count - 1), scroll=False)
            table.keep_scroll(scroll_y)

    # -- visual selection: one action on many messages --------------------------

    BULK_VERBS = {
        "delete": ("Deleting", "deleted"),
        "archive": ("Archiving", "archived"),
        "move": ("Moving", "moved"),
        "toggle_read": ("Updating", "updated"),
        "add_to_priority": ("Adding", "added"),
    }

    def on_message_table_visual_changed(self, event: MessageTable.VisualChanged) -> None:
        self._update_status()

    def on_message_table_bulk_requested(self, event: MessageTable.BulkRequested) -> None:
        table = self.query_one("#messages", MessageTable)
        # (message id, its folder, subject) — subject from the list, so no fetch per message
        items = [(mid, self._folder_of(mid), table._by_id[mid].subject) for mid in event.message_ids if mid in table._by_id]
        if not items:
            return
        top_row = table.get_row_index(items[0][0])
        if event.action == "move":
            folders = self.query_one("#folders", FolderList).folders

            def _picked(dest_id: str | None) -> None:
                if dest_id is not None:
                    self._start_bulk("move", items, top_row, dest_id=dest_id)

            self.push_screen(
                MoveToFolderScreen(f"{len(items)} messages", folders, self._recent_move_targets, self.current_folder_id),
                _picked,
            )
            return
        extra = None
        if event.action == "toggle_read":
            # Like most mail clients: any unread in the selection -> mark all read.
            extra = any(not table._by_id[mid].is_read for mid, _, _ in items)
        self._start_bulk(event.action, items, top_row, extra=extra)

    def _start_bulk(self, action: str, items: list, top_row: int, dest_id: str | None = None, extra=None) -> None:
        self.notify(f"{self.BULK_VERBS[action][0]} {len(items)} messages…", timeout=3)
        if action == "move":
            self._recent_move_targets = [dest_id] + [f for f in self._recent_move_targets if f != dest_id][:4]
        self._bulk_worker(action, items, top_row, dest_id, extra)

    @work(thread=True, group="bulk")
    def _bulk_worker(self, action: str, items: list, top_row: int, dest_id: str | None, extra) -> None:
        """One EWS call per message, off the UI thread; results applied by
        _bulk_done. A failure on one message doesn't stop the rest."""
        done, errors = [], []
        for message_id, folder_id, subject in items:
            try:
                if action == "delete":
                    result = self.mail_client.delete_message(folder_id, message_id)
                elif action == "archive":
                    result = self.mail_client.archive_message(folder_id, message_id)
                elif action == "move":
                    result = self.mail_client.move_message(folder_id, message_id, dest_id)
                elif action == "toggle_read":
                    result = self.mail_client.mark_read(folder_id, message_id, read=extra)
                else:  # add_to_priority: needs sender and Message-ID
                    result = self.mail_client.get_message(folder_id, message_id)
            except Exception as e:  # noqa: BLE001 - report, carry on with the rest
                log.warning("bulk %s failed for one message", action, exc_info=True)
                errors.append(e)
                continue
            done.append((message_id, folder_id, subject, result))
        self.call_from_thread(self._bulk_done, action, done, errors, top_row, dest_id, extra)

    def _bulk_done(self, action: str, done: list, errors: list, top_row: int, dest_id, extra) -> None:
        verb = self.BULK_VERBS[action][1]
        if action in ("delete", "archive", "move"):
            undo = []
            for message_id, folder_id, subject, moved in done:
                if moved is None:  # permanently deleted (was in Deleted Items)
                    continue
                undo.append(UndoEntry(verb, subject, folder_id, moved))
                self.priority_store.relocate(message_id, moved.folder_id, moved.message_id)
            if undo:
                self.undo_stack.append(undo)  # one u undoes the whole batch
            where = ""
            if action == "move":
                where = " to " + next((f.name for f in self.query_one("#folders", FolderList).folders if f.id == dest_id), "folder")
            message = f"{verb.capitalize()} {len(done)} messages{where}"
            self.notify(message + (" — press u to undo" if undo else ""))
            self._refresh_keeping_row(top_row)
        elif action == "toggle_read":
            self.notify(f"Marked {len(done)} messages as {'read' if extra else 'unread'}")
            self._refresh_keeping_row(top_row)
        else:
            for message_id, folder_id, _subject, detail in done:
                self.priority_store.add_email(
                    message_id=message_id,
                    folder_id=folder_id,
                    subject=detail.subject,
                    sender=detail.sender,
                    priority=None,
                    internet_id=detail.internet_message_id,
                )
            self.notify(f"Added {len(done)} messages to the priority list ({self._priority_path()})")
            self._priorities_changed()
        if errors:
            self.notify(f"{len(errors)} message(s) failed: {errors[0]}", severity="error", timeout=10)

    @work(thread=True, group="bulk")
    def _undo_batch(self, entries: list) -> None:
        restored, errors = [], []
        for entry in entries:
            try:
                new = self.mail_client.move_message(entry.moved.folder_id, entry.moved.message_id, entry.original_folder_id)
            except Exception as e:  # noqa: BLE001
                errors.append(e)
                continue
            restored.append((entry, new))
        self.call_from_thread(self._undo_batch_done, restored, errors)

    def _undo_batch_done(self, restored: list, errors: list) -> None:
        for entry, new in restored:
            self.priority_store.relocate(entry.moved.message_id, new.folder_id, new.message_id)
        if restored:
            self.notify(f"Restored {len(restored)} {restored[0][0].verb} messages")
        if errors:
            self.notify(f"{len(errors)} message(s) couldn't be restored: {errors[0]}", severity="error", timeout=10)
        self.select_folder(self.current_folder_id)

    @ews_guard("Opening attachments")
    def on_message_table_view_attachments_requested(self, event: MessageTable.ViewAttachmentsRequested) -> None:
        if not self.current_folder_id:
            return
        folder_id = self._folder_of(event.message_id)
        try:
            attachments = self.mail_client.list_attachments(folder_id, event.message_id)
        except Exception as e:  # noqa: BLE001
            self.notify(f"Failed to load attachments: {e}", severity="error", timeout=10)
            return
        if not attachments:
            self.notify("No attachments on this message")
            return
        detail = self.mail_client.get_message(folder_id, event.message_id)

        def _on_result(attachment_id: str | None) -> None:
            if attachment_id is None:
                return
            try:
                path = self.mail_client.save_attachment(
                    folder_id, event.message_id, attachment_id, self.config.attachment_dir
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

    def _priority_path(self) -> str:
        """The todo.txt in use, ~-shortened, so it's clear which file changes."""
        path = self.config.priority_file
        try:
            return "~/" + str(path.relative_to(Path.home()))
        except ValueError:
            return str(path)

    def _priority_file_changed(self) -> None:
        """A key no longer matched: the file changed on disk under us."""
        self.notify(f"{self.config.priority_file.name} changed on disk — reloaded, nothing was changed", severity="warning")
        self.load_priority_list()

    def _priorities_changed(self) -> None:
        self.load_priority_list()
        self.query_one("#messages", MessageTable).set_priorities(self.priority_store.priorities_by_message())

    def on_priority_view_priority_changed(self, event: PriorityView.PriorityChanged) -> None:
        if not self.priority_store.set_priority_many(event.entry_keys, event.priority):
            self._priority_file_changed()
            return
        n = len(event.entry_keys)
        self.notify(f"Set {n} item{'s' if n != 1 else ''} to ({event.priority})")
        self._priorities_changed()

    def on_priority_view_complete_toggled(self, event: PriorityView.CompleteToggled) -> None:
        if not self.priority_store.toggle_complete(event.entry_key):
            self._priority_file_changed()
            return
        self._priorities_changed()

    def on_priority_view_remove_requested(self, event: PriorityView.RemoveRequested) -> None:
        if not self.priority_store.remove(event.entry_key):
            self._priority_file_changed()
            return
        self.notify("Removed from priority list")
        self._priorities_changed()

    def on_priority_view_entry_opened(self, event: PriorityView.EntryOpened) -> None:
        entry = next((e for e in self.priority_store.list_entries(include_completed=True) if e.key == event.entry_key), None)
        if entry is None:
            return
        if not entry.message_id or not entry.folder_id:
            self.notify("This item isn't linked to an email", severity="warning")
            return
        # The tab switch is asynchronous and its handler focuses the message
        # list, so do the jump from there (on_tabs_tab_activated), after it.
        self._pending_jump = (entry.key, entry.folder_id, entry.message_id, entry.internet_id)
        self.action_show_mail()

    def _jump_to_entry(self, key: str, folder_id: str, message_id: str, internet_id: str | None) -> None:
        """Open a prioritised email: where it was last seen if it's still
        there (no search needed), otherwise find it by its Message-ID — it
        may have been moved, in ewstui or elsewhere — and update the entry."""
        if self._show_if_listed(folder_id, message_id):
            return
        if not internet_id:
            self.notify(
                "That email has moved or been deleted, and this entry predates Message-ID tracking",
                severity="warning",
            )
            return
        self.notify("Looking for the email…", timeout=3)
        self._find_and_jump(key, internet_id, folder_id)

    @work(thread=True, exclusive=True, group="find-message")
    def _find_and_jump(self, key: str, internet_id: str, hint_folder_id: str) -> None:
        try:
            found = self.mail_client.find_message(internet_id, hint_folder_id)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self.notify, f"Search failed: {e}", severity="error", timeout=10)
            return
        self.call_from_thread(self._found_message, key, found)

    def _found_message(self, key: str, found) -> None:
        if found is None:
            self.notify("That email wasn't found in any folder (deleted?)", severity="warning")
            return
        self.priority_store.relocate_key(key, found.folder_id, found.message_id)
        if not self._show_if_listed(found.folder_id, found.message_id):
            # Found, but older than the loaded page of its folder: show it anyway.
            self.open_message(found.message_id)
            self.query_one("#preview", PreviewPane).focus()

    def _show_if_listed(self, folder_id: str, message_id: str) -> bool:
        """Show a message in the mail view as if navigated to by hand —
        folder highlighted and loaded, cursor on it (which loads the reading
        pane), focus in the reading pane. False if it isn't listed there."""
        folder_list = self.query_one("#folders", FolderList)
        if folder_id not in {f.id for f in folder_list.folders}:
            return False
        folder_list.highlight_folder(folder_id)
        self.select_folder(folder_id)
        table = self.query_one("#messages", MessageTable)
        try:
            row = table.get_row_index(message_id)
        except RowDoesNotExist:
            return False
        table.move_cursor(row=row)
        self.query_one("#preview", PreviewPane).focus()
        return True

    # -- calendar pane events ---------------------------------------------

    @ews_guard("Opening the event")
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
            try:
                self.calendar_client.create_event(
                    subject=result["subject"], start=result["start"], end=result["end"], location=result["location"]
                )
            except Exception as e:  # noqa: BLE001 - surface any EWS error, never crash
                log.warning("creating event failed", exc_info=True)
                self.notify(f"Creating the event failed: {e}", severity="error", timeout=10)
                return
            self.notify("Event created")
            self.load_calendar_range()

        self.push_screen(NewEventScreen(default_start=self.calendar_range_start), _on_result)

    # -- folder pane arrangement (V to move, H to hide) -----------------------------

    def on_folder_list_prefs_changed(self, event: FolderList.PrefsChanged) -> None:
        self.config.folder_prefs = event.prefs
        notice = event.notice or "Folder order saved"
        if not (self.config.account and self.config.config_path):
            self.notify(f"{notice} (this session only — start with --account NAME to keep it)", timeout=6)
            return
        names = {f.id: f.name for f in self.query_one("#folders", FolderList)._all}
        try:
            config_file.save_folder_prefs(self.config.config_path, self.config.account, event.prefs, names)
        except (OSError, config_file.ConfigFileError) as e:
            self.notify(f"{notice}, but couldn't save it: {e}", severity="warning", timeout=10)
            return
        if event.notice:
            self.notify(event.notice)

    def on_folder_list_move_mode_changed(self, event: FolderList.MoveModeChanged) -> None:
        self._update_status()

    def _save_found_room(self, room) -> None:
        """A room picked with / in the room grid: remember it for next time."""
        self.config.rooms.append(room)
        if not (self.config.account and self.config.config_path):
            self.notify(
                f"Added {room.name} for this session — start with --account NAME to keep found rooms",
                severity="warning",
                timeout=10,
            )
            return
        try:
            config_file.add_room(self.config.config_path, self.config.account, room)
        except (OSError, config_file.ConfigFileError) as e:
            self.notify(f"Added {room.name}, but couldn't save it: {e}", severity="warning", timeout=10)
            return
        self.notify(f"Added {room.name} to your room list ({self.config.config_path})")

    def on_calendar_view_find_room_requested(self, event: CalendarView.FindRoomRequested) -> None:
        def _book(result: dict | None) -> None:
            if result is None:
                return
            rooms = result["rooms"]  # one room, or several from a visual selection
            names = ", ".join(r.name for r in rooms)

            def _on_event(details: dict | None) -> None:
                if details is None:
                    return
                try:
                    self.calendar_client.create_event(
                        subject=details["subject"],
                        start=details["start"],
                        end=details["end"],
                        location=details["location"],
                        resources=[r.email for r in rooms],
                    )
                except Exception as e:  # noqa: BLE001 - surface any EWS error
                    self.notify(f"Booking failed: {e}", severity="error", timeout=10)
                    return
                them = "it" if len(rooms) == 1 else "each room"
                self.notify(f"Invite sent to {names} — {them} will accept or decline shortly")
                self.load_calendar_range()

            self.push_screen(
                NewEventScreen(
                    default_start=result["start"],
                    default_end=result["end"],
                    default_location=names,
                    title=f"New event in {names}",
                ),
                _on_event,
            )

        self.push_screen(
            FindRoomScreen(
                self.config.rooms,
                self.calendar_client.rooms_day,
                search=self.calendar_client.search_rooms,
                on_room_added=self._save_found_room,
            ),
            _book,
        )

    @ews_guard("Deleting the event")
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

    # -- header + status bar -------------------------------------------------

    def _set_mode_status(self, mode: str) -> None:
        self.query_one(StatusBar).mode = mode.upper()
        self._update_status()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self._update_status()

    def _update_status(self) -> None:
        """Hints for the focused pane; counts for the visible view."""
        try:
            status, top = self.query_one(StatusBar), self.query_one(TopBar)
        except NoMatches:  # focus events can arrive while the app is shutting down
            return
        mode = status.mode.lower()
        # The main view's focus, even while a popup (help, links, …) has its
        # own: the hints describe the view underneath and stay put.
        focused = self.screen_stack[0].focused
        pane = {FolderList: "folders", MessageTable: "messages", PreviewPane: "preview"}.get(type(focused), mode)
        if focused is not None and any(isinstance(a, EventFromEmailPanel) for a in focused.ancestors_with_self):
            pane = "event"
        # The message list opens into the pane to its right ("l") in the
        # columns layout, but into the one below it ("o") when stacked; the
        # folder pane always opens rightwards.
        open_key = "o" if pane == "messages" and self.config.layout == "stacked" else "l"
        status.hints = STATUS_HINTS.get(pane, STATUS_HINTS.get(mode, "")).format(open=open_key)
        if pane == "folders" and focused.moving is not None:
            status.hints = STATUS_HINTS["moving"]
        table = self.query_one("#messages", MessageTable)
        if pane in ("messages", "preview"):
            current = table._by_id.get(self.current_message_id or "")
            invite_hint = INVITE_HINTS.get(getattr(current, "kind", "mail"))
            if invite_hint:  # the email shown is an invite / cancellation
                status.hints = f"{invite_hint} · {status.hints}"
        if mode == "mail" and table.in_visual_mode:
            status.hints = STATUS_HINTS["visual"].format(count=len(table.selected_ids()))
        if mode == "mail":
            table = self.query_one("#messages", MessageTable)
            messages = table._messages
            unread = sum(not m.is_read for m in messages)
            folder = next(
                (f.name for f in self.query_one("#folders", FolderList).folders if f.id == self.current_folder_id), ""
            )
            items = [(folder, "normal"), (f"{len(messages)} messages", "dim")]
            if unread:
                items.append((f"{unread} unread", "accent"))
            if table.threaded:
                items.append(("threads", "dim"))
            top.items = tuple(items)
            status.info = f"{len(messages)} messages"
        elif mode == "priority":
            n = len(self.priority_store.list_entries(email_only=True))
            # Name the file, so it's obvious which todo.txt ewstui writes (the
            # full path is in the add/remove notices; it'd crowd the header).
            top.items = (
                ("Priority list", "normal"),
                (self.config.priority_file.name, "dim"),
                (f"{n} items", "dim"),
            )
            status.info = f"{n} items"
        else:
            end = self.calendar_range_start + timedelta(days=self.calendar_range_days - 1)
            top.items = (("Calendar", "normal"), (f"{self.calendar_range_start:%d %b} – {end:%d %b}", "dim"))
            status.info = ""

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        mode = event.tab.id.removeprefix("mode-")
        if mode != "mail":  # the event-from-email panel belongs to the email view
            for panel in self.screen.query(EventFromEmailPanel):
                panel.remove()
        views = {"mail": "#main", "priority": "#priority", "calendar": "#calendar"}
        for name, selector in views.items():
            self.query_one(selector).set_class(name != mode, "hidden")
        self._set_mode_status(mode)
        if mode == "mail":
            self.query_one("#messages", MessageTable).focus()
            # The calendar shows its events in the same reading pane: put
            # the current email back.
            if self.current_message_id:
                self.open_message(self.current_message_id)
            else:
                self.query_one("#preview", PreviewPane).clear()
            if self._pending_jump is not None:  # from the priority view (Enter/o)
                pending, self._pending_jump = self._pending_jump, None
                self._jump_to_entry(*pending)
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
        if isinstance(entry, list):  # a whole visual-selection batch
            self.notify(f"Restoring {len(entry)} messages…", timeout=3)
            self._undo_batch(entry)
            return
        try:
            restored = self.mail_client.move_message(
                entry.moved.folder_id, entry.moved.message_id, entry.original_folder_id
            )
        except Exception as e:  # noqa: BLE001 - e.g. already purged from Deleted Items
            self.notify(f"Undo failed: {e}", severity="error", timeout=10)
            return
        self.priority_store.relocate(entry.moved.message_id, restored.folder_id, restored.message_id)
        self.notify(f"Restored {entry.verb} message: {entry.subject}")
        table = self.query_one("#messages", MessageTable)
        # In thread view a restored Sent Items reply shows up in this folder's
        # threads too, so reload whenever threads are on.
        if table.threaded or self.current_folder_id in (entry.original_folder_id, entry.moved.folder_id):
            self.select_folder(self.current_folder_id)
            try:
                table.move_cursor(row=table.get_row_index(restored.message_id))
            except RowDoesNotExist:
                pass  # not listed here (older than the loaded page, or another folder)

    def action_compose_new(self) -> None:
        def _send(result: dict) -> None:
            self.mail_client.send_mail(
                to=_split_addresses(result["to"]), subject=result["subject"], body=result["body"]
            )

        def _save_draft(result: dict) -> None:
            self.mail_client.save_draft(
                to=_split_addresses(result["to"]), subject=result["subject"], body=result["body"]
            )

        self._compose_and_send(ComposeScreen(), _send, sent_message="Message sent", save_draft=_save_draft)

    # -- links in the current email (U) ----------------------------------------

    def action_toggle_recipients(self) -> None:
        """e (or a click on "+N more"): show every To/Cc recipient of the
        email in the reading pane, one per line with address; e folds again."""
        if self.query_one(StatusBar).mode != "MAIL":
            return
        self.query_one("#preview", PreviewPane).toggle_recipients()

    def action_show_links(self) -> None:
        """List the links in the email shown in the reading pane; the chosen
        one opens in the default browser."""
        if self.query_one(StatusBar).mode != "MAIL" or not self.current_message_id:
            return
        message_id = self.current_message_id
        detail = self._previewed(message_id)
        if detail is not None:
            self._pick_link(detail)
            return
        self._fetch_for_links(self._folder_of(message_id), message_id)

    @work(thread=True, exclusive=True, group="links")
    def _fetch_for_links(self, folder_id: str, message_id: str) -> None:
        try:
            detail = self.mail_client.get_message(folder_id, message_id)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self.notify, f"Couldn't load the message: {e}", severity="error", timeout=10)
            return
        self.call_from_thread(self._pick_link, detail)

    def _pick_link(self, detail) -> None:
        links = extract_links(detail.body_text or "")
        if not links:
            self.notify("No links in this email")
            return

        def _open(url: str | None) -> None:
            if url is None:
                return
            try:
                open_link(url)
            except Exception as e:  # noqa: BLE001
                self.notify(f"Couldn't open the link: {e}", severity="error", timeout=10)
                return
            self.notify(f"Opening {url}", timeout=3)

        self.push_screen(LinkPickerScreen(detail.subject, links), _open)

    def action_show_help(self) -> None:
        # Help for the view you're in (the status bar's mode chip).
        self.push_screen(HelpScreen(self.query_one(StatusBar).mode.lower()))
