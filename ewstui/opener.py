"""
Open a file with the platform's default application, and pick the
default place attachments are saved to.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


class OpenError(RuntimeError):
    """No way to open files on this system, or the opener failed to start."""


def open_with_default_app(path: Path) -> None:
    """Hand `path` to the OS default handler without blocking or tying
    the child to our terminal. Raises OpenError if that's not possible.
    """
    if sys.platform == "win32":
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]  # Windows-only
        except OSError as e:
            raise OpenError(str(e)) from e
        return

    if sys.platform == "darwin":
        command = ["open", str(path)]
    else:
        opener = shutil.which("xdg-open")
        if opener is None:
            raise OpenError("xdg-open not found (install xdg-utils)")
        command = [opener, str(path)]
    try:
        subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
    except OSError as e:
        raise OpenError(str(e)) from e


def _safe_dirname(name: str) -> str:
    # Account names / email addresses are user-chosen; keep them to a
    # single, harmless path component.
    return re.sub(r"[^\w.@+-]", "_", name).strip(".") or "default"


def default_attachment_dir(account: str | None, email: str | None) -> Path:
    """macOS: ~/Downloads/Attachments/<account> (the email address if no
    --account profile is in use). Elsewhere: ~/Downloads/ewstui-attachments.
    """
    downloads = Path.home() / "Downloads"
    if sys.platform == "darwin":
        return downloads / "Attachments" / _safe_dirname(account or email or "default")
    return downloads / "ewstui-attachments"
