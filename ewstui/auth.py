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

from .config import AuthMethod, Config

log = logging.getLogger(__name__)

DEFAULT_OAUTH_SCOPE = ["https://outlook.office365.com/EWS.AccessAsUser.All"]


class AuthError(RuntimeError):
    """Raised when a specific auth method fails outright."""


class AuthUnavailable(AuthError):
    """Raised when a method can't even be attempted (e.g. missing config).

    `auto` catches this specifically to decide whether to fall back.
    """


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

def _password_account(cfg: Config, auth_type: str) -> Account:
    if not cfg.email:
        raise AuthUnavailable("--email is required")

    username = cfg.username or cfg.email
    if cfg.domain and auth_type == NTLM and "\\" not in username:
        username = f"{cfg.domain}\\{username}"

    password = cfg.password or getpass.getpass(f"EWS password for {username}: ")

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
        except AuthError as e:
            log.warning("auto: OAuth2 failed (%s), falling back to NTLM", e)
        return _password_account(cfg, NTLM)

    raise AssertionError(f"unhandled auth method {method!r}")  # pragma: no cover
