"""`ewstui search`: Exchange's text search from the command line."""
from __future__ import annotations

import json

import pytest

from ewstui import cli_search, export
from ewstui.__main__ import main
from ewstui.demo_backend import DemoMailClient


def test_default_folders_are_inbox_and_sent_items():
    folders = cli_search.resolve_search_folders(DemoMailClient(), [])
    assert [f.id for f in folders] == ["inbox", "sent"]


def test_all_searches_every_folder_not_hidden():
    ids = [f.id for f in cli_search.resolve_search_folders(DemoMailClient(), ["ALL"])]
    assert "archive" in ids and "sync" not in ids


def test_unknown_folder_lists_the_closest_paths():
    with pytest.raises(export.ExportError, match="Archive"):
        cli_search.resolve_search_folders(DemoMailClient(), ["Archiv"])


def test_hits_from_inbox_and_sent_are_merged_newest_first():
    client = DemoMailClient()
    hits = cli_search.search_folders(client, "EWS bridge", cli_search.resolve_search_folders(client, []))
    assert [(m.id, m.folder_id) for m in hits] == [("m2", "inbox"), ("s1", "sent")]
    assert hits[0].internet_message_id == "<m2@demo.corp.example>"


def test_folder_scopes_the_search_and_limit_caps_it():
    client = DemoMailClient()
    sent = cli_search.resolve_search_folders(client, ["sent items"])
    assert [m.id for m in cli_search.search_folders(client, "EWS bridge", sent)] == ["s1"]
    both = cli_search.resolve_search_folders(client, [])
    assert [m.id for m in cli_search.search_folders(client, "EWS bridge", both, limit=1)] == ["m2"]


def test_command_line_prints_message_ids(tmp_path, capsys):
    common = ["--demo", "--config", str(tmp_path / "config.toml")]
    assert main(["search", "budget", *common]) == 0
    out = capsys.readouterr().out
    assert "Q3 budget review" in out and "<m1@demo.corp.example>" in out
    assert " @ " in out  # m1 has an attachment


def test_command_line_json(tmp_path, capsys):
    common = ["--demo", "--config", str(tmp_path / "config.toml")]
    assert main(["search", "EWS", "bridge", "--json", *common]) == 0
    hits = json.loads(capsys.readouterr().out)
    assert [(h["folder"], h["message_id"]) for h in hits] == [
        ("Inbox", "<m2@demo.corp.example>"), ("Sent Items", "<s1@demo.corp.example>"),
    ]
    assert set(hits[0]) == {"date", "folder", "from", "subject", "message_id", "conversation_id", "has_attachments"}


def test_command_line_no_hits_and_missing_query(tmp_path, capsys):
    common = ["--demo", "--config", str(tmp_path / "config.toml")]
    assert main(["search", "zzzqqq", *common]) == 0
    assert "No matches." in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["search", *common])
