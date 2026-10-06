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
    ("o / Enter", "open folder / email"),
    ("l / h", "next / previous pane"),
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
    ("f", "forward (with its attachments)"),
    ("d", "delete (to Deleted Items)"),
    ("A", "archive"),
    ("m", "move to folder (type to filter)"),
    ("Space", "toggle read / unread"),
    ("t", "thread view on / off"),
    ("v", "attachments: save + open"),
    ("U", "open a link from the email in the browser"),
    ("e", "all To / Cc recipients (e again folds)"),
    ("i", "answer an invite / remove a cancelled meeting"),
    ("c", "calendar event from the email (save-the-dates)"),
    ("/", "search this folder (fuzzy, as you type)"),
    ("p / P", "add to priority list (+ note / none); p on a listed one edits its note"),
    ("V", "select several (j/k), then d/A/m/Space/P"),
    ("Esc", "cancel the selection"),
]

PREVIEW_KEYS = [
    ("j / k", "scroll a line"),
    ("Ctrl+d / u", "half a page down / up"),
    ("Ctrl+f / b", "page down / up (Space too)"),
    ("g / G", "top / bottom of the message"),
    ("h", "back to the message list"),
    ("U", "links in this email"),
    ("e", "all To / Cc recipients"),
    ("r R f", "reply / all, forward"),
    ("d A m", "delete, archive, move"),
    ("P / p v", "priority (+ note), attachments"),
    ("i", "answer the invite"),
    ("c", "calendar event from the email"),
]

CALENDAR_KEYS = [
    ("n", "new event"),
    ("d", "delete event"),
    ("[ / ]", "previous / next week"),
    ("f", "find a free meeting room"),
    ("T", "join its Teams meeting / make it one in Teams"),
]

ROOM_KEYS = [
    ("[ / ]", "previous / next day"),
    ("h / l", "earlier / later slot"),
    ("j / k", "room"),
    ("v", "select rooms × times"),
    ("Enter", "book cell or selection"),
    ("/", "find more rooms"),
    ("a", "add attendees (shown under the rooms)"),
    ("r", "refresh availability"),
    ("t", "today"),
]

PRIORITY_KEYS = [
    ("A-Z", "set priority (current or selection)"),
    ("V", "visual selection, then a letter"),
    ("Esc", "cancel the selection"),
    ("x", "toggle complete"),
    ("d", "remove from the list"),
    ("Enter / o", "jump to the email"),
]

FOLDER_KEYS = [
    ("V", "pick up a folder: j/k move it, Enter/V put down"),
    ("Esc", "put it back where it was"),
    ("H", "hide / unhide the folder"),
    ("/", "find a folder by name"),
    (".", "show hidden folders (dimmed)"),
]

COMPOSE_KEYS = [
    ("Tab", "next field"),
    ("Ctrl+s", "send / save"),
    ("Cmd+Enter", "send (if the terminal passes Cmd)"),
    ("Esc", "save to Drafts and close"),
    ("Ctrl+O", "your calendar beside the email"),
    ("Ctrl+B / F", "calendar: earlier / later weeks"),
]

# The help overlay's two columns (tuxedo-style), per view: the view's own
# keys on the left (first, when stacked on a narrow terminal), the keys
# that work everywhere on the right.
HELP_COLUMNS = {
    "mail": [
        [("MAIL", MAIL_KEYS), ("READING PANE", PREVIEW_KEYS)],
        [("GLOBAL", GLOBAL_KEYS), ("FOLDERS", FOLDER_KEYS), ("COMPOSE", COMPOSE_KEYS)],
    ],
    "priority": [
        [("PRIORITY", PRIORITY_KEYS)],
        [("GLOBAL", GLOBAL_KEYS)],
    ],
    "calendar": [
        [("CALENDAR", CALENDAR_KEYS), ("FIND A ROOM", ROOM_KEYS)],
        [("GLOBAL", GLOBAL_KEYS)],
    ],
}

# Put in front of the mail hints when the email shown is a meeting item.
INVITE_HINTS = {"invite": "i answer invite", "cancellation": "i remove from calendar"}

# Short hints for the status bar, by what has focus.
STATUS_HINTS = {
    # {open}: the key the layout makes natural — "l" (right) when the next
    # pane is to the right, "o" when the email sits below the list.
    "folders": "j/k folder · {open} open · / find a folder · V move · H hide · . show hidden · ? help",
    "moving": "moving folder · j/k move it · Enter/V put down · Esc cancel",
    "search": "type to search · Enter/↓ to the results · Esc end the search",
    "find-folder": "type part of a folder's name · Enter open the top match · ↓ choose · Esc all folders",
    "event": "new event · h/l j/k [ ] pick a day · v select days · n next date · Tab next field · Ctrl+S create · Ctrl+T in Teams · Esc close",
    "messages": "{open} open · r reply · f forward · d delete · m move · A archive · / search · t threads · ? help",
    "preview": "j/k scroll · Ctrl+d/u half page · r reply · m move · A archive · P priority · h back · ? help",
    "calendar": "j/k event · n new · f find a room · T open in Teams · [ ] earlier/later · ? help",
    "priority": "A-Z priority · V select · x done · Enter jump to email · ? help",
    "visual": "{count} selected · j/k extend · d delete · A archive · m move · Space read · P priority · Esc cancel",
}
