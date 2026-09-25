"""
Colour themes, after the tuxedo todo.txt TUI (github.com/webstonehq/tuxedo):
its default "Muted Slate" and its "Nord" palettes, as Textual themes.

Standard Textual variables ($background, $panel, $border, $accent, ...)
are set from tuxedo's palette; the extra tuxedo roles are exposed as
custom variables, usable in CSS as $tux-dim etc. and from Python via
`app.theme_variables["tux-dim"]` (for Rich text in the header, status bar
and help overlay).
"""
from __future__ import annotations

from textual.theme import Theme

DEFAULT_THEME = "muted-slate"


def _theme(name: str, p: dict[str, str]) -> Theme:
    return Theme(
        name=name,
        dark=True,
        background=p["bg"],
        surface=p["panel"],
        panel=p["panel"],
        foreground=p["fg"],
        primary=p["accent"],
        secondary=p["project"],
        accent=p["accent"],
        warning=p["due"],
        error=p["overdue"],
        success=p["pri_c"],
        variables={
            # Textual roles
            "border": p["border"],
            "border-blurred": p["border"],
            "block-cursor-background": p["cursor"],
            "block-cursor-foreground": p["fg"],
            "block-cursor-text-style": "none",
            "block-cursor-blurred-background": p["selection"],
            "block-cursor-blurred-foreground": p["fg"],
            "block-cursor-blurred-text-style": "none",
            "block-hover-background": p["selection"],
            "input-selection-background": p["selection"],
            "scrollbar": p["border"],
            "scrollbar-hover": p["dim"],
            "scrollbar-active": p["accent"],
            "scrollbar-background": p["panel"],
            "scrollbar-corner-color": p["panel"],
            "footer-background": p["statusbar"],
            # tuxedo roles
            "tux-dim": p["dim"],
            "tux-key": p["context"],  # keys in the help overlay
            "tux-unread": p["accent"],
            "tux-project": p["project"],
            "tux-statusbar": p["statusbar"],
            "tux-status-fg": p["status_fg"],
            "tux-mode-fg": p["mode_fg"],
            "tux-mode-bg": p["mode_bg"],
            "tux-done": p["done"],
        },
    )


MUTED_SLATE = _theme(
    "muted-slate",
    {
        "bg": "#1a1d23", "panel": "#1f232b", "border": "#2a2f38", "fg": "#c8ccd4", "dim": "#6b7280",
        "accent": "#8aa9c9", "cursor": "#3a4150", "selection": "#2f3947", "statusbar": "#252a33",
        "status_fg": "#a8b0bc", "mode_fg": "#1a1d23", "mode_bg": "#8aa9c9", "pri_c": "#7aa67a",
        "project": "#7fb3a8", "context": "#c89a6e", "due": "#d4b06a", "overdue": "#e07a7a", "done": "#5a6270",
    },
)

NORD = _theme(
    "nord",
    {
        "bg": "#2e3440", "panel": "#3b4252", "border": "#434c5e", "fg": "#d8dee9", "dim": "#6c7686",
        "accent": "#88c0d0", "cursor": "#434c5e", "selection": "#434c5e", "statusbar": "#3b4252",
        "status_fg": "#d8dee9", "mode_fg": "#2e3440", "mode_bg": "#88c0d0", "pri_c": "#a3be8c",
        "project": "#a3be8c", "context": "#d08770", "due": "#ebcb8b", "overdue": "#bf616a", "done": "#4c566a",
    },
)

THEMES = {t.name: t for t in (MUTED_SLATE, NORD)}
# Short names accepted in the config / --theme.
ALIASES = {"muted": "muted-slate", "slate": "muted-slate", "muted-slate": "muted-slate", "nord": "nord"}
