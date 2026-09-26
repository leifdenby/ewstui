"""Meeting invites in the mail view: the card shown above an invite's text
(when, where, your response, what it clashes with, and your day as a strip
of half-hour cells) and the pieces the respond popup (`i`) reuses.

All pure functions over MeetingInfo / EventSummary, so they're easy to
test; the calendar lookup itself happens in the app.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from textual.content import Content

from .ews_client import DEFAULT_WORK_HOURS, EventSummary, MeetingInfo
from .room_grid import day_slots
from .widgets.message_header import DEFAULT_COLORS, LABEL_WIDTH

SLOT = timedelta(minutes=30)
COLORS = {**DEFAULT_COLORS, "error": "#e07a7a", "warning": "#d4b06a", "success": "#7aa67a"}

RESPONSES = {
    "Accept": ("Accepted ✓", "success"),
    "Tentative": ("Tentatively accepted", "warning"),
    "Decline": ("Declined", "error"),
    "Organizer": ("You're the organizer", "foreground"),
}


def conflicts(events: list[EventSummary], info: MeetingInfo) -> list[EventSummary]:
    """Your timed events overlapping the meeting, earliest first. The
    meeting's own entry (Exchange adds an invite to your calendar as
    tentative) isn't a conflict."""
    if info.start is None or info.end is None or info.is_all_day:
        return []
    return sorted(
        (
            e for e in events
            if not e.is_all_day and e.id != info.calendar_item_id and e.start < info.end and e.end > info.start
        ),
        key=lambda e: e.start,
    )


def _duration(start: datetime, end: datetime) -> str:
    minutes = int((end - start).total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    return " ".join(part for part in (f"{hours} h" if hours else "", f"{minutes} min" if minutes else "") if part)


def when_text(info: MeetingInfo) -> str:
    """"Tue 29 Sep 2026 10:00–11:00 (1 h)", or "… all day"."""
    if info.start is None:
        return "(no time given)"
    if info.is_all_day:
        last = (info.end - timedelta(days=1)) if info.end else info.start
        days = f"{info.start:%a %d %b %Y}" + (f" – {last:%a %d %b}" if last.date() > info.start.date() else "")
        return f"{days}, all day"
    end = info.end or info.start
    end_text = f"{end:%H:%M}" if end.date() == info.start.date() else f"{end:%a %d %b %H:%M}"
    return f"{info.start:%a %d %b %Y %H:%M}–{end_text} ({_duration(info.start, end)})"


def strip_hours(info: MeetingInfo, hours: tuple[time, time] = DEFAULT_WORK_HOURS) -> tuple[time, time]:
    """Your working day, widened to whole hours around the meeting."""
    start, end = hours
    if info.start and info.start.time() < start:
        start = time(info.start.hour)
    if info.end and info.start and info.end.date() == info.start.date() and info.end.time() > end:
        up = info.end.hour + (1 if info.end.minute else 0)
        end = time(up) if up < 24 else time(23, 59)
    return start, end


def day_strip(events: list[EventSummary], info: MeetingInfo, width: int,
              hours: tuple[time, time] = DEFAULT_WORK_HOURS, colors: dict | None = None) -> list[Content]:
    """Two lines: hour labels, then one cell per half hour of the day —
    · free, █ busy, ▒ the meeting, × the meeting clashing with something."""
    c = {**COLORS, **(colors or {})}
    if info.start is None or info.end is None or info.is_all_day:
        return []
    slots = day_slots(info.start.date(), strip_hours(info, hours))
    cell = 3 if len(slots) * 3 <= width else 2 if len(slots) * 2 <= width else 1
    busy = [e for e in events if not e.is_all_day and e.id != info.calendar_item_id]
    labels, cells = Content(""), Content("")
    for slot in slots:
        end = slot + SLOT
        label = f"{slot:%H}" if slot.minute == 0 else ""
        labels += Content.from_markup(f"[{c['tux-dim']}]$l[/]", l=label.ljust(cell)[:cell])
        taken = any(e.start < end and e.end > slot for e in busy)
        mine = info.start < end and info.end > slot
        if mine and taken:
            text, color = "×" * cell, c["warning"]
        elif mine:
            text, color = "▒" * cell, c["accent"]
        elif taken:
            text, color = "█" * cell, c["error"]
        else:
            text, color = "·".center(cell), c["tux-dim"]
        cells += Content.from_markup(f"[{color}]$t[/]", t=text)
    return [labels, cells]


def _label(text: str, c: dict) -> Content:
    return Content.from_markup(f"[{c['tux-dim']}]$label[/]", label=f"{text:<{LABEL_WIDTH}}")


def availability_lines(info: MeetingInfo, events: list[EventSummary] | None, width: int,
                       hours: tuple[time, time] = DEFAULT_WORK_HOURS, colors: dict | None = None) -> list[Content]:
    """"Clash" (what it overlaps in your calendar) and the day strip, or a
    "checking your calendar…" line while `events` is still None."""
    c = {**COLORS, **(colors or {})}
    if info.start is None:
        return []
    if events is None:
        return [_label("Clash", c) + Content.from_markup(f"[{c['tux-dim']}]checking your calendar…[/]")]
    clashing = conflicts(events, info)
    if clashing:
        text = ", ".join(f"{e.subject} {e.start:%H:%M}–{e.end:%H:%M}" for e in clashing)
        line = _label("Clash", c) + Content.from_markup(f"[{c['warning']}]$t[/]", t=text)
    elif info.is_all_day:
        line = _label("Clash", c) + Content.from_markup(f"[{c['tux-dim']}]all-day meeting[/]")
    else:
        line = _label("Clash", c) + Content.from_markup(f"[{c['success']}]none — you're free[/]")
    indent = Content(" " * LABEL_WIDTH)
    strip = day_strip(events, info, max(10, width - LABEL_WIDTH), hours, c)
    return [line] + [indent + part for part in strip]


def response_line(info: MeetingInfo, colors: dict | None = None) -> Content:
    c = {**COLORS, **(colors or {})}
    if info.kind == "cancellation":
        hint = "i removes it from your calendar" if info.calendar_item_id else "already gone from your calendar"
        return _label("You", c) + Content.from_markup(f"[b {c['error']}]Cancelled[/] [{c['tux-dim']}]· $h[/]", h=hint)
    text, role = RESPONSES.get(info.my_response, ("Not answered yet", "accent"))
    line = _label("You", c) + Content.from_markup(f"[b {c[role]}]$t[/]", t=text)
    if info.my_response != "Organizer":
        line += Content.from_markup(f" [{c['tux-dim']}]· i to respond[/]")
    if info.is_out_of_date:
        line += Content.from_markup(f"  [{c['warning']}](out of date: there's a newer version of this invite)[/]")
    return line


def render_invite_card(info: MeetingInfo, events: list[EventSummary] | None, width: int,
                       colors: dict | None = None) -> Content:
    """The block between an invite's header and its text: When, Where,
    You (your response), then your availability; ends with a rule."""
    c = {**COLORS, **(colors or {})}
    lines = []
    if info.start is not None:  # a cancellation of a meeting that's no longer in your calendar has none
        when = when_text(info) + (" · recurring" if info.is_recurring else "")
        lines.append(_label("When", c) + Content.from_markup(f"[b {c['foreground']}]$w[/]", w=when))
    if info.location:
        lines.append(_label("Where", c) + Content(info.location))
    lines.append(response_line(info, c))
    if info.kind == "invite":
        lines += availability_lines(info, events, width, colors=c)
    lines.append(Content.from_markup(f"[{c['tux-dim']}]$rule[/]", rule="─" * max(10, width)))
    return Content("\n").join(lines) + Content("\n\n")
