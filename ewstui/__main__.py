from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from .app import EwstuiApp
from .config import Config, config_from_args
from .log_file import ensure_log_dir


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["export"]:
        return _export(argv[1:])
    if argv[:1] == ["search"]:
        return _search(argv[1:])
    if argv[:1] == ["folders"]:
        return _folders(argv[1:])
    if argv[:1] == ["calendar"]:
        return _calendar(argv[1:])
    if argv[:1] == ["draft"]:
        return _draft(argv[1:])
    if argv[:1] == ["contacts"]:
        return _contacts(argv[1:])
    cfg = config_from_args(argv)
    _setup_logging(cfg)
    _sync_contacts_at_startup(cfg)

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


def _setup_logging(cfg: Config) -> None:
    """Send logging to ``cfg.log_file``, creating its directory if needed.

    exchangelib is chatty at INFO; keep it quiet unless the user wants
    verbose diagnostics. Logging goes to a file, never stdout/stderr,
    since Textual owns the terminal once the app starts.

    Parameters
    ----------
    cfg : Config
        From ``config_from_args``; its ``log_file`` and ``debug`` are used.

    Raises
    ------
    OSError
        If the log directory can't be created.
    """
    ensure_log_dir(cfg.log_file)
    logging.basicConfig(
        filename=cfg.log_file,
        level=logging.DEBUG if cfg.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _connect(cfg):
    """Log in and check the connection (probe, then one Inbox fetch).
    The exchangelib Account, or None after printing why it failed."""
    from . import auth

    if cfg.debug:
        print(f"Debug logging to {cfg.log_file.resolve()}", file=sys.stderr)
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
            print(f"Re-run with --debug for a traceback and full EWS traffic in the log ({cfg.log_file}).",
                  file=sys.stderr)
        _handle_rejected_stored_password(cfg, e, offered)
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
    unknown = [arg for arg in leftover if arg.startswith("-") and arg != "-"]  # "-" is stdin
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

    progress = export.ProgressPrinter()
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
            folders = client.list_folders()
            path, folder = export.find_folder(folders, folder_path)
            if not ns.update and not folder.total_count:
                n_sub, n_emails = export.describe_subfolders(folders, path)
                if n_sub:
                    print(f"error: {path!r} holds no emails itself, but {n_sub} of its subfolders hold {n_emails}. "
                          f"Export one of them instead (list them with: ewstui folders --filter {path!r})",
                          file=sys.stderr)
                    return 1
            source = {
                "mailbox": mailbox,
                "account": cfg.account,
                "ews_url": cfg.ews_url,
                "folder_path": path,
                "folder_id": folder.id,
                "with_sent": with_sent,
                "attachments": attachments,
            }
            progress.label = path
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


def _folders(argv: list[str]) -> int:
    """`ewstui folders [--filter TEXT] [--json]`: every mail folder's path
    with its total and unread counts, spelled as `ewstui export FOLDER`
    takes it. Any other options are the usual connection/account ones.

    Parameters
    ----------
    argv : list[str]
        The arguments after ``folders``.

    Returns
    -------
    int
        Exit code: 0, or 1 if the connection failed.
    """
    import argparse
    import json

    from . import export

    p = argparse.ArgumentParser(
        prog="ewstui folders",
        usage="ewstui folders [--filter TEXT] [--hidden] [--json] [options]",
        description="List the mail folders: total and unread emails, then the folder's path as `ewstui export "
        "FOLDER` and `ewstui search --folder` take it. Counts are of the folder itself, not its subfolders. "
        "Connection options (--account, --email, --auth, ...) are as for ewstui itself.",
    )
    p.add_argument("--filter", metavar="TEXT", help="Only folders whose path contains TEXT (any case)")
    p.add_argument("--hidden", action="store_true",
                   help="Also list the folders hidden by default (Sync Issues, Conversation History, ...)")
    p.add_argument("--json", action="store_true", help="Print the folders as a JSON list")
    ns, positionals, rest = _split_positionals(p, argv)
    if positionals:
        p.error(f"unrecognized arguments: {' '.join(positionals)}")

    opened = _open_mail_client(config_from_args(rest))
    if opened is None:
        return 1
    client, _ = opened
    records = export.folder_records(client.list_folders(), include_hidden=ns.hidden, text=ns.filter)
    if ns.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
    elif records:
        print(f"{'total':>6} {'unread':>6}  path")
        for r in records:
            print(f"{r['total']:6d} {r['unread']:6d}  {r['path']}")
    else:
        print("No folders." if not ns.filter else f"No folder path contains {ns.filter!r}.")
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


def _calendar(argv: list[str]) -> int:
    """`ewstui calendar --from DATE [--to DATE] [--json]`: list your calendar
    events over a date range (see cli_calendar.py). Any other options are
    the usual connection/account ones.

    Parameters
    ----------
    argv : list[str]
        The arguments after ``calendar``.

    Returns
    -------
    int
        Exit code: 0, or 1 if the connection failed.
    """
    import argparse
    import json
    from datetime import date, timedelta

    from . import cli_calendar

    p = argparse.ArgumentParser(
        prog="ewstui calendar",
        usage="ewstui calendar --from YYYY-MM-DD [--to YYYY-MM-DD] [--json] [options]",
        description="List your calendar events over a date range (both days included), oldest first. "
        "Connection options (--account, --email, --auth, ...) are as for ewstui itself.",
    )
    p.add_argument("--from", dest="start", type=date.fromisoformat, required=True, metavar="YYYY-MM-DD",
                   help="First day to list")
    p.add_argument("--to", dest="end", type=date.fromisoformat, default=date.today(), metavar="YYYY-MM-DD",
                   help="Last day to list (default today)")
    p.add_argument("--json", action="store_true", help="Print the events as a JSON list")
    ns, positionals, rest = _split_positionals(p, argv)
    if positionals:
        p.error(f"unrecognized arguments: {' '.join(positionals)}")
    if ns.end < ns.start:
        p.error("--to is before --from")

    cfg = config_from_args(rest)
    _setup_logging(cfg)
    if cfg.demo:
        from .demo_backend import DemoCalendarClient

        client = DemoCalendarClient()
    else:
        from .ews_client import CalendarClient

        account = _connect(cfg)
        if account is None:
            return 1
        client = CalendarClient(account)
    events = cli_calendar.fetch_events(client, ns.start, ns.end + timedelta(days=1))
    if ns.json:
        print(json.dumps([cli_calendar.build_event_record(e) for e in events], indent=2, ensure_ascii=False))
    else:
        print("\n".join(cli_calendar.format_event_lines(events)) if events else "No events.")
        print(f"\n{len(events)} events from {ns.start} to {ns.end}", file=sys.stderr)
    return 0


def _draft(argv: list[str]) -> int:
    """`ewstui draft FILE [--send]`: a new email, reply or forward written
    elsewhere as Markdown, saved to Drafts or sent (see cli_draft.py). Any
    other options are the usual connection/account ones.

    Parameters
    ----------
    argv : list[str]
        The arguments after ``draft``.

    Returns
    -------
    int
        Exit code: 0, or 1 if the file, the connection or Exchange failed.
    """
    import argparse

    from . import cli_draft
    from .recipients import parse_recipients

    p = argparse.ArgumentParser(
        prog="ewstui draft",
        usage="ewstui draft FILE [--send] [--to ADDR ...] [--subject TEXT] [--in-reply-to ID [--reply-all]] "
        "[--forward ID] [--attach PATH ...] [options]",
        description="Save an email written in a file (Markdown, sent as HTML) to Drafts, or send it. FILE ('-' "
        "for stdin) may start with a header block between --- lines: to, cc, subject, in-reply-to (the "
        "Message-ID of the email answered, as `ewstui search` prints it), reply-all (yes/no), forward (a "
        "Message-ID), quote (yes/no: Exchange adds the original below a reply), attach (files, one per line; "
        "relative to FILE's folder). The options below override the file's headers (--attach adds to its "
        "files). Connection options (--account, --email, --auth, ...) are as for ewstui itself.",
    )
    p.add_argument("--send", action="store_true", help="Send it instead of saving it to Drafts")
    p.add_argument("--to", action="append", default=[], metavar="ADDR", help="Recipient (repeat for more)")
    p.add_argument("--cc", action="append", default=[], metavar="ADDR", help="Cc recipient (repeat for more)")
    p.add_argument("--subject", help="Subject (default for a reply / forward: Re: / Fwd: the original's)")
    p.add_argument("--in-reply-to", metavar="ID", help="Reply to the email with this Message-ID")
    p.add_argument("--reply-all", action="store_true", help="With --in-reply-to: reply to everyone")
    p.add_argument("--forward", metavar="ID", help="Forward the email with this Message-ID (with its attachments)")
    p.add_argument("--no-quote", action="store_true",
                   help="With --in-reply-to: send only the text, without Exchange adding the original below")
    p.add_argument("--attach", action="append", default=[], metavar="PATH",
                   help="Attach this file (repeat for more; added to the file's attach list)")
    ns, positionals, rest = _split_positionals(p, argv)
    if len(positionals) != 1:
        p.error("give one FILE (or - for stdin)")
    source = positionals[0]

    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except OSError as e:
        print(f"error: can't read {source}: {e}", file=sys.stderr)
        return 1
    try:
        spec = cli_draft.parse_draft(text, base_dir=None if source == "-" else Path(source).absolute().parent)
        spec.attachments += [cli_draft.resolve_attachment_path(a) for a in ns.attach]
        if ns.to:
            spec.to = parse_recipients("\n".join(ns.to))
        if ns.cc:
            spec.cc = parse_recipients("\n".join(ns.cc))
        if ns.subject is not None:
            spec.subject = ns.subject
        if ns.in_reply_to:
            spec.in_reply_to = cli_draft.normalize_message_id(ns.in_reply_to)
        if ns.forward:
            spec.forward = cli_draft.normalize_message_id(ns.forward)
        spec.reply_all = spec.reply_all or ns.reply_all
        spec.quote = spec.quote and not ns.no_quote
        cli_draft.check_draft(spec, ns.send)  # before logging in
    except cli_draft.DraftError as e:
        print(f"error: {source}: {e}", file=sys.stderr)
        return 1

    cfg = config_from_args(rest)
    opened = _open_mail_client(cfg)
    if opened is None:
        return 1
    client, mailbox = opened
    me = getattr(client, "my_address", "") or mailbox or ""
    try:
        result = cli_draft.submit_draft(client, spec, send=ns.send, me=me)
    except cli_draft.DraftError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - Exchange's reason, not a traceback
        print(f"error: {'sending' if ns.send else 'saving to Drafts'} failed: {e}", file=sys.stderr)
        if cfg.debug:
            traceback.print_exc()
        return 1
    print("\n".join(cli_draft.format_result_lines(result, ns.send)))
    return 0


def _sync_contacts_at_startup(cfg: Config) -> None:
    """Bring the address book up to date with its note folders, if any
    note changed since the last sync (see contacts.py). Never stops ewstui
    from starting: problems go to the log.

    Parameters
    ----------
    cfg : Config
        Its ``contacts_path`` and ``contacts_sync_from`` are used.

    Returns
    -------
    None
    """
    from .contacts import sync_contacts_quietly

    if cfg.contacts_path is None or not cfg.contacts_sync_from:
        return
    result = sync_contacts_quietly(cfg.contacts_path, cfg.contacts_sync_from)
    if result is not None and not result.skipped:
        logging.getLogger(__name__).info(
            "address book %s: %d contacts from %d notes (+%d, -%d)",
            cfg.contacts_path, result.contacts, result.notes, len(result.added), len(result.removed),
        )


def _contacts(argv: list[str]) -> int:
    """`ewstui contacts sync` / `ewstui contacts find QUERY [--json]`: bring
    your address book up to date with its note folders, or search it as the
    To / Cc lookup does (see contacts.py). Needs no connection.

    Parameters
    ----------
    argv : list[str]
        The arguments after ``contacts``.

    Returns
    -------
    int
        Exit code: 0, or 1 if the address book can't be read or written.
    """
    import argparse
    import json

    from . import contacts

    p = argparse.ArgumentParser(
        prog="ewstui contacts",
        usage="ewstui contacts sync [options]\n       ewstui contacts find QUERY [--json] [options]",
        description="Your address book (contacts.toml next to config.toml), which To / Cc lookups search as "
        "well as the directory. `sync` rebuilds its synced entries from the note folders in config.toml "
        "([contacts] sync_from); `find` searches it (syncing first if a note changed). --config picks "
        "the config file as for ewstui itself.",
    )
    p.add_argument("--json", action="store_true", help="find: print the matches as a JSON list")
    ns, positionals, rest = _split_positionals(p, argv)
    if not positionals or positionals[0] not in ("sync", "find"):
        p.error("give `sync` or `find QUERY`")
    command, words = positionals[0], positionals[1:]
    if command == "sync" and words:
        p.error(f"unrecognized arguments: {' '.join(words)}")
    query = " ".join(words).strip()
    if command == "find" and not query:
        p.error("give a QUERY")

    cfg = config_from_args(["--demo", *rest] if "--demo" not in rest else rest)  # no login needed
    path, folders = cfg.contacts_path, cfg.contacts_sync_from
    try:
        if command == "sync":
            if not folders:
                print(f"No [contacts] sync_from folders in {cfg.config_path}: nothing to sync.", file=sys.stderr)
                return 0
            result = contacts.sync_contacts_from_notes(path, folders, force=True)
            print(f"{path}: {result.contacts} contacts from {result.notes} notes in "
                  f"{', '.join(contacts.shorten_home(f) for f in folders)}")
            for email in result.added:
                print(f"  + {email}")
            for email in result.removed:
                print(f"  - {email}")
            return 0
        if folders:
            contacts.sync_contacts_from_notes(path, folders)
        found = contacts.find_contacts(contacts.load_contacts(path), query)
    except (OSError, contacts.ContactsError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if ns.json:
        print(json.dumps([{"name": name, "email": email} for name, email in found], indent=2, ensure_ascii=False))
    else:
        from .recipients import format_person

        print("\n".join(format_person(name, email) for name, email in found) if found else "No matches.")
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
    if cfg.password_op_ref:  # the user chose 1Password for this account
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


def _handle_rejected_stored_password(cfg, error: Exception, offered: list[str] | None = None) -> None:
    """A stored password the server rejects (e.g. after a password
    change) would fail on every start — remove it so the next run
    prompts again. Kept if the server doesn't offer the auth method at
    all (`offered`, from the probe): then the password was never tested.
    """
    from exchangelib.errors import UnauthorizedError

    from . import auth, keychain

    if cfg.password_source not in ("keychain", "1password") or not isinstance(error.__cause__, UnauthorizedError):
        return
    if auth.not_offered(auth.password_auth_type(cfg), offered):
        return
    if cfg.password_source == "1password":  # read-only: never touch the 1Password item
        print(
            f"The password from 1Password ({cfg.password_op_ref}) was rejected. Update the item in 1Password.",
            file=sys.stderr,
        )
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
