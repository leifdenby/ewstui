"""Time `export --with-sent`'s reply lookup against the real server, old
way (paging through Sent Items) versus new (a ConversationId restriction
on Sent Items), and check both find the same replies.

    uv run --project ~/git-repos/ewstui python ~/git-repos/ewstui/scripts/time_with_sent.py FOLDER [--new-only] [ewstui options]

FOLDER and every non-empty folder under it are timed, one line each, as
they finish. Only reads; nothing is exported. --new-only skips the old
scan (which can take minutes per folder on old folders).
"""
from __future__ import annotations

import sys
import time

from ewstui import export
from ewstui.__main__ import _open_mail_client
from ewstui.config import config_from_args


def scan_sent_replies(client, sent_id: str, folder_messages: list) -> list:
    """The old --with-sent lookup: page through Sent Items, newest first,
    until SENT_LOOKBACK before the folder's oldest email.

    Parameters
    ----------
    client : MailClient
        The mailbox.
    sent_id : str
        The Sent Items folder id.
    folder_messages : list[MessageSummary]
        The emails in the folder.

    Returns
    -------
    list[MessageSummary]
        The replies, newest first.
    """
    conversations = {m.conversation_id for m in folder_messages if m.conversation_id}
    dates = [m.received for m in folder_messages if m.received]
    if not conversations or not dates:
        return []
    cutoff = min(dates) - export.SENT_LOOKBACK
    replies = []
    for m in export.iter_all(client, sent_id):
        if m.received and m.received < cutoff:
            break
        if m.conversation_id in conversations:
            replies.append(m)
    return replies


def main(argv: list[str]) -> int:
    """Run the timing.

    Parameters
    ----------
    argv : list[str]
        FOLDER, optionally --new-only, then ewstui connection options.

    Returns
    -------
    int
        Exit code: 0 if both lookups agreed everywhere, 1 otherwise.
    """
    if not argv or argv[0].startswith("-"):
        print(__doc__, file=sys.stderr)
        return 2
    path, rest = argv[0], argv[1:]
    new_only = "--new-only" in rest
    rest = [a for a in rest if a != "--new-only"]
    opened = _open_mail_client(config_from_args(rest))
    if opened is None:
        return 1
    client, _ = opened
    sent_id = client.sent_folder_id()
    total_old = total_new = 0.0
    agreed = True
    print(f"{'emails':>6} {'replies':>7} {'old s':>8} {'new s':>8}  folder", flush=True)
    for full, _, folder in export.subtree(client.list_folders(), path):
        if not folder.total_count:
            continue
        in_folder = list(export.iter_all(client, folder.id))
        t0 = time.perf_counter()
        _, new = export._sent_replies(client, in_folder)
        t_new = time.perf_counter() - t0
        total_new += t_new
        old_s = "-"
        if not new_only:
            t0 = time.perf_counter()
            old = scan_sent_replies(client, sent_id, in_folder)
            t_old = time.perf_counter() - t0
            total_old += t_old
            old_s = f"{t_old:8.1f}"
            if [m.id for m in old] != [m.id for m in new]:
                agreed = False
                print(f"  MISMATCH in {full}: old {len(old)} replies, new {len(new)}", flush=True)
        print(f"{len(in_folder):6d} {len(new):7d} {old_s:>8} {t_new:8.1f}  {full}", flush=True)
    if new_only:
        print(f"total: new {total_new:.1f} s")
        return 0
    print(f"total: old {total_old:.1f} s, new {total_new:.1f} s")
    print("both lookups found the same replies" if agreed else "the lookups DISAGREE (see MISMATCH lines)")
    return 0 if agreed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
