"""
Where ewstui writes its log: a per-user, OS-appropriate location rather
than the current directory, unless overridden with --log-file or the
EWSTUI_LOG_FILE environment variable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

LOG_FILE_ENV = "EWSTUI_LOG_FILE"
LOG_FILE_NAME = "ewstui.log"


def build_default_log_path() -> Path:
    """Build the platform's conventional per-user path for ewstui's log.

    - macOS: ``~/Library/Logs/ewstui/ewstui.log``
    - Windows: ``%LOCALAPPDATA%\\ewstui\\Logs\\ewstui.log``
    - Linux and other Unix: ``$XDG_STATE_HOME/ewstui/ewstui.log``,
      falling back to ``~/.local/state/ewstui/ewstui.log``

    Nothing is created on disk; see ``ensure_log_dir``.

    Returns
    -------
    Path
        The default log file path for the current user and platform.
    """
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Logs" / "ewstui" / LOG_FILE_NAME
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local"
        return Path(base) / "ewstui" / "Logs" / LOG_FILE_NAME
    base = os.environ.get("XDG_STATE_HOME") or home / ".local" / "state"
    return Path(base) / "ewstui" / LOG_FILE_NAME


def resolve_log_path(cli_path: Path | None = None) -> Path:
    """Pick the log file: ``--log-file``, else ``$EWSTUI_LOG_FILE``, else
    the platform default.

    Parameters
    ----------
    cli_path : Path or None
        The ``--log-file`` value, if given.

    Returns
    -------
    Path
        The log file to write to (``~`` expanded).
    """
    if cli_path is not None:
        return cli_path.expanduser()
    env_path = os.environ.get(LOG_FILE_ENV)
    if env_path:
        return Path(env_path).expanduser()
    return build_default_log_path()


def ensure_log_dir(log_path: Path) -> None:
    """Create the directory that holds ``log_path``, if it's missing.

    Parameters
    ----------
    log_path : Path
        The log file whose parent directory is needed.

    Raises
    ------
    OSError
        If the directory can't be created.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
