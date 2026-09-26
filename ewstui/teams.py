"""Handing a meeting over to Microsoft Teams to schedule (Ctrl+T in the new
event and event-from-email screens).

Exchange (EWS) can't make a Teams meeting — the join link comes from the
Teams service — so ewstui opens Teams' own "New meeting" form, filled in
with the title, times, attendees and description, through Teams' deep link
(msteams:/l/meeting/new?...). You send it from Teams, which creates the
meeting, its join link and the invites. The Teams app on macOS; on Linux
(no official app since 2022) whatever handles msteams: links, e.g.
teams-for-linux; otherwise the same form on teams.microsoft.com in the
browser.
"""
from __future__ import annotations

import logging
import re
import shutil
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


def _run(command: list[str]):
    return subprocess.run(command, capture_output=True, timeout=10)


def _linux_msteams_handler() -> bool:
    """Is an app registered for msteams: links (e.g. teams-for-linux)?
    There's no official Teams app for Linux any more."""
    if not (shutil.which("xdg-mime") and shutil.which("xdg-open")):
        return False
    try:
        done = _run(["xdg-mime", "query", "default", "x-scheme-handler/msteams"])
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and bool(done.stdout.strip())


def _open_app_link(link: str) -> bool:
    """Open an msteams: link in a Teams app: the Teams app on macOS, on
    Linux whatever is registered for msteams: links. False if there's none
    or it didn't work (then the browser it is)."""
    if sys.platform == "darwin":
        command = ["open", link]
    elif sys.platform.startswith("linux") and _linux_msteams_handler():
        command = ["xdg-open", link]
    else:
        return False
    try:
        done = _run(command)
    except (OSError, subprocess.SubprocessError):
        log.info("%s failed", command[0], exc_info=True)
        return False
    if done.returncode != 0:
        log.info("%s msteams: failed (%s): %s", command[0], done.returncode,
                 (done.stderr or b"").decode(errors="replace").strip())
    return done.returncode == 0


def join_meeting(link: str) -> str:
    """Open a Teams meeting's join link in a Teams app (see _open_app_link),
    else the browser (which offers to open Teams). Returns "app" or "browser"."""
    if "/l/meetup-join/" in link:
        app_link = re.sub(r"^https://teams\.microsoft\.com/", "msteams:/", link, flags=re.I)
        if _open_app_link(app_link):
            return "app"
    webbrowser.open(link)
    return "browser"


def open_in_teams(subject: str, start: datetime, end: datetime, attendees: list[str] | None = None,
                  content: str = "") -> str:
    """Open the form in a Teams app (see _open_app_link), else in the
    browser. Returns "app" or "browser" (where it opened)."""
    if _open_app_link(meeting_link(subject, start, end, attendees, content, app=True)):
        return "app"
    webbrowser.open(meeting_link(subject, start, end, attendees, content, app=False))
    return "browser"
