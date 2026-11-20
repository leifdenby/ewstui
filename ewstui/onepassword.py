"""
Read the account password from 1Password with the `op` CLI.

Read-only: the user creates the item themselves and points ewstui at it
with a secret reference (`password_op_ref = "op://Vault/Item/password"`).
`op` handles unlocking (desktop-app integration / biometrics, or an
`op signin` session); stdin stays attached so its prompts still work.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger(__name__)

READ_TIMEOUT = 120  # seconds; allows for the 1Password unlock dialog


def available() -> bool:
    return shutil.which("op") is not None


def read(ref: str) -> str | None:
    """The secret at `ref`, or None if `op` is missing or the read failed
    (not signed in, item not found, unlock cancelled)."""
    try:
        result = subprocess.run(
            ["op", "read", "--no-newline", ref],
            capture_output=True,
            text=True,
            timeout=READ_TIMEOUT,
        )
    except FileNotFoundError:
        log.warning("1Password CLI `op` not found")
        return None
    except subprocess.TimeoutExpired:
        log.warning("`op read` timed out after %ss", READ_TIMEOUT)
        return None
    if result.returncode != 0:
        log.warning("`op read %s` failed: %s", ref, result.stderr.strip())
        return None
    return result.stdout or None
