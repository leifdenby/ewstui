"""`ewstui draft`: emails written in a file, saved to Drafts or sent."""
from __future__ import annotations

import io

import pytest

from ewstui import cli_draft
from ewstui.__main__ import main
from ewstui.demo_backend import DemoMailClient

REPLY = """---
in-reply-to: <m2@demo.corp.example>
reply-all: yes
---
Thanks, **sounds good**.
"""


def test_file_without_header_block_is_all_body() -> None:
    spec = cli_draft.parse_draft("Hello\n\n---\nnot a header\n")
    assert spec.body == "Hello\n\n---\nnot a header" and spec.to == [] and spec.subject == ""


def test_header_block_fields_and_list_continuations() -> None:
    spec = cli_draft.parse_draft(
        "---\n"
        "To: Doe, Jane <jane@corp.example>\n"
        "cc:\n"
        "  - bob@corp.example\n"
        "  - Ann <ann@corp.example>\n"
        "subject: Q3   budget\n"
        "in_reply_to: abc@host\n"
        "quote: no\n"
        "---\n\n"
        "Body\n"
    )
    assert spec.to == ["jane@corp.example"]
    assert spec.cc == ["bob@corp.example", "ann@corp.example"]
    assert spec.subject == "Q3 budget"
    assert spec.in_reply_to == "<abc@host>"
    assert spec.quote is False and spec.reply_all is False
    assert spec.body == "Body"


@pytest.mark.parametrize("text, message", [
    ("---\nto: a@x\nBody\n", "no closing"),
    ("---\nbcc: a@x\n---\nBody\n", "expected one of"),
    ("---\nto: a@x\nto: b@x\n---\nBody\n", "twice"),
    ("---\nreply-all: maybe\n---\nBody\n", "yes or no"),
])
def test_malformed_header_blocks(text: str, message: str) -> None:
    with pytest.raises(cli_draft.DraftError, match=message):
        cli_draft.parse_draft(text)


@pytest.mark.parametrize("spec, send, message", [
    (cli_draft.DraftSpec(body="x", subject="s", in_reply_to="<a>", forward="<b>"), False, "not both"),
    (cli_draft.DraftSpec(body="x", subject="s", reply_all=True), False, "only go with in-reply-to"),
    (cli_draft.DraftSpec(subject="s", to=["a@x"]), False, "no text"),
    (cli_draft.DraftSpec(body="x", to=["a@x"]), False, "needs a subject"),
    (cli_draft.DraftSpec(body="x", subject="s"), True, "who to"),
])
def test_check_refuses_what_cant_be_right(spec: cli_draft.DraftSpec, send: bool, message: str) -> None:
    with pytest.raises(cli_draft.DraftError, match=message):
        cli_draft.check_draft(spec, send)


def test_new_email_without_recipients_can_still_be_a_draft() -> None:
    cli_draft.check_draft(cli_draft.DraftSpec(body="x", subject="s"), send=False)


def test_reply_all_draft_goes_to_everyone_but_me_and_stays_a_reply() -> None:
    client = DemoMailClient()
    result = cli_draft.submit_draft(client, cli_draft.parse_draft(REPLY), me="you@corp.example")
    assert result.kind == "reply-all"
    assert result.subject == "Re: EWS bridge project"  # no "Re: Re:"
    assert (result.to, result.cc) == (["colleague@corp.example"], ["team@corp.example"])
    draft = client.list_messages("drafts")[0]
    assert draft.subject == "Re: EWS bridge project"
    detail = client.get_message("drafts", draft.id)
    assert detail.body_text.startswith("Thanks, **sounds good**.")
    assert client.sent == []  # nothing sent


def test_reply_sent_without_quote_is_exactly_the_body() -> None:
    client = DemoMailClient()
    spec = cli_draft.DraftSpec(body="Yes.", in_reply_to="<m1@demo.corp.example>", quote=False)
    cli_draft.submit_draft(client, spec, send=True, me="you@corp.example")
    assert client.sent == [{"in_reply_to": "m1", "to": ["finance@corp.example"], "cc": [],
                            "subject": "Re: Q3 budget review", "body": "Yes.", "reply_all": False,
                            "full_body": True}]


def test_forward_draft_and_new_email_sent() -> None:
    client = DemoMailClient()
    fwd = cli_draft.DraftSpec(body="FYI", forward="<m1@demo.corp.example>", to=["boss@corp.example"])
    result = cli_draft.submit_draft(client, fwd)
    assert result.subject == "Fwd: Q3 budget review"
    assert client.list_messages("drafts")[0].subject == "Fwd: Q3 budget review"
    new = cli_draft.DraftSpec(body="Hi", subject="Hello", to=["a@corp.example"], cc=["b@corp.example"])
    cli_draft.submit_draft(client, new, send=True)
    assert client.sent == [{"to": ["a@corp.example"], "subject": "Hello", "body": "Hi", "cc": ["b@corp.example"]}]


def test_unknown_message_id_is_an_error() -> None:
    with pytest.raises(cli_draft.DraftError, match="no email with Message-ID <nope@x>"):
        cli_draft.submit_draft(DemoMailClient(), cli_draft.DraftSpec(body="x", in_reply_to="<nope@x>"))


def test_command_line_saves_a_reply_from_a_file(tmp_path, capsys) -> None:
    path = tmp_path / "reply.md"
    path.write_text(REPLY, encoding="utf-8")
    assert main(["draft", str(path), "--demo", "--config", str(tmp_path / "config.toml")]) == 0
    out = capsys.readouterr().out
    assert "Reply to all saved to Drafts: Re: EWS bridge project" in out
    assert "answering: Re: EWS bridge project (from colleague@corp.example" in out
    assert "Cc: Project Team <team@corp.example>" in out


def test_command_line_options_override_and_stdin(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("---\nsubject: Old\nto: x@corp.example\n---\nHi there\n"))
    argv = ["draft", "-", "--subject", "New", "--to", "Ann <ann@corp.example>", "--send",
            "--demo", "--config", str(tmp_path / "config.toml")]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "New email sent: New" in out and "To: ann@corp.example" in out


def test_command_line_reports_bad_file_before_connecting(tmp_path, capsys) -> None:
    path = tmp_path / "bad.md"
    path.write_text("---\nfrom: me\n---\nx\n", encoding="utf-8")
    # no --demo and no account: it must fail on the file, not try to log in
    assert main(["draft", str(path), "--config", str(tmp_path / "config.toml")]) == 1
    assert "expected one of" in capsys.readouterr().err
