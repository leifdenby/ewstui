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


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ewstui",
        description="Terminal email + calendar client for Exchange (EWS), vim-style bindings.",
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
    ns = build_arg_parser().parse_args(argv)

    if not ns.demo and not ns.email:
        raise SystemExit("error: --email is required unless --demo is set")

    return Config(
        email=ns.email,
        username=ns.username or ns.email,
        password=os.environ.get("EWSTUI_PASSWORD"),  # never taken from argv; env var or prompted at runtime
        ews_url=ns.ews_url,
        autodiscover=ns.autodiscover,
        domain=ns.domain,
        auth_method=AuthMethod(ns.auth),
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
