"""Check against the real server that EWS accepts a ConversationId
restriction across all mail folders (what `export --thread` relies on).

    uv run --directory ~/git-repos/ewstui python scripts/probe_conversation_filter.py [ewstui options]

Takes the newest Inbox email that has a conversation id, then lists every
email in that conversation, in any mail folder.
"""
from __future__ import annotations

import sys

from exchangelib.folders import FolderCollection

from ewstui.__main__ import _connect
from ewstui.config import config_from_args
from ewstui.ews_client import _is_mail_folder


def main(argv: list[str]) -> int:
    """Run the probe.

    Parameters
    ----------
    argv : list[str]
        ewstui connection options (e.g. ``--account work``).

    Returns
    -------
    int
        Exit code: 0 if the restriction worked, 1 otherwise.
    """
    account = _connect(config_from_args(argv))
    if account is None:
        return 1
    item = next(iter(account.inbox.all().order_by("-datetime_received").only("subject", "conversation_id")[:1]))
    conversation_id = item.conversation_id.id
    print(f"conversation of {item.subject!r}: {conversation_id[:24]}...")

    folders, stack = [], list(account.msg_folder_root.children)
    while stack:
        f = stack.pop()
        if _is_mail_folder(f):
            folders.append(f)
            stack.extend(f.children)
    qs = FolderCollection(account=account, folders=folders).filter(conversation_id=item.conversation_id)
    try:
        hits = list(qs.only("subject", "datetime_received", "parent_folder_id"))
    except Exception as e:  # noqa: BLE001 - this is what the probe is for
        print(f"ConversationId restriction failed: {type(e).__name__}: {e}")
        return 1
    for h in hits:
        print(f"  {h.datetime_received:%Y-%m-%d %H:%M}  {h.subject}")
    print(f"OK: {len(hits)} emails in the conversation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
