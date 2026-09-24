"""
ewstui
======

A terminal (TUI) email + calendar client for Microsoft Exchange,
talking EWS directly via `exchangelib` — no IMAP, no CalDAV, no
local gateway process, no JVM.

Every screen renders live data fetched from Exchange on demand.
Nothing is cached to disk except, optionally, an OAuth2 refresh
token (auth-state, not mail-state).

Modules:
    config       CLI argument parsing / Config dataclass
    auth         Builds an authenticated exchangelib.Account
    ews_client   Thin wrappers over exchangelib for mail + calendar
    demo_backend In-memory fake backend for UI development/testing
    keymap       Vim-style key -> action bindings
    app          The Textual App (three-pane mail UI + calendar screen)
    widgets/     Individual Textual widgets
"""

__version__ = "0.1.0"
