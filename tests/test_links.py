"""U: list the links in an email, pick one, open it in the browser."""
from __future__ import annotations

import pytest

from ewstui import links as links_module
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.links import Link, LinkPickerScreen, extract_links, open_link

# -- finding links ----------------------------------------------------------------


def test_finds_links_with_the_text_before_them():
    text = (
        "Details on the status page <https://status.example/x>.\n"
        "Join: https://meet.example/j/1 or mail mailto:help@example.org.\n"
    )
    assert extract_links(text) == [
        Link("https://status.example/x", "Details on the status page"),
        Link("https://meet.example/j/1", "Join"),
        Link("mailto:help@example.org", "Join: https://meet.example/j/1 or mail"),
    ]


@pytest.mark.parametrize(
    ("text", "url"),
    [
        ("see (https://example.org/a).", "https://example.org/a"),
        ("wiki https://en.wikipedia.org/wiki/Foo_(bar), yes", "https://en.wikipedia.org/wiki/Foo_(bar)"),
        ("go to www.example.org/faq!", "https://www.example.org/faq"),
        ('"https://example.org/q?a=1&b=2"', "https://example.org/q?a=1&b=2"),
        ("[https://example.org/in-brackets]", "https://example.org/in-brackets"),
    ],
)
def test_trims_punctuation_and_brackets(text, url):
    assert [link.url for link in extract_links(text)] == [url]


def test_duplicates_and_unsafe_schemes_are_dropped():
    text = "https://a.example/x and again HTTPS://A.example/x\njavascript:alert(1) file:///etc/passwd https://"
    assert [link.url for link in extract_links(text)] == ["https://a.example/x"]


def test_long_context_is_shortened():
    (link,) = extract_links("x" * 200 + " https://example.org")
    assert link.context.startswith("…") and len(link.context) <= links_module.CONTEXT_CHARS + 1


def test_open_link_refuses_other_schemes(monkeypatch):
    opened = []
    monkeypatch.setattr(links_module.webbrowser, "open_new_tab", lambda url: opened.append(url) or True)
    open_link("https://example.org")
    with pytest.raises(ValueError):
        open_link("file:///etc/passwd")
    assert opened == ["https://example.org"]


# -- in the app (demo m3 has links) ---------------------------------------------------


@pytest.fixture
def opened(monkeypatch):
    urls = []
    monkeypatch.setattr(links_module.webbrowser, "open_new_tab", lambda url: urls.append(url) or True)
    return urls


def make_app(tmp_path) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "p.todo.txt")])
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


async def on_m3(app, pilot):
    await pilot.pause()
    await pilot.press("j", "j")
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_u_lists_links_and_enter_opens_the_chosen_one(tmp_path, opened):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await on_m3(app, pilot)
        await pilot.press("U")
        await pilot.pause()
        assert isinstance(app.screen, LinkPickerScreen)
        assert [link.url for link in app.screen._links] == [
            "https://status.corp.example/maintenance",
            "https://meet.corp.example/j/123456",
            "mailto:it-support@corp.example",
            "https://www.corp.example/it/faq",
            "https://lists.corp.example/unsubscribe?id=42",
        ]
        await pilot.press("j", "enter")
        await pilot.pause()
        assert not isinstance(app.screen, LinkPickerScreen)
    assert opened == ["https://meet.corp.example/j/123456"]


async def test_digit_opens_directly_and_works_from_the_reading_pane(tmp_path, opened):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await on_m3(app, pilot)
        await pilot.press("o")  # into the reading pane
        await pilot.pause()
        await pilot.press("U", "5")
        await pilot.pause()
    assert opened == ["https://lists.corp.example/unsubscribe?id=42"]


async def test_escape_cancels(tmp_path, opened):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await on_m3(app, pilot)
        await pilot.press("U", "escape")
        await pilot.pause()
        assert not isinstance(app.screen, LinkPickerScreen)
    assert opened == []


async def test_email_without_links_says_so(tmp_path, opened):
    app = make_app(tmp_path)
    seen = []
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        original = app.notify
        app.notify = lambda msg, **kw: (seen.append(msg), original(msg, **kw))
        await pilot.press("U")  # m1: no links
        await pilot.pause()
        assert not isinstance(app.screen, LinkPickerScreen)
    assert "No links in this email" in seen


async def test_fetches_the_message_if_it_is_not_loaded_yet(tmp_path, opened):
    app = make_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await on_m3(app, pilot)
        from ewstui.message_cache import MessageCache

        app._message_cache = MessageCache()  # as if it had never been loaded
        await pilot.press("U")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, LinkPickerScreen)
