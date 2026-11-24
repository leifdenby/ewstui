# Changelog

All notable changes to ewstui. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the version
numbers [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `ewstui folders [--filter TEXT] [--hidden] [--json]` lists every mail
  folder's path, as `ewstui export` takes it, with its total and unread
  counts.

### Changed

- `ewstui export FOLDER` refuses a folder that holds no emails itself
  while its subfolders do, and says how many subfolders and emails there
  are, instead of quietly writing an empty export.
- `ewstui export --with-sent` finds your replies with one Sent Items
  search per 50 of the folder's conversations, instead of paging through
  Sent Items back to the folder's oldest email. The same replies are
  found, but a folder with mail from years ago no longer takes minutes.
  `scripts/time_with_sent.py` times both ways against a real mailbox.
- `ewstui export` shows progress when stderr isn't a terminal too: a
  plain line when each folder starts and every 50 emails in bigger ones,
  flushed at once, instead of nothing until the end.

## [0.1.0] — 2026-11-20

First release. A terminal email + calendar client for Microsoft Exchange
that talks **EWS directly**, for servers where IMAP is switched off and
you'd rather not run a DavMail-style gateway. Vim-style keys, built on
Textual and exchangelib. Install with `uv tool install ewstui` (or
`pipx install ewstui`); try it without a server: `ewstui --demo`.

### Mail

- Three panes (folders · list · reading pane, side by side or stacked),
  thread view, Today / Yesterday / … date groups with `{` `}` to jump
  between them, fuzzy search as you type plus Exchange's own search.
- Formatted HTML emails in the reading pane, links (`U`), copy text with
  the mouse or `y` / `Y`.
- Reply / reply all / forward written in Markdown; replies keep the
  original's formatting; drafts saved and edited; To / Cc look people up
  in the directory and in your own address book (`contacts.toml`, synced
  from person notes).
- Delete, archive, move (one email or a visual selection, `V`), all
  undoable with `u`.
- Attachments: open, or save one or several to any folder, with download
  progress.
- Invites: accept / tentative / decline with a note or a proposed new
  time, see your calendar for that day beside it, remove cancelled
  meetings.

### Priority list (`2`)

- Turn emails into todo.txt entries with a priority (A/B/C), a note and
  `due:` / `t:` dates; `F` shows only emails not on the list yet; changes
  made to the file elsewhere show up live.

### Calendar (`3`)

- Week view, new events (also from an email, `c`), Teams meetings (`T`),
  and a room finder: free rooms × times, select and book.

### Hand an email to an LLM agent (`C`)

- Type what you want done; the email and its attachments go in a folder,
  and Claude Code (or another agent CLI) starts in a new terminal tab,
  working on your mailbox through ewstui's command line. Configured in
  `[llm-agent]`.

### Command line

- `ewstui export` (folders or single emails to text + index.json),
  `search`, `calendar`, `contacts`, and `draft` (an email written in
  Markdown, new, reply or forward, with attachments, saved to Drafts or
  sent).

### Login & connection

- OAuth2, NTLM, Basic and Kerberos (`ewstui[kerberos]`), with `auto`
  falling back from modern auth.
- Password from `EWSTUI_PASSWORD`, **1Password** (`password_op_ref`, via
  the `op` CLI), the macOS Keychain with Touch ID, or a prompt.
- Named accounts in `~/.config/ewstui/config.toml`; colour themes.
- Made for slow or flaky gateways: short timeouts, keepalive, reconnect
  after sleep, a connection indicator in the status bar, and slow work
  (delete / archive / move, sending, attachment downloads) in the
  background so the UI doesn't freeze.

Requires Python 3.10+. MIT licence.

[Unreleased]: https://github.com/leifdenby/ewstui/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/leifdenby/ewstui/releases/tag/v0.1.0
