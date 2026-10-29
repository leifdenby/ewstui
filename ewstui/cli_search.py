"""
`ewstui search`: Exchange's own text search (subject, body, people) from
the command line, over the Inbox and Sent Items by default. Prints each
hit's Internet Message-ID, which `ewstui export --message` takes.

Works on the MailClient interface only (list_folders, search,
default_folder_id, sent_folder_id), so the demo backend stands in for tests.
"""
from __future__ import annotations

from datetime import datetime

from .ews_client import FolderSummary, MessageSummary
from .export import find_folder

ALL_FOLDERS = "all"


def resolve_search_folders(client, folder_paths: list[str]) -> list[FolderSummary]:
    """Work out which folders to search.

    Parameters
    ----------
    client : MailClient or DemoMailClient
        The mailbox.
    folder_paths : list[str]
        Folder paths as given with ``--folder`` (case-insensitive), or
        ``["all"]`` for every mail folder not hidden by default. Empty means
        the Inbox and Sent Items.

    Returns
    -------
    list[FolderSummary]
        The folders, without duplicates.

    Raises
    ------
    export.ExportError
        If a folder path isn't in the mailbox (with the closest paths).
    """
    folders = client.list_folders()
    if [p.casefold() for p in folder_paths] == [ALL_FOLDERS]:
        return [f for f in folders if not f.hidden_by_default]
    if not folder_paths:
        by_id = {f.id: f for f in folders}
        wanted = [client.default_folder_id(), client.sent_folder_id()]
        return [by_id[i] for i in wanted if i in by_id]
    found = {}
    for path in folder_paths:
        _, folder = find_folder(folders, path)
        found.setdefault(folder.id, folder)
    return list(found.values())


def search_folders(client, query: str, folders: list[FolderSummary], limit: int = 25) -> list[MessageSummary]:
    """Search each folder (Exchange can only search one per request) and merge the hits.

    Parameters
    ----------
    client : MailClient or DemoMailClient
        The mailbox.
    query : str
        Words to look for in the subject, body and people of each email.
    folders : list[FolderSummary]
        Where to search.
    limit : int
        The most hits to return (and to fetch per folder).

    Returns
    -------
    list[MessageSummary]
        The hits, newest first, each with its folder as ``folder_id``.
    """
    hits = []
    for folder in folders:
        hits += client.search(query, folder.id, limit=limit)
    hits.sort(key=_sort_key, reverse=True)
    return hits[:limit]


def _sort_key(message: MessageSummary) -> float:
    """Return the email's received time as a timestamp (0 if it has none), for sorting.

    Parameters
    ----------
    message : MessageSummary
        The email.

    Returns
    -------
    float
        Seconds since the epoch; naive times are taken as local.
    """
    return message.received.timestamp() if message.received else 0.0


def _format_date(when: datetime | None) -> str:
    """Format a received time in local time, minutes resolution.

    Parameters
    ----------
    when : datetime or None
        The time (tz-aware from Exchange, naive in the demo).

    Returns
    -------
    str
        ``YYYY-MM-DD HH:MM``, or ``(no date)``.
    """
    if when is None:
        return "(no date)"
    if when.tzinfo is not None:
        when = when.astimezone()
    return when.strftime("%Y-%m-%d %H:%M")


def build_hit_record(message: MessageSummary, folder_names: dict[str, str]) -> dict:
    """Build the ``--json`` record of one hit.

    Parameters
    ----------
    message : MessageSummary
        The hit.
    folder_names : dict[str, str]
        Folder names by id.

    Returns
    -------
    dict
        date, folder, from, subject, message_id, conversation_id, has_attachments.
    """
    received = message.received.astimezone() if message.received and message.received.tzinfo else message.received
    return {
        "date": received.isoformat(timespec="seconds") if received else None,
        "folder": folder_names.get(message.folder_id, message.folder_id),
        "from": message.sender,
        "subject": message.subject,
        "message_id": message.internet_message_id,
        "conversation_id": message.conversation_id,
        "has_attachments": message.has_attachments,
    }


def format_hit_lines(hits: list[MessageSummary], folder_names: dict[str, str]) -> list[str]:
    """Format the hits for the terminal: two lines each.

    The first line has the date, folder, sender and subject (with ``@``
    marking attachments), the second the Message-ID to pass to
    ``ewstui export --message``.

    Parameters
    ----------
    hits : list[MessageSummary]
        The hits, in the order to print.
    folder_names : dict[str, str]
        Folder names by id.

    Returns
    -------
    list[str]
        The lines, without newlines.
    """
    lines = []
    for m in hits:
        clip = "@" if m.has_attachments else " "
        folder = folder_names.get(m.folder_id, m.folder_id or "?")
        lines.append(f"{_format_date(m.received)}  {folder:<12.12}  {m.sender:<32.32} {clip} {m.subject}")
        lines.append(f"    {m.internet_message_id or '(no Message-ID)'}")
    return lines
