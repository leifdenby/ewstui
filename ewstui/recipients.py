"""Recipients as written in the To / Cc fields (and attendee fields): one
per line — or several on a line, separated by commas or semicolons — each
either a plain address or "Name <address>".
"""
from __future__ import annotations

import re

_ANGLE = re.compile(r"<\s*([^<>\s]+@[^<>\s]+)\s*>")


def parse_recipients(text: str) -> list[str]:
    """The addresses, in order, each once: "Casey <c@x>\\nb@x; d@x" ->
    ["c@x", "b@x", "d@x"]."""
    out: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        # a comma or semicolon inside "Doe, Jane <j@x>" is part of the name
        parts = [line] if _ANGLE.search(line) and line.count("<") == 1 else re.split(r"[,;]", line)
        for part in parts:
            m = _ANGLE.search(part)
            address = (m[1] if m else part).strip().strip(",;").strip()
            if address and address.casefold() not in seen:
                seen.add(address.casefold())
                out.append(address)
    return out


def format_recipients(addresses: list[str], names: dict[str, str] | None = None) -> str:
    """One per line, with the name where it's known: "Casey Colleague
    <colleague@corp.example>"."""
    names = names or {}
    return "\n".join(f"{names[a]} <{a}>" if names.get(a) and names[a] != a else a for a in addresses)


def format_person(name: str, address: str) -> str:
    return f"{name} <{address}>" if name and name != address else address


def reply_recipients(detail, reply_all: bool, me: str) -> tuple[list[str], list[str]]:
    """(To, Cc) to prefill: the sender; for reply-all also everyone the
    email went to (its To in To, its Cc in Cc) — without you, and each once."""
    mine = me.casefold()
    seen: set[str] = set()

    def keep(addresses: list[str]) -> list[str]:
        out = []
        for a in addresses:
            if a and "@" in a and a.casefold() != mine and a.casefold() not in seen:
                seen.add(a.casefold())
                out.append(a)
        return out

    if not reply_all:
        return [detail.sender], []
    to = keep([detail.sender, *detail.to])
    cc = keep(list(detail.cc))
    return (to or [detail.sender]), cc
