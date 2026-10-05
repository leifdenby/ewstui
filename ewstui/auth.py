"""
Authentication against EWS.

Design: `get_account(config) -> exchangelib.Account` is the single
entry point the rest of the app uses. It never caches mail — the
only thing that ever touches disk is an OAuth2 refresh token, written
to `config.token_cache_path` (auth-state, not mail-state).

Auth methods:
  - oauth2:    MSAL device-code flow (works against Azure AD / hybrid
               modern auth). Requires --client-id (and usually
               --tenant-id). Prints a URL + code to the terminal for
               you to approve in a browser, once per token lifetime.
  - ntlm/basic: Classic password auth, sent straight through to EWS.
               No token, no disk state at all.
  - kerberos:  GSSAPI, uses your existing ticket cache (kinit).
               Needs the optional `requests-gssapi` package and a
               system Kerberos install.
  - auto:      Try oauth2 first. If that's clearly not going to work
               (no client-id configured, or the server rejects the
               OAuth2 token endpoint / EWS calls with an auth error),
               fall back to ntlm automatically.

None of this is Exchange-Online-specific: on-prem Exchange with
Hybrid Modern Auth speaks the same OAuth2 shape, just usually with a
different --oauth-authority (ADFS) — see the README.
"""
from __future__ import annotations

import getpass
import logging
import sys
from pathlib import Path

from exchangelib import (
    DELEGATE,
    BASIC,
    NTLM,
    Account,
    Configuration,
    Credentials,
    OAuth2AuthorizationCodeCredentials,
)
from exchangelib.errors import UnauthorizedError

from . import connection, keychain
from .config import AuthMethod, Config

log = logging.getLogger(__name__)

DEFAULT_OAUTH_SCOPE = ["https://outlook.office365.com/EWS.AccessAsUser.All"]


class AuthError(RuntimeError):
    """Raised when a specific auth method fails outright."""


class AuthUnavailable(AuthError):
    """Raised when a method can't even be attempted (e.g. missing config).

    `auto` catches this specifically to decide whether to fall back.
    """


def _status(msg: str) -> None:
    # Pre-TUI progress output. stderr, like the device-flow prompt, so
    # it's visible before Textual takes over the terminal.
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# OAuth2 (device code flow via MSAL)
# --------------------------------------------------------------------------

def _oauth_authority(cfg: Config) -> str:
    if cfg.oauth_authority:
        return cfg.oauth_authority
    return f"https://login.microsoftonline.com/{cfg.tenant_id or 'organizations'}"


def _load_msal_app(cfg: Config):
    try:
        import msal
    except ImportError as e:  # pragma: no cover
        raise AuthUnavailable("msal is not installed (pip install msal)") from e

    if not cfg.client_id:
        raise AuthUnavailable("--client-id is required for --auth oauth2/auto")

    cache = msal.SerializableTokenCache()
    cache_path: Path = cfg.token_cache_path
    if cache_path.exists():
        cache.deserialize(cache_path.read_text())

    app = msal.PublicClientApplication(
        client_id=cfg.client_id,
        authority=_oauth_authority(cfg),
        token_cache=cache,
    )
    return app, cache, cache_path


def _save_cache_if_changed(cache, cache_path: Path) -> None:
    if not cache.has_state_changed:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(cache.serialize())
    try:
        cache_path.chmod(0o600)
    except OSError:
        pass


def _msal_acquire_token(cfg: Config) -> tuple[dict, "msal.PublicClientApplication", object, Path]:
    """Returns (token_dict, msal_app, cache, cache_path). Uses the silent
    (cached refresh token) path first, falls back to an interactive
    device-code flow printed to stderr.
    """
    app, cache, cache_path = _load_msal_app(cfg)
    scopes = DEFAULT_OAUTH_SCOPE

    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=scopes)
        if "user_code" not in flow:
            raise AuthError(f"Failed to start device flow: {flow.get('error_description', flow)}")
        print(flow["message"], file=sys.stderr)  # e.g. "Go to https://microsoft.com/devicelogin and enter code ABC123"
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise AuthError(f"OAuth2 authentication failed: {result.get('error_description', result)}")

    _save_cache_if_changed(cache, cache_path)
    return result, app, cache, cache_path


def _oauth2_account(cfg: Config) -> Account:
    if not cfg.email:
        raise AuthUnavailable("--email is required")

    token, app, cache, cache_path = _msal_acquire_token(cfg)

    def refresh_callback(access_token=None):
        # Called by exchangelib when the token has expired and it needs a
        # fresh one. We go back through MSAL's silent (refresh-token)
        # path rather than re-prompting the user interactively.
        accounts = app.get_accounts()
        result = app.acquire_token_silent(DEFAULT_OAUTH_SCOPE, account=accounts[0]) if accounts else None
        if not result or "access_token" not in result:
            raise AuthError("OAuth2 token expired and silent refresh failed; restart ewstui to re-authenticate")
        _save_cache_if_changed(cache, cache_path)
        return result

    credentials = OAuth2AuthorizationCodeCredentials(
        client_id=cfg.client_id,
        client_secret=None,  # public client (device flow) has no secret
        tenant_id=cfg.tenant_id,
        access_token=token,
    )
    # exchangelib calls this when a request comes back as expired/unauthorized
    credentials.on_token_auto_refreshed = lambda new_token: credentials.__setattr__("access_token", new_token)

    config_kwargs = {}
    if cfg.ews_url:
        config_kwargs["service_endpoint"] = cfg.ews_url
    configuration = Configuration(credentials=credentials, **config_kwargs)

    try:
        return Account(
            primary_smtp_address=cfg.email,
            config=configuration,
            autodiscover=cfg.autodiscover and not cfg.ews_url,
            access_type=DELEGATE,
        )
    except UnauthorizedError as e:
        raise AuthError(f"EWS rejected the OAuth2 token: {e}") from e


# --------------------------------------------------------------------------
# NTLM / Basic (password passthrough, no disk state)
# --------------------------------------------------------------------------

def login_username(cfg: Config, auth_type: str | None = None) -> str:
    """The username actually sent for ntlm/basic (and used as the
    Keychain item key): --username or --email, plus --domain for NTLM.
    """
    if auth_type is None:
        auth_type = BASIC if cfg.auth_method == AuthMethod.BASIC else NTLM
    username = cfg.username or cfg.email or ""
    if cfg.domain and auth_type == NTLM and "\\" not in username:
        username = f"{cfg.domain}\\{username}"
    return username


def _resolve_password(cfg: Config, username: str) -> str:
    """EWSTUI_PASSWORD env var, else Keychain (after Touch ID), else a
    getpass prompt. Records where it came from in cfg.password_source.
    """
    if cfg.password:
        cfg.password_source = "env"
        return cfg.password
    if cfg.use_keychain and keychain.available():
        _status("Looking up password in the macOS Keychain (Touch ID) ...")
        password = keychain.get_password(username, cfg.ews_url)
        if password is not None:
            cfg.password_source = "keychain"
            return password
    cfg.password_source = "prompt"
    return getpass.getpass(f"EWS password for {username}: ")


def _password_account(cfg: Config, auth_type: str) -> Account:
    if not cfg.email:
        raise AuthUnavailable("--email is required")

    username = login_username(cfg, auth_type)
    _status(f"Using {auth_type} auth as {username}")
    password = _resolve_password(cfg, username)

    credentials = Credentials(username=username, password=password)
    config_kwargs = {"credentials": credentials, "auth_type": auth_type}
    if cfg.ews_url:
        config_kwargs["service_endpoint"] = cfg.ews_url
    configuration = Configuration(**config_kwargs)

    try:
        return Account(
            primary_smtp_address=cfg.email,
            config=configuration,
            autodiscover=cfg.autodiscover and not cfg.ews_url,
            access_type=DELEGATE,
        )
    except UnauthorizedError as e:
        raise AuthError(f"EWS rejected {auth_type} credentials for {username}: {e}") from e


# --------------------------------------------------------------------------
# Kerberos (GSSAPI, uses your existing ticket cache)
# --------------------------------------------------------------------------

def _kerberos_account(cfg: Config) -> Account:
    if not cfg.email:
        raise AuthUnavailable("--email is required")
    try:
        from exchangelib import GSSAPI
    except ImportError as e:  # pragma: no cover
        raise AuthUnavailable("This exchangelib build has no GSSAPI support") from e
    try:
        import requests_gssapi  # noqa: F401  (exchangelib uses this under the hood)
    except ImportError as e:
        raise AuthUnavailable(
            "requests-gssapi is not installed (pip install requests-gssapi); "
            "also requires a system Kerberos install and a valid ticket (kinit)"
        ) from e

    config_kwargs = {"auth_type": GSSAPI, "credentials": None}
    if cfg.ews_url:
        config_kwargs["service_endpoint"] = cfg.ews_url
    configuration = Configuration(**config_kwargs)

    try:
        return Account(
            primary_smtp_address=cfg.email,
            config=configuration,
            autodiscover=cfg.autodiscover and not cfg.ews_url,
            access_type=DELEGATE,
        )
    except UnauthorizedError as e:
        raise AuthError(f"EWS rejected Kerberos ticket: {e}") from e


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def get_account(cfg: Config) -> Account:
    """Log in with the configured method; the returned account's
    connections use TCP keepalive and are refreshed after idle periods
    (see connection.py), so a long pause doesn't freeze the next request."""
    # Before any session exists: every connection gets keepalive sockets.
    connection.install_keepalive_adapter(verify_ssl=cfg.verify_ssl)
    if not cfg.verify_ssl:
        _status("WARNING: TLS certificate verification is disabled (--no-verify-ssl)")
    account = _get_account(cfg)
    connection.harden(account.protocol)
    return account


def _get_account(cfg: Config) -> Account:
    if not cfg.ntlm_send_cbt:
        import functools

        import requests_ntlm
        from exchangelib import transport

        transport.AUTH_TYPE_MAP[NTLM] = functools.partial(requests_ntlm.HttpNtlmAuth, send_cbt=False)
        if cfg.auth_method in (AuthMethod.NTLM, AuthMethod.AUTO):  # irrelevant (and confusing) otherwise
            _status("NTLM channel binding disabled (--ntlm-no-cbt)")

    method = cfg.auth_method

    if method == AuthMethod.OAUTH2:
        return _oauth2_account(cfg)
    if method == AuthMethod.NTLM:
        return _password_account(cfg, NTLM)
    if method == AuthMethod.BASIC:
        return _password_account(cfg, BASIC)
    if method == AuthMethod.KERBEROS:
        return _kerberos_account(cfg)

    if method == AuthMethod.AUTO:
        try:
            log.info("auto: trying OAuth2 first")
            return _oauth2_account(cfg)
        except AuthUnavailable as e:
            log.warning("auto: OAuth2 unavailable (%s), falling back to NTLM", e)
            _status(f"auto: skipping OAuth2 ({e}), falling back to NTLM")
        except AuthError as e:
            log.warning("auto: OAuth2 failed (%s), falling back to NTLM", e)
            _status(f"auto: OAuth2 failed ({e}), falling back to NTLM")
        return _password_account(cfg, NTLM)

    raise AssertionError(f"unhandled auth method {method!r}")  # pragma: no cover


# --------------------------------------------------------------------------
# Pre-flight diagnostics
#
# With an explicit --ews-url, building an exchangelib Account makes no
# network calls at all, so a wrong password / unreachable server only
# surfaces once the TUI is up (as a blank screen). These run first and
# report in plain terminal output instead.
# --------------------------------------------------------------------------

PROBE_TIMEOUT = 15  # seconds

# The WWW-Authenticate schemes each password method can log in with.
_SCHEMES = {BASIC: {"basic"}, NTLM: {"ntlm", "negotiate"}}


def password_auth_type(cfg: Config) -> str | None:
    """BASIC or NTLM when that's the method configured; None otherwise
    (oauth2, kerberos, auto)."""
    return {AuthMethod.BASIC: BASIC, AuthMethod.NTLM: NTLM}.get(cfg.auth_method)


def not_offered(auth_type: str | None, offered: list[str] | None) -> bool:
    """Did the probe show the server won't take this auth type at all?
    (False if unknown: no probe, or nothing advertised.)"""
    if not offered or auth_type not in _SCHEMES:
        return False
    return not _SCHEMES[auth_type] & {s.lower() for s in offered}


def _not_offered_hint(auth_type: str, offered: list[str]) -> str:
    other = "--auth ntlm --username 'DOMAIN\\user' (or --domain DOMAIN)" if auth_type == BASIC else "--auth basic"
    return (
        f"The server at this address doesn't offer {'Basic' if auth_type == BASIC else auth_type} auth (it offers {', '.join(offered)}), "
        "so the password wasn't really checked.\n"
        "  If this works elsewhere: on a VPN the same hostname often reaches the internal Exchange\n"
        f"  server directly instead of the gateway. Try {other}."
    )


def probe_endpoint(url: str, verify_ssl: bool = True, auth_type: str | None = None) -> list[str]:
    """Unauthenticated GET against the EWS URL: checks DNS/TCP/TLS and
    reports which auth schemes the server advertises (returned), with a
    warning if `auth_type` isn't one of them. Raises AuthError if the
    server can't be reached at all.
    """
    import requests

    _status(f"Probing {url} ...")
    try:
        r = requests.get(url, timeout=PROBE_TIMEOUT, verify=verify_ssl, allow_redirects=False)
    except requests.exceptions.SSLError as e:
        raise AuthError(
            f"TLS error talking to {url}: {e}\n"
            "  Hint: if the server uses an internal CA, retry with --no-verify-ssl "
            "(or add the CA to your trust store)."
        ) from e
    except requests.exceptions.Timeout as e:
        raise AuthError(
            f"No response from {url} within {PROBE_TIMEOUT}s.\n"
            "  Hint: is the server only reachable on the internal network / VPN?"
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise AuthError(
            f"Could not connect to {url}: {e}\n"
            "  Hint: check the hostname, and whether you need VPN."
        ) from e

    offered = r.headers.get("WWW-Authenticate", "")
    schemes = sorted({part.strip().split(" ")[0] for part in offered.split(",") if part.strip()})
    _status(f"  HTTP {r.status_code}; server offers auth: {', '.join(schemes) or '(none advertised)'}")
    if r.is_redirect:
        _status(f"  Redirects to {r.headers.get('Location')} — the EWS URL may be wrong.")
    if schemes and schemes == ["Bearer"]:
        _status("  Note: server only offers Bearer (OAuth2); NTLM/Basic won't work — use --auth oauth2.")
    elif not_offered(auth_type, schemes):
        _status(f"  Note: {_not_offered_hint(auth_type, schemes)}")
    return schemes


VERIFY_TIMEOUT = 30  # seconds; exchangelib's own default is 120
_HANG_HINT = (
    "  Still waiting... the server hasn't answered. Some gateways (e.g. F5 in front\n"
    "  of Exchange) hang instead of rejecting a login they don't like. If this times\n"
    "  out, try --auth basic, or --username 'DOMAIN\\user' / --domain DOMAIN."
)


def verify_account(account: Account, timeout: int = VERIFY_TIMEOUT, offered: list[str] | None = None) -> None:
    """One real authenticated EWS call (fetch the Inbox folder). Raises
    AuthError with a human-readable explanation on failure, including
    when the server doesn't answer within `timeout` seconds.
    """
    import threading

    _status(f"Checking credentials by fetching the Inbox (timeout {timeout}s) ...")
    protocol = account.protocol
    saved_timeout = protocol.TIMEOUT
    protocol.TIMEOUT = timeout  # per-instance override, restored below
    hint = threading.Timer(min(10, timeout / 2), _status, args=(_HANG_HINT,))
    hint.daemon = True
    hint.start()
    try:
        inbox = account.inbox
        _status(f"  OK: Inbox has {inbox.total_count} messages ({inbox.unread_count} unread)")
    except Exception as e:  # noqa: BLE001 - translate anything EWS throws
        auth_type = getattr(getattr(protocol, "config", None), "auth_type", None)
        raise AuthError(explain_error(e, auth_type, offered)) from e
    finally:
        hint.cancel()
        protocol.TIMEOUT = saved_timeout


def explain_error(e: Exception, auth_type: str | None = None, offered: list[str] | None = None) -> str:
    """A plain-language explanation of a failed login. `offered`: the
    auth schemes the probe saw (see probe_endpoint)."""
    import requests
    from exchangelib.errors import (
        ErrorNonExistentMailbox,
        ErrorTimeoutExpired,
        RateLimitError,
        TransportError,
    )

    msg = f"{type(e).__name__}: {e}"
    # exchangelib re-raises requests' connection/read timeouts as ErrorTimeoutExpired
    if isinstance(e, (ErrorTimeoutExpired, requests.exceptions.Timeout)):
        tips = [
            "--auth basic --username <your plain user id> (if the server offers Basic; still encrypted "
            "over HTTPS). Gateways such as F5 that stall on NTLM logins usually accept it"
        ]
        if auth_type == NTLM:
            tips.append("--ntlm-no-cbt (NTLM behind a TLS-terminating proxy often hangs on channel binding)")
        return (
            f"{msg}\n"
            "  The server accepted the connection but never answered the login. Things to try:\n"
            + "".join(f"   - {t}\n" for t in tips)
            + "   - a different username form: --username 'DOMAIN\\user' or --domain DOMAIN"
        )
    if isinstance(e, UnauthorizedError) and not_offered(auth_type, offered):
        return f"{msg}\n  {_not_offered_hint(auth_type, offered)}"
    if isinstance(e, UnauthorizedError):
        other = {NTLM: "--auth basic", BASIC: "--auth ntlm"}.get(auth_type, "--auth ntlm or --auth basic")
        return (
            f"{msg}\n"
            f"  The server rejected the credentials (HTTP 401{f', {auth_type} auth' if auth_type else ''}). "
            "Things to try:\n"
            "   - check the password\n"
            "   - a different username form: your plain user id (e.g. B123456 — often what Basic\n"
            "     wants behind a gateway), 'DOMAIN\\user' (quote it in the shell), your email,\n"
            "     or your UPN (user@domain)\n"
            f"   - a different auth method: {other} (or oauth2 if the server only offers Bearer)"
        )
    if isinstance(e, ErrorNonExistentMailbox):
        return f"{msg}\n  Logged in, but no mailbox for that --email on this server. Check the address."
    if isinstance(e, RateLimitError):
        return f"{msg}\n  The server is throttling requests; wait a bit and retry."
    if isinstance(e, TransportError):
        return f"{msg}\n  Network/HTTP-level failure talking to EWS. Check --ews-url and VPN."
    return msg
