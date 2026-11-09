"""Check against the real server what Exchange does with a reply's body:
does it add the original email below what you wrote (as it does for a
forward), and does an HTML body stay HTML?

    uv run --directory ~/git-repos/ewstui python scripts/probe_reply_body.py [ewstui options]

Saves a reply to the newest Inbox email as a *draft* (nothing is sent),
reads the draft back, reports, and deletes the draft again. Then does the
same with the draft's body replaced afterwards (how ewstui saves a reply
answered inline) and checks nothing of the original is left.
"""
from __future__ import annotations

import sys

from exchangelib.properties import HTMLBody

from ewstui.__main__ import _connect
from ewstui.config import config_from_args

MARKER = "EWSTUI-PROBE-7f3a"


def main(argv: list[str]) -> int:
    """Run the probe.

    Parameters
    ----------
    argv : list[str]
        ewstui connection options (e.g. ``--account work``).

    Returns
    -------
    int
        Exit code: 0 if the probe ran, 1 if it couldn't connect.
    """
    account = _connect(config_from_args(argv))
    if account is None:
        return 1
    original = next(iter(account.inbox.all().order_by("-datetime_received")[:1]))
    snippet = " ".join((original.text_body or "").split())[:40]
    print(f"replying (as a draft) to {original.subject!r}")
    print(f"  original text starts: {snippet!r}")
    print(f"  original body is {'HTML' if isinstance(original.body, HTMLBody) else 'plain text'}")

    draft = original.create_reply(subject=f"Re: {original.subject}", body=HTMLBody(f"<!-- {MARKER}-comment --><p><b>{MARKER}</b></p>"))
    saved = draft.save(account.drafts)
    saved_id = getattr(saved, "id", None) or next(
        d.id for d in account.drafts.all().order_by("-datetime_created")[:5] if MARKER in str(d.body or "")
    )
    try:
        item = account.drafts.get(id=saved_id)
        body = str(item.body or "")
        text = " ".join((item.text_body or "").split())
        print(f"draft body is {'HTML' if isinstance(item.body, HTMLBody) else 'plain text'}, {len(body)} chars")
        print(f"  marker kept as bold: {'<b>' + MARKER in body}")
        print(f"  HTML comment kept:   {MARKER + '-comment' in body}")
        print(f"  original appended:   {bool(snippet) and snippet in text}")
        tail = text.split(MARKER, 1)[-1].strip()
        print(f"  text after marker:   {tail[:200]!r}")
    finally:
        account.drafts.get(id=saved_id).delete()
        print("draft deleted")

    # How ewstui saves an inline-answered reply: create the reply draft,
    # then replace its whole body. Nothing of the original should remain.
    print("inline reply: replacing the reply draft's body")
    saved = original.create_reply(subject=f"Re: {original.subject}", body=HTMLBody("<p></p>")).save(account.drafts)
    try:
        item = account.drafts.get(id=saved.id)
        item.body = HTMLBody(f"<p><b>{MARKER}</b></p>")
        item.save(update_fields=["body"])
        item = account.drafts.get(id=saved.id)
        text = " ".join((item.text_body or "").split())
        print(f"  body is ours only:   {bool(snippet) and snippet not in text and MARKER in text}")
        print(f"  still a reply:       in_reply_to={getattr(item, 'in_reply_to', None)!r}")
    finally:
        account.drafts.get(id=saved.id).delete()
        print("draft deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
