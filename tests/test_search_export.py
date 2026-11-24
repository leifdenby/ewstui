"""`ewstui search ... --export DIR`: export what a search finds."""
from __future__ import annotations

import json

import pytest

from ewstui import cli_search, export
from ewstui.__main__ import main
from ewstui.demo_backend import DemoMailClient

SOURCE = {"mailbox": "you@corp.example"}
BY = {"tool": "ewstui", "version": "test"}


def index(path) -> dict:
    return json.loads((path / "index.json").read_text())


def run(client, out, queries, **kwargs):
    kwargs = {"folders": ["all"], "limit": 25} | kwargs
    return export.export_search(client, out, source=SOURCE, exported_by=BY, queries=queries, **kwargs)


def test_several_queries_are_merged_without_duplicates():
    client = DemoMailClient()
    folders = cli_search.resolve_search_folders(client, ["all"])
    hits = cli_search.search_queries(client, ["EWS bridge", "colleague", "budget"], folders)
    assert [m.id for m in hits] == ["m1", "m2", "s1"]


def test_exclude_subject_drops_hits_any_case():
    client = DemoMailClient()
    folders = cli_search.resolve_search_folders(client, ["all"])
    hits = cli_search.search_queries(client, ["EWS bridge", "budget"], folders, exclude_subject=["BUDGET"])
    assert [m.id for m in hits] == ["m2", "s1"]


def test_hits_are_exported_and_the_search_is_recorded(tmp_path):
    n_hits, stats = run(DemoMailClient(), tmp_path, ["budget", "lunch"], exclude_subject=["zzz"])
    assert (n_hits, stats.folder_total, stats.new) == (2, 2, 2)
    idx = index(tmp_path)
    assert idx["source"] == {
        "mailbox": "you@corp.example", "kind": "search", "queries": ["budget", "lunch"], "folders": ["all"],
        "limit": 25, "thread": False, "exclude_subject": ["zzz"], "attachments": True,
    }
    assert {r["ews_item_id"]: r["source_folder"] for r in idx["emails"]} == {"m1": "Inbox", "m4": "Inbox"}


def test_thread_looks_up_each_conversation_once(tmp_path, monkeypatch):
    client = DemoMailClient()
    looked_up = []
    find = client.find_conversation
    monkeypatch.setattr(client, "find_conversation", lambda c: looked_up.append(c) or find(c))
    _, stats = run(client, tmp_path, ["EWS bridge"], thread=True)  # hits m2 and s1, one conversation
    assert looked_up == ["conv-ews"]
    assert stats.folder_total == 2


def test_rerun_adds_new_hits_and_keeps_old_ones(tmp_path):
    client = DemoMailClient()
    run(client, tmp_path, ["budget"])
    client._messages["inbox"][0].subject = "Q3 numbers"  # no longer a hit for "budget"
    client._messages["inbox"][0].body_text = ""
    client._messages["inbox"][0].changekey = "c1+"
    _, stats = run(client, tmp_path, ["lunch"])
    idx = index(tmp_path)
    assert idx["source"]["queries"] == ["budget", "lunch"]
    assert stats.new == 1
    assert {r["ews_item_id"]: r["present"] for r in idx["emails"]} == {"m1": True, "m4": True}


def test_search_and_message_exports_dont_mix(tmp_path):
    client = DemoMailClient()
    export.export_messages(client, ["<m1@demo.corp.example>"], tmp_path, source=SOURCE, exported_by=BY)
    with pytest.raises(export.ExportError, match="another --out directory"):
        run(client, tmp_path, ["budget"])


def test_command_line_export_and_update(tmp_path, capsys):
    out = tmp_path / "found"
    common = ["--demo", "--config", str(tmp_path / "config.toml")]
    assert main(["search", "EWS", "bridge", "--query", "budget", "--folder", "all", "--export", str(out),
                 "--thread", "--no-attachments", *common]) == 0
    printed = capsys.readouterr().out
    assert f"3 hits, 3 emails with their threads -> {out}" in printed and "3 new" in printed
    source = index(out)["source"]
    assert (source["queries"], source["thread"], source["attachments"]) == (["EWS bridge", "budget"], True, False)
    assert not (out / "attachments").exists()

    assert main(["export", "--update", str(out), *common]) == 0
    assert "0 new, 0 updated, 3 unchanged" in capsys.readouterr().out


def test_command_line_exclude_subject_applies_to_printed_hits(tmp_path, capsys):
    common = ["--demo", "--config", str(tmp_path / "config.toml")]
    assert main(["search", "budget", "--query", "lunch", "--exclude-subject", "budget", *common]) == 0
    out = capsys.readouterr().out
    assert "Lunch Friday?" in out and "Q3 budget review" not in out


def test_command_line_export_options_need_export(tmp_path):
    common = ["--demo", "--config", str(tmp_path / "config.toml")]
    with pytest.raises(SystemExit):
        main(["search", "budget", "--thread", *common])
    with pytest.raises(SystemExit):
        main(["search", "budget", "--json", "--export", str(tmp_path / "x"), *common])
