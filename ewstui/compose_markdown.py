"""Turn what you write in the compose view (Markdown) into an HTML email.

You write plain text, optionally with Markdown: ``**bold**``, ``*italic*``,
``~~strike~~``, ``- lists`` / ``1. lists``, ``[links](https://...)``,
``> quotes``, `` `code` `` and fenced code blocks, and tables. It goes out as
HTML, so the recipient sees the formatting, and plain text written without
any Markdown in mind still looks as typed:

- every line break is kept (as in a plain-text email),
- bare URLs and addresses become links,
- raw HTML is shown as text, never interpreted,
- the Markdown rules that would surprise someone writing an email are
  off: "Text" over a line of ``---`` (e.g. a signature) is not a heading,
  and indented lines are not code.

The HTML gets a sans-serif font (Outlook would otherwise pick Times New
Roman) and styles for quotes, code and tables that Outlook and OWA show.
"""
from __future__ import annotations

from functools import lru_cache

from markdown_it import MarkdownIt

FONT_STYLE = "font-family: Aptos, Calibri, Arial, Helvetica, sans-serif; font-size: 11pt"
EMAIL_CSS = (
    "p { margin: 0 0 0.8em 0; }"
    " blockquote { margin: 0 0 0.8em 0; padding: 0 0 0 0.8em; border-left: 2px solid #c0c0c0; color: #505050; }"
    " code { font-family: Consolas, Menlo, monospace; background: #f2f2f2; padding: 0 2px; }"
    " pre { font-family: Consolas, Menlo, monospace; background: #f2f2f2; padding: 6px; }"
    " table { border-collapse: collapse; } th, td { border: 1px solid #c0c0c0; padding: 2px 6px; }"
)


@lru_cache(maxsize=1)
def build_markdown_parser() -> MarkdownIt:
    """Return the Markdown parser set up for writing emails.

    Returns
    -------
    MarkdownIt
        CommonMark plus tables, strike-through and link detection, with
        hard line breaks, raw HTML escaped, and setext headings and
        indented code blocks turned off.
    """
    md = MarkdownIt("commonmark", {"breaks": True, "html": False, "linkify": True, "typographer": False})
    md.enable(["table", "strikethrough", "linkify"])
    md.disable(["lheading", "code"])
    return md


def render_markdown_fragment(text: str) -> str:
    """Render Markdown to an HTML fragment (no document wrapper).

    Parameters
    ----------
    text : str
        What was written in the compose view.

    Returns
    -------
    str
        The HTML.
    """
    return build_markdown_parser().render(text)


def render_markdown_email(text: str) -> str:
    """Render what was written in the compose view as an HTML email body.

    Parameters
    ----------
    text : str
        The body as written (plain text, optionally with Markdown).

    Returns
    -------
    str
        A complete HTML document: a ``<style>`` for quotes, code and
        tables, and the rendered text in a sans-serif ``<div>``.
    """
    return (
        '<html><head><meta charset="utf-8"><style>'
        + EMAIL_CSS
        + f'</style></head><body><div style="{FONT_STYLE}">'
        + render_markdown_fragment(text)
        + "</div></body></html>"
    )
