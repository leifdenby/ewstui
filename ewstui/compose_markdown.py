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

import re
from functools import lru_cache

from bs4 import BeautifulSoup, Comment, Tag
from markdown_it import MarkdownIt
from markdownify import MarkdownConverter

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


def is_hidden_element(tag: Tag) -> bool:
    """Tell whether an HTML element is hidden (``display:none``, Outlook's ``mso-hide:all``).

    Parameters
    ----------
    tag : Tag
        The element.

    Returns
    -------
    bool
        True when its inline style or ``hidden`` attribute hides it.
    """
    style = (tag.get("style") or "").replace(" ", "").lower()
    return tag.has_attr("hidden") or "display:none" in style or "mso-hide:all" in style


def is_single_line_paragraph(tag: Tag) -> bool:
    """Tell whether a ``<p>`` is one line rather than a paragraph.

    Outlook writes every line as ``<p class=MsoNormal>`` with no margin
    (a blank line is ``<p>&nbsp;</p>``); others do the same with
    ``margin:0``.

    Parameters
    ----------
    tag : Tag
        A ``<p>`` element.

    Returns
    -------
    bool
        True for Outlook's / margin-less paragraphs.
    """
    classes = " ".join(tag.get("class") or []).lower()
    style = (tag.get("style") or "").replace(" ", "").lower()
    return classes.startswith("mso") or bool(re.search(r"(^|;)margin:0(px|cm|pt|in)?(;|$)", style))


def convert_html_to_markdown(html: str) -> str:
    """Convert an email's HTML body to Markdown, for quoting it in a reply.

    Bold, italics, links, lists, headings, quotes and tables carry over;
    colours and fonts can't. Hidden parts (``<head>``, styles, scripts,
    ``display:none`` preheaders) are dropped, and Outlook's one-line
    paragraphs become lines rather than blank-line-separated paragraphs.

    Parameters
    ----------
    html : str
        The HTML body.

    Returns
    -------
    str
        Markdown, without leading/trailing blank lines and with at most
        one blank line in a row.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["head", "style", "script", "title", "template", "noscript", "xml"]):
        tag.decompose()
    for tag in soup.find_all(is_hidden_element):
        if not tag.decomposed:
            tag.decompose()
    for p in soup.find_all("p"):
        if is_single_line_paragraph(p):
            p.append(soup.new_tag("br"))
            p.unwrap()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    # Line breaks in the HTML source are just spaces (the reply is sent with
    # every line break kept, so they'd become real ones).
    for string in soup.find_all(string=True):
        if "\n" in string and not string.find_parent("pre"):
            string.replace_with(re.sub(r"\s*\n\s*", " ", str(string)))
    text = MarkdownConverter(
        heading_style="ATX", bullets="-", strong_em_symbol="*", escape_misc=False, escape_underscores=False,
    ).convert_soup(soup)
    # A single leading space is left over from source line breaks between
    # elements (nested list indentation is two or more).
    lines = [re.sub(r"^ (?=\S)", "", line.replace("\xa0", " ").rstrip()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")


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
