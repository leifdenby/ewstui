from __future__ import annotations

import pytest
from textual.color import Color
from textual.content import Content
from textual.style import Style

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.html_render import adjust_color_for_background, compute_contrast, parse_color, render_html
from ewstui.widgets.preview import PreviewPane

DARK = "#101010"
LIGHT = "#fafafa"


def style_at(content: Content, needle: str) -> Style:
    """Return the combined style over the first character of `needle`.

    Parameters
    ----------
    content : Content
        Rendered content.
    needle : str
        Text to look for.

    Returns
    -------
    Style
        Every span covering that character, added together.
    """
    at = content.plain.index(needle)
    style = Style()
    for span in content.spans:
        if span.start <= at < span.end:
            style += span.style if isinstance(span.style, Style) else Style.parse(span.style)
    return style


def test_bold_italic_underline_strike() -> None:
    content = render_html("<p><b>bold</b> <i>italic</i> <u>under</u> <s>gone</s> plain</p>")
    assert content.plain == "bold italic under gone plain"
    assert style_at(content, "bold").bold
    assert style_at(content, "italic").italic
    assert style_at(content, "under").underline
    assert style_at(content, "gone").strike
    assert not style_at(content, "plain").bold


def test_inline_css_weight_and_style() -> None:
    content = render_html('<span style="font-weight:700">heavy</span> <span style="font-style: italic">lean</span>')
    assert style_at(content, "heavy").bold and style_at(content, "lean").italic


def test_whitespace_collapses_and_entities_decode() -> None:
    content = render_html("<div>  a\n   lot   of\tspace &amp; &lt;tags&gt;  </div>")
    assert content.plain == "a lot of space & <tags>"


def test_paragraphs_and_line_breaks() -> None:
    content = render_html("<p>one</p><p>two<br>three</p><div>four</div><div>five</div>")
    assert content.plain == "one\n\ntwo\nthree\n\nfour\nfive"


def test_outlook_paragraphs_are_single_lines_with_nbsp_blank_lines() -> None:
    html = "<p class=MsoNormal>Hi,</p><p class=MsoNormal>&nbsp;</p><p class=MsoNormal>Text</p>"
    assert render_html(html).plain == "Hi,\n\xa0\nText"


def test_bulleted_and_numbered_lists_nest() -> None:
    html = "<ul><li>a<ul><li>a1</li></ul></li><li>b</li></ul><ol start=3><li>x</li><li>y</li></ol>"
    assert render_html(html).plain.splitlines() == ["• a", "  ◦ a1", "• b", "3. x", "4. y"]


def test_unclosed_list_items() -> None:
    assert render_html("<ul><li>a<li>b</ul>").plain == "• a\n• b"


def test_blockquote_gets_bars() -> None:
    assert render_html("<p>me</p><blockquote>you<br>said</blockquote>").plain == "me\n\n│ you\n│ said"


def test_nested_blockquotes_get_one_bar_per_level() -> None:
    html = '<blockquote type="cite">outer<blockquote type="cite">inner</blockquote></blockquote>'
    assert render_html(html).plain == "│ outer\n\n│ │ inner"


def test_outlook_left_border_div_is_a_quote() -> None:
    html = (
        "<p class=MsoNormal>Sure.</p>"
        "<div style='border:none;border-left:solid #0000FF 1.5pt;padding:0cm 0cm 0cm 4.0pt'>"
        "<p class=MsoNormal>Can we meet?</p><p class=MsoNormal>Thanks</p></div>"
    )
    assert render_html(html).plain == "Sure.\n│ Can we meet?\n│ Thanks"


def test_div_without_visible_left_border_is_not_a_quote() -> None:
    assert render_html('<div style="border-left:none">a</div><div style="border:none">b</div>').plain == "a\nb"


def test_head_style_script_and_hidden_are_skipped() -> None:
    html = (
        "<html><head><title>T</title><style>p{color:red}</style></head><body>"
        '<div style="display:none">preheader</div><script>x()</script><p>seen</p></body></html>'
    )
    assert render_html(html).plain == "seen"


def test_table_rows_and_cells() -> None:
    html = "<table><tr><td>a</td><td>b</td></tr><tr><td></td><td>c</td></tr></table>"
    assert render_html(html).plain == "a  b\nc"


def test_links_are_underlined_and_clickable() -> None:
    content = render_html('see <a href="https://x.example/">here</a>', link_color="#88aaff")
    style = style_at(content, "here")
    assert style.underline and style.link == "https://x.example/"
    assert style.foreground == Color.parse("#88aaff")


def test_space_before_a_link_is_not_underlined() -> None:
    content = render_html('see <a href="https://x.example/">the page</a>')
    assert not style_at(content, " the").underline
    assert style_at(content, " page").underline  # inside the link it is


def test_image_alt_text_shown_and_untitled_images_dropped() -> None:
    assert render_html('<img src="a.png" alt="Logo"> hi <img src="pixel.gif">').plain == "[Logo] hi"


def test_greys_fall_back_to_theme_text_colour() -> None:
    content = render_html('<span style="color:#000000">black</span> <font color="#808080">grey</font>')
    assert style_at(content, "black").foreground is None
    assert style_at(content, "grey").foreground is None


@pytest.mark.parametrize("background", [DARK, LIGHT])
def test_colours_kept_but_made_readable(background: str) -> None:
    content = render_html('<span style="color:#1F497D">navy</span> <font color=red>red</font>', background=background)
    for word in ("navy", "red"):
        foreground = style_at(content, word).foreground
        assert foreground is not None
        assert compute_contrast(foreground, Color.parse(background)) >= 3.0


def test_readable_colour_is_left_unchanged() -> None:
    red = Color.parse("#ff4040")
    assert adjust_color_for_background(red, Color.parse(DARK)) == red


def test_inline_highlight_gets_contrasting_text() -> None:
    style = style_at(render_html('<span style="background-color:yellow">note</span>'), "note")
    assert style.background == Color.parse("yellow") and style.foreground == Color(0, 0, 0)


def test_block_backgrounds_ignored() -> None:
    assert style_at(render_html('<td style="background:#336699">cell</td>'), "cell").background is None


def test_parse_color_variants() -> None:
    assert parse_color("CC0000") == Color(204, 0, 0)
    assert parse_color("rgb(0, 128, 0)") == Color(0, 128, 0)
    assert parse_color("windowtext") is None


def test_broken_html_does_not_raise() -> None:
    assert render_html("<b>unclosed <i>tags</b> and </div> strays").plain == "unclosed tags and strays"


# -- in the reading pane ---------------------------------------------------


@pytest.fixture
def app(tmp_path) -> EwstuiApp:
    mail = DemoMailClient()
    m1 = mail.get_message("inbox", "m1")
    m1.body_text = "PLAIN version"
    m1.body_html = "<p><b>FORMATTED</b> version</p>"
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "priorities.todo.txt")])
    return EwstuiApp(mail, DemoCalendarClient(), cfg)


def preview_text(app: EwstuiApp) -> str:
    return str(app.query_one("#preview-body").render())


async def test_preview_shows_html_and_H_toggles_plain_text(app: EwstuiApp) -> None:
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("l")  # open m1, focus the reading pane
        await pilot.pause()
        assert "FORMATTED version" in preview_text(app) and "PLAIN" not in preview_text(app)
        await pilot.press("H")
        await pilot.pause()
        assert "PLAIN version" in preview_text(app) and "FORMATTED" not in preview_text(app)
        await pilot.press("H")
        await pilot.pause()
        assert "FORMATTED version" in preview_text(app)
        assert app.query_one("#preview", PreviewPane).show_plain_text is False
