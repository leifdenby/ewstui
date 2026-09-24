"""
Optional password storage in the OS keychain (macOS Keychain via
`keyring`), unlocked with Touch ID on each read.

The password lives in the user's login Keychain as a generic password
item. Before reading it we require user presence through macOS
LocalAuthentication (Touch ID, falling back to the Mac login password).
That check is enforced by ewstui, not by the Keychain item itself —
Keychain items gated by biometrics at the OS level need a signed app
with keychain entitlements, which a Python CLI doesn't have.
"""
from __future__ import annotations

import logging
import sys
import threading

log = logging.getLogger(__name__)

SERVICE = "ewstui"
PRESENCE_TIMEOUT = 60  # seconds to wait for the Touch ID dialog


def _item_key(username: str, ews_url: str | None) -> str:
    return f"{username} @ {ews_url or 'autodiscover'}"


def available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import keyring  # noqa: F401
        import LocalAuthentication  # noqa: F401
    except ImportError:
        return False
    return True


def require_user_presence(reason: str) -> bool:
    """Show the macOS Touch ID / password dialog. True if the user
    authenticated, False if they cancelled, failed, or it's unavailable.
    """
    try:
        import LocalAuthentication as LA
    except ImportError:
        return False

    ctx = LA.LAContext.alloc().init()
    policy = LA.LAPolicyDeviceOwnerAuthentication  # Touch ID, or Mac password if no sensor
    ok, err = ctx.canEvaluatePolicy_error_(policy, None)
    if not ok:
        log.warning("LocalAuthentication unavailable: %s", err)
        return False

    done = threading.Event()
    result = {"ok": False}

    def reply(success, error):
        result["ok"] = bool(success)
        if error is not None:
            log.info("LocalAuthentication declined: %s", error)
        done.set()

    ctx.evaluatePolicy_localizedReason_reply_(policy, reason, reply)
    if not done.wait(PRESENCE_TIMEOUT):
        ctx.invalidate()
        return False
    return result["ok"]


def get_password(username: str, ews_url: str | None) -> str | None:
    """Stored password after a successful Touch ID check, else None
    (nothing stored, or the user cancelled/failed the check).
    """
    import keyring

    try:
        password = keyring.get_password(SERVICE, _item_key(username, ews_url))
    except Exception:  # noqa: BLE001 - a broken keychain shouldn't block login
        log.warning("keychain read failed", exc_info=True)
        return None
    if password is None:
        return None
    if not require_user_presence(f"unlock the Exchange password for {username}"):
        return None
    return password


def save_password(username: str, ews_url: str | None, password: str) -> None:
    import keyring

    keyring.set_password(SERVICE, _item_key(username, ews_url), password)


def delete_password(username: str, ews_url: str | None) -> bool:
    """Remove the stored item. True if something was deleted."""
    import keyring
    from keyring.errors import PasswordDeleteError

    try:
        keyring.delete_password(SERVICE, _item_key(username, ews_url))
    except PasswordDeleteError:
        return False
    return True
