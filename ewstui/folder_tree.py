"""Your arrangement of the folder pane: the order you've put folders in
(V, then j/k) and which ones are hidden (H), on top of the tree Exchange
returns. Pure functions over FolderSummary lists; saved per account in the
config file (see config_file.FolderPrefs).

Folders are moved among their siblings only, subfolders going along. The
order is one list of folder ids for the whole tree: siblings sort by their
place in it; folders not in it (new ones) come after, in server order.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .ews_client import FolderSummary


@dataclass
class FolderPrefs:
    order: list[str] = field(default_factory=list)  # folder ids, the whole tree in pane order
    hidden: list[str] = field(default_factory=list)  # hidden with H
    shown: list[str] = field(default_factory=list)  # hidden by default, but unhidden with H


def arrange(folders: list[FolderSummary], order: list[str]) -> list[FolderSummary]:
    """The whole tree, depth first, siblings in `order`."""
    place = {fid: i for i, fid in enumerate(order)}
    server = {f.id: i for i, f in enumerate(folders)}
    ids = set(server)
    children: dict[str | None, list[FolderSummary]] = defaultdict(list)
    for f in folders:
        children[f.parent_id if f.parent_id in ids else None].append(f)

    def key(f: FolderSummary) -> int:
        return place.get(f.id, len(order) + server[f.id])

    out: list[FolderSummary] = []

    def walk(f: FolderSummary) -> None:
        out.append(f)
        for child in sorted(children[f.id], key=key):
            walk(child)

    for top in sorted(children[None], key=key):
        walk(top)
    return out


def hidden_ids(folders: list[FolderSummary], prefs: FolderPrefs) -> set[str]:
    """Folders not listed (unless showing hidden ones): hidden with H,
    hidden by default and not unhidden, or inside a hidden folder."""
    hidden, shown = set(prefs.hidden), set(prefs.shown)
    out: set[str] = set()
    for f in arrange(folders, prefs.order):  # parents before children
        if f.id in hidden or (f.hidden_by_default and f.id not in shown) or f.parent_id in out:
            out.add(f.id)
    return out


def hidden_itself(folder: FolderSummary, prefs: FolderPrefs) -> bool:
    """Hidden on its own account (not just because its parent is)."""
    return folder.id in prefs.hidden or (folder.hidden_by_default and folder.id not in prefs.shown)


def toggle_hidden(folder: FolderSummary, prefs: FolderPrefs) -> FolderPrefs:
    """H: hide a listed folder, or unhide a hidden one."""
    hidden = [fid for fid in prefs.hidden if fid != folder.id]
    shown = [fid for fid in prefs.shown if fid != folder.id]
    if hidden_itself(folder, prefs):
        if folder.hidden_by_default:
            shown.append(folder.id)
    elif not folder.hidden_by_default:  # a default-hidden one is hidden again by leaving `shown`
        hidden.append(folder.id)
    return FolderPrefs(order=list(prefs.order), hidden=hidden, shown=shown)


def move(folders: list[FolderSummary], order: list[str], folder_id: str, step: int,
         skip: set[str] = frozenset()) -> list[str]:
    """The order with `folder_id` moved `step` (-1 up / +1 down) past its
    neighbour at the same level, ignoring siblings in `skip` (hidden ones
    not on screen). Unchanged at either end."""
    tree = arrange(folders, order)
    by_id = {f.id: f for f in tree}
    moving = by_id.get(folder_id)
    if moving is None:
        return list(order) or [f.id for f in tree]
    siblings = [f.id for f in tree if f.parent_id == moving.parent_id and (f.id == folder_id or f.id not in skip)]
    i = siblings.index(folder_id)
    j = i + step
    if not 0 <= j < len(siblings):
        return [f.id for f in tree]
    siblings[i], siblings[j] = siblings[j], siblings[i]
    # Re-rank: those siblings in their new order, everything else as it was.
    rank = {fid: n for n, fid in enumerate(f.id for f in tree)}
    slots = sorted(rank[fid] for fid in siblings)
    for slot, fid in zip(slots, siblings):
        rank[fid] = slot
    return [f.id for f in arrange(folders, sorted(rank, key=rank.get))]
