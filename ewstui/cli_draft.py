"""`ewstui draft`: an email written outside ewstui — in an editor, by a
script or an AI assistant — as a Markdown file, saved to Drafts (or sent)
from the command line. It can be a new email, a reply or a forward.

The file is the email's text in Markdown (sent as HTML, as from the
compose view), optionally under a header block between ``---`` lines::

    ---
    to: Jane Doe <jane@corp.example>, bob@corp.example
    cc: team@corp.example
    subject: Q3 budget
    in-reply-to: <abc123@mail.corp.example>
    reply-all: yes
    ---
    Hi Jane,

    Thanks — **looks good** to me.

A list can also continue on the next lines (``  - bob@corp.example``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .recipients import format_recipients, parse_recipients, reply_recipients

KEYS = ("to", "cc", "subject", "in-reply-to", "reply-all", "forward", "quote")
_TRUE = {"yes", "y", "true", "on", "1"}
_FALSE = {"no", "n", "false", "off", "0"}


class DraftError(Exception):
    """The draft file or options don't make a sendable email (or the email
    it answers can't be found)."""


@dataclass
class DraftSpec:
    """What to write: the fields of the header block, and the body.

    ``in_reply_to`` / ``forward`` are the Internet Message-ID of the email
    answered / forwarded (as ``ewstui search`` and ``export`` print it).
    ``quote`` (replies only): Exchange adds the original below the text;
    off, the reply is exactly the body.
    """

    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    subject: str = ""
    body: str = ""
    in_reply_to: str | None = None
    reply_all: bool = False
    forward: str | None = None
    quote: bool = True


@dataclass
class DraftResult:
    """What was saved or sent, to report back."""

    kind: str  # "new", "reply", "reply-all" or "forward"
    subject: str
    to: list[str]
    cc: list[str]
    original: object | None = None  # the MessageDetail answered / forwarded
    names: dict[str, str] = field(default_factory=dict)


def parse_bool(key: str, value: str) -> bool:
    """Read a yes/no header value.

    Parameters
    ----------
    key : str
        The header's name, for the error message.
    value : str
        As written: yes/no, true/false, on/off, 1/0 (any case).

    Returns
    -------
    bool

    Raises
    ------
    DraftError
        If it is none of those.
    """
    word = value.strip().casefold()
    if word in _TRUE:
        return True
    if word in _FALSE:
        return False
    raise DraftError(f"{key}: expected yes or no, not {value.strip()!r}")


def normalize_message_id(value: str) -> str:
    """Return an Internet Message-ID in Exchange's form, ``<...>``.

    Parameters
    ----------
    value : str
        With or without the angle brackets.

    Returns
    -------
    str
    """
    value = value.strip()
    return value if value.startswith("<") else f"<{value}>"


def split_header_block(text: str) -> tuple[dict[str, str], str]:
    """Separate the ``---`` header block from the body.

    Parameters
    ----------
    text : str
        The whole file. Without a ``---`` first line it is all body.

    Returns
    -------
    tuple[dict[str, str], str]
        The headers (lowercase name, ``_`` read as ``-``; continuation
        lines joined with newlines) and the body.

    Raises
    ------
    DraftError
        On an unclosed block, a line that isn't ``name: value``, an
        unknown or repeated name.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        raise DraftError("the header block starting with --- on line 1 has no closing --- line") from None
    headers: dict[str, str] = {}
    key = None
    for n, line in enumerate(lines[1:end], 2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1].isspace() or line.startswith("- "):
            if key is None:
                raise DraftError(f"line {n}: continues no header: {line.strip()!r}")
            item = line.strip().removeprefix("- ").strip()
            headers[key] = f"{headers[key]}\n{item}".strip("\n")
            continue
        name, sep, value = line.partition(":")
        key = name.strip().casefold().replace("_", "-")
        if not sep or key not in KEYS:
            raise DraftError(f"line {n}: expected one of {', '.join(KEYS)} as 'name: value', got {line.strip()!r}")
        if key in headers:
            raise DraftError(f"line {n}: {key} is given twice")
        headers[key] = value.strip()
    return headers, "\n".join(lines[end + 1:])


def parse_draft(text: str) -> DraftSpec:
    """Read a draft file (see the module docstring).

    Parameters
    ----------
    text : str
        The file's content.

    Returns
    -------
    DraftSpec

    Raises
    ------
    DraftError
        If the header block is malformed.
    """
    headers, body = split_header_block(text)
    spec = DraftSpec(body=body.strip("\n").rstrip())
    spec.to = parse_recipients(headers.get("to", ""))
    spec.cc = parse_recipients(headers.get("cc", ""))
    spec.subject = " ".join(headers.get("subject", "").split())
    if headers.get("in-reply-to"):
        spec.in_reply_to = normalize_message_id(headers["in-reply-to"])
    if headers.get("forward"):
        spec.forward = normalize_message_id(headers["forward"])
    if "reply-all" in headers:
        spec.reply_all = parse_bool("reply-all", headers["reply-all"])
    if "quote" in headers:
        spec.quote = parse_bool("quote", headers["quote"])
    return spec


def check_draft(spec: DraftSpec, send: bool) -> None:
    """Refuse combinations that can't be right, before connecting.

    Parameters
    ----------
    spec : DraftSpec
    send : bool
        Sending (rather than saving to Drafts) also needs recipients and,
        for a new email, a subject.

    Raises
    ------
    DraftError
    """
    if spec.in_reply_to and spec.forward:
        raise DraftError("give in-reply-to or forward, not both")
    if not spec.in_reply_to and (spec.reply_all or not spec.quote):
        raise DraftError("reply-all and quote only go with in-reply-to")
    if not spec.body and not spec.forward:
        raise DraftError("the email has no text")
    if not spec.in_reply_to and not spec.forward and not spec.subject:
        raise DraftError("a new email needs a subject")
    if send and not spec.in_reply_to and not (spec.to or spec.cc):
        raise DraftError("who to? Give to (or cc) to send it")


def build_reply_subject(subject: str) -> str:
    """Return "Re: <subject>", unless it already starts with Re: / AW: / SV:.

    Parameters
    ----------
    subject : str

    Returns
    -------
    str
    """
    return subject if re.match(r"(?i)\s*(re|aw|sv)\s*:", subject) else f"Re: {subject}"


def build_forward_subject(subject: str) -> str:
    """Return "Fwd: <subject>", unless it already starts with Fw: / Fwd:.

    Parameters
    ----------
    subject : str

    Returns
    -------
    str
    """
    return subject if re.match(r"(?i)\s*(fw|fwd)\s*:", subject) else f"Fwd: {subject}"


def fetch_original(client, internet_message_id: str):
    """Find and fetch the email a reply or forward is about.

    Parameters
    ----------
    client : MailClient or DemoMailClient
    internet_message_id : str

    Returns
    -------
    tuple[str, str, MessageDetail]
        Its folder id, EWS item id and details.

    Raises
    ------
    DraftError
        If no mail folder has it.
    """
    found = client.find_message(internet_message_id)
    if found is None:
        raise DraftError(f"no email with Message-ID {internet_message_id} in any mail folder")
    return found.folder_id, found.message_id, client.get_message(found.folder_id, found.message_id)


def submit_draft(client, spec: DraftSpec, send: bool = False, me: str = "") -> DraftResult:
    """Save the email to Drafts, or send it.

    A reply stays a reply (threaded, Outlook shows it as answering the
    email); without to / cc it goes to the sender (everyone, for
    reply-all, as the compose view prefills them). A forward carries the
    original and its attachments below the text.

    Parameters
    ----------
    client : MailClient or DemoMailClient
    spec : DraftSpec
        Checked with ``check_draft`` first.
    send : bool
        Send instead of saving to Drafts.
    me : str
        Your address, left out of reply-all's recipients.

    Returns
    -------
    DraftResult

    Raises
    ------
    DraftError
        On an invalid spec or a missing original. Exchange's own errors
        pass through.
    """
    check_draft(spec, send)
    to, cc, subject = list(spec.to), list(spec.cc), spec.subject
    if spec.in_reply_to:
        folder_id, item_id, original = fetch_original(client, spec.in_reply_to)
        if not to and not cc:
            to, cc = reply_recipients(original, spec.reply_all, me)
        subject = subject or build_reply_subject(original.subject)
        fields = {"subject": subject, "body": spec.body, "to": to, "cc": cc, "reply_all": spec.reply_all,
                  "full_body": not spec.quote}
        (client.reply if send else client.save_reply_draft)(folder_id, item_id, **fields)
        kind = "reply-all" if spec.reply_all else "reply"
        return DraftResult(kind, subject, to, cc, original, dict(getattr(original, "names", None) or {}))
    if spec.forward:
        folder_id, item_id, original = fetch_original(client, spec.forward)
        if send and not (to or cc):
            raise DraftError("who to? Give to (or cc) to forward it")
        subject = subject or build_forward_subject(original.subject)
        (client.forward if send else client.save_forward_draft)(folder_id, item_id, subject=subject,
                                                                body=spec.body, to=to, cc=cc)
        return DraftResult("forward", subject, to, cc, original)
    if send:
        client.send_mail(to, subject, spec.body, cc=cc)
    else:
        client.save_draft(to, subject, spec.body, cc=cc)
    return DraftResult("new", subject, to, cc)


def format_result_lines(result: DraftResult, send: bool) -> list[str]:
    """Describe what was done, for the terminal.

    Parameters
    ----------
    result : DraftResult
    send : bool
        Whether it was sent (else saved to Drafts).

    Returns
    -------
    list[str]
    """
    what = {"new": "New email", "reply": "Reply", "reply-all": "Reply to all", "forward": "Forward"}[result.kind]
    lines = [f"{what} {'sent' if send else 'saved to Drafts'}: {result.subject}"]
    if result.original is not None:
        o = result.original
        when = f", {o.received:%Y-%m-%d %H:%M}" if getattr(o, "received", None) else ""
        lines.append(f"  {'answering' if result.kind != 'forward' else 'forwarding'}: "
                     f"{o.subject} (from {o.sender}{when})")
    for label, addresses in (("To", result.to), ("Cc", result.cc)):
        if addresses:
            lines.append(f"  {label}: " + format_recipients(addresses, result.names).replace("\n", ", "))
    if not send:
        lines.append("Review and send it from Drafts (ewstui, Outlook or OWA).")
    return lines
