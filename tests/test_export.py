"""`ewstui export`: a mail folder copied to text files + index.json."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from ewstui import export
from ewstui.__main__ import main
from ewstui.demo_backend import DemoMailClient
from ewstui.ews_client import FolderSummary

SOURCE = {"mailbox": "you@corp.example", "folder_path": "Projects/0 - Proposals/SBEP", "folder_id": "sbep"}
BY = {"tool": "ewstui", "version": "test"}


def proposal_client() -> DemoMailClient:
    """The demo mailbox plus Projects/0 - Proposals/SBEP holding the
    budget email (with an attachment) and the EWS bridge thread (whose
    earlier reply is in Sent Items)."""
    client = DemoMailClient()
    client._folders += [
        FolderSummary(id="projects", name="Projects", total_count=0, unread_count=0, depth=0),
        FolderSummary(id="proposals", name="0 - Proposals", total_count=0, unread_count=0, depth=1),
        FolderSummary(id="sbep", name="SBEP", total_count=2, unread_count=0, depth=2),
    ]
    client._messages["sbep"] = [m for m in client._messages["inbox"] if m.id in ("m1", "m2")]
    return client


def run(client, out, **kwargs):
    folder = export.find_folder(client.list_folders(), "Projects/0 - Proposals/SBEP")[1]
    return export.export_folder(client, folder, out, source=SOURCE, exported_by=BY, **kwargs)


def index(out) -> dict:
    return json.loads((out / "index.json").read_text())


def test_folder_paths_are_found_case_insensitively():
    path, folder = export.find_folder(proposal_client().list_folders(), "/projects/0 - proposals/sbep/")
    assert (path, folder.id) == ("Projects/0 - Proposals/SBEP", "sbep")


def test_unknown_folder_lists_the_closest_paths():
    with pytest.raises(export.ExportError, match="Projects/0 - Proposals/SBEP"):
        export.find_folder(proposal_client().list_folders(), "Projects/0 - Proposal/SBEP")


def test_exports_emails_index_and_attachments(tmp_path):
    stats = run(proposal_client(), tmp_path)
    assert (stats.folder_total, stats.new, stats.attachments) == (2, 2, 1)

    idx = index(tmp_path)
    assert idx["format"] == export.FORMAT
    assert idx["source"] == SOURCE and idx["exported_by"] == BY
    assert idx["counts"]["exported"] == 2
    budget = next(r for r in idx["emails"] if r["subject"] == "Q3 budget review")
    assert budget["message_id"] == "<m1@demo.corp.example>"
    assert (budget["ews_item_id"], budget["ews_folder_id"], budget["changekey"]) == ("m1", "sbep", "c1")
    assert budget["from"] == {"name": "Finance Team", "address": "finance@corp.example"}
    assert budget["source_folder"] == "SBEP" and budget["present"] is True

    text = (tmp_path / budget["file"]).read_text()
    assert text.startswith("Subject: Q3 budget review\nFrom: Finance Team <finance@corp.example>\n")
    assert "Message-ID: <m1@demo.corp.example>" in text and "This is a demo message body." in text
    [attachment] = budget["attachments"]
    assert attachment.startswith("attachments/") and (tmp_path / attachment).read_bytes().startswith(b"PK")


def test_second_run_skips_everything_and_keeps_first_synced(tmp_path):
    client = proposal_client()
    run(client, tmp_path, now=datetime(2026, 10, 1, 9).astimezone())
    first = index(tmp_path)
    stats = run(client, tmp_path, now=datetime(2026, 10, 2, 9).astimezone())
    assert (stats.new, stats.updated, stats.unchanged, stats.attachments) == (0, 0, 2, 1)
    again = index(tmp_path)
    assert again["first_synced"] == first["first_synced"] != again["last_synced"]
    assert again["emails"] == first["emails"]
    assert len(list((tmp_path / "attachments").rglob("*.xlsx"))) == 1  # not downloaded twice


def test_changed_email_is_rewritten_in_place(tmp_path):
    client = proposal_client()
    run(client, tmp_path)
    m2 = client.get_message("sbep", "m2")
    m2.changekey, m2.body_text = "c2+", "Edited text"
    stats = run(client, tmp_path)
    assert (stats.updated, stats.unchanged) == (1, 1)
    record = next(r for r in index(tmp_path)["emails"] if r["ews_item_id"] == "m2")
    assert record["changekey"] == "c2+" and "Edited text" in (tmp_path / record["file"]).read_text()
    assert len(list(tmp_path.glob("*.md"))) == 2


def test_email_gone_from_the_folder_is_kept_but_marked(tmp_path):
    client = proposal_client()
    run(client, tmp_path)
    client.move_message("sbep", "m1", "archive")
    stats = run(client, tmp_path)
    assert stats.exported == 1
    gone = next(r for r in index(tmp_path)["emails"] if r["ews_item_id"] == "m1")
    assert gone["present"] is False and (tmp_path / gone["file"]).exists()


def test_with_sent_adds_only_replies_in_the_folders_conversations(tmp_path):
    stats = run(proposal_client(), tmp_path, with_sent=True)
    assert stats.from_sent == 1
    [reply] = [r for r in index(tmp_path)["emails"] if r["source_folder"] == "Sent Items"]
    assert (reply["ews_item_id"], reply["conversation_id"]) == ("s1", "conv-ews")


def test_another_folder_is_refused_in_the_same_directory(tmp_path):
    client = proposal_client()
    run(client, tmp_path)
    inbox = export.find_folder(client.list_folders(), "Inbox")[1]
    with pytest.raises(export.ExportError, match="another --out directory"):
        export.export_folder(client, inbox, tmp_path, source=SOURCE | {"folder_id": "inbox"}, exported_by=BY)


def test_command_line_export_and_update(tmp_path, capsys):
    out = tmp_path / "emails"
    common = ["--demo", "--config", str(tmp_path / "config.toml")]
    assert main(["export", "Inbox", "--out", str(out), "--with-sent", *common]) == 0
    assert "5 new" in capsys.readouterr().out  # 4 from the Inbox + your reply in the EWS thread
    idx = index(out)
    assert idx["source"]["folder_path"] == "Inbox" and idx["source"]["with_sent"] is True
    assert idx["exported_by"]["command"][:3] == ["ewstui", "export", "Inbox"]

    assert main(["export", "--update", str(out), *common]) == 0
    assert "0 new" in capsys.readouterr().out
    assert index(out)["source"]["with_sent"] is True  # kept from the first run


def test_command_line_unknown_folder_fails(tmp_path, capsys):
    common = ["--demo", "--config", str(tmp_path / "config.toml")]
    assert main(["export", "Inbx/Nope", "--out", str(tmp_path / "x"), *common]) == 1
    assert "no folder" in capsys.readouterr().err


MESSAGES_SOURCE = {"mailbox": "you@corp.example", "kind": "messages"}


def run_messages(client, out, ids, **kwargs):
    return export.export_messages(client, ids, out, source=MESSAGES_SOURCE, exported_by=BY, **kwargs)


def test_single_message_is_exported_with_its_attachment(tmp_path):
    stats = run_messages(DemoMailClient(), tmp_path, ["<m1@demo.corp.example>"])
    assert (stats.folder_total, stats.new, stats.attachments) == (1, 1, 1)
    idx = index(tmp_path)
    assert idx["source"]["kind"] == "messages" and idx["source"]["message_ids"] == ["<m1@demo.corp.example>"]
    [record] = idx["emails"]
    assert (record["subject"], record["source_folder"]) == ("Q3 budget review", "Inbox")
    assert (tmp_path / record["file"]).read_text().startswith("Subject: Q3 budget review\n")


def test_thread_pulls_in_the_reply_from_sent_items(tmp_path):
    stats = run_messages(DemoMailClient(), tmp_path, ["<m2@demo.corp.example>"], thread=True)
    assert stats.folder_total == 2
    folders = {r["ews_item_id"]: r["source_folder"] for r in index(tmp_path)["emails"]}
    assert folders == {"m2": "Inbox", "s1": "Sent Items"}


def test_messages_from_several_runs_accumulate_without_duplicates(tmp_path):
    client = DemoMailClient()
    run_messages(client, tmp_path, ["<m1@demo.corp.example>"])
    stats = run_messages(client, tmp_path, ["<s2@demo.corp.example>", "<m1@demo.corp.example>"])
    assert (stats.new, stats.unchanged, stats.exported) == (1, 1, 2)
    assert index(tmp_path)["source"]["message_ids"] == ["<m1@demo.corp.example>", "<s2@demo.corp.example>"]


def test_unknown_message_id_fails(tmp_path):
    with pytest.raises(export.ExportError, match="<nope@x>"):
        run_messages(DemoMailClient(), tmp_path, ["<nope@x>"])


def test_folder_and_message_exports_dont_mix(tmp_path):
    client = proposal_client()
    run(client, tmp_path)
    with pytest.raises(export.ExportError, match="another --out directory"):
        run_messages(client, tmp_path, ["<m1@demo.corp.example>"])


def test_command_line_message_export_and_update(tmp_path, capsys):
    out = tmp_path / "emails"
    common = ["--demo", "--config", str(tmp_path / "config.toml")]
    assert main(["export", "--message", "<m2@demo.corp.example>", "--thread", "--out", str(out), *common]) == 0
    assert "2 new" in capsys.readouterr().out
    assert index(out)["source"]["thread"] is True

    assert main(["export", "--update", str(out), *common]) == 0
    assert "0 new, 0 updated, 2 unchanged" in capsys.readouterr().out


def test_command_line_rejects_folder_with_message(tmp_path):
    common = ["--demo", "--config", str(tmp_path / "config.toml")]
    with pytest.raises(SystemExit):
        main(["export", "Inbox", "--message", "<m1@demo.corp.example>", "--out", str(tmp_path / "x"), *common])
    with pytest.raises(SystemExit):
        main(["export", "Inbox", "--thread", "--out", str(tmp_path / "x"), *common])
