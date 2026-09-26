"""The folder pane: only mail folders, your order (V then j/k), hidden
folders (H, and housekeeping hidden by default), saved per account."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import tomlkit

from ewstui import config_file
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import FolderSummary, MailClient
from ewstui.folder_tree import FolderPrefs, arrange, hidden_ids, move, toggle_hidden
from ewstui.widgets.folder_list import FolderList


def f(fid, parent=None, depth=0, hidden_by_default=False) -> FolderSummary:
    return FolderSummary(fid, fid.title(), 0, 0, depth, parent, hidden_by_default)


# inbox (with two subfolders), projects (with one), sent, sync (housekeeping)
TREE = [
    f("inbox"), f("backlog", "inbox", 1), f("reference", "inbox", 1),
    f("projects"), f("deode", "projects", 1),
    f("sent"),
    f("sync", hidden_by_default=True), f("conflicts", "sync", 1),
]


def ids(folders):
    return [x.id for x in folders]


# -- list_folders ---------------------------------------------------------------

def folder(fid, name, cls="IPF.Note", children=()):
    return SimpleNamespace(id=fid, name=name, folder_class=cls, total_count=1, unread_count=0, children=list(children))


def test_list_folders_lists_only_mail_folders_below_the_root():
    root = folder("root", "Top of Information Store", None, [
        folder("inbox", "Inbox", children=[folder("backlog", "Backlog")]),
        folder("cal", "Calendar", "IPF.Appointment", [folder("bday", "Birthdays", "IPF.Appointment.Birthday")]),
        folder("contacts", "Contacts", "IPF.Contact", [folder("odd", "Mail inside contacts")]),
        folder("yammer", "Yammer Root", "IPF"),
        folder("history", "Conversation History", None),
        folder("rss", "RSS-kilder", "IPF.Note.OutlookHomepage"),
        folder("sync", "Synkroniseringsfejl", children=[folder("conf", "Konflikter")]),
        folder("f2", "Flyt til F2 - Adgang alle", None),
        folder("projects", "Projects"),
    ])
    listed = MailClient(SimpleNamespace(root=MagicMock(), msg_folder_root=root)).list_folders()
    assert [(x.id, x.depth, x.parent_id) for x in listed] == [
        ("inbox", 0, None), ("backlog", 1, "inbox"),
        ("history", 0, None), ("rss", 0, None), ("sync", 0, None), ("conf", 1, "sync"), ("f2", 0, None),
        ("projects", 0, None),
    ]
    assert {x.id for x in listed if x.hidden_by_default} == {"history", "rss", "sync", "conf", "f2"}


# -- arranging ------------------------------------------------------------------

def test_arrange_keeps_server_order_without_prefs_and_follows_the_saved_order():
    assert ids(arrange(TREE, [])) == ids(TREE)
    order = ["sent", "projects", "deode", "inbox", "reference", "backlog"]
    assert ids(arrange(TREE, order)) == ["sent", "projects", "deode", "inbox", "reference", "backlog", "sync", "conflicts"]


def test_move_among_siblings_takes_subfolders_along():
    order = move(TREE, [], "projects", -1)
    assert ids(arrange(TREE, order))[:5] == ["projects", "deode", "inbox", "backlog", "reference"]
    order = move(TREE, order, "backlog", 1)
    assert ids(arrange(TREE, order))[2:5] == ["inbox", "reference", "backlog"]
    assert move(TREE, order, "projects", -1) == order  # already first: unchanged


def test_move_skips_hidden_siblings():
    order = move(TREE, [], "sent", 1, skip={"sync"})  # sync is hidden and last
    assert ids(arrange(TREE, order)) == ids(TREE)  # nothing visible below sent


def test_hidden_by_default_and_their_subfolders():
    assert hidden_ids(TREE, FolderPrefs()) == {"sync", "conflicts"}
    assert hidden_ids(TREE, FolderPrefs(shown=["sync"])) == set()
    assert hidden_ids(TREE, FolderPrefs(hidden=["projects"])) == {"projects", "deode", "sync", "conflicts"}


def test_toggle_hidden():
    prefs = toggle_hidden(TREE[3], FolderPrefs())  # hide projects
    assert prefs.hidden == ["projects"]
    assert toggle_hidden(TREE[3], prefs).hidden == []
    prefs = toggle_hidden(TREE[6], FolderPrefs())  # unhide sync (hidden by default)
    assert prefs.shown == ["sync"] and prefs.hidden == []
    assert toggle_hidden(TREE[6], prefs).shown == []


# -- config file ----------------------------------------------------------------

def test_prefs_round_trip_with_folder_names_as_comments(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('default_account = "work"\n[accounts.work]\nemail = "me@corp.example"\n')
    prefs = FolderPrefs(order=["sent", "inbox"], hidden=["projects"], shown=[])
    config_file.save_folder_prefs(path, "work", prefs, {"sent": "Sent Items", "inbox": "Inbox", "projects": "Projects"})
    text = path.read_text()
    assert '"sent", # Sent Items' in text and "shown_folders" not in text
    assert config_file.account_folder_prefs(tomlkit.parse(text), "work") == prefs
    assert config_from_args(["--config", str(path), "--demo"]).folder_prefs == prefs


# -- in the app -----------------------------------------------------------------

def make_app(tmp_path, with_account=True) -> EwstuiApp:
    path = tmp_path / "config.toml"
    path.write_text('default_account = "work"\n[accounts.work]\nemail = "me@corp.example"\n')
    args = ["--demo", "--priority-file", str(tmp_path / "todo.txt")]
    cfg = config_from_args((["--config", str(path)] if with_account else ["--config", str(tmp_path / "none.toml")]) + args)
    return EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)


def listed(app) -> list[str]:
    return ids(app.query_one("#folders", FolderList).folders)


def saved(tmp_path) -> FolderPrefs:
    return config_file.account_folder_prefs(config_file.load(tmp_path / "config.toml"), "work")


async def to_folders(pilot):
    await pilot.pause()
    await pilot.press("h")  # message list -> folder pane
    await pilot.pause()


async def test_housekeeping_is_hidden_and_dot_shows_it(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await to_folders(pilot)
        assert "sync" not in listed(app)
        await pilot.press("full_stop")
        await pilot.pause()
        assert listed(app)[-1] == "sync"
        await pilot.press("full_stop")
        await pilot.pause()
        assert "sync" not in listed(app)


async def test_v_then_j_moves_the_folder_and_saves_the_order(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await to_folders(pilot)  # cursor on Inbox
        await pilot.press("V")
        await pilot.pause()
        assert "moving folder" in str(app.query_one("#statusbar").render())
        await pilot.press("j", "j", "enter")
        await pilot.pause()
        assert listed(app)[:3] == ["sent", "drafts", "inbox"]
        assert app.query_one("#folders", FolderList).highlighted_folder_id() == "inbox"
        assert saved(tmp_path).order[:3] == ["sent", "drafts", "inbox"]
        assert app.current_folder_id == "inbox"  # putting it down doesn't open anything else


async def test_escape_puts_the_folder_back(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await to_folders(pilot)
        before = listed(app)
        await pilot.press("V", "j", "j", "escape")
        await pilot.pause()
        assert listed(app) == before
        assert saved(tmp_path).order == []


async def test_h_hides_and_unhides_and_saves(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await to_folders(pilot)
        await pilot.press("j", "j", "H")  # Drafts
        await pilot.pause()
        assert "drafts" not in listed(app)
        assert saved(tmp_path).hidden == ["drafts"]
        await pilot.press("full_stop")
        await pilot.pause()
        app.query_one("#folders", FolderList).highlight_folder("drafts")
        await pilot.press("H")
        await pilot.pause()
        assert saved(tmp_path).hidden == []
        app.query_one("#folders", FolderList).highlight_folder("sync")
        await pilot.press("H")  # unhide the housekeeping folder
        await pilot.pause()
        assert saved(tmp_path).shown == ["sync"]


async def test_without_an_account_the_order_lasts_the_session(tmp_path):
    app = make_app(tmp_path, with_account=False)
    async with app.run_test(size=(140, 40)) as pilot:
        await to_folders(pilot)
        await pilot.press("V", "j", "enter")
        await pilot.pause()
        assert listed(app)[:2] == ["sent", "inbox"]
        assert not (tmp_path / "none.toml").exists()
