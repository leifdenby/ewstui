"""`ewstui folders`, and `ewstui export` pointing at a folder whose mail is in subfolders."""
from __future__ import annotations

import json

import pytest
from demo_trees import tree_client

from ewstui import export
from ewstui.__main__ import main


@pytest.fixture
def common(tmp_path, monkeypatch):
    """Connection options for main(), with the tree mailbox standing in for Exchange."""
    client = tree_client()
    monkeypatch.setattr("ewstui.__main__._open_mail_client", lambda cfg: (client, "you@corp.example"))
    return ["--demo", "--config", str(tmp_path / "config.toml")]


def test_folder_records_have_paths_and_counts():
    records = {r["path"]: r for r in export.folder_records(tree_client().list_folders())}
    assert records["Collaborations/Past/Bob"] == {
        "path": "Collaborations/Past/Bob", "name": "Bob", "depth": 2, "total": 1, "unread": 0, "hidden": False,
    }
    assert records["Collaborations/Alice"]["unread"] == 1


def test_hidden_folders_and_their_subfolders_are_left_out_unless_asked():
    client = tree_client()
    paths = [r["path"] for r in export.folder_records(client.list_folders())]
    assert "Sync Issues" not in paths and "Sync Issues/Old conflicts" not in paths
    paths = [r["path"] for r in export.folder_records(client.list_folders(), include_hidden=True)]
    assert "Sync Issues" in paths and "Sync Issues/Old conflicts" in paths


def test_filter_matches_anywhere_in_the_path_any_case():
    paths = [r["path"] for r in export.folder_records(tree_client().list_folders(), text="past")]
    assert paths == ["Collaborations/Past", "Collaborations/Past/Bob"]


def test_subtree_lists_relative_paths_top_first():
    rel = [(rel, f.id) for _, rel, f in export.subtree(tree_client().list_folders(), "collaborations")]
    assert rel == [("", "collab"), ("Alice", "alice"), ("Empty", "empty"), ("Past", "past"), ("Past/Bob", "bob")]


def test_describe_subfolders_counts_non_empty_ones():
    assert export.describe_subfolders(tree_client().list_folders(), "Collaborations") == (2, 3)
    assert export.describe_subfolders(tree_client().list_folders(), "Collaborations/Alice") == (0, 0)


def test_command_line_lists_paths_as_export_takes_them(common, capsys):
    assert main(["folders", *common]) == 0
    out = capsys.readouterr().out
    assert "     1      0  Collaborations/Past/Bob" in out.splitlines()
    assert "Sync Issues" not in out
    assert main(["folders", "--hidden", *common]) == 0
    assert "Sync Issues/Old conflicts" in capsys.readouterr().out


def test_command_line_json_and_filter(common, capsys):
    assert main(["folders", "--filter", "alice", "--json", *common]) == 0
    assert [r["path"] for r in json.loads(capsys.readouterr().out)] == ["Collaborations/Alice"]
    assert main(["folders", "--filter", "zzz", *common]) == 0
    assert "No folder path contains 'zzz'" in capsys.readouterr().out


def test_export_of_an_empty_parent_folder_points_to_its_subfolders(common, tmp_path, capsys):
    out = tmp_path / "emails"
    assert main(["export", "Collaborations", "--out", str(out), *common]) == 1
    err = capsys.readouterr().err
    assert "holds no emails itself, but 2 of its subfolders hold 3" in err
    assert not out.exists()  # nothing written


def test_export_of_an_empty_leaf_folder_still_works(common, tmp_path, capsys):
    assert main(["export", "Collaborations/Empty", "--out", str(tmp_path / "e"), *common]) == 0
    assert "0 new" in capsys.readouterr().out
