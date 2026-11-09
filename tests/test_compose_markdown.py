from __future__ import annotations

from exchangelib.properties import HTMLBody

from ewstui.compose_markdown import render_markdown_email, render_markdown_fragment
from ewstui.ews_client import build_html_body


def test_emphasis_lists_and_links() -> None:
    html = render_markdown_fragment("**bold** *it* ~~gone~~ [site](https://x.example)\n\n- a\n- b\n\n1. one")
    assert "<strong>bold</strong>" in html and "<em>it</em>" in html and "<s>gone</s>" in html
    assert '<a href="https://x.example">site</a>' in html
    assert "<ul>\n<li>a</li>" in html and "<ol>\n<li>one</li>" in html


def test_line_breaks_are_kept_as_typed() -> None:
    assert render_markdown_fragment("Hi,\nsecond line") == "<p>Hi,<br />\nsecond line</p>\n"


def test_signature_dashes_are_not_a_heading() -> None:
    html = render_markdown_fragment("Thanks\n--\nLeif")
    assert "<h2>" not in html and "--<br />" in html


def test_indented_text_is_not_code() -> None:
    assert "<pre>" not in render_markdown_fragment("    indented")


def test_raw_html_is_escaped() -> None:
    assert "&lt;script&gt;" in render_markdown_fragment("<script>alert(1)</script>")


def test_bare_urls_become_links() -> None:
    assert '<a href="https://x.example/a">https://x.example/a</a>' in render_markdown_fragment("see https://x.example/a")


def test_quotes_and_tables() -> None:
    html = render_markdown_fragment("> quoted\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<blockquote>" in html and "<td>1</td>" in html


def test_email_document_has_font_and_styles() -> None:
    html = render_markdown_email("hi")
    assert html.startswith("<html>") and "<style>" in html and "font-family" in html and "<p>hi</p>" in html


def test_build_html_body_is_an_exchange_html_body() -> None:
    body = build_html_body("**hi**")
    assert isinstance(body, HTMLBody) and "<strong>hi</strong>" in body
