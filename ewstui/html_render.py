"""Render an HTML email body as styled terminal text (a Textual `Content`).

The terminal can't change fonts or sizes, but most of the rest of an HTML
email carries over: bold, italics, underline, strike-through, text colour
(and inline highlights), links (clickable in terminals that support OSC 8
hyperlinks), headings, bulleted and numbered lists, quoted replies, rules
and paragraph breaks. Tables are flattened to one row per line.

Colours are made readable on the current theme: greys, black and white
(which emails use for "normal" text and which would vanish on one theme
or the other) fall back to the theme's text colour, and the remaining
colours are lightened or darkened until they stand out from the
background. Only the standard library's HTML parser is used, so broken
HTML degrades instead of failing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import reduce
from html.parser import HTMLParser

from textual.color import Color
from textual.content import Content, Span
from textual.style import Style

# Elements whose content is never shown.
SKIPPED_TAGS = {"head", "style", "script", "title", "template", "noscript", "xml"}
# Elements that never have content or an end tag.
VOID_TAGS = {"br", "img", "hr", "meta", "link", "input", "col", "area", "base", "wbr", "source", "param", "embed"}
# Blocks: how many line breaks they put before and after themselves
# (1: own line, 2: a blank line in between).
BLOCK_BREAKS = {
    "p": 2, "h1": 2, "h2": 2, "h3": 2, "h4": 2, "h5": 2, "h6": 2,
    "blockquote": 2, "pre": 2, "table": 1, "ul": 1, "ol": 1, "dl": 1,
    "div": 1, "li": 1, "tr": 1, "dt": 1, "dd": 1, "section": 1, "article": 1,
    "header": 1, "footer": 1, "center": 1, "address": 1, "figure": 1, "form": 1,
    "fieldset": 1, "main": 1, "nav": 1, "aside": 1, "caption": 1, "tbody": 1,
    "thead": 1, "tfoot": 1, "body": 1, "html": 1,
}
# Inline elements that may carry a highlight (background colour). Blocks
# (table cells, divs) use backgrounds for page layout, which would only
# paint big coloured slabs in a terminal, so their backgrounds are ignored.
HIGHLIGHT_TAGS = {"span", "font", "mark", "b", "strong", "i", "em", "u", "a"}
TAG_STYLES = {
    "b": Style(bold=True), "strong": Style(bold=True),
    "i": Style(italic=True), "em": Style(italic=True), "cite": Style(italic=True),
    "var": Style(italic=True), "dfn": Style(italic=True),
    "u": Style(underline=True), "ins": Style(underline=True),
    "s": Style(strike=True), "strike": Style(strike=True), "del": Style(strike=True),
    "h1": Style(bold=True, underline=True), "h2": Style(bold=True), "h3": Style(bold=True),
    "h4": Style(bold=True), "h5": Style(bold=True), "h6": Style(bold=True),
    "th": Style(bold=True), "dt": Style(bold=True), "mark": Style(reverse=True),
}
BULLETS = ["•", "◦", "▪"]
LIST_INDENT = "  "
QUOTE_BAR = "│ "
MIN_CONTRAST = 3.0  # WCAG ratio a text colour must reach against the background
_WHITESPACE = re.compile(r"[ \t\r\n\f]+")  # not \xa0: Outlook's &nbsp; lines are meant to show


@dataclass
class _Element:
    """An open element and what it contributes to the text inside it."""

    tag: str
    style: Style | None = None
    hidden: bool = False
    breaks: int = 0
    quote: bool = False  # a quoted block: its lines get a bar
    list_kind: str | None = None  # "ul" / "ol" for lists
    counter: int = 0  # items seen so far in an "ol"
    attrs: dict[str, str] = field(default_factory=dict)


def parse_css_declarations(css: str) -> dict[str, str]:
    """Parse an inline `style` attribute into lower-cased property -> value.

    Parameters
    ----------
    css : str
        The attribute's text, e.g. ``"color: red; font-weight: bold"``.

    Returns
    -------
    dict[str, str]
        The declarations; later ones win, as in CSS.
    """
    out: dict[str, str] = {}
    for declaration in css.split(";"):
        name, sep, value = declaration.partition(":")
        if sep:
            out[name.strip().lower()] = value.replace("!important", "").strip().lower()
    return out


def has_left_border(css: dict[str, str]) -> bool:
    """Tell whether inline CSS draws a left border, as Outlook's quotes do.

    Outlook marks quoted text with
    ``<div style="border:none;border-left:solid #0000FF 1.5pt;padding:...">``
    rather than a ``<blockquote>``.

    Parameters
    ----------
    css : dict[str, str]
        Declarations from `parse_css_declarations`.

    Returns
    -------
    bool
        True when `border-left` (or `border-left-style`) is set to a
        visible line.
    """
    value = css.get("border-left", "") or css.get("border-left-style", "")
    if not value or "none" in value or "hidden" in value:
        return False
    width = css.get("border-left-width", "")
    return not re.fullmatch(r"0(\.0+)?(px|pt|cm|em)?", width.strip())


def compute_luminance(color: Color) -> float:
    """Return the WCAG relative luminance of a colour.

    Parameters
    ----------
    color : Color
        The colour.

    Returns
    -------
    float
        0 for black up to 1 for white.
    """

    def linearize(channel: int) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * linearize(color.r) + 0.7152 * linearize(color.g) + 0.0722 * linearize(color.b)


def compute_contrast(a: Color, b: Color) -> float:
    """Return the WCAG contrast ratio between two colours.

    Parameters
    ----------
    a, b : Color
        The two colours.

    Returns
    -------
    float
        From 1 (identical) to 21 (black on white).
    """
    la, lb = sorted((compute_luminance(a), compute_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def parse_color(value: str) -> Color | None:
    """Parse a CSS / HTML colour value, or return None if it isn't one.

    Parameters
    ----------
    value : str
        E.g. ``"#c00"``, ``"red"``, ``"rgb(200, 0, 0)"``, or a bare
        ``"CC0000"`` as old ``<font color>`` attributes sometimes have.

    Returns
    -------
    Color | None
        The colour, or None for anything unparseable (``inherit``,
        ``windowtext``, ...).
    """
    value = value.strip().lower()
    if re.fullmatch(r"[0-9a-f]{6}|[0-9a-f]{3}", value):
        value = "#" + value
    try:
        color = Color.parse(value)
    except Exception:  # noqa: BLE001 - Textual raises its own ColorParseError and others
        return None
    return color if color.a > 0 else None


def is_neutral(color: Color) -> bool:
    """Tell whether a colour is a grey, black or white (no real hue).

    Parameters
    ----------
    color : Color
        The colour.

    Returns
    -------
    bool
        True when the channels are within a small range of each other.
    """
    return max(color.r, color.g, color.b) - min(color.r, color.g, color.b) < 40


def adjust_color_for_background(color: Color, background: Color) -> Color | None:
    """Make an email's text colour readable on the terminal's background.

    Parameters
    ----------
    color : Color
        The colour the email asks for.
    background : Color
        The theme's background colour.

    Returns
    -------
    Color | None
        None for greys / black / white (the theme's own text colour is
        used instead), otherwise the colour blended towards white (on a
        dark background) or black (on a light one) until it reaches
        MIN_CONTRAST.
    """
    if is_neutral(color):
        return None
    target = Color(255, 255, 255) if compute_luminance(background) < 0.5 else Color(0, 0, 0)
    adjusted = color
    for step in range(1, 11):
        if compute_contrast(adjusted, background) >= MIN_CONTRAST:
            break
        adjusted = color.blend(target, step / 10)
    return adjusted


def pick_text_color_for(background: Color) -> Color:
    """Return black or white, whichever reads better on `background`.

    Parameters
    ----------
    background : Color
        A highlight colour.

    Returns
    -------
    Color
        Black or white.
    """
    black, white = Color(0, 0, 0), Color(255, 255, 255)
    return black if compute_contrast(black, background) >= compute_contrast(white, background) else white


class _ContentBuilder(HTMLParser):
    """Walks the HTML, collecting text and styled spans."""

    def __init__(self, background: Color, link_color: Color | None, dim_color: Color | None) -> None:
        """Start an empty document.

        Parameters
        ----------
        background : Color
            The theme's background, which colours are adjusted against.
        link_color : Color | None
            Colour for links (the theme accent), or None for the text colour.
        dim_color : Color | None
            Colour for decorations (bullets, quote bars, rules).
        """
        super().__init__(convert_charrefs=True)
        self.background = background
        self.link_color = link_color
        self.dim_style = Style(foreground=dim_color) if dim_color else Style(dim=True)
        self.parts: list[str] = []
        self.spans: list[Span] = []
        self.length = 0
        self.stack: list[_Element] = []
        self.pending_gap = ""  # space(s) to put before the next text on the line
        self.pending_gap_style: Style | None = None
        self.pending_bullet: str | None = None

    # -- output ----------------------------------------------------------

    def _tail(self, n: int) -> str:
        """Return the last `n` characters written so far."""
        out = ""
        for part in reversed(self.parts):
            out = part + out
            if len(out) >= n:
                break
        return out[-n:]

    def _at_line_start(self) -> bool:
        """Tell whether the next character starts a new line."""
        return self.length == 0 or self._tail(1) == "\n"

    def _write(self, text: str, style: Style | None = None) -> None:
        """Append raw text, styled with `style` if given."""
        if not text:
            return
        if style is not None:
            self.spans.append(Span(self.length, self.length + len(text), style))
        self.parts.append(text)
        self.length += len(text)

    def _write_line_prefix(self) -> None:
        """Start a line: quote bars, list indentation, and a pending bullet."""
        quotes = sum(1 for e in self.stack if e.quote)
        depth = sum(1 for e in self.stack if e.list_kind)
        if quotes:
            self._write(QUOTE_BAR * quotes, self.dim_style)
        if self.pending_bullet is not None:
            self._write(LIST_INDENT * max(0, depth - 1))
            self._write(self.pending_bullet, self.dim_style)
            self.pending_bullet = None
        elif depth:
            self._write(LIST_INDENT * depth)

    def _break(self, n: int) -> None:
        """End the current line so that `n` - 1 blank lines follow (at most).

        Nothing is written at the very start of the document, and line
        breaks already there count, so nested blocks don't pile up blank
        lines.
        """
        self.pending_gap = ""
        if self.length == 0:
            return
        have = len(self._tail(n)) - len(self._tail(n).rstrip("\n"))
        self._write("\n" * (n - have))

    def _current_style(self) -> Style | None:
        """Combine the styles of every open element."""
        styles = [e.style for e in self.stack if e.style is not None]
        return reduce(lambda a, b: a + b, styles) if styles else None

    def _emit_text(self, text: str, style: Style | None = None) -> None:
        """Write text (whitespace already handled) after any pending gap.

        Parameters
        ----------
        text : str
            The text.
        style : Style | None
            Style to use instead of the open elements' style.
        """
        if not text:
            return
        if self._at_line_start():
            self._write_line_prefix()
        elif self.pending_gap:
            self._write(self.pending_gap, self.pending_gap_style)
        self.pending_gap = ""
        self._write(text, style or self._current_style())

    def _request_gap(self, gap: str) -> None:
        """Ask for `gap` before the next text on this line (never at its start).

        A single space keeps the style where it was in the HTML (so it is
        underlined between two linked words, but not before a link).
        """
        if not self._at_line_start() and len(gap) > len(self.pending_gap):
            self.pending_gap = gap
            self.pending_gap_style = self._current_style() if gap == " " else None

    # -- parser callbacks ------------------------------------------------

    def _hidden(self) -> bool:
        return any(e.hidden for e in self.stack)

    def _in_pre(self) -> bool:
        return any(e.tag == "pre" for e in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open an element: break the line for blocks and push its style."""
        attributes = {k.lower(): (v or "") for k, v in attrs}
        if tag in VOID_TAGS:
            self._handle_void(tag, attributes)
            return
        if tag == "p" and self.stack and self.stack[-1].tag == "p":
            self.handle_endtag("p")  # <p> implicitly closes an open <p>
        if tag == "li":  # an <li> implicitly closes the previous one
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i].list_kind:
                    break
                if self.stack[i].tag == "li":
                    self.handle_endtag("li")
                    break
        element = _Element(tag=tag, attrs=attributes)
        css = parse_css_declarations(attributes.get("style", ""))
        element.hidden = (
            tag in SKIPPED_TAGS
            or "hidden" in attributes
            or css.get("display") == "none"
            or css.get("mso-hide") == "all"
        )
        element.quote = tag == "blockquote" or (tag == "div" and has_left_border(css))
        element.breaks = self._block_breaks(tag, attributes, css)
        element.style = self._element_style(tag, attributes, css)
        if tag in ("ul", "ol"):
            element.list_kind = tag
            start = attributes.get("start", "")
            element.counter = int(start) - 1 if start.isdigit() else 0
        visible = not self._hidden() and not element.hidden
        if element.breaks and visible:
            self._break(element.breaks)
        if tag == "li" and visible:
            self._start_list_item()
        elif tag in ("td", "th"):
            self._request_gap("  ")  # cells in a row: two spaces apart
        self.stack.append(element)

    def _start_list_item(self) -> None:
        """Queue the bullet / number for the item about to start."""
        lists = [e for e in self.stack if e.list_kind]
        if not lists:
            self.pending_bullet = BULLETS[0] + " "
            return
        current = lists[-1]
        if current.list_kind == "ol":
            current.counter += 1
            self.pending_bullet = f"{current.counter}. "
        else:
            self.pending_bullet = BULLETS[(len(lists) - 1) % len(BULLETS)] + " "

    def _block_breaks(self, tag: str, attributes: dict[str, str], css: dict[str, str]) -> int:
        """Line breaks around a block element (0 for inline ones)."""
        breaks = BLOCK_BREAKS.get(tag, 0)
        if tag == "p":
            # Outlook writes every line as <p class=MsoNormal> with no margin
            # (blank lines are <p>&nbsp;</p>), as do others with margin:0.
            classes = attributes.get("class", "").lower()
            margin = css.get("margin", css.get("margin-bottom", ""))
            if classes.startswith("mso") or re.fullmatch(r"0(px|cm|pt|in)?( 0(px|cm|pt|in)?)*", margin):
                breaks = 1
        if tag in ("ul", "ol", "p") and any(e.list_kind for e in self.stack):
            breaks = 1  # a nested list / paragraph sits right under its item
        return breaks

    def _element_style(self, tag: str, attributes: dict[str, str], css: dict[str, str]) -> Style | None:
        """The style an element adds: from its tag, attributes and CSS."""
        style = TAG_STYLES.get(tag, Style())
        if tag == "a" and attributes.get("href"):
            href = attributes["href"]
            style += Style(underline=True, foreground=self.link_color)
            if re.match(r"(?i)(https?|mailto|ftp):", href):
                style += Style(link=href)
        weight = css.get("font-weight", "")
        if weight in ("bold", "bolder") or (weight.isdigit() and int(weight) >= 600):
            style += Style(bold=True)
        elif weight in ("normal", "lighter") or (weight.isdigit() and int(weight) < 600):
            style += Style(bold=False)
        if css.get("font-style") in ("italic", "oblique"):
            style += Style(italic=True)
        elif css.get("font-style") == "normal":
            style += Style(italic=False)
        decoration = css.get("text-decoration", css.get("text-decoration-line", ""))
        if "underline" in decoration:
            style += Style(underline=True)
        if "line-through" in decoration:
            style += Style(strike=True)
        if decoration == "none" and tag == "a":
            style += Style(underline=False)
        foreground = parse_color(css.get("color", "") or (attributes.get("color", "") if tag == "font" else ""))
        if foreground is not None:
            adjusted = adjust_color_for_background(foreground, self.background)
            if adjusted is not None:
                style += Style(foreground=adjusted)
        if tag in HIGHLIGHT_TAGS:
            highlight = parse_color(css.get("background-color", "") or css.get("background", "").split(" ")[0])
            if highlight is not None and not is_neutral(highlight):
                style += Style(background=highlight, foreground=pick_text_color_for(highlight))
        return style if style != Style() else None

    def _handle_void(self, tag: str, attributes: dict[str, str]) -> None:
        """Handle elements without content: line breaks, rules, images."""
        if self._hidden():
            return
        if tag == "br":
            if self._at_line_start() and self.pending_bullet is not None:
                self._write_line_prefix()
            self.pending_gap = ""
            self._write("\n")
        elif tag == "hr":
            self._break(1)
            self._write("─" * 40, self.dim_style)
            self._write("\n")
        elif tag == "img":
            alt = _WHITESPACE.sub(" ", attributes.get("alt", "")).strip()
            if alt:  # images show as their dimmed description
                self._emit_text(f"[{alt}]", self.dim_style)

    def handle_endtag(self, tag: str) -> None:
        """Close `tag` (and anything left open inside it)."""
        if tag in VOID_TAGS or not any(e.tag == tag for e in self.stack):
            return  # a stray end tag: ignore, as browsers do
        while self.stack:
            element = self.stack.pop()
            if element.breaks and not self._hidden() and not element.hidden:
                self._break(element.breaks)
            if element.tag == tag:
                break

    def handle_data(self, data: str) -> None:
        """Write text, collapsing whitespace outside <pre>."""
        if self._hidden():
            return
        if self._in_pre():
            lines = data.split("\n")
            for i, line in enumerate(lines):
                if i:
                    self._write("\n")
                self._emit_text(line)
            return
        if not data:
            return
        text = _WHITESPACE.sub(" ", data).strip(" ")
        if data[0] in " \t\r\n\f":
            self._request_gap(" ")
        if text:
            self._emit_text(text)
            if data[-1] in " \t\r\n\f":
                self._request_gap(" ")

    # -- result ----------------------------------------------------------

    def build(self) -> Content:
        """Return what was collected as Content, trailing whitespace trimmed."""
        text = "".join(self.parts)
        end = len(text.rstrip())
        spans = [Span(s.start, min(s.end, end), s.style) for s in self.spans if s.start < end]
        return Content(text[:end], spans)


def render_html(
    html: str,
    background: str | Color = "#000000",
    link_color: str | Color | None = None,
    dim_color: str | Color | None = None,
) -> Content:
    """Render an HTML email body as styled text for the reading pane.

    Parameters
    ----------
    html : str
        The email's HTML body.
    background : str | Color
        The theme's background colour; text colours are adjusted to read on it.
    link_color : str | Color | None
        Colour for links (e.g. the theme's accent); None keeps the text colour.
    dim_color : str | Color | None
        Colour for bullets, quote bars, rules and image descriptions; None
        uses the terminal's dim attribute.

    Returns
    -------
    Content
        The text with its styles. Empty if the HTML has no visible text.
    """

    def to_color(value: str | Color | None) -> Color | None:
        if value is None or isinstance(value, Color):
            return value
        return parse_color(value)

    builder = _ContentBuilder(
        background=to_color(background) or Color(0, 0, 0),
        link_color=to_color(link_color),
        dim_color=to_color(dim_color),
    )
    builder.feed(html)
    builder.close()
    return builder.build()
