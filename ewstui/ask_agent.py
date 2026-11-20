"""
Hand an email to an LLM agent (by default Claude Code): the email, its
attachments and your instruction go into a folder, and the agent starts
in a new terminal tab with the instruction as its opening prompt.

Configured in the config file's ``[llm-agent]`` table::

    [llm-agent]
    workdir = "~/Notes"                    # where the agent session starts
    email_subdir = "emails/{date}-{slug}"  # where the email goes, inside workdir
    command = "claude"                     # the agent CLI; prompt is its last argument
    terminal = ""                          # own launcher, with {cwd} and {command} or {shell_command}

The agent acts on the mailbox through ewstui's own command line
(``ewstui draft`` and friends), so nothing is sent without you seeing it.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from .export import _slug, message_text

DEFAULT_WORKDIR = Path("~/.local/state/ewstui/agent")
DEFAULT_EMAIL_SUBDIR = "emails/{date}-{slug}"
DEFAULT_COMMAND = "claude"
KEYS = ("workdir", "email_subdir", "command", "terminal")
PLACEHOLDERS = ("date", "time", "slug", "account")


class AgentConfigError(ValueError):
    """A bad [llm-agent] setting; the message names it."""


class NoTerminal(RuntimeError):
    """No known terminal to open a tab in; `.command` is what to run by hand."""

    def __init__(self, command: str):
        super().__init__("couldn't tell which terminal this is to open a new tab in")
        self.command = command


@dataclass
class AgentSettings:
    workdir: Path = field(default_factory=lambda: DEFAULT_WORKDIR.expanduser())
    email_subdir: str = DEFAULT_EMAIL_SUBDIR
    command: str = DEFAULT_COMMAND
    terminal: str = ""


def parse_settings(table: dict) -> AgentSettings:
    """The [llm-agent] table (plain dict) as AgentSettings, checked."""
    unknown = set(table) - set(KEYS)
    if unknown:
        raise AgentConfigError(f"Unknown setting(s) in [llm-agent]: {', '.join(sorted(unknown))}. "
                               f"Allowed: {', '.join(KEYS)}")
    for key in KEYS:
        if key in table and not isinstance(table[key], str):
            raise AgentConfigError(f"[llm-agent] {key} must be a string")
    settings = AgentSettings()
    if table.get("workdir"):
        settings.workdir = Path(table["workdir"]).expanduser()
    if table.get("email_subdir"):
        settings.email_subdir = table["email_subdir"]
    sub = PurePosixPath(settings.email_subdir.replace("\\", "/"))
    if sub.is_absolute() or settings.email_subdir.startswith("~") or ".." in sub.parts:
        raise AgentConfigError(f"[llm-agent] email_subdir must be a folder inside workdir, not {settings.email_subdir!r}")
    names = set(re.findall(r"{(\w*)}", settings.email_subdir))
    if names - set(PLACEHOLDERS):
        raise AgentConfigError(f"[llm-agent] email_subdir: unknown placeholder(s) "
                               f"{', '.join('{' + n + '}' for n in sorted(names - set(PLACEHOLDERS)))}; "
                               f"use {', '.join('{' + p + '}' for p in PLACEHOLDERS)}")
    if table.get("command"):
        settings.command = table["command"]
    settings.terminal = table.get("terminal", "") or ""
    return settings


def task_folder(settings: AgentSettings, subject: str, now: datetime, account: str | None = None) -> Path:
    """A new folder for this email under workdir (not created yet): a
    folder that's already there gets -2, -3, ... appended."""
    sub = settings.email_subdir.format(
        date=now.strftime("%Y-%m-%d"), time=now.strftime("%H%M"), slug=_slug(subject), account=account or "default",
    )
    folder = settings.workdir / sub
    candidate, n = folder, 2
    while candidate.exists():
        candidate = folder.with_name(f"{folder.name}-{n}")
        n += 1
    return candidate


def write_email(folder: Path, detail, attachment_names: list[str]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "email.md"
    path.write_text(message_text(detail, attachment_names))
    return path


def ewstui_command(account: str | None) -> str:
    """How the agent should run ewstui: this very executable (a plain
    `ewstui` may not be on its PATH), with the account in use."""
    exe = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    cmd = shlex.quote(str(exe)) if exe and exe.exists() and exe.name.startswith("ewstui") \
        else f"{shlex.quote(sys.executable)} -m ewstui"
    return f"{cmd} --account {shlex.quote(account)}" if account else cmd


def build_prompt(instruction: str, email_path: Path, detail, attachments: list[Path], ewstui: str) -> str:
    message_id = detail.internet_message_id or "(none)"
    files = "\n".join(f"- {p}" for p in attachments) or "- (none)"
    return f"""I'm handing you an email from my Exchange mailbox to work on.

The email (headers, then its text): {email_path}
Its Message-ID: {message_id}
Its attachments, downloaded next to it:
{files}

What I'd like you to do:
{instruction.strip()}

You can act on my mailbox with ewstui's command line, `{ewstui}`:
- `{ewstui} draft FILE.md` saves an email written in Markdown to my Drafts. For a reply,
  start the file with a header block:
      ---
      in-reply-to: {message_id}
      ---
  (`reply-all: yes`, `to:`, `cc:`, `subject:`, `attach:` also work; see `{ewstui} draft -h`).
  Save to Drafts so I can check it; only add --send if I asked you to send it.
- `{ewstui} calendar` lists my calendar, `{ewstui} search` searches my mailbox,
  `{ewstui} contacts` looks people up; each has -h.
"""


def _terminal_argv(cwd: Path, command: list[str], settings: AgentSettings, env) -> list[str] | None:
    """The command that opens a new tab running `command` in `cwd`, for
    the terminal ewstui runs in; None if it can't tell which."""
    shell_line = f"cd {shlex.quote(str(cwd))} && {shlex.join(command)}"
    if settings.terminal:
        # {command}: the agent's argv, as separate words (no quoting to get
        # wrong). {shell_command}: one word, a shell line with the cd, for
        # launchers that take a command string (e.g. bash -c {shell_command}).
        argv = []
        for part in shlex.split(settings.terminal):
            if part == "{command}":
                argv.extend(command)
            else:
                argv.append(part.replace("{cwd}", str(cwd)).replace("{shell_command}", shell_line))
        return argv
    if env.get("TMUX") and shutil.which("tmux"):
        return ["tmux", "new-window", "-c", str(cwd), "--", *command]
    if env.get("GNOME_TERMINAL_SCREEN") and shutil.which("gnome-terminal"):
        return ["gnome-terminal", "--tab", f"--working-directory={cwd}", "--", *command]
    if env.get("KITTY_WINDOW_ID") and shutil.which("kitty"):
        return ["kitty", "@", "launch", "--type=tab", f"--cwd={cwd}", *command]
    if env.get("WEZTERM_PANE") and shutil.which("wezterm"):
        return ["wezterm", "cli", "spawn", "--cwd", str(cwd), "--", *command]
    program = env.get("TERM_PROGRAM", "")
    if program == "iTerm.app":
        script = ('tell application "iTerm2" to tell current window\n'
                  "  create tab with default profile\n"
                  f"  tell current session to write text {_applescript_string(shell_line)}\n"
                  "end tell")
        return ["osascript", "-e", script]
    if program == "Apple_Terminal":
        script = f'tell application "Terminal"\n  activate\n  do script {_applescript_string(shell_line)}\nend tell'
        return ["osascript", "-e", script]
    return None


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def launch(cwd: Path, prompt: str, settings: AgentSettings, env=None, popen=subprocess.Popen) -> list[str]:
    """Start the agent with `prompt` in a new tab of this terminal, in
    `cwd`. Returns the launcher's argv; raises NoTerminal (with the
    command to run by hand) if there's no terminal it knows."""
    env = os.environ if env is None else env
    command = [*shlex.split(settings.command), prompt]
    argv = _terminal_argv(cwd, command, settings, env)
    if argv is None:
        raise NoTerminal(f"cd {shlex.quote(str(cwd))} && {shlex.join(command)}")
    popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
          start_new_session=True)
    return argv
