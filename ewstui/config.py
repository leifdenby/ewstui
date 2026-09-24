"""
Configuration: CLI flags -> a plain Config object used by the rest
of the app. Nothing here talks to Exchange; see auth.py for that.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import config_file
from .priority_store import DEFAULT_PATH as DEFAULT_PRIORITY_PATH


class AuthMethod(str, Enum):
    OAUTH2 = "oauth2"
    NTLM = "ntlm"
    BASIC = "basic"
    KERBEROS = "kerberos"
    AUTO = "auto"  # try oauth2, fall back to ntlm if the server rejects it


DEFAULT_TOKEN_CACHE = Path.home() / ".config" / "ewstui" / "token_cache.bin"
DEFAULT_PAGE_SIZE = 50
DEFAULT_REFRESH_MINUTES = 5.0


@dataclass
class Config:
    # Identity / connection
    email: str | None = None
    username: str | None = None           # defaults to `email` if unset
    password: str | None = None           # only used for ntlm/basic; prefer prompting
    use_keychain: bool = True             # read/offer to save the password in the macOS Keychain
    forget_password: bool = False         # delete the stored Keychain password and exit
    password_source: str | None = None    # set at login: "env" | "keychain" | "prompt"
    ews_url: str | None = None            # e.g. https://mail.example.com/EWS/Exchange.asmx
    autodiscover: bool = False
    domain: str | None = None             # NTLM domain, e.g. "CORP"

    # Auth
    auth_method: AuthMethod = AuthMethod.AUTO
    tenant_id: str | None = None
    client_id: str | None = None
    oauth_authority: str | None = None    # override for ADFS / non-AAD OAuth2 issuers
    token_cache_path: Path = field(default_factory=lambda: DEFAULT_TOKEN_CACHE)

    ntlm_send_cbt: bool = True            # NTLM channel binding (EPA); some TLS-terminating proxies choke on it

    # TLS
    verify_ssl: bool = True

    # UI / behavior
    page_size: int = DEFAULT_PAGE_SIZE
    refresh_interval: float = DEFAULT_REFRESH_MINUTES  # minutes between background mail checks; 0 = off
    demo: bool = False                    # run against fake in-memory data, no network
    debug: bool = False                   # verbose exchangelib logging to the log file + tracebacks
    priority_file: Path = field(default_factory=lambda: DEFAULT_PRIORITY_PATH)
    attachment_dir: Path = field(default_factory=lambda: Path.home() / "Downloads" / "ewstui-attachments")

    # Config file profile (see config_file.py)
    account: str | None = None            # profile in use, if any
    config_path: Path | None = None
    # Explicit CLI args to save into the profile once login succeeds
    # (only when --account was given explicitly).
    pending_account_updates: dict = field(default_factory=dict)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ewstui",
        description="Terminal email + calendar client for Exchange (EWS), vim-style bindings.",
    )

    prof = p.add_argument_group("config file")
    prof.add_argument(
        "--account",
        metavar="NAME",
        help="Use the [accounts.NAME] profile from the config file. Any other options given alongside it "
        "are saved into that profile (created if new) once the connection succeeds. Without --account, "
        "the file's default_account is used and options only apply to this run",
    )
    prof.add_argument(
        "--config",
        type=Path,
        metavar="PATH",
        help=f"Config file to use (default: {config_file.default_path()})",
    )

    conn = p.add_argument_group("connection")
    conn.add_argument("--email", help="Primary SMTP address of the mailbox, e.g. you@corp.example")
    conn.add_argument("--ews-url", help="Explicit EWS endpoint, e.g. https://mail.corp.example/EWS/Exchange.asmx")
    conn.add_argument("--autodiscover", action="store_true", help="Use Exchange Autodiscover instead of --ews-url")
    conn.add_argument("--no-verify-ssl", action="store_true", help="Disable TLS certificate verification (internal CAs only, use with care)")

    auth = p.add_argument_group("authentication")
    auth.add_argument(
        "--auth",
        choices=[m.value for m in AuthMethod],
        default=AuthMethod.AUTO.value,
        help="Auth method against EWS. 'auto' tries oauth2 first and falls back to ntlm if the server rejects modern auth.",
    )
    auth.add_argument("--username", help="Login username for ntlm/basic (defaults to --email)")
    auth.add_argument("--domain", help="NTLM domain, e.g. CORP (only for --auth ntlm/auto fallback)")
    auth.add_argument(
        "--no-keychain",
        action="store_true",
        help="Don't read the password from, or offer to save it to, the macOS Keychain",
    )
    auth.add_argument(
        "--forget-password",
        action="store_true",
        help="Delete the password stored in the macOS Keychain for this username/server, then exit",
    )
    auth.add_argument(
        "--ntlm-no-cbt",
        action="store_true",
        help="Don't send NTLM channel binding tokens. Try this if NTLM login hangs behind a "
        "TLS-terminating proxy/load balancer (e.g. F5)",
    )
    auth.add_argument("--tenant-id", help="Azure AD tenant id (oauth2)")
    auth.add_argument("--client-id", help="Azure AD app registration client id (oauth2)")
    auth.add_argument("--oauth-authority", help="Override OAuth2 authority URL (e.g. for ADFS / hybrid modern auth)")
    auth.add_argument("--token-cache", type=Path, default=DEFAULT_TOKEN_CACHE, help="Where to persist the OAuth2 refresh token")

    ui = p.add_argument_group("ui")
    ui.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="Messages fetched per page")
    ui.add_argument(
        "--refresh-interval",
        type=float,
        default=DEFAULT_REFRESH_MINUTES,
        metavar="MINUTES",
        help=f"Check for new mail in the background every MINUTES (default {DEFAULT_REFRESH_MINUTES:g}; 0 = off). "
        "Ctrl+l refreshes on demand",
    )
    ui.add_argument("--demo", action="store_true", help="Run with fake in-memory data, no network/EWS calls at all")
    ui.add_argument(
        "--debug",
        action="store_true",
        help="Log full EWS request/response traffic to ewstui.log and print tracebacks on connection errors "
        "(the log can contain mail content — delete it afterwards)",
    )
    ui.add_argument(
        "--priority-file",
        type=Path,
        default=DEFAULT_PRIORITY_PATH,
        help="Path to the local todo.txt priority list (default: ~/.local/share/ewstui/priorities.todo.txt)",
    )
    ui.add_argument(
        "--attachment-dir",
        type=Path,
        default=Path.home() / "Downloads" / "ewstui-attachments",
        help="Where attachments are saved before being opened (default: ~/Downloads/ewstui-attachments)",
    )

    return p


def config_from_args(argv: list[str] | None = None) -> Config:
    parser = build_arg_parser()
    # Parse storable options with SUPPRESS defaults, so `explicit` holds
    # only what was actually typed; real defaults are layered in below.
    # (Not parser.set_defaults(SUPPRESS): argparse would then fill in the
    # literal "==SUPPRESS==" string as a parser-level default.)
    builtin = {}
    for action in parser._actions:  # noqa: SLF001 - no public API for this
        if action.dest in config_file.STORED_KEYS:
            builtin[action.dest] = action.default
            action.default = argparse.SUPPRESS
    ns = parser.parse_args(argv)
    explicit = {key: value for key, value in vars(ns).items() if key in config_file.STORED_KEYS}

    config_path = ns.config or config_file.default_path()
    try:
        doc = config_file.load(config_path)
        account = config_file.resolve_account_name(doc, ns.account)
        profile = config_file.account_settings(doc, account) if account else {}
    except config_file.ConfigFileError as e:
        raise SystemExit(f"error: {e}") from e
    if account and not config_file.has_account(doc, account):
        if ns.account is None:
            raise SystemExit(f"error: default_account {account!r} has no [accounts.{account}] in {config_path}")
        if not explicit:
            raise SystemExit(
                f"error: no account {account!r} in {config_path}\n"
                f"  Create it by passing its settings once, e.g.:\n"
                f"  ewstui --account {account} --email you@corp.example --ews-url https://.../EWS/Exchange.asmx"
            )

    for key, value in (builtin | profile | explicit).items():
        setattr(ns, key, value)

    if not ns.demo and not ns.email:
        raise SystemExit("error: --email is required unless --demo is set (or set email in a config-file account)")
    try:
        auth_method = AuthMethod(ns.auth)
    except ValueError as e:
        raise SystemExit(f"error: invalid auth {ns.auth!r} (from {config_path})") from e

    return Config(
        account=account,
        config_path=config_path,
        pending_account_updates=explicit if ns.account else {},
        email=ns.email,
        username=ns.username or ns.email,
        password=os.environ.get("EWSTUI_PASSWORD"),  # never taken from argv; env var or prompted at runtime
        use_keychain=not ns.no_keychain,
        forget_password=ns.forget_password,
        ews_url=ns.ews_url,
        autodiscover=ns.autodiscover,
        domain=ns.domain,
        auth_method=auth_method,
        tenant_id=ns.tenant_id,
        client_id=ns.client_id,
        oauth_authority=ns.oauth_authority,
        token_cache_path=ns.token_cache,
        ntlm_send_cbt=not ns.ntlm_no_cbt,
        verify_ssl=not ns.no_verify_ssl,
        page_size=ns.page_size,
        refresh_interval=ns.refresh_interval,
        demo=ns.demo,
        debug=ns.debug,
        priority_file=ns.priority_file,
        attachment_dir=ns.attachment_dir,
    )
