"""vim modes in the email text box (VimTextArea)."""
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.binding import Binding

from ewstui.widgets.vim_text_area import INSERT, NORMAL, VISUAL, VLINE, VimTextArea

TEXT = "Hello there world\nsecond line here\nthird"


class Host(App):
    # Esc as a binding here, like the compose screen's (save & close)
    BINDINGS = [Binding("escape", "esc", "Close")]

    def __init__(self, text: str = TEXT) -> None:
        super().__init__()
        self.text = text
        self.escaped = 0

    def compose(self) -> ComposeResult:
        yield VimTextArea(self.text, id="body")

    def action_esc(self) -> None:
        self.escaped += 1


async def start(pilot, *keys):
    area = pilot.app.query_one(VimTextArea)
    area.focus()
    area.move_cursor((0, 0))
    await pilot.pause()
    await pilot.press(*keys)
    await pilot.pause()
    return area


async def normal(pilot, *keys):
    return await start(pilot, "escape", *keys)


async def test_starts_in_insert_and_shows_the_mode():
    async with Host().run_test() as pilot:
        area = await start(pilot, "H", "i")
        assert area.mode == INSERT and area.text.startswith("HiHello")
        assert area.border_subtitle == "-- INSERT --"
        await pilot.press("escape")
        assert area.mode == NORMAL and area.border_subtitle == "NORMAL"


async def test_escape_in_normal_is_left_to_the_screen():
    async with Host().run_test() as pilot:
        area = await normal(pilot)
        assert pilot.app.escaped == 0  # the first Esc only left insert mode
        await pilot.press("escape")
        await pilot.pause()
        assert pilot.app.escaped == 1 and area.mode == NORMAL


async def test_motions():
    async with Host().run_test() as pilot:
        area = await normal(pilot, "w")
        assert area.cursor_location == (0, 6)
        await pilot.press("e")
        assert area.cursor_location == (0, 10)
        await pilot.press("b")
        assert area.cursor_location == (0, 6)
        await pilot.press("dollar_sign")
        assert area.cursor_location == (0, 16)
        await pilot.press("0", "j", "2", "l")
        assert area.cursor_location == (1, 2)
        await pilot.press("G")
        assert area.cursor_location == (2, 0)
        await pilot.press("g", "g")
        assert area.cursor_location == (0, 0)


async def test_x_dd_and_counts():
    async with Host().run_test() as pilot:
        area = await normal(pilot, "x")
        assert area.text.startswith("ello there")
        await pilot.press("3", "x")
        assert area.text.startswith("o there")
        await pilot.press("d", "d")
        assert area.text == "second line here\nthird"
        await pilot.press("2", "d", "d")
        assert area.text == ""


async def test_dw_d_dollar_and_cw():
    async with Host().run_test() as pilot:
        area = await normal(pilot, "d", "w")
        assert area.text.startswith("there world")
        await pilot.press("c", "w", *"Hi", "escape")
        assert area.text.startswith("Hi world") and area.mode == NORMAL
        await pilot.press("w", "D")
        assert area.text.startswith("Hi \n")


async def test_yank_and_put_lines_and_words():
    async with Host().run_test() as pilot:
        area = await normal(pilot, "y", "y", "j", "p")
        assert area.text == "Hello there world\nsecond line here\nHello there world\nthird"
        await pilot.press("g", "g", "y", "w", "dollar_sign", "p")
        assert area.text.startswith("Hello there worldHello ")


async def test_undo_and_redo():
    async with Host().run_test() as pilot:
        area = await normal(pilot, "d", "d")
        assert area.text.startswith("second")
        await pilot.press("u")
        assert area.text == TEXT
        await pilot.press("ctrl+r")
        assert area.text.startswith("second")


async def test_insert_keys():
    async with Host().run_test() as pilot:
        area = await normal(pilot, "A", *"!", "escape")
        assert area.text.startswith("Hello there world!\n")
        await pilot.press("o", *"new", "escape")
        assert area.text.startswith("Hello there world!\nnew\nsecond")
        await pilot.press("I", *"> ", "escape")
        assert "\n> new\n" in area.text


async def test_visual_char_and_line():
    async with Host().run_test() as pilot:
        area = await normal(pilot, "v", "e")
        assert area.mode == VISUAL and area.border_subtitle == "-- VISUAL --"
        assert area.selected_text == "Hello"
        await pilot.press("d")
        assert area.text.startswith(" there") and area.mode == NORMAL
        await pilot.press("V", "j", "y")
        assert area.mode == NORMAL and area.text == " there world\nsecond line here\nthird"
        await pilot.press("G", "p")
        assert area.text.endswith("third\n there world\nsecond line here")


async def test_visual_line_delete_and_change():
    async with Host().run_test() as pilot:
        area = await normal(pilot, "V")
        assert area.mode == VLINE and area.border_subtitle == "-- V-LINE --"
        await pilot.press("j", "c", *"replaced", "escape")
        assert area.text == "replaced\nthird"


async def test_join_and_substitute():
    async with Host().run_test() as pilot:
        area = await normal(pilot, "J")
        assert area.text.startswith("Hello there world second line here\n")
        await pilot.press("0", "s", "J", "escape")
        assert area.text.startswith("Jello")


async def test_in_the_compose_view_esc_twice_saves_to_drafts(tmp_path):
    from ewstui.app import EwstuiApp
    from ewstui.config import config_from_args
    from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
    from ewstui.screens import ComposeScreen

    mail = DemoMailClient()
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    app = EwstuiApp(mail, DemoCalendarClient(), cfg)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        body = app.screen.query_one(VimTextArea)
        body.focus()
        await pilot.press(*"Thanks!", "escape")
        await pilot.pause()
        assert isinstance(app.screen, ComposeScreen) and body.mode == NORMAL
        assert "i/a/o insert" in str(app.screen.query_one("#compose-hints").render())
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ComposeScreen)
    assert mail.get_message("drafts", mail.list_messages("drafts")[0].id).body_text.startswith("Thanks!")


async def test_normal_mode_keys_are_not_typed():
    async with Host().run_test() as pilot:
        area = await normal(pilot, "q", "z")
        assert area.text == TEXT


async def test_backspace_moves_and_arrows_extend_a_visual_selection():
    async with Host().run_test() as pilot:
        area = await normal(pilot, "l", "l", "backspace")
        assert area.text == TEXT and area.cursor_location == (0, 1)
        await pilot.press("v", "right", "right", "d")
        assert area.text.startswith("Ho there")


async def test_ctrl_s_still_reaches_the_screen_in_normal_mode():
    class SendHost(Host):
        BINDINGS = [Binding("ctrl+s", "send", "Send")]
        sent = 0

        def action_send(self) -> None:
            self.sent += 1

    async with SendHost().run_test() as pilot:
        await normal(pilot, "ctrl+s")
        assert pilot.app.sent == 1
