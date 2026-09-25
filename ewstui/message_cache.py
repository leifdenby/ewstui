"""
In-memory cache of fully loaded messages, so revisiting one (or reading
one that was prefetched) needs no server round trip.

Entries are checked against the message's EWS change key, which the
message list already has: Exchange changes it whenever the item changes
(read state, flags, edits), so a stale copy is never shown — a mismatch
just means "fetch again". Memory only, for the session: nothing is written
to disk.
"""
from __future__ import annotations

from collections import OrderedDict

DEFAULT_SIZE = 200  # messages


class MessageCache:
    def __init__(self, size: int = DEFAULT_SIZE) -> None:
        self.size = size
        self._items: OrderedDict[str, object] = OrderedDict()  # message id -> MessageDetail

    def get(self, message_id: str, changekey: str | None = None):
        """The cached message, or None if absent or out of date (its change
        key differs from `changekey`, when one is given)."""
        detail = self._items.get(message_id)
        if detail is None:
            return None
        if changekey is not None and getattr(detail, "changekey", None) != changekey:
            del self._items[message_id]
            return None
        self._items.move_to_end(message_id)  # recently used
        return detail

    def put(self, message_id: str, detail) -> None:
        self._items[message_id] = detail
        self._items.move_to_end(message_id)
        while len(self._items) > self.size:
            self._items.popitem(last=False)  # least recently used

    def forget(self, message_id: str) -> None:
        self._items.pop(message_id, None)

    def __contains__(self, message_id: str) -> bool:
        return message_id in self._items

    def __len__(self) -> int:
        return len(self._items)
