from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from .app import EwstuiApp
from .config import config_from_args

LOG_FILE = Path("ewstui.log")


def main(argv: list[str] | None = None) -> int:
    cfg = config_from_args(argv)

    # exchangelib is chatty at INFO; keep it quiet unless the user wants
    # verbose diagnostics. Logging goes to a file, never stdout/stderr,
    # since Textual owns the terminal once the app starts.
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.DEBUG if cfg.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if cfg.demo:
        from .demo_backend import DemoCalendarClient, DemoMailClient

        mail_client = DemoMailClient(page_size=cfg.page_size)
        calendar_client = DemoCalendarClient()
    else:
        from . import auth
        from .ews_client import CalendarClient, MailClient

        if cfg.forget_password:
            return _forget_password(cfg)
        if cfg.debug:
            print(f"Debug logging to {LOG_FILE.resolve()}", file=sys.stderr)
        try:
            if cfg.ews_url:
                auth.probe_endpoint(cfg.ews_url, verify_ssl=cfg.verify_ssl)
            account = auth.get_account(cfg)
            auth.verify_account(account)
        except Exception as e:  # noqa: BLE001 - report anything, never start a blank TUI
            reason = str(e) if isinstance(e, auth.AuthError) else auth.explain_error(e)
            print(f"\nConnection failed: {reason}", file=sys.stderr)
            if cfg.debug:
                traceback.print_exc()
            else:
                print("Re-run with --debug for a traceback and full EWS traffic in the log.", file=sys.stderr)
            _drop_rejected_keychain_password(cfg, e)
            return 1
        _offer_to_save_password(cfg, account)
        mail_client = MailClient(account, page_size=cfg.page_size)
        calendar_client = CalendarClient(account)

    # Only now, after a working connection, so typos never get persisted.
    _save_account_updates(cfg)
    app = EwstuiApp(mail_client, calendar_client, cfg)
    app.run()
    return 0


def _save_account_updates(cfg) -> None:
    from . import config_file

    if not cfg.account or not cfg.pending_account_updates:
        return
    try:
        config_file.save_account(cfg.config_path, cfg.account, cfg.pending_account_updates)
    except (OSError, config_file.ConfigFileError) as e:
        print(f"Couldn't save account {cfg.account!r} to {cfg.config_path}: {e}", file=sys.stderr)
        return
    keys = ", ".join(cfg.pending_account_updates)
    print(f"Saved {keys} to account {cfg.account!r} in {cfg.config_path}", file=sys.stderr)


def _forget_password(cfg) -> int:
    from . import auth, keychain

    username = auth.login_username(cfg)
    if keychain.available() and keychain.delete_password(username, cfg.ews_url):
        print(f"Removed the Keychain password for {username}", file=sys.stderr)
    else:
        print(f"No Keychain password stored for {username}", file=sys.stderr)
    return 0


def _offer_to_save_password(cfg, account) -> None:
    """After a verified login with a typed password, offer to keep it in
    the Keychain. Only asked interactively, and only once it's known good.
    """
    from . import keychain

    if cfg.password_source != "prompt" or not cfg.use_keychain or not keychain.available():
        return
    if not sys.stdin.isatty():
        return
    creds = account.protocol.credentials
    answer = input("Save this password in the macOS Keychain (unlock with Touch ID next time)? [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        return
    try:
        keychain.save_password(creds.username, cfg.ews_url, creds.password)
    except Exception as e:  # noqa: BLE001 - saving is a convenience; never block startup
        print(f"Couldn't save to the Keychain: {e}", file=sys.stderr)
        return
    print("Saved. Use --forget-password to remove it.", file=sys.stderr)


def _drop_rejected_keychain_password(cfg, error: Exception) -> None:
    """A stored password the server rejects (e.g. after a password
    change) would fail on every start — remove it so the next run
    prompts again.
    """
    from exchangelib.errors import UnauthorizedError

    from . import auth, keychain

    if cfg.password_source != "keychain" or not isinstance(error.__cause__, UnauthorizedError):
        return
    username = auth.login_username(cfg)
    keychain.delete_password(username, cfg.ews_url)
    print(
        f"The password stored in the Keychain for {username} was rejected and has been removed. "
        "Run again to enter the new one.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
