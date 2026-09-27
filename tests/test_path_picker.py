"""s in the attachments list: save to a folder picked with a fuzzy search
under the home folder."""
from __future__ import annotations

from pathlib import Path

import pytest

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
import shutil

from ewstui import path_picker
from ewstui.path_picker import (
    PathPickerScreen,
    display,
    fzf_available,
    fzf_filter,
    list_dirs,
    rank,
    split_input,
    typed_path,
)
from ewstui.screens import AttachmentListScreen


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    for d in ["Documents/Work/Invoices", "Documents/Personal", "Downloads", "Library/Caches", ".hidden/x",
              "code/project/node_modules/pkg", "code/project/src"]:
        (home / d).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def test_list_dirs_skips_hidden_and_clutter(home):
    found = {display(p, home) for p in list_dirs(home)}
    assert {"~/Documents", "~/Documents/Work", "~/Documents/Work/Invoices", "~/Downloads", "~/code/project/src"} <= found
    assert not any(part in f for f in found for part in ("Library", ".hidden", "node_modules"))


def test_list_dirs_is_shallow_first_and_limited(home):
    dirs = list_dirs(home, max_depth=1)
    assert {display(p, home) for p in dirs} == {"~/Documents", "~/Downloads", "~/code"}
    assert len(list_dirs(home, limit=2)) == 2


def test_rank(home):
    dirs = list_dirs(home)
    assert display(rank(dirs, "invo", home, [])[0], home) == "~/Documents/Work/Invoices"
    assert display(rank(dirs, "dl", home, [])[0], home) == "~/Downloads"
    empty = rank(dirs, "", home, [home / "Downloads"])
    assert [display(p, home) for p in empty[:2]] == ["~/Downloads", "~"]  # recent first, then ~


@pytest.mark.skipif(shutil.which("fzf") is None, reason="fzf not installed")
def test_rank_with_fzf(home):
    dirs = list_dirs(home)
    assert display(rank(dirs, "invo", home, [], use_fzf=True)[0], home) == "~/Documents/Work/Invoices"
    assert rank(dirs, "zzqqxx", home, [], use_fzf=True) == []
    assert fzf_filter(["Documents/Work", "Downloads"], "dl") == ["Downloads"]


def test_without_fzf_our_own_matching_is_used(home, monkeypatch):
    monkeypatch.setattr(path_picker.shutil, "which", lambda name: None)
    assert fzf_filter(["a"], "a") is None
    assert display(rank(list_dirs(home), "invo", home, [], use_fzf=True)[0], home) == "~/Documents/Work/Invoices"


def test_a_failing_fzf_falls_back(home, monkeypatch):
    def broken(*a, **kw):
        raise OSError("exec format error")

    monkeypatch.setattr(path_picker.subprocess, "run", broken)
    assert display(rank(list_dirs(home), "invo", home, [], use_fzf=True)[0], home) == "~/Documents/Work/Invoices"


def test_typed_path(home):
    assert typed_path("~/Documents/Personal", home) == home / "Documents/Personal"
    assert typed_path(str(home / "code"), home) == home / "code"
    assert typed_path("~/nope", home) is None and typed_path("Documents", home) is None
    assert typed_path("~", home) == home
    # "~N" is on the way to "~/Nextcloud", not user N's home (that used to crash the app)
    assert typed_path("~N", home) is None and typed_path("~nobody-here/x", home) is None
    assert split_input("~D", home) == (home, "D")  # still a fuzzy search, from ~


def test_split_input(home):
    assert split_input("", home) == (home, "")
    assert split_input("invo", home) == (home, "invo")
    assert split_input("~/Documents/", home) == (home / "Documents", "")
    assert split_input("~/Documents/wo", home) == (home / "Documents", "wo")
    assert split_input("~/nope/wo", home) == (home, "~/nope/wo")  # not a folder: a plain search
    assert split_input(f"{home}/code/pro", home) == (home / "code", "pro")


def test_shortest_path_first(home):
    (home / "Documents" / "Personal" / "Work notes").mkdir()
    shown = [display(p, home) for p in rank(list_dirs(home), "work", home, [])]
    assert shown[0] == "~/Documents/Work"  # two levels down, before the three-level one
    assert shown.index("~/Documents/Work") < shown.index("~/Documents/Personal/Work notes")
    shown = [display(p, home) for p in rank(list_dirs(home), "work", home, [], use_fzf=fzf_available())]
    assert shown[0] == "~/Documents/Work"


def test_matching_is_inside_the_base_folder(home):
    base = home / "Documents"
    shown = rank(list_dirs(base), "w", base, [])
    assert shown[0] == base / "Work"  # "Documents" itself doesn't count as a match for the letters
    assert rank(list_dirs(home), "~/code/project", home, [])[0] == home / "code/project"


# -- in the app -----------------------------------------------------------------

async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_typing_a_tilde_and_letters_does_not_crash(home, tmp_path):
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    app = EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("v", "s")
        await settle(app, pilot)
        await pilot.press(*"~D")
        await settle(app, pilot)
        await pilot.press(*"ocs")
        await settle(app, pilot)
        assert isinstance(app.screen, PathPickerScreen)
        assert app.screen._shown  # found something, no crash


async def test_tab_goes_into_the_folder_and_searches_from_there(home, tmp_path):
    deep = home / "Documents" / "Work" / "Invoices" / "2026" / "Q3" / "Supplier" / "Acme"
    deep.mkdir(parents=True)  # deeper than the listing from ~ goes
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    app = EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("v", "s")
        await settle(app, pilot)
        screen = app.screen
        field = screen.query_one("#path-query")
        await pilot.press(*"doc")
        await settle(app, pilot)
        assert screen._shown[0] == home / "Documents"  # the shortest match first
        await pilot.press("tab")
        await settle(app, pilot)
        assert field.value == "~/Documents/"
        assert screen._shown[0] == home / "Documents" and home / "Documents" / "Work" in screen._shown
        for step in ("wo", "inv", "20", "q3", "sup", "acm"):  # all the way down, a Tab at a time
            await pilot.press(*step)
            await settle(app, pilot)
            await pilot.press("tab")
            await settle(app, pilot)
        assert field.value == "~/Documents/Work/Invoices/2026/Q3/Supplier/Acme/"
        assert screen._shown[0] == deep
        await pilot.press("enter")
        await settle(app, pilot)
    assert (deep / "Q3-budget.xlsx").exists()


async def test_tab_completes_even_with_the_list_focused_and_never_moves_focus(home, tmp_path):
    from textual.widgets import Input, ListView

    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    app = EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("v", "s")
        await settle(app, pilot)
        screen = app.screen
        await pilot.press(*"docu", "tab")
        await settle(app, pilot)
        assert isinstance(app.focused, Input)  # completed, focus stays in the box
        screen.query_one(ListView).focus()
        screen.query_one(ListView).index = screen._shown.index(home / "Documents" / "Work")
        await pilot.press("tab")
        await settle(app, pilot)
        assert screen.query_one("#path-query").value == "~/Documents/Work/"
        assert isinstance(app.focused, Input)
        await pilot.press(*"in")  # typing goes to the box again
        await settle(app, pilot)
        assert screen.query_one("#path-query").value == "~/Documents/Work/in"


async def test_backspacing_over_the_slash_goes_back_up(home, tmp_path):
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    app = EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("v", "s")
        await settle(app, pilot)
        screen = app.screen
        await pilot.press(*"~/Documents/")
        await settle(app, pilot)
        assert screen._base == home / "Documents"
        await pilot.press("backspace")
        await settle(app, pilot)
        assert screen._base == home and screen._shown[0] == home / "Documents"


async def test_s_saves_the_attachment_where_you_pick(home, tmp_path):
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    app = EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("v")  # m1 has Q3-budget.xlsx
        await settle(app, pilot)
        assert isinstance(app.screen, AttachmentListScreen)
        await pilot.press("s")
        await settle(app, pilot)
        assert isinstance(app.screen, PathPickerScreen)
        await pilot.press(*"invo", "enter")  # straight away: even before fzf has answered
        await settle(app, pilot)
        await settle(app, pilot)
        saved = home / "Documents/Work/Invoices/Q3-budget.xlsx"
        assert saved.exists()
        assert app._recent_save_dirs == [home / "Documents/Work/Invoices"]

        await pilot.press("v", "s")  # again: the folder used last comes first
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        assert (home / "Documents/Work/Invoices/Q3-budget (1).xlsx").exists()  # never overwrites
