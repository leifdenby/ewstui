"""C: hand an email to an LLM agent — the email and its attachments go in
a folder under the agent's workdir, and the agent starts in a new tab."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from ewstui import ask_agent
from ewstui.app import EwstuiApp
from ewstui.ask_agent import AgentConfigError, AgentSettings, NoTerminal, parse_settings
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.screens import AskAgentScreen

NOW = datetime(2026, 11, 20, 15, 32)
DETAIL = SimpleNamespace(subject="Re: Q3 budget — numbers?", internet_message_id="<abc@corp.example>")


# -- settings ----------------------------------------------------------------------


def test_defaults():
    s = parse_settings({})
    assert s.command == "claude" and s.email_subdir == "emails/{date}-{slug}"
    assert s.workdir == Path("~/.local/state/ewstui/agent").expanduser()


def test_workdir_expands_home():
    assert parse_settings({"workdir": "~/Notes"}).workdir == Path.home() / "Notes"


@pytest.mark.parametrize("bad", ["/tmp/emails", "~/emails", "../emails", "emails/../../x"])
def test_email_subdir_must_stay_inside_workdir(bad):
    with pytest.raises(AgentConfigError, match="inside workdir"):
        parse_settings({"email_subdir": bad})


def test_unknown_key_and_placeholder_rejected():
    with pytest.raises(AgentConfigError, match="Unknown setting"):
        parse_settings({"dir": "~/x"})
    with pytest.raises(AgentConfigError, match="placeholder"):
        parse_settings({"email_subdir": "{sender}"})


def test_config_file_llm_agent_table(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[llm-agent]\nworkdir = "~/Notes"\nemail_subdir = "inbox/{date}_{time}"\n')
    cfg = config_from_args(["--demo", "--config", str(cfg_path)])
    assert cfg.agent.workdir == Path.home() / "Notes" and cfg.agent.email_subdir == "inbox/{date}_{time}"
    cfg_path.write_text('[llm-agent]\nemail_subdir = "/abs"\n')
    with pytest.raises(SystemExit, match="config.toml"):
        config_from_args(["--demo", "--config", str(cfg_path)])


# -- the folder and the prompt -----------------------------------------------------


def test_task_folder_fills_in_placeholders(tmp_path):
    s = AgentSettings(workdir=tmp_path, email_subdir="emails/{account}/{date}-{time}-{slug}")
    assert ask_agent.task_folder(s, DETAIL.subject, NOW, "dmi") == tmp_path / "emails/dmi/2026-11-20-1532-q3-budget-numbers"


def test_task_folder_never_reuses_a_folder(tmp_path):
    s = AgentSettings(workdir=tmp_path)
    first = ask_agent.task_folder(s, "Hi", NOW)
    first.mkdir(parents=True)
    second = ask_agent.task_folder(s, "Hi", NOW)
    assert second.name == first.name + "-2"


def test_prompt_has_the_instruction_email_and_how_to_draft(tmp_path):
    prompt = ask_agent.build_prompt("  draft a polite no  ", tmp_path / "email.md", DETAIL,
                                    [tmp_path / "budget.xlsx"], "/venv/bin/ewstui --account dmi")
    assert "draft a polite no" in prompt
    assert str(tmp_path / "email.md") in prompt and str(tmp_path / "budget.xlsx") in prompt
    assert "in-reply-to: <abc@corp.example>" in prompt
    assert "/venv/bin/ewstui --account dmi draft FILE.md" in prompt
    assert "only add --send if I asked" in prompt


# -- opening the tab ---------------------------------------------------------------


@pytest.fixture
def which(monkeypatch):
    monkeypatch.setattr(ask_agent.shutil, "which", lambda name: f"/usr/bin/{name}")


def run_launch(env, settings=None, cwd=Path("/w")):
    calls = []
    ask_agent.launch(cwd, "do it", settings or AgentSettings(), env=env,
                     popen=lambda argv, **kw: calls.append(argv))
    return calls[0]


@pytest.mark.parametrize(("env", "expected"), [
    ({"TMUX": "/tmp/tmux", "GNOME_TERMINAL_SCREEN": "x"}, ["tmux", "new-window", "-c", "/w", "--", "claude", "do it"]),
    ({"GNOME_TERMINAL_SCREEN": "x"}, ["gnome-terminal", "--tab", "--working-directory=/w", "--", "claude", "do it"]),
    ({"KITTY_WINDOW_ID": "1"}, ["kitty", "@", "launch", "--type=tab", "--cwd=/w", "claude", "do it"]),
    ({"WEZTERM_PANE": "0"}, ["wezterm", "cli", "spawn", "--cwd", "/w", "--", "claude", "do it"]),
])
def test_launch_in_a_new_tab_of_this_terminal(which, env, expected):
    assert run_launch(env) == expected


@pytest.mark.parametrize("program", ["iTerm.app", "Apple_Terminal"])
def test_launch_on_macos_via_applescript(program):
    argv = run_launch({"TERM_PROGRAM": program}, cwd=Path("/my notes"))
    assert argv[:2] == ["osascript", "-e"]
    assert "cd '/my notes' && claude 'do it'" in argv[2]


def test_own_terminal_command(which):
    s = AgentSettings(terminal="foot --working-directory {cwd} {command}", command="claude --model opus")
    assert run_launch({}, s) == ["foot", "--working-directory", "/w", "claude", "--model", "opus", "do it"]
    s = AgentSettings(terminal="xterm -e bash -c {shell_command}")
    assert run_launch({}, s) == ["xterm", "-e", "bash", "-c", "cd /w && claude 'do it'"]


def test_no_known_terminal_gives_the_command_to_run_by_hand():
    with pytest.raises(NoTerminal) as e:
        ask_agent.launch(Path("/w"), "do it", AgentSettings(), env={}, popen=None)
    assert e.value.command == "cd /w && claude 'do it'"


# -- in the app --------------------------------------------------------------------


@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(ask_agent, "launch", lambda cwd, prompt, settings: calls.append((cwd, prompt)))
    return calls


def make_app(tmp_path) -> EwstuiApp:
    mail = DemoMailClient()
    mail._attachments["m1"] = [("budget.xlsx", "application/vnd.ms-excel", b"xlsx")]
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f'[llm-agent]\nworkdir = "{tmp_path / "notes"}"\nemail_subdir = "emails/{{slug}}"\n')
    cfg = config_from_args(["--demo", "--config", str(cfg_path), "--priority-file", str(tmp_path / "todo.txt")])
    return EwstuiApp(mail, DemoCalendarClient(), cfg)


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_C_hands_the_email_and_attachments_to_the_agent(tmp_path, launched):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("C")
        await pilot.pause()
        assert isinstance(app.screen, AskAgentScreen)
        await pilot.press(*"summarise it", "enter")
        await settle(app, pilot)
        assert not isinstance(app.screen, AskAgentScreen)
    (cwd, prompt), = launched
    assert cwd == tmp_path / "notes"
    folder = next((tmp_path / "notes" / "emails").iterdir())
    assert sorted(p.name for p in folder.iterdir()) == ["budget.xlsx", "email.md", "prompt.md"]
    assert "Attachments: budget.xlsx" in (folder / "email.md").read_text()
    assert "summarise it" in prompt and (folder / "prompt.md").read_text() == prompt


async def test_escape_does_nothing(tmp_path, launched):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("C", "escape")
        await settle(app, pilot)
    assert launched == [] and not (tmp_path / "notes").exists()


async def test_no_terminal_puts_the_command_on_the_clipboard(tmp_path, monkeypatch, system_clipboard):
    def no_terminal(cwd, prompt, settings):
        raise NoTerminal("cd /w && claude 'x'")

    monkeypatch.setattr(ask_agent, "launch", no_terminal)
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("C", *"x", "enter")
        await settle(app, pilot)
        assert system_clipboard[-1] == "cd /w && claude 'x'"
        assert any("on the clipboard" in n.message for n in app._notifications)
