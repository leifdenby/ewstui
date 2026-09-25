# ewstui

A terminal (TUI) email + calendar client for Microsoft Exchange, with
vim-style keybindings — talking **EWS directly**, no IMAP, no CalDAV,
no local gateway process, no JVM.

Built with [Textual](https://textual.textualize.io/) for the UI and
[`exchangelib`](https://github.com/ecederstrand/exchangelib) for the
Exchange side.

Every screen is fetched live from Exchange when you open it. Nothing
is cached to disk except, optionally, an OAuth2 refresh token — that's
auth-state, not mail-state; delete `~/.config/ewstui/token_cache.bin`
any time and it just re-prompts for login next run.

## Why this exists

If your Exchange server has IMAP disabled (common on locked-down
on-prem/enterprise deployments) but EWS is reachable, tools like
`aerc`/`neomutt` can't talk to it directly, and gateways like DavMail
require running a JVM. `ewstui` is a from-scratch client instead of a
gateway: it speaks EWS as its *only* protocol and renders the result
directly in the terminal, so there's no IMAP/CalDAV emulation layer
to get wrong.

## Install

```bash
uv sync
```

For `--auth kerberos`, include the optional extra: `uv sync --extra kerberos`.

## Try it without a server first

```bash
uv run ewstui --demo
```

This runs the full UI against fake in-memory mail + calendar data —
useful to check the keybindings and layout work in your terminal
before you touch a real mailbox. See `ewstui/demo_backend.py`.

## Run against a real mailbox

```bash
uv run ewstui --email you@corp.example --ews-url https://mail.corp.example/EWS/Exchange.asmx --auth auto
```

If you don't know your EWS endpoint, try `--autodiscover` instead of
`--ews-url` (requires Autodiscover to be reachable/working, which is
not a given on locked-down servers — an explicit `--ews-url` is more
reliable if you can get it from IT).

### Choosing `--auth`

| Value      | What it does | Needs |
|---|---|---|
| `oauth2`   | MSAL device-code flow. Prints a URL + code once; approve it in a browser. Refresh token cached to disk. | `--client-id` (Azure AD app registration), usually `--tenant-id` |
| `ntlm`     | Password sent straight through as NTLM. No disk state. | `--username`/`--email` + password (prompted, or `EWSTUI_PASSWORD` env var) |
| `basic`    | Same as ntlm but plain Basic auth. | same as ntlm |
| `kerberos` | Uses your existing ticket cache (`kinit`) via GSSAPI. No password. | system Kerberos + `uv sync --extra kerberos` |
| `auto` (default) | Tries `oauth2` first; if that's unconfigured or the server rejects it, falls back to `ntlm` automatically. | whatever either path needs |

Since you likely don't know in advance whether your on-prem server
supports modern auth (OAuth2 / Hybrid Modern Auth) or only NTLM,
`auto` is the sensible starting point — pass `--client-id`/`--tenant-id`
in case OAuth2 works, and it'll gracefully fall back to an NTLM
password prompt if not.

If your org uses ADFS rather than Azure AD for OAuth2, override the
token endpoint with `--oauth-authority https://adfs.corp.example/adfs`.

### NTLM domain

If your NTLM username needs a domain prefix (`CORP\jdoe` rather than
just `jdoe`), pass `--domain CORP` and it's prepended automatically.

### Password handling

Never pass `--password` on the CLI (it'd be visible in shell history
and `ps`). Either set `EWSTUI_PASSWORD` in the environment for a
scripted/non-interactive run, or just leave it unset — you'll get a
`getpass` prompt at startup.

**macOS Keychain + Touch ID**: after a successful login with a typed
password, ewstui asks whether to save it in your login Keychain
(service `ewstui`, one item per username + EWS URL). On later starts it
asks for Touch ID (or your Mac password if there's no sensor) and uses
the stored password; cancel the dialog to type it instead. If the
server rejects a stored password (e.g. after a password change) the
item is removed and the next run prompts again. `--forget-password`
deletes it; `--no-keychain` skips the Keychain entirely.

Note the Touch ID check is enforced by ewstui, not by the Keychain:
OS-level biometric protection needs a signed app with Keychain
entitlements. The item itself is protected like any login Keychain
item (encrypted, unlocked while you're logged in).

### Config file and accounts

Instead of repeating the flags, keep them as a named account in
`~/.config/ewstui/config.toml` (or `$XDG_CONFIG_HOME/ewstui/config.toml`;
override with `--config PATH`). The easiest way to create one is to
run with `--account NAME` plus your flags once — they're saved after
the connection succeeds, and the first account becomes the default:

```bash
uv run ewstui --account work --email you@corp.example \
  --ews-url https://mail.corp.example/EWS/Exchange.asmx --ntlm-no-cbt --domain CORP
uv run ewstui                 # uses default_account from then on
uv run ewstui --account work  # or pick one by name
```

```toml
default_account = "work"

[accounts.work]
email = "you@corp.example"
ews_url = "https://mail.corp.example/EWS/Exchange.asmx"
domain = "CORP"
ntlm_no_cbt = true
```

- Precedence: built-in defaults < the account's settings < flags on
  the command line.
- With `--account NAME`, any other flags you pass are saved into that
  account. Without it (default account), flags only apply to that run.
- Keys are the option names with `_` instead of `-` (`ews_url`,
  `priority_file`, `refresh_interval`, ...). The password, `--debug`
  and `--demo` are never stored.
- On/off flags like `--ntlm-no-cbt` can only be switched on from the
  command line; edit the file to turn one off. Comments you add to the
  file are kept when ewstui updates it.

### Troubleshooting the connection

Before the UI opens, ewstui checks the connection in plain terminal
output: it probes the EWS URL (reachability, TLS, which auth schemes
the server offers), then fetches the Inbox once with your credentials
(30 s timeout). On failure it exits with the error and hints instead
of opening an empty UI.

- `--debug` prints tracebacks and logs full EWS traffic to
  `ewstui.log` (it can contain mail content and auth tokens — delete it
  afterwards).
- **NTLM login hangs** (the Inbox check never answers, while
  `--auth basic` at least gets a quick reply): the server is likely
  behind a TLS-terminating proxy/load balancer (e.g. F5) that can't
  handle NTLM channel binding. Add `--ntlm-no-cbt`, e.g.
  `--ntlm-no-cbt --domain PROD --username jdoe`.
- In zsh/bash, quote `DOMAIN\user` (`--username 'PROD\jdoe'`) or the
  backslash is eaten — or use `--domain` instead.

## Priority list (`2`)

A local, `todo.txt`-formatted list for triaging mail — separate from
any Exchange folder, stored only on your machine at
`~/.local/share/ewstui/priorities.todo.txt` (override with
`--priority-file`). It's a plain [todo.txt](https://github.com/todotxt/todo.txt)
file, so any other todo.txt tool can read/edit it too (they'll just
see the `+email`, `id:`, `folder:`, `from:` fields as ordinary text).

**Add a message to the list**: from the mail view, select a message
and press `P` (shift+p) to add it straight to the priority list with
no priority set yet and no prompt — or `p` to do the same but first
prompt you for a short note, which gets appended to the entry's text
(e.g. "Re: EWS bridge project — waiting on legal sign-off"). Either
way it's an unprioritized todo.txt line (no `(X)` prefix); set its
priority later from the priority view.

**Work the list**: press `2` (or click the **Priority** tab) to
switch to the priority view. It's sorted `A` first through `Z`, then
unprioritized, then by the date added.

- `A`–`Z` on the row under your cursor — reprioritizes just that item.
- `V` — start a visual selection at the current row (vim visual-line
  style); `j`/`k` extend it; pressing a letter applies that priority
  to every selected row at once and exits visual mode; `Esc` cancels
  without changing anything.
- `x` — toggle complete (writes `x <date>` per the todo.txt spec, and
  remembers the old priority as `pri:A` so it's not lost).
- `d` — remove from the priority list (does not touch the actual
  email or Exchange).
- `Enter`/`o` — jump straight to that email (switches to the mail
  view, opens its folder, opens the message).

Example line this produces:
```
(A) 2026-09-24 Q3 budget review +email id:AAMkAD...== folder:inbox from:finance@corp.example
```

## Attachments (`v`)

From the mail view, press `v` on a message to list its attachments
(no-op with a "No attachments" notice if there are none). `j`/`k` to
move, `Enter`/`l` to pick one — it's saved and then opened with the
system's default app (`open` on macOS, the file association on
Windows, `xdg-open` on Linux). If that isn't possible, you still get
the saved path in the notification so you can open it yourself.

Where it's saved: `--attachment-dir` (or `attachment_dir` in the
account's config) if set; otherwise on macOS
`~/Downloads/Attachments/<account>` — the `--account`/default account
name, or your email address when no account profile is in use — and
`~/Downloads/ewstui-attachments` elsewhere.
Re-downloading the same file never overwrites a previous copy — it's
suffixed `(1)`, `(2)`, etc.

Only real file attachments are downloadable; an attachment that's
itself an embedded email or calendar item (e.g. a forwarded message)
shows up greyed out in the list since EWS represents those
differently and this doesn't handle that case yet.

## Keybindings

Press `?` inside the app for the full list. Summary:

Pane navigation follows vim's `h`/`l` (plus `Tab`/`Shift+Tab`):
`l`/`Enter` opens whatever's under the cursor *and* moves focus into
the next pane over (folders → messages → preview); `h` moves focus
back one pane without changing what's displayed. Plain `j`/`k`
navigation never steals focus on its own — moving through the message
list with `j`/`k` just live-updates the preview pane's content, the
same as mutt/aerc; only an explicit open (`l`/`Enter`) shifts focus.

**Global**: `j`/`k` move, `g`/`G` top/bottom, `Tab`/`l` next pane
(`l` also opens the item under the cursor), `Shift+Tab`/`h` previous
pane, `Enter`/`l` open, `1`/`2`/`3` switch to mail/priority/calendar
view (also shown as clickable mode tabs under the header, with the
active one highlighted), `Ctrl+l` refresh, `q` quit, `?` help.

**New mail**: `Ctrl+l` checks for new mail now (in the calendar or
priority view it reloads that view instead). ewstui also checks in the
background every 5 minutes (`--refresh-interval MINUTES`, `0` turns it
off) and shows e.g. "New mail: Inbox (+2)". Fetching happens off the UI
thread, and the cursor and open message stay where they were.

**Mail**: `r` reply, `R` reply-all, `w` compose new, `d` delete,
`Space` toggle read/unread, `P` add to priority list (no priority,
no prompt), `p` add to priority list with a note, `A` archive, `v`
view/save/open attachments, `u` undo.

**Reading pane** (after `l`/`Enter` moves focus into it): `j`/`k`
scroll a line, `Ctrl+d`/`Ctrl+u` half a page, `Ctrl+f`/`Ctrl+b` (or
`Space`) a full page, `g`/`G` top/bottom, `h` back to the list.

**Deleting and undo**: `d` moves the message to Exchange's **Deleted
Items** folder (same as Delete in Outlook) — nothing is purged. `u`
moves the most recently deleted or archived message back to the folder
it came from; press it repeatedly to walk back further. The undo
history lasts for the session only; after a restart, recover from
Deleted Items by hand. Pressing `d` *inside* Deleted Items soft-deletes
the message (only recoverable via Outlook/OWA's "Recover deleted
items") and can't be undone with `u`.

**Calendar**: `n` new event, `d` delete event, `[`/`]` shift the
visible date range.

**Priority list**: `A`-`Z` set priority, `V` visual-select, `x` toggle
complete, `d` remove, `Enter`/`o` jump to email.

**Compose/new-event modals**: `Ctrl+S` send/save, `Esc` discard.
In compose, `Cmd+Enter` also sends — if your terminal passes the Cmd
key through (Ghostty, kitty and WezTerm do; in iTerm2 enable Profiles →
Keys → "Report keys using CSI u"; Terminal.app can't).

## Project layout

```
ewstui/
  config.py        CLI args -> Config dataclass
  auth.py           oauth2 / ntlm / basic / kerberos / auto -> exchangelib.Account
  ews_client.py      Live MailClient + CalendarClient wrapping exchangelib
  demo_backend.py    Fake in-memory stand-ins for --demo (same interface as ews_client)
  keymap.py          Single source of truth for the help-screen text
  screens.py         Modal screens: compose/reply, new event, help
  priority_store.py  Local todo.txt-backed priority list (parse/format/store)
  app.py             Main Textual App: three-pane mail view + calendar + priority views
  widgets/
    folder_list.py    Left pane
    message_table.py  Middle pane
    preview.py         Right pane
    calendar_view.py   Calendar agenda view
    priority_view.py   Priority list view (visual-select + reprioritize)
```

`app.py` never imports `exchangelib` directly — it only knows the
plain dataclasses in `ews_client.py` (`MessageSummary`, `EventSummary`,
etc.), which is what lets `--demo` swap in fake data with zero changes
to the UI code.

## Known limitations (v1)

- **Folder lookup is O(folders) per call** (`MailClient._folder_by_id`
  walks the folder tree each time) — fine for normal mailbox folder
  counts, but a hot path worth caching if you have hundreds of folders.
- **Embedded-item attachments** (a forwarded email or calendar invite
  attached as an item, rather than a file) aren't downloadable yet —
  `v` will show them in the list greyed out with a note, but selecting
  one just beeps rather than crashing.
- **No search** — `/` is documented as a future binding but not wired
  up yet.
- **No offline/local cache** — by design (see "Why this exists"), but
  it does mean no network = no mail. If you want offline reading,
  that's a different, larger project (sync EWS -> local Maildir, point
  a separate reader at that).
- **Kerberos path is untested** against a real KDC — the plumbing is
  there (`GSSAPI` auth_type via `requests-gssapi`) but wasn't
  exercised against a live server while building this.
- **OAuth2 scope is hardcoded** to
  `https://outlook.office365.com/EWS.AccessAsUser.All` — if your
  Azure AD app registration exposes a different scope, edit
  `DEFAULT_OAUTH_SCOPE` in `auth.py`.

- **`A` (archive) requires a folder literally named "Archive"** in
  your mailbox — `MailClient._find_archive_folder` looks it up by
  name (case-insensitive) under the mailbox root, since on-prem EWS
  doesn't expose a dedicated "online archive" well-known folder the
  way some cloud setups do. If you don't have one, create it once
  (or move a message into a folder called that manually) and archiving
  will find it from then on.

## Testing changes

There's no formal test suite yet, but `--demo` mode plus Textual's
`App.run_test()` (headless pilot testing) is how this was built and
verified — see the project history for example smoke-test patterns:
drive the app with `pilot.press("j")`, `pilot.press("enter")`, etc.,
and assert on `app.current_folder_id` / widget row counts.
