"""
Single source of truth for the vim-ish bindings, used by the help
overlay (press `?`) and the status bar hints. The actual key -> action
wiring lives on each widget/screen (Textual's BINDINGS), since that's how
Textual scopes key handling — this module is documentation, not a
dispatch table.
"""

GLOBAL_KEYS = [
    ("j / k", "move down / up"),
    ("g / G", "top / bottom"),
    ("l / Enter", "open (moves into the next pane)"),
    ("h", "back to the previous pane"),
    ("Tab / S-Tab", "next / previous pane"),
    ("1 / 2 / 3", "mail / priority / calendar"),
    ("Ctrl+l", "refresh (also every few minutes)"),
    ("w", "compose a new message"),
    ("u", "undo delete / archive / move"),
    ("Esc", "cancel / close"),
    ("?", "this help"),
    ("q", "quit"),
]

MAIL_KEYS = [
    ("r / R", "reply / reply all"),
    ("d", "delete (to Deleted Items)"),
    ("A", "archive"),
    ("m", "move to folder (type to filter)"),
    ("Space", "toggle read / unread"),
    ("t", "thread view on / off"),
    ("v", "attachments: save + open"),
    ("p / P", "add to priority list (+ note / none)"),
]

PREVIEW_KEYS = [
    ("j / k", "scroll a line"),
    ("Ctrl+d / u", "half a page down / up"),
    ("Ctrl+f / b", "page down / up (Space too)"),
    ("g / G", "top / bottom of the message"),
    ("h", "back to the message list"),
]

CALENDAR_KEYS = [
    ("n", "new event"),
    ("d", "delete event"),
    ("[ / ]", "earlier / later"),
    ("f", "find a free meeting room"),
]

ROOM_KEYS = [
    ("[ / ]", "previous / next day"),
    ("h / l", "earlier / later slot"),
    ("j / k", "room"),
    ("v", "select rooms × times"),
    ("Enter", "book cell or selection"),
    ("/", "find more rooms"),
    ("r", "refresh availability"),
    ("t", "today"),
]

PRIORITY_KEYS = [
    ("A-Z", "set priority (current or selection)"),
    ("V", "visual selection, then a letter"),
    ("x", "toggle complete"),
    ("d", "remove from the list"),
    ("Enter / o", "jump to the email"),
]

COMPOSE_KEYS = [
    ("Tab", "next field"),
    ("Ctrl+s", "send / save"),
    ("Cmd+Enter", "send (if the terminal passes Cmd)"),
    ("Esc", "discard and close"),
]

# The help overlay's two columns (tuxedo-style), top to bottom.
HELP_COLUMNS = [
    [("GLOBAL", GLOBAL_KEYS), ("READING PANE", PREVIEW_KEYS), ("COMPOSE", COMPOSE_KEYS)],
    [("MAIL", MAIL_KEYS), ("CALENDAR", CALENDAR_KEYS), ("FIND A ROOM", ROOM_KEYS), ("PRIORITY", PRIORITY_KEYS)],
]

# Short hints for the status bar, by what has focus.
STATUS_HINTS = {
    "folders": "j/k folder · l open · Tab next pane · ? help",
    "messages": "l open · r reply · d delete · m move · A archive · t threads · ? help",
    "preview": "j/k scroll · Ctrl+d/u half page · h back · ? help",
    "calendar": "j/k event · n new · f find a room · [ ] earlier/later · ? help",
    "priority": "A-Z priority · V select · x done · Enter jump to email · ? help",
}
