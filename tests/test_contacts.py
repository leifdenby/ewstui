"""Your own address book (contacts.toml): synced from folders of person
notes, searched by the To / Cc lookup (Tab) along with the directory, and
`ewstui contacts sync` / `find`."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from ewstui import contacts
from ewstui.__main__ import main
from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.screens import ComposeScreen, PickPersonScreen, RecipientsArea
from textual.widgets import Static

ROMICK = """\
# Chris Romick

- **Org:** KNMI
- **Email:** chris.romick@knmi.nl
- **UWC role:** Team Leader System.
"""

ONVLEE = """\
# Jeanette Onvlee

- **Org:** KNMI (until 2026-10-31; retired 2026-11-01)
- **Email:** jeanette.onvlee@gmail.com (from November 2026). The KNMI address jeanette.onvlee@knmi.nl will expire soon.
"""

KORNICH = """\
# Heiner Körnich

- **Email:** heiner.kornich@smhi.se
"""

NO_EMAIL = """\
# Swapan Mallin

- **Role:** to fill in
"""


def write_notes(folder: Path) -> Path:
    """A folder of person notes, plus an exported-emails subfolder that
    must be ignored.

    Parameters
    ----------
    folder : Path
        Where to create it.

    Returns
    -------
    Path
        The folder.
    """
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Chris Romick.md").write_text(ROMICK, encoding="utf-8")
    (folder / "Jeanette Onvlee.md").write_text(ONVLEE, encoding="utf-8")
    (folder / "Heiner Körnich.md").write_text(KORNICH, encoding="utf-8")
    (folder / "Swapan Mallin.md").write_text(NO_EMAIL, encoding="utf-8")
    (folder / "emails").mkdir(exist_ok=True)
    (folder / "emails" / "ignored.md").write_text("- **Email:** nobody@x.test\n", encoding="utf-8")
    return folder


def make_older(path: Path, seconds: int = 10) -> None:
    """Push a file's modification time into the past.

    Parameters
    ----------
    path : Path
        The file.
    seconds : int, optional
        How far back.

    Returns
    -------
    None
    """
    t = path.stat().st_mtime - seconds
    os.utime(path, (t, t))


# -- notes and matching ------------------------------------------------------------


def test_parse_person_note_name_org_and_every_address(tmp_path):
    note = write_notes(tmp_path / "people") / "Jeanette Onvlee.md"
    found = contacts.parse_person_note(note)
    assert [(c.name, c.email, c.org) for c in found] == [
        ("Jeanette Onvlee", "jeanette.onvlee@gmail.com", "KNMI"),
        ("Jeanette Onvlee", "jeanette.onvlee@knmi.nl", "KNMI"),
    ]
    assert found[0].source.endswith("people/Jeanette Onvlee.md")


def test_parse_person_note_without_address_or_heading(tmp_path):
    folder = write_notes(tmp_path / "people")
    assert contacts.parse_person_note(folder / "Swapan Mallin.md") == []
    bare = folder / "Bare Name.md"
    bare.write_text("- **Email:** bare@x.test\n", encoding="utf-8")
    (found,) = contacts.parse_person_note(bare)
    assert (found.name, found.email, found.org) == ("Bare Name", "bare@x.test", "")


def test_find_contacts_parts_of_words_case_and_accents():
    book = [
        contacts.Contact("Heiner Körnich", "heiner.kornich@smhi.se"),
        contacts.Contact("Chris Romick", "chris.romick@knmi.nl", org="KNMI"),
    ]
    assert contacts.find_contacts(book, "KÖRN") == [("Heiner Körnich", "heiner.kornich@smhi.se")]
    assert contacts.find_contacts(book, "kornich hei") == [("Heiner Körnich", "heiner.kornich@smhi.se")]
    assert contacts.find_contacts(book, "knmi") == [("Chris Romick", "chris.romick@knmi.nl")]
    assert contacts.find_contacts(book, "nobody") == []
    assert contacts.find_contacts(book, "  ") == []


# -- sync ----------------------------------------------------------------------------


def test_sync_keeps_your_entries_and_comments(tmp_path):
    folder = write_notes(tmp_path / "people")
    path = tmp_path / "contacts.toml"
    path.write_text(
        '# my address book\n\n[[contact]]\nname = "Heleen ter Pelkwijk"  # Eumetcal\nemail = "heleen@knmi.nl"\n',
        encoding="utf-8",
    )
    make_older(path)
    result = contacts.sync_contacts_from_notes(path, [folder])
    assert not result.skipped
    assert result.notes == 4 and result.contacts == 4  # Jeanette twice, Swapan none, emails/ ignored
    text = path.read_text(encoding="utf-8")
    assert "# my address book" in text and "# Eumetcal" in text
    book = contacts.load_contacts(path)
    assert book[0] == contacts.Contact("Heleen ter Pelkwijk", "heleen@knmi.nl")
    assert {c.email for c in book[1:]} == {
        "chris.romick@knmi.nl", "jeanette.onvlee@gmail.com", "jeanette.onvlee@knmi.nl", "heiner.kornich@smhi.se",
    }
    assert "nobody@x.test" not in text
    assert sorted(result.added) == sorted(c.email for c in book[1:])


def test_sync_drops_a_deleted_note_and_skips_when_nothing_changed(tmp_path):
    folder = write_notes(tmp_path / "people")
    path = tmp_path / "contacts.toml"
    contacts.sync_contacts_from_notes(path, [folder])
    assert contacts.sync_contacts_from_notes(path, [folder]).skipped  # nothing newer than the file

    make_older(path)
    (folder / "Chris Romick.md").unlink()  # the folder's mtime changes
    result = contacts.sync_contacts_from_notes(path, [folder])
    assert result.removed == ["chris.romick@knmi.nl"]
    assert "chris.romick@knmi.nl" not in {c.email for c in contacts.load_contacts(path)}


def test_repeated_syncs_leave_your_grouped_entries_unchanged(tmp_path):
    """Regression: each sync added a blank line after every one of your
    entries. Group comments between entries must stay where they are."""
    folder = write_notes(tmp_path / "people")
    path = tmp_path / "contacts.toml"
    path.write_text(
        "# my partners\n\n# FMI\n\n"
        '[[contact]]\nname = "A"\nemail = "a@fmi.fi"\n\n'
        '[[contact]]\nname = "B"\nemail = "b@fmi.fi"\n\n'
        "# KNMI\n\n"
        '[[contact]]\nname = "C"\nemail = "c@knmi.nl"\n',
        encoding="utf-8",
    )
    contacts.sync_contacts_from_notes(path, [folder], force=True)
    once = path.read_text(encoding="utf-8")
    contacts.sync_contacts_from_notes(path, [folder], force=True)
    contacts.sync_contacts_from_notes(path, [folder], force=True)
    assert path.read_text(encoding="utf-8") == once
    assert once.startswith(
        "# my partners\n\n# FMI\n\n"
        '[[contact]]\nname = "A"\nemail = "a@fmi.fi"\n\n'
        '[[contact]]\nname = "B"\nemail = "b@fmi.fi"\n\n'
        "# KNMI\n\n"
        '[[contact]]\nname = "C"\nemail = "c@knmi.nl"\n\n'
        '[[contact]]\nname = "'
    )


def test_new_file_gets_the_explaining_header(tmp_path):
    folder = write_notes(tmp_path / "people")
    path = tmp_path / "sub" / "contacts.toml"
    contacts.sync_contacts_from_notes(path, [folder])
    assert path.read_text(encoding="utf-8").startswith("# ewstui address book")


def test_config_contacts_settings(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[contacts]\nsync_from = ["~/notes/people"]\n', encoding="utf-8")
    cfg = config_from_args(["--demo", "--config", str(config)])
    assert cfg.contacts_path == tmp_path / "contacts.toml"
    assert cfg.contacts_sync_from == [Path("~/notes/people").expanduser()]
    config.write_text("[contacts]\nsync_from = 3\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="sync_from must be a list"):
        config_from_args(["--demo", "--config", str(config)])


# -- command line --------------------------------------------------------------------


def write_config(tmp_path: Path) -> Path:
    """A config.toml syncing from a folder of test notes.

    Parameters
    ----------
    tmp_path : Path
        The test's directory.

    Returns
    -------
    Path
        The config file.
    """
    folder = write_notes(tmp_path / "people")
    config = tmp_path / "config.toml"
    config.write_text(f'[contacts]\nsync_from = ["{folder}"]\n', encoding="utf-8")
    return config


def test_cli_sync_then_find(tmp_path, capsys):
    config = write_config(tmp_path)
    assert main(["contacts", "sync", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "4 contacts from 4 notes" in out and "+ chris.romick@knmi.nl" in out
    assert main(["contacts", "find", "onvlee", "--json", "--config", str(config)]) == 0
    found = json.loads(capsys.readouterr().out)
    assert [p["email"] for p in found] == ["jeanette.onvlee@gmail.com", "jeanette.onvlee@knmi.nl"]
    assert main(["contacts", "find", "zzz", "--config", str(config)]) == 0
    assert "No matches." in capsys.readouterr().out


def test_cli_needs_a_command(tmp_path):
    with pytest.raises(SystemExit):
        main(["contacts", "--config", str(tmp_path / "config.toml")])
    with pytest.raises(SystemExit):
        main(["contacts", "find", "--config", str(tmp_path / "config.toml")])


# -- compose: Tab searches the address book too -----------------------------------------


@asynccontextmanager
async def composing(tmp_path: Path, find_people=None):
    """The demo app with a compose window whose address book is the test
    notes (and, optionally, a directory).

    Parameters
    ----------
    tmp_path : Path
        The test's directory.
    find_people : callable, optional
        The directory lookup; None for no directory.

    Yields
    ------
    tuple
        (app, pilot, screen).
    """
    path = tmp_path / "contacts.toml"
    contacts.sync_contacts_from_notes(path, [write_notes(tmp_path / "people")])
    screen = ComposeScreen()
    screen.find_people = find_people
    screen.find_contacts = contacts.build_contact_finder(path)
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "todo.txt")])
    app = EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(screen)
        await pilot.pause()
        yield app, pilot, screen


async def settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_tab_finds_someone_only_in_your_contacts(tmp_path):
    async with composing(tmp_path, DemoMailClient().find_people) as (app, pilot, screen):
        to = screen.query_one("#compose-to", RecipientsArea)
        to.focus()
        await pilot.press(*"romick", "tab")
        await settle(app, pilot)
        assert to.text == "Chris Romick <chris.romick@knmi.nl>"
        await settle(app, pilot)
        check = str(screen.query_one("#compose-to-check", Static).render())
        assert "✓ 1 in your contacts" in check and "not in the directory" not in check


async def test_tab_without_a_directory_uses_your_contacts(tmp_path):
    async with composing(tmp_path) as (app, pilot, screen):
        to = screen.query_one("#compose-to", RecipientsArea)
        to.focus()
        await pilot.press(*"onvlee", "tab")
        await settle(app, pilot)
        assert isinstance(app.screen, PickPersonScreen)  # both of Jeanette's addresses
        assert [e for _, e in app.screen._people] == ["jeanette.onvlee@gmail.com", "jeanette.onvlee@knmi.nl"]


async def test_your_contacts_come_first_and_each_address_once(tmp_path):
    def directory(query: str) -> list[tuple[str, str]]:
        return [("ROMICK Chris (KNMI)", "chris.romick@knmi.nl"), ("Chris Other", "chris.other@corp.example")]

    async with composing(tmp_path, directory) as (app, pilot, screen):
        to = screen.query_one("#compose-to", RecipientsArea)
        to.focus()
        await pilot.press(*"chris", "tab")
        await settle(app, pilot)
        assert isinstance(app.screen, PickPersonScreen)
        assert app.screen._people == [("Chris Romick", "chris.romick@knmi.nl"),
                                      ("Chris Other", "chris.other@corp.example")]
