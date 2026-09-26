"""Matching emails against what you type in the search bar (`/`): each
word of the query has to fuzzy-match (fzf-style, see
folder_picker.fuzzy_score) the subject or the sender, closely enough that
"budg" finds "Q3 budget review" but a few scattered letters don't match
everything. Best matches first.
"""
from __future__ import annotations

from .folder_picker import fuzzy_score


def _word_score(word: str, fields: list[str]) -> int | None:
    """The best score of `word` in any field, if it matches well: roughly,
    most of its letters consecutive or at word starts."""
    best = None
    for field in fields:
        score = fuzzy_score(word, field)
        if score is not None and score >= 2 * len(word) - 1 and (best is None or score > best):
            best = score
    return best


def message_score(query: str, message, names: dict[str, str] | None = None) -> int | None:
    """How well `message` matches (higher is better), or None."""
    fields = [message.subject or "", message.sender or ""]
    name = (names or {}).get(message.sender)
    if name:
        fields.append(name)
    total = 0
    for word in query.split():
        score = _word_score(word, fields)
        if score is None:
            return None
        total += score
    return total


def rank(messages: list, query: str, names: dict[str, str] | None = None) -> list:
    """The messages matching `query`, best first (newest first among equals);
    all of them, in their order, for an empty query."""
    if not query.strip():
        return list(messages)
    scored = [(message_score(query, m, names), i, m) for i, m in enumerate(messages)]
    return [m for score, _, m in sorted((s for s in scored if s[0] is not None), key=lambda s: (-s[0], s[1]))]
