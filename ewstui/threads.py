"""
Group a folder's messages into conversation threads (the `t` thread view).

Messages are grouped by their EWS conversation id; a message without one
is a thread of its own. Threads are ordered by their newest message
(newest first, like the flat list), and each thread is shown as a tree
(mutt-style): the first message, with replies nested under the message
they answer. The parent comes from the conversation index — a reply's
index is its parent's plus 5 bytes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .ews_client import MessageSummary

# Reply/forward prefixes to drop when naming a thread: English, Danish
# (SV/VS), German (AW/WG), Dutch (Antw), any number of them.
_PREFIX = re.compile(r"^\s*((re|sv|aw|antw|fw|fwd|vs|wg)\s*(\[\d+\])?\s*:\s*)+", re.IGNORECASE)


def topic(subject: str) -> str:
    return _PREFIX.sub("", subject).strip() or subject


@dataclass
class Thread:
    key: str  # conversation id, or the message id for a lone message
    messages: list[MessageSummary]  # oldest first

    @property
    def latest(self) -> MessageSummary:
        return self.messages[-1]

    @property
    def unread(self) -> int:
        return sum(not m.is_read for m in self.messages)

    @property
    def topic(self) -> str:
        return topic(self.messages[0].subject)

    def senders(self, limit: int = 2) -> str:
        """Distinct senders, most recent first: 'Anna, Leif +1'."""
        names: list[str] = []
        for m in reversed(self.messages):
            if m.sender not in names:
                names.append(m.sender)
        shown = ", ".join(names[:limit])
        return shown + (f" +{len(names) - limit}" if len(names) > limit else "")


INDEX_HEADER = 22  # bytes; each reply level adds 5
INDEX_STEP = 5


def _parent(m: MessageSummary, by_index: dict[bytes, MessageSummary]) -> MessageSummary | None:
    """Nearest listed ancestor, by stripping 5-byte reply levels off the
    conversation index (the direct parent may not be in this folder)."""
    index = m.conversation_index
    if not index:
        return None
    probe = index[:-INDEX_STEP]
    while len(probe) >= INDEX_HEADER:
        if probe in by_index and by_index[probe] is not m:
            return by_index[probe]
        probe = probe[:-INDEX_STEP]
    return None


def tree_rows(thread: Thread, newest_on_top: bool = False) -> list[tuple[MessageSummary, str]]:
    """(message, tree prefix) in display order: the thread's first message
    unprefixed, replies under the message they answer with ├─ / └─ / │
    guides, siblings oldest first. Messages whose parent isn't listed
    (e.g. an original that isn't in this folder) hang under the first one.

    `newest_on_top` flips the tree vertically: replies are ordered by their
    branch's latest activity and the rows reversed, so the newest message
    is the top row and the original the bottom one, with ┌─ guides running
    down from each reply to the message it answers.
    """
    msgs = thread.messages  # oldest first
    by_index = {m.conversation_index: m for m in msgs if m.conversation_index}
    children: dict[str, list[MessageSummary]] = {m.id: [] for m in msgs}
    root = msgs[0]
    for m in msgs[1:]:
        # A parent always has a strictly shorter index, so links can't form
        # a cycle and every message is reached from the root.
        parent = _parent(m, by_index) or root
        children[parent.id].append(m)

    if newest_on_top:
        # Order each level by its branch's latest message, oldest first; once
        # the rows are reversed, the most recently active branch is on top
        # and, recursively, the newest message is the very first row.
        latest: dict[str, float] = {}

        def branch_latest(m: MessageSummary) -> float:
            if m.id not in latest:
                latest[m.id] = max([_when(m)] + [branch_latest(k) for k in children[m.id]])
            return latest[m.id]

        for kids in children.values():
            kids.sort(key=branch_latest)

    rows: list[tuple[MessageSummary, str]] = []

    def walk(m: MessageSummary, prefix: str, last: bool, top: bool) -> None:
        rows.append((m, "" if top else prefix + ("└─ " if last else "├─ ")))
        below = "" if top else prefix + ("   " if last else "│  ")
        kids = children[m.id]
        for i, kid in enumerate(kids):
            walk(kid, below, i == len(kids) - 1, False)

    walk(root, "", True, True)
    if newest_on_top:
        # Upside down: a last child's └─ becomes ┌─ (the line now runs down to
        # its parent); ├─ and │ read the same either way.
        return [(m, prefix.replace("└─", "┌─")) for m, prefix in reversed(rows)]
    return rows


def _when(m: MessageSummary) -> float:
    return m.received.timestamp() if m.received else float("-inf")


def build_threads(messages: list[MessageSummary]) -> list[Thread]:
    groups: dict[str, list[MessageSummary]] = {}
    for m in messages:
        groups.setdefault(m.conversation_id or f"msg:{m.id}", []).append(m)
    threads = [Thread(key, sorted(msgs, key=_when)) for key, msgs in groups.items()]
    threads.sort(key=lambda t: _when(t.latest), reverse=True)
    return threads
