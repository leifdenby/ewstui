"""The header above an email in the reading pane: subject, then From / To /
Cc / Date in an aligned label column.

People are shown by name where Exchange gives one ("Jane Doe <jane@…>").
A long To or Cc list is folded onto one line: the names that fit, then
"+37 more". `e` (or a click on it) unfolds every list to one person per
line with their address.
"""
from __future__ import annotations

from datetime import datetime

from textual.content import Content

LABEL_WIDTH = 6  # "From  ", "To    ", ...
DEFAULT_COLORS = {"foreground": "#c8ccd4", "tux-dim": "#6b7280", "accent": "#8aa9c9"}


def display_name(address: str, names: dict[str, str]) -> str:
    return names.get(address) or address


def fold(addresses: list[str], names: dict[str, str], width: int) -> tuple[list[str], int]:
    """The names that fit on one line of `width` columns (always at least
    one) and how many didn't, leaving room for the " +N more" note."""
    shown: list[str] = []
    used = 0
    for i, address in enumerate(addresses):
        name = display_name(address, names)
        left = len(addresses) - i - 1
        note = len(f"  +{left} more · e") if left else 0
        needed = used + (2 if shown else 0) + len(name)
        if shown and needed + note > width:
            break
        shown.append(name)
        used = needed
    return shown, len(addresses) - len(shown)


def _label(text: str, c: dict[str, str]) -> Content:
    return Content.from_markup(f"[{c['tux-dim']}]$label[/]", label=f"{text:<{LABEL_WIDTH}}")


def _person(address: str, names: dict[str, str], c: dict[str, str]) -> Content:
    """"Jane Doe <jane@corp>" with the name bold and the address dim, or
    just the address when there's no name."""
    name = names.get(address)
    if not name:
        return Content.from_markup(f"[b {c['foreground']}]$a[/]", a=address)
    return Content.from_markup(f"[b {c['foreground']}]$n[/] [{c['tux-dim']}]<$a>[/]", n=name, a=address)


def _recipients(label: str, addresses: list[str], names: dict[str, str], width: int, expanded: bool,
                c: dict[str, str]) -> list[Content]:
    if not addresses:
        return []
    if expanded or len(addresses) == 1:
        indent = Content(" " * LABEL_WIDTH)
        lines = [(_label(label, c) if i == 0 else indent) + _person(a, names, c) for i, a in enumerate(addresses)]
        if len(addresses) > 1:
            lines[0] = lines[0] + Content.from_markup(f"  [{c['tux-dim']}]$n people[/]", n=len(addresses))
        return lines
    shown, hidden = fold(addresses, names, max(20, width - LABEL_WIDTH))
    line = _label(label, c) + Content.from_markup(f"[{c['foreground']}]$names[/]", names=", ".join(shown))
    if hidden:
        line = line + Content.from_markup(f"  [@click=app.toggle_recipients]+$n more[/][{c['tux-dim']}] · e[/]", n=hidden)
    return [line]


def render_header(msg, width: int, expanded: bool = False, colors: dict[str, str] | None = None) -> Content:
    """Subject, From, To, Cc, Date (and an attachments note), then a rule
    the width of the pane."""
    c = {**DEFAULT_COLORS, **(colors or {})}
    names = getattr(msg, "names", {}) or {}
    when = msg.received.strftime("%a %d %b %Y %H:%M") if isinstance(msg.received, datetime) else "(no date)"
    lines = [
        Content.from_markup(f"[b {c['foreground']}]$s[/]", s=msg.subject),
        Content(""),
        _label("From", c) + _person(msg.sender, names, c),
    ]
    lines += _recipients("To", msg.to, names, width, expanded, c) or [_label("To", c) + Content.from_markup(f"[{c['tux-dim']}](none)[/]")]
    lines += _recipients("Cc", msg.cc, names, width, expanded, c)
    lines.append(_label("Date", c) + Content(when))
    if msg.has_attachments:
        lines.append(Content(" " * LABEL_WIDTH) + Content.from_markup(f"[{c['tux-dim']}]has attachments · v to save / open[/]"))
    lines.append(Content.from_markup(f"[{c['tux-dim']}]$rule[/]", rule="─" * max(10, width)))
    return Content("\n").join(lines) + Content("\n\n")
