"""`ewstui draft` with attachments: the `attach:` header, --attach, and
how the clients attach the files."""
from __future__ import annotations

from pathlib import Path

import pytest

from ewstui import cli_draft
from ewstui.__main__ import main
from ewstui.demo_backend import DemoMailClient
from ewstui.ews_client import MailClient


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    """Two files to attach, next to where the draft file goes."""
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-1.4 " + b"x" * 1500)
    (tmp_path / "figs").mkdir()
    fig = tmp_path / "figs" / "fig1.png"
    fig.write_bytes(b"\x89PNG fake")
    return {"report": report, "fig": fig}


def test_attach_header_single_and_list(tmp_path: Path, files: dict[str, Path]) -> None:
    one = cli_draft.parse_draft("---\nsubject: S\nattach: report.pdf\n---\nHi\n", base_dir=tmp_path)
    assert one.attachments == [files["report"]]
    many = cli_draft.parse_draft("---\nattach:\n  - report.pdf\n  - figs/fig1.png\n---\nHi\n", base_dir=tmp_path)
    assert many.attachments == [files["report"], files["fig"]]


def test_attach_paths_expand_home_and_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli_draft.resolve_attachment_path("~/a.txt", Path("/elsewhere")) == tmp_path / "a.txt"
    assert cli_draft.resolve_attachment_path("/abs/b.txt", tmp_path) == Path("/abs/b.txt")


def test_check_refuses_missing_file_and_folder(tmp_path: Path) -> None:
    spec = cli_draft.DraftSpec(subject="S", body="x", attachments=[tmp_path / "nope.pdf"])
    with pytest.raises(cli_draft.DraftError, match="attach: no file"):
        cli_draft.check_draft(spec, send=False)
    spec.attachments = [tmp_path]
    with pytest.raises(cli_draft.DraftError, match="is not a file"):
        cli_draft.check_draft(spec, send=False)


def test_new_email_draft_has_the_files(files: dict[str, Path]) -> None:
    client = DemoMailClient()
    spec = cli_draft.DraftSpec(subject="Report", body="See attached", to=["a@corp.example"],
                               attachments=[files["report"], files["fig"]])
    result = cli_draft.submit_draft(client, spec)
    draft = client.list_messages("drafts")[0]
    assert draft.has_attachments
    assert [a.name for a in client.list_attachments("drafts", draft.id)] == ["report.pdf", "fig1.png"]
    assert result.attachments == [files["report"], files["fig"]]


def test_reply_and_forward_sent_carry_the_files(files: dict[str, Path]) -> None:
    client = DemoMailClient()
    cli_draft.submit_draft(client, cli_draft.DraftSpec(body="Yes", in_reply_to="<m1@demo.corp.example>",
                                                      attachments=[files["report"]]), send=True)
    cli_draft.submit_draft(client, cli_draft.DraftSpec(body="FYI", forward="<m1@demo.corp.example>",
                                                      to=["b@corp.example"], attachments=[files["fig"]]),
                           send=True)
    cli_draft.submit_draft(client, cli_draft.DraftSpec(body="Hi", subject="S", to=["c@corp.example"],
                                                      attachments=[files["fig"]]), send=True)
    assert [s["attachments"] for s in client.sent] == [["report.pdf"], ["fig1.png"], ["fig1.png"]]


def test_forward_draft_keeps_the_originals_files_and_adds_yours(files: dict[str, Path]) -> None:
    client = DemoMailClient()
    cli_draft.submit_draft(client, cli_draft.DraftSpec(body="FYI", forward="<m1@demo.corp.example>",
                                                      attachments=[files["report"]]))
    draft = client.list_messages("drafts")[0]
    assert [a.name for a in client.list_attachments("drafts", draft.id)] == ["Q3-budget.xlsx", "report.pdf"]


def test_result_lines_list_files_with_sizes(files: dict[str, Path]) -> None:
    result = cli_draft.DraftResult("new", "S", ["a@corp.example"], [], attachments=[files["report"], files["fig"]])
    assert "  Attached: report.pdf (2 kB), fig1.png (9 B)" in cli_draft.format_result_lines(result, send=False)


def test_format_size() -> None:
    assert [cli_draft.format_size(n) for n in (850, 85_000, 1_234_567, 2_500_000_000)] == [
        "850 B", "85 kB", "1.2 MB", "2.5 GB"]


def test_command_line_file_and_option_attachments(tmp_path: Path, files: dict[str, Path],
                                                  capsys: pytest.CaptureFixture[str],
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text("---\nto: a@corp.example\nsubject: Report\nattach: report.pdf\n---\nHi\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "figs")  # --attach is relative to the current folder
    argv = ["draft", str(draft), "--attach", "fig1.png", "--demo", "--config", str(tmp_path / "config.toml")]
    assert main(argv) == 0
    assert "Attached: report.pdf (2 kB), fig1.png (9 B)" in capsys.readouterr().out


def test_command_line_missing_attachment_fails_before_connecting(tmp_path: Path,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text("---\nsubject: S\nattach: gone.pdf\n---\nHi\n", encoding="utf-8")
    # no --demo and no account: it must fail on the file, not try to log in
    assert main(["draft", str(draft), "--config", str(tmp_path / "config.toml")]) == 1
    assert "attach: no file" in capsys.readouterr().err


class FakeDraft:
    """A saved draft: records attach / send calls in a shared log."""

    def __init__(self, log: list, draft_id: str = "D1") -> None:
        self.id, self.log, self.body = draft_id, log, None

    def attach(self, attachment: object) -> None:
        """Record the attachment's name."""
        self.log.append(("attach", attachment.name))

    def send(self, save_copy: bool) -> None:
        """Record sending."""
        self.log.append(("send", self.id, save_copy))

    def save(self, update_fields: list[str]) -> None:
        """Record a field update."""
        self.log.append(("update", tuple(update_fields)))


class FakeCreated:
    """Exchange's reply / forward, not yet saved."""

    def __init__(self, log: list, kind: str) -> None:
        self.log, self.kind = log, kind

    def save(self, folder: object) -> object:
        """Record saving to Drafts; return what Exchange returns (an id)."""
        self.log.append(("save", self.kind))
        return type("Saved", (), {"id": "D1"})()


class FakeItem:
    """The email answered / forwarded."""

    def __init__(self, log: list) -> None:
        self.log = log

    def create_reply(self, **kwargs: object) -> FakeCreated:
        """A reply."""
        return FakeCreated(self.log, "reply")

    def create_forward(self, **kwargs: object) -> FakeCreated:
        """A forward."""
        return FakeCreated(self.log, "forward")

    def reply(self, **kwargs: object) -> None:
        """Send a reply directly."""
        self.log.append(("reply-direct",))

    def forward(self, **kwargs: object) -> None:
        """Forward directly."""
        self.log.append(("forward-direct",))


def make_live_client(log: list) -> MailClient:
    """A MailClient whose account and folders are fakes recording calls."""
    client = object.__new__(MailClient)
    drafts = type("Drafts", (), {"get": lambda self, id: FakeDraft(log, id)})()
    client.account = type("Account", (), {"drafts": drafts})()
    client._folder_by_id = lambda folder_id: type("Folder", (), {"get": lambda self, id: FakeItem(log)})()
    return client


@pytest.mark.parametrize("method", ["reply", "forward"])
def test_live_reply_and_forward_with_files_go_through_drafts(method: str, files: dict[str, Path]) -> None:
    log: list = []
    client = make_live_client(log)
    getattr(client, method)("inbox", "m1", subject="S", body="x", to=["a@corp.example"],
                            attachments=[files["report"]])
    assert log == [("save", method), ("attach", "report.pdf"), ("send", "D1", True)]


@pytest.mark.parametrize("method", ["save_reply_draft", "save_forward_draft"])
def test_live_reply_and_forward_drafts_with_files_stay_in_drafts(method: str, files: dict[str, Path]) -> None:
    log: list = []
    client = make_live_client(log)
    getattr(client, method)("inbox", "m1", subject="S", body="x", to=["a@corp.example"],
                            attachments=[files["report"], files["fig"]])
    kind = "reply" if "reply" in method else "forward"
    assert log == [("save", kind), ("attach", "report.pdf"), ("attach", "fig1.png")]


@pytest.mark.parametrize("method", ["reply", "forward"])
def test_live_reply_and_forward_without_files_unchanged(method: str) -> None:
    log: list = []
    getattr(make_live_client(log), method)("inbox", "m1", subject="S", body="x", to=["a@corp.example"])
    assert log == [(f"{method}-direct",)]
