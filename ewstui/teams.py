"""Handing a meeting over to Microsoft Teams to schedule (Ctrl+T in the new
event and event-from-email screens).

Exchange (EWS) can't make a Teams meeting — the join link comes from the
Teams service — so ewstui opens Teams' own "New meeting" form, filled in
with the title, times, attendees and description, through Teams' deep link
(msteams:/l/meeting/new?...). You send it from Teams, which creates the
meeting, its join link and the invites. Without the Teams app, the same
form opens on teams.microsoft.com in the browser.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import webbrowser
from datetime import datetime
from urllib.parse import urlencode, quote

log = logging.getLogger(__name__)

WEB = "https://teams.microsoft.com/l/meeting/new"
APP = "msteams:/l/meeting/new"
MAX_CONTENT = 1500  # characters of description: links get too long for Teams beyond that


def _iso(when: datetime) -> str:
    """Local wall-clock time with its UTC offset, as Teams wants it
    ("2026-10-14T10:00:00+02:00")."""
    return when.astimezone().isoformat(timespec="seconds")


def meeting_query(subject: str, start: datetime, end: datetime, attendees: list[str] | None = None,
                  content: str = "") -> str:
    params = {"subject": subject, "startTime": _iso(start), "endTime": _iso(end)}
    if attendees:
        params["attendees"] = ",".join(attendees)
    if content:
        params["content"] = content if len(content) <= MAX_CONTENT else content[: MAX_CONTENT - 1] + "…"
    return urlencode(params, quote_via=quote)


def meeting_link(subject: str, start: datetime, end: datetime, attendees: list[str] | None = None,
                 content: str = "", app: bool = True) -> str:
    """The deep link to Teams' "New meeting" form, filled in."""
    return f"{APP if app else WEB}?{meeting_query(subject, start, end, attendees, content)}"


# A Teams meeting's join link, as Outlook/Teams write it into the invite.
_JOIN = re.compile(r"https://teams\.(?:microsoft|live)\.com/(?:l/meetup-join|meet)/[^\s<>\"')\]]+", re.I)


def find_join_link(text: str) -> str | None:
    """The first Teams join link in an event's (or invite's) text."""
    m = _JOIN.search(text or "")
    return m[0] if m else None


def join_meeting(link: str) -> str:
    """Open a Teams meeting's join link in the Teams app (macOS), else the
    browser (which offers to open Teams). Returns "app" or "browser"."""
    if sys.platform == "darwin" and "/l/meetup-join/" in link:
        app_link = re.sub(r"^https://teams\.microsoft\.com/", "msteams:/", link, flags=re.I)
        try:
            if subprocess.run(["open", app_link], capture_output=True, timeout=10).returncode == 0:
                return "app"
        except (OSError, subprocess.SubprocessError):
            log.info("open msteams: failed", exc_info=True)
    webbrowser.open(link)
    return "browser"


def open_in_teams(subject: str, start: datetime, end: datetime, attendees: list[str] | None = None,
                  content: str = "") -> str:
    """Open the form in the Teams app (macOS), else in the browser.
    Returns "app" or "browser" (where it opened)."""
    if sys.platform == "darwin":
        link = meeting_link(subject, start, end, attendees, content, app=True)
        try:
            done = subprocess.run(["open", link], capture_output=True, timeout=10)
            if done.returncode == 0:
                return "app"
            log.info("open msteams: failed (%s): %s", done.returncode, done.stderr.decode(errors="replace").strip())
        except (OSError, subprocess.SubprocessError):
            log.info("open msteams: failed", exc_info=True)
    webbrowser.open(meeting_link(subject, start, end, attendees, content, app=False))
    return "browser"
