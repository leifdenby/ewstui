"""A demo mailbox with a folder tree, for the export / folders tests."""
from __future__ import annotations

from ewstui.demo_backend import DemoMailClient
from ewstui.ews_client import FolderSummary


def tree_client() -> DemoMailClient:
    """Build the demo mailbox plus a ``Collaborations`` tree.

    ::

        Collaborations              (no emails)
          Alice                     m1 (attachment), m2 (your reply s1 is in Sent Items)
          Empty                     (no emails)
          Past                      (no emails)
            Bob                     m4
        Sync Issues                 (hidden)
          Old conflicts             a1 (hidden through its parent)

    Returns
    -------
    DemoMailClient
        The mailbox; its folder counts match the emails in each folder.
    """
    client = DemoMailClient()
    inbox = {m.id: m for m in client._messages["inbox"]}
    sync = next(i for i, f in enumerate(client._folders) if f.id == "sync")
    client._folders[sync + 1:sync + 1] = [
        FolderSummary(id="sync-old", name="Old conflicts", total_count=1, unread_count=0, depth=1,
                      parent_id="sync"),
    ]
    client._folders += [
        FolderSummary(id="collab", name="Collaborations", total_count=0, unread_count=0, depth=0),
        FolderSummary(id="alice", name="Alice", total_count=2, unread_count=1, depth=1, parent_id="collab"),
        FolderSummary(id="empty", name="Empty", total_count=0, unread_count=0, depth=1, parent_id="collab"),
        FolderSummary(id="past", name="Past", total_count=0, unread_count=0, depth=1, parent_id="collab"),
        FolderSummary(id="bob", name="Bob", total_count=1, unread_count=0, depth=2, parent_id="past"),
    ]
    client._messages["alice"] = [inbox["m1"], inbox["m2"]]
    client._messages["bob"] = [inbox["m4"]]
    client._messages["sync-old"] = list(client._messages["archive"])
    return client


def add_folder(client: DemoMailClient, folder: FolderSummary, messages: list) -> None:
    """Add a folder to the demo mailbox after its parent's last descendant.

    Parameters
    ----------
    client : DemoMailClient
        The mailbox.
    folder : FolderSummary
        The new folder; ``parent_id`` and ``depth`` must be set.
    messages : list[MessageDetail]
        The emails in it (its ``total_count`` is set from them).
    """
    folders = client._folders
    at = next(i for i, f in enumerate(folders) if f.id == folder.parent_id) + 1
    while at < len(folders) and folders[at].depth >= folder.depth:
        at += 1
    folder.total_count = len(messages)
    folders.insert(at, folder)
    client._messages[folder.id] = list(messages)
