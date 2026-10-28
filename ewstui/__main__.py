from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from .app import EwstuiApp
from .config import config_from_args

LOG_FILE = Path("ewstui.log")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["export"]:
        return _export(argv[1:])
    cfg = config_from_args(argv)
    _setup_logging(cfg)

    if cfg.demo:
        from .demo_backend import DEMO_ROOMS, DemoCalendarClient, DemoMailClient

        calendar_client = DemoCalendarClient()
        mail_client = DemoMailClient(page_size=cfg.page_size, calendar=calendar_client, with_invites=True)
        cfg.rooms = cfg.rooms or list(DEMO_ROOMS)  # so "find a room" (f) has something to show
    else:
        from .ews_client import CalendarClient, MailClient

        if cfg.forget_password:
            return _forget_password(cfg)
        account = _connect(cfg)
        if account is None:
            return 1
        _offer_to_save_password(cfg, account)
        mail_client = MailClient(account, page_size=cfg.page_size)
        calendar_client = CalendarClient(account)

    # Only now, after a working connection, so typos never get persisted.
    _save_account_updates(cfg)
    app = EwstuiApp(mail_client, calendar_client, cfg)
    app.run()
    return 0


def _setup_logging(cfg) -> None:
    # exchangelib is chatty at INFO; keep it quiet unless the user wants
    # verbose diagnostics. Logging goes to a file, never stdout/stderr,
    # since Textual owns the terminal once the app starts.
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.DEBUG if cfg.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _connect(cfg):
    """Log in and check the connection (probe, then one Inbox fetch).
    The exchangelib Account, or None after printing why it failed."""
    from . import auth

    if cfg.debug:
        print(f"Debug logging to {LOG_FILE.resolve()}", file=sys.stderr)
    offered = None  # auth schemes the server advertises, once probed
    try:
        if cfg.ews_url:
            offered = auth.probe_endpoint(
                cfg.ews_url, verify_ssl=cfg.verify_ssl, auth_type=auth.password_auth_type(cfg)
            )
        account = auth.get_account(cfg)
        auth.verify_account(account, offered=offered)
    except Exception as e:  # noqa: BLE001 - report anything, never start a blank TUI
        reason = str(e) if isinstance(e, auth.AuthError) else auth.explain_error(e)
        print(f"\nConnection failed: {reason}", file=sys.stderr)
        if cfg.debug:
            traceback.print_exc()
        else:
            print("Re-run with --debug for a traceback and full EWS traffic in the log.", file=sys.stderr)
        _drop_rejected_keychain_password(cfg, e, offered)
        return None
    return account


def _export(argv: list[str]) -> int:
    """`ewstui export FOLDER --out DIR` / `ewstui export --update DIR`:
    copy a mail folder to files (see export.py). Any other options are
    the usual connection/account ones."""
    import argparse

    from . import __version__, export

    from .config import build_arg_parser

    p = argparse.ArgumentParser(
        prog="ewstui export",
        usage="ewstui export FOLDER --out DIR [options]\n       ewstui export --update DIR [options]",
        description="Copy a mail folder to text files + index.json (re-runs add what's new). FOLDER is its "
        'path, e.g. "Projects/0 - Proposals/SBEP" (any case). Connection options (--account, --email, '
        "--auth, ...) are as for ewstui itself.",
    )
    p.add_argument("--out", type=Path, help="Directory to export into (created if needed)")
    p.add_argument("--update", type=Path, metavar="DIR",
                   help="Refresh an earlier export: folder, account and options are read from DIR/index.json")
    p.add_argument("--with-sent", action="store_true",
                   help="Also export your replies from Sent Items to the folder's conversations")
    p.add_argument("--no-attachments", action="store_true", help="Don't download attachments")
    ns, rest = p.parse_known_args(argv)
    # FOLDER is what's left once ewstui's own options (and their values)
    # are taken out: only that parser knows which options take a value.
    _, leftover = build_arg_parser().parse_known_args(rest)
    if len(leftover) > 1 or any(arg.startswith("-") for arg in leftover):
        p.error(f"unrecognized arguments: {' '.join(leftover)}")
    ns.folder = leftover[0] if leftover else None
    if ns.folder:
        rest.remove(ns.folder)

    try:
        if ns.update:
            index = export.load_index(ns.update)
            if index is None:
                p.error(f"no {export.INDEX} in {ns.update}: nothing to update")
            source = index["source"]
            out_dir, folder_path = ns.update, source["folder_path"]
            with_sent = ns.with_sent or source.get("with_sent", False)
            attachments = source.get("attachments", True) and not ns.no_attachments
            if source.get("account") and "--account" not in rest:
                rest = ["--account", source["account"], *rest]
        else:
            if not ns.folder or not ns.out:
                p.error("give a FOLDER and --out DIR (or --update DIR)")
            out_dir, folder_path = ns.out, ns.folder
            with_sent, attachments = ns.with_sent, not ns.no_attachments
    except export.ExportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    cfg = config_from_args(rest)
    _setup_logging(cfg)
    if cfg.demo:
        from .demo_backend import DemoMailClient

        client, mailbox = DemoMailClient(), DemoMailClient.my_address
    else:
        from .ews_client import MailClient

        account = _connect(cfg)
        if account is None:
            return 1
        client, mailbox = MailClient(account), cfg.email

    def progress(done: int, total: int) -> None:
        if sys.stderr.isatty():
            print(f"\r{done}/{total} emails", end="" if done < total else "\n", file=sys.stderr)

    try:
        path, folder = export.find_folder(client.list_folders(), folder_path)
        source = {
            "mailbox": mailbox,
            "account": cfg.account,
            "ews_url": cfg.ews_url,
            "folder_path": path,
            "folder_id": folder.id,
            "with_sent": with_sent,
            "attachments": attachments,
        }
        exported_by = {
            "tool": "ewstui",
            "version": __version__,
            "git_commit": export.git_commit(),
            "command": ["ewstui", "export", *argv],
        }
        stats = export.export_folder(client, folder, out_dir, source=source, exported_by=exported_by,
                                     with_sent=with_sent, attachments=attachments, progress=progress)
    except export.ExportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(
        f"{path}: {stats.folder_total} emails in the folder"
        + (f" + {stats.from_sent} of your replies" if with_sent else "")
        + f" -> {out_dir}\n"
        f"  {stats.new} new, {stats.updated} updated, {stats.unchanged} unchanged; "
        f"{stats.attachments} attachments"
        + (f", {stats.skipped_embedded} embedded items skipped" if stats.skipped_embedded else "")
    )
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


def _drop_rejected_keychain_password(cfg, error: Exception, offered: list[str] | None = None) -> None:
    """A stored password the server rejects (e.g. after a password
    change) would fail on every start — remove it so the next run
    prompts again. Kept if the server doesn't offer the auth method at
    all (`offered`, from the probe): then the password was never tested.
    """
    from exchangelib.errors import UnauthorizedError

    from . import auth, keychain

    if cfg.password_source != "keychain" or not isinstance(error.__cause__, UnauthorizedError):
        return
    if auth.not_offered(auth.password_auth_type(cfg), offered):
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
