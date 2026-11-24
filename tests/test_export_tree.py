"""`ewstui export FOLDER --recursive`: a folder tree, one export per folder."""
from __future__ import annotations

import json

import pytest
from demo_trees import add_folder, tree_client

from ewstui import export
from ewstui.__main__ import main
from ewstui.ews_client import FolderSummary

SOURCE = {"mailbox": "you@corp.example"}
BY = {"tool": "ewstui", "version": "test"}


def index(path) -> dict:
    return json.loads((path / "index.json").read_text())


def run(client, out, **kwargs):
    return export.export_tree(client, "collaborations", out, source=SOURCE, exported_by=BY, **kwargs)


def test_each_non_empty_folder_gets_its_own_export(tmp_path):
    entries, stats = run(tree_client(), tmp_path)
    assert [(e["path"], e["dir"]) for e in entries] == [
        ("Collaborations/Alice", "Alice"), ("Collaborations/Past/Bob", "Past/Bob"),
    ]
    assert (stats.folder_total, stats.new) == (3, 3)
    assert not (tmp_path / "Empty").exists() and not (tmp_path / export.TREE_TOP).exists()

    alice = index(tmp_path / "Alice")  # exactly what a single-folder export writes
    assert alice["source"] == {
        "mailbox": "you@corp.example", "folder_path": "Collaborations/Alice", "folder_id": "alice",
        "with_sent": False, "attachments": True,
    }
    assert {r["ews_item_id"] for r in alice["emails"]} == {"m1", "m2"}
    assert [r["ews_item_id"] for r in index(tmp_path / "Past" / "Bob")["emails"]] == ["m4"]


def test_tree_index_lists_the_folders_and_counts(tmp_path):
    run(tree_client(), tmp_path, with_sent=True)
    idx = index(tmp_path)
    assert idx["format"] == export.FORMAT
    assert idx["source"] == {
        "mailbox": "you@corp.example", "kind": "tree", "folder_path": "Collaborations", "folder_id": "collab",
        "with_sent": True, "attachments": True, "subfolders": None,
    }
    assert idx["counts"] == {"folders": 2, "folder_total": 3, "from_sent": 1, "exported": 4, "attachments": 1}
    alice = next(f for f in idx["folders"] if f["folder_id"] == "alice")
    assert alice == {"path": "Collaborations/Alice", "dir": "Alice", "folder_id": "alice", "present": True,
                     "counts": {"folder_total": 2, "from_sent": 1, "exported": 3, "attachments": 1}}


def test_top_folders_own_emails_go_to_their_own_subdirectory(tmp_path):
    client = tree_client()
    client._messages["collab"] = list(client._messages["archive"])
    client._folders[[f.id for f in client._folders].index("collab")].total_count = 1
    entries, _ = run(client, tmp_path)
    assert entries[0]["dir"] == export.TREE_TOP
    assert index(tmp_path / export.TREE_TOP)["source"]["folder_id"] == "collab"


def test_refresh_picks_up_new_subfolders_and_marks_removed_ones(tmp_path):
    client = tree_client()
    run(client, tmp_path)
    add_folder(client, FolderSummary(id="carol", name="Carol", total_count=0, unread_count=0, depth=1,
                                     parent_id="collab"), client._messages["archive"])
    client._folders = [f for f in client._folders if f.id != "bob"]
    entries, stats = run(client, tmp_path)
    assert [e["path"] for e in entries] == ["Collaborations/Alice", "Collaborations/Carol"]
    assert (stats.new, stats.unchanged) == (1, 2)
    folders = {f["folder_id"]: f["present"] for f in index(tmp_path)["folders"]}
    assert folders == {"alice": True, "bob": False, "carol": True}
    assert (tmp_path / "Past" / "Bob" / "index.json").exists()  # kept


def test_renamed_folder_stays_in_its_directory(tmp_path):
    client = tree_client()
    run(client, tmp_path)
    next(f for f in client._folders if f.id == "alice").name = "Alice Smith"
    entries, _ = run(client, tmp_path)
    assert entries[0] == entries[0] | {"path": "Collaborations/Alice Smith", "dir": "Alice"}


def test_folder_emptied_since_is_refreshed_not_skipped(tmp_path):
    client = tree_client()
    run(client, tmp_path)
    client._messages["bob"] = []
    next(f for f in client._folders if f.id == "bob").total_count = 0
    run(client, tmp_path)
    [record] = index(tmp_path / "Past" / "Bob")["emails"]
    assert record["present"] is False


def test_subfolder_restricts_the_run_and_runs_add_up(tmp_path):
    client = tree_client()
    entries, _ = run(client, tmp_path, subfolders=["past"])
    assert [e["path"] for e in entries] == ["Collaborations/Past/Bob"]
    assert index(tmp_path)["source"]["subfolders"] == ["past"]

    entries, _ = run(client, tmp_path, subfolders=["Alice"])
    assert [e["path"] for e in entries] == ["Collaborations/Alice"]
    idx = index(tmp_path)
    assert idx["source"]["subfolders"] == ["past", "Alice"]
    assert {f["folder_id"] for f in idx["folders"]} == {"alice", "bob"}  # the earlier run's folder is kept


def test_unknown_subfolder_fails(tmp_path):
    with pytest.raises(export.ExportError, match="no subfolder 'Zed'"):
        run(tree_client(), tmp_path, subfolders=["Zed"])


def test_tree_and_folder_exports_dont_mix(tmp_path):
    client = tree_client()
    alice = export.find_folder(client.list_folders(), "Collaborations/Alice")[1]
    export.export_folder(client, alice, tmp_path, source=SOURCE | {"folder_id": "alice"}, exported_by=BY)
    with pytest.raises(export.ExportError, match="another --out directory"):
        run(client, tmp_path)


@pytest.fixture
def common(tmp_path, monkeypatch):
    client = tree_client()
    monkeypatch.setattr("ewstui.__main__._open_mail_client", lambda cfg: (client, "you@corp.example"))
    return ["--demo", "--config", str(tmp_path / "config.toml")]


def test_command_line_recursive_prints_a_line_per_folder_and_updates(common, tmp_path, capsys):
    out = tmp_path / "collab"
    assert main(["export", "Collaborations", "--recursive", "--with-sent", "--out", str(out), *common]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[:2] == ["    2 (+1 sent)   Collaborations/Alice", "    1 (+0 sent)   Collaborations/Past/Bob"]
    assert printed[2] == f"Collaborations: 2 folders, 3 emails + 1 of your replies -> {out}"

    assert main(["export", "--update", str(out), *common]) == 0
    assert "0 new, 0 updated, 4 unchanged" in capsys.readouterr().out
    assert main(["export", "--update", str(out / "Alice"), *common]) == 0  # one folder on its own
    assert "Collaborations/Alice: 2 emails in the folder + 1 of your replies" in capsys.readouterr().out


def test_command_line_empty_parent_points_to_recursive(common, tmp_path, capsys):
    assert main(["export", "Collaborations", "--out", str(tmp_path / "x"), *common]) == 1
    assert "Add --recursive" in capsys.readouterr().err


def test_command_line_subfolder_needs_recursive(common, tmp_path):
    with pytest.raises(SystemExit):
        main(["export", "Collaborations", "--subfolder", "Past", "--out", str(tmp_path / "x"), *common])
    with pytest.raises(SystemExit):
        main(["export", "--message", "<m1@demo.corp.example>", "--recursive", "--out", str(tmp_path / "x"),
              *common])
