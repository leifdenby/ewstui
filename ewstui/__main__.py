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
    if argv[:1] == ["search"]:
        return _search(argv[1:])
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


def _split_positionals(p, argv: list[str]) -> tuple:
    """Parse a subcommand's own options and separate its positional
    arguments from ewstui's connection options.

    The positionals are what's left once ewstui's own options (and their
    values) are taken out: only that parser knows which options take a value.

    Parameters
    ----------
    p : argparse.ArgumentParser
        The subcommand's parser (its own options only).
    argv : list[str]
        The arguments after the subcommand name.

    Returns
    -------
    tuple[argparse.Namespace, list[str], list[str]]
        The subcommand's options, its positional arguments, and the
        remaining (connection) arguments for ``config_from_args``.
    """
    from .config import build_arg_parser

    ns, rest = p.parse_known_args(argv)
    _, leftover = build_arg_parser().parse_known_args(rest)
    unknown = [arg for arg in leftover if arg.startswith("-")]
    if unknown:
        p.error(f"unrecognized arguments: {' '.join(unknown)}")
    for arg in leftover:
        rest.remove(arg)
    return ns, leftover, rest


def _open_mail_client(cfg) -> tuple | None:
    """Connect for a non-interactive subcommand (`export`, `search`).

    Parameters
    ----------
    cfg : Config
        From ``config_from_args``; ``--demo`` gives the demo mailbox.

    Returns
    -------
    tuple[MailClient or DemoMailClient, str] or None
        The client and the mailbox address, or None if the connection
        failed (the reason has been printed).
    """
    _setup_logging(cfg)
    if cfg.demo:
        from .demo_backend import DemoMailClient

        return DemoMailClient(), DemoMailClient.my_address
    from .ews_client import MailClient

    account = _connect(cfg)
    if account is None:
        return None
    return MailClient(account), cfg.email


def _export(argv: list[str]) -> int:
    """`ewstui export FOLDER --out DIR`, `ewstui export --message ID --out DIR`
    or `ewstui export --update DIR`: copy a mail folder, or selected emails,
    to files (see export.py). Any other options are the usual
    connection/account ones."""
    import argparse

    from . import __version__, export

    p = argparse.ArgumentParser(
        prog="ewstui export",
        usage="ewstui export FOLDER --out DIR [options]\n"
        "       ewstui export --message ID [--message ID ...] [--thread] --out DIR [options]\n"
        "       ewstui export --update DIR [options]",
        description="Copy a mail folder, or selected emails, to text files + index.json (re-runs add what's "
        'new). FOLDER is its path, e.g. "Projects/0 - Proposals/SBEP" (any case). Message-IDs are as '
        "`ewstui search` prints them. Connection options (--account, --email, --auth, ...) are as for "
        "ewstui itself.",
    )
    p.add_argument("--out", type=Path, help="Directory to export into (created if needed)")
    p.add_argument("--update", type=Path, metavar="DIR",
                   help="Refresh an earlier export: folder/emails, account and options are read from DIR/index.json")
    p.add_argument("--message", action="append", default=[], metavar="ID",
                   help="Export this email (its Internet Message-ID, <...@host>) from whichever folder it is in; "
                   "repeat for more. Re-running into the same --out adds to it")
    p.add_argument("--thread", action="store_true", help="With --message: also export the rest of each email's "
                   "conversation, from any folder (incl. your replies in Sent Items)")
    p.add_argument("--with-sent", action="store_true",
                   help="With FOLDER: also export your replies from Sent Items to the folder's conversations")
    p.add_argument("--no-attachments", action="store_true", help="Don't download attachments")
    ns, positionals, rest = _split_positionals(p, argv)
    if len(positionals) > 1:
        p.error(f"unrecognized arguments: {' '.join(positionals[1:])}")
    ns.folder = positionals[0] if positionals else None
    if ns.thread and not ns.message and not ns.update:
        p.error("--thread goes with --message")
    if ns.message and ns.folder:
        p.error("give either a FOLDER or --message, not both")

    try:
        if ns.update:
            index = export.load_index(ns.update)
            if index is None:
                p.error(f"no {export.INDEX} in {ns.update}: nothing to update")
            source = index["source"]
            out_dir = ns.update
            kind = source.get("kind", "folder")
            folder_path = source.get("folder_path")
            message_ids = [*source.get("message_ids", []), *ns.message]
            thread = ns.thread or source.get("thread", False)
            with_sent = ns.with_sent or source.get("with_sent", False)
            attachments = source.get("attachments", True) and not ns.no_attachments
            if source.get("account") and "--account" not in rest:
                rest = ["--account", source["account"], *rest]
        else:
            if not (ns.folder or ns.message) or not ns.out:
                p.error("give a FOLDER or --message ID, and --out DIR (or --update DIR)")
            out_dir, folder_path, message_ids = ns.out, ns.folder, ns.message
            kind = "messages" if ns.message else "folder"
            thread, with_sent, attachments = ns.thread, ns.with_sent, not ns.no_attachments
    except export.ExportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    cfg = config_from_args(rest)
    opened = _open_mail_client(cfg)
    if opened is None:
        return 1
    client, mailbox = opened

    def progress(done: int, total: int) -> None:
        if sys.stderr.isatty():
            print(f"\r{done}/{total} emails", end="" if done < total else "\n", file=sys.stderr)

    exported_by = {
        "tool": "ewstui",
        "version": __version__,
        "git_commit": export.git_commit(),
        "command": ["ewstui", "export", *argv],
    }
    try:
        if kind == "messages":
            source = {"mailbox": mailbox, "account": cfg.account, "ews_url": cfg.ews_url, "kind": "messages",
                      "attachments": attachments}
            stats = export.export_messages(client, message_ids, out_dir, source=source, exported_by=exported_by,
                                           thread=thread, attachments=attachments, progress=progress)
            what = f"{stats.folder_total} emails" + (" (with their threads)" if thread else "")
        else:
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
            stats = export.export_folder(client, folder, out_dir, source=source, exported_by=exported_by,
                                         with_sent=with_sent, attachments=attachments, progress=progress)
            what = (f"{path}: {stats.folder_total} emails in the folder"
                    + (f" + {stats.from_sent} of your replies" if with_sent else ""))
    except export.ExportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(
        f"{what} -> {out_dir}\n"
        f"  {stats.new} new, {stats.updated} updated, {stats.unchanged} unchanged; "
        f"{stats.attachments} attachments"
        + (f", {stats.skipped_embedded} embedded items skipped" if stats.skipped_embedded else "")
    )
    return 0


def _search(argv: list[str]) -> int:
    """`ewstui search QUERY [--folder PATH ...]`: Exchange's text search,
    printed with each hit's Message-ID for `ewstui export --message` (see
    cli_search.py). Any other options are the usual connection/account ones.

    Parameters
    ----------
    argv : list[str]
        The arguments after ``search``.

    Returns
    -------
    int
        Exit code: 0 (also with no hits), 1 on connection or folder errors.
    """
    import argparse
    import json

    from . import cli_search, export

    p = argparse.ArgumentParser(
        prog="ewstui search",
        usage="ewstui search QUERY [--folder PATH ...] [--limit N] [--json] [options]",
        description="Search the mailbox with Exchange's own search (words in the subject, body and people; a "
        "word also matches the start of a longer one), in the Inbox and Sent Items unless --folder says "
        "otherwise. Prints each hit's Message-ID, for `ewstui export --message`. Connection options "
        "(--account, --email, --auth, ...) are as for ewstui itself.",
    )
    p.add_argument("--folder", action="append", default=[], metavar="PATH",
                   help='Search this folder (path, any case; repeat for more) instead of Inbox + Sent Items; '
                   f'"{cli_search.ALL_FOLDERS}" searches every mail folder')
    p.add_argument("--limit", type=int, default=25, help="Show at most this many hits, newest first (default 25)")
    p.add_argument("--json", action="store_true", help="Print the hits as a JSON list")
    ns, positionals, rest = _split_positionals(p, argv)
    query = " ".join(positionals).strip()
    if not query:
        p.error("give a QUERY")

    opened = _open_mail_client(config_from_args(rest))
    if opened is None:
        return 1
    client, _ = opened
    try:
        folders = cli_search.resolve_search_folders(client, ns.folder)
    except export.ExportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    hits = cli_search.search_folders(client, query, folders, limit=ns.limit)
    names = {f.id: f.name for f in folders}
    if ns.json:
        print(json.dumps([cli_search.build_hit_record(m, names) for m in hits], indent=2, ensure_ascii=False))
    else:
        print("\n".join(cli_search.format_hit_lines(hits, names)) if hits else "No matches.")
        print(f"\n{len(hits)} hits in {', '.join(f.name for f in folders)}", file=sys.stderr)
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
