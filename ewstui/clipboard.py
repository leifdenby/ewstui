"""
Copying to the system clipboard.

Textual's `App.copy_to_clipboard` sends the text to the terminal as an
OSC 52 escape sequence, which only some terminals honour (iTerm2 with
clipboard access allowed, kitty, WezTerm, Ghostty, ...; not macOS
Terminal). On macOS the app also hands the text to `pbcopy`, so copying
works whatever the terminal.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)

# Give up on pbcopy after this long rather than freeze the UI.
PBCOPY_TIMEOUT = 2.0  # seconds


def copy_to_system_clipboard(text: str) -> bool:
    """Put `text` on the macOS clipboard with `pbcopy`, if there is one.

    Parameters
    ----------
    text : str
        The text to copy.

    Returns
    -------
    bool
        True if `pbcopy` took the text; False when not on macOS, `pbcopy`
        isn't there or it failed (the OSC 52 copy may still have worked).
    """
    if sys.platform != "darwin":
        return False
    pbcopy = shutil.which("pbcopy")
    if pbcopy is None:
        return False
    env = dict(os.environ)
    # pbcopy reads its input in the locale's encoding: make sure that's UTF-8
    # (a bare C locale garbles å, ø, é, ...).
    if "utf-8" not in env.get("LANG", "").lower().replace("utf8", "utf-8"):
        env["LANG"] = "en_US.UTF-8"
        env.pop("LC_ALL", None)  # it would override LANG
    try:
        subprocess.run(
            [pbcopy], input=text.encode("utf-8"), env=env,
            check=True, timeout=PBCOPY_TIMEOUT, capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("pbcopy failed: %s", e)
        return False
    return True
