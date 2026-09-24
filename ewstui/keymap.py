"""
Single source of truth for the vim-ish bindings, used by the help
screen (press `?`). The actual key -> action wiring lives on each
widget/screen (Textual's BINDINGS), since that's how Textual scopes
key handling — this module is documentation + a couple of shared
constants, not a dispatch table.
"""

GLOBAL_KEYS = [
    ("j / down", "move down"),
    ("k / up", "move up"),
    ("g", "jump to top"),
    ("G", "jump to bottom"),
    ("Tab / l", "next pane (l also opens the item under the cursor)"),
    ("Shift+Tab / h", "previous pane"),
    ("Enter / l", "open / select"),
    ("Esc", "cancel / close modal"),
    ("/", "search current list"),
    ("1", "mail view"),
    ("2", "priority list view"),
    ("3", "calendar view"),
    ("Ctrl+l", "refresh (check for new mail; also automatic, see --refresh-interval)"),
    ("q", "quit"),
    ("?", "toggle this help"),
]

MAIL_KEYS = [
    ("Enter / l", "open message"),
    ("r", "reply"),
    ("R", "reply all"),
    ("w", "compose new message"),
    ("d", "delete message"),
    ("Space", "toggle read/unread"),
    ("P", "add to priority list (no priority, no prompt)"),
    ("p", "add to priority list with a note"),
    ("A", "archive message"),
    ("u", "undo last delete/archive (repeatable)"),
    ("v", "view/save/open attachments"),
]

PREVIEW_KEYS = [
    ("j / k", "scroll down / up one line"),
    ("Ctrl+d / Ctrl+u", "scroll half a page down / up"),
    ("Ctrl+f / Ctrl+b", "scroll a page down / up (Space also pages down)"),
    ("g / G", "top / bottom of the message"),
    ("h", "back to the message list"),
]

CALENDAR_KEYS = [
    ("n", "new event"),
    ("d", "delete selected event"),
    ("[ / ]", "previous / next day"),
]

PRIORITY_KEYS = [
    ("A-Z", "set priority of current (or selected) item(s)"),
    ("V", "start/apply visual selection (then press a letter)"),
    ("Esc", "cancel visual selection"),
    ("x", "toggle complete"),
    ("d", "remove from priority list"),
    ("Enter / o", "jump to the email"),
]

COMPOSE_KEYS = [
    ("Tab", "next field"),
    ("Ctrl+s / Cmd+Enter", "send (Cmd+Enter needs a terminal that reports Cmd)"),
    ("Esc", "discard and close"),
]

HELP_TEXT = "\n".join(
    ["Global", *(f"  {k:<12} {d}" for k, d in GLOBAL_KEYS), ""]
    + ["Mail screen", *(f"  {k:<12} {d}" for k, d in MAIL_KEYS), ""]
    + ["Reading pane (focused)", *(f"  {k:<16} {d}" for k, d in PREVIEW_KEYS), ""]
    + ["Calendar screen", *(f"  {k:<12} {d}" for k, d in CALENDAR_KEYS), ""]
    + ["Priority screen", *(f"  {k:<12} {d}" for k, d in PRIORITY_KEYS), ""]
    + ["Compose screen", *(f"  {k:<20} {d}" for k, d in COMPOSE_KEYS)]
)
