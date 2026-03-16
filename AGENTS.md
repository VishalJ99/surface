# AGENTS.md

## Purpose

Surface is a provider-first mail backend for a future macOS menu bar app and future mobile/desktop mail surfaces.

The backend is the automation surface. Agents should be able to:

- set up provider accounts
- export raw unread mail
- search mail and export raw search results
- run mail actions such as reply or archive
- apply filtering and summarization layers

Frontends should consume derived data from the backend rather than reimplement provider logic.

## Read First

- [README.md](/Users/vishaljain/surface/README.md)
- [docs/cli-architecture.md](/Users/vishaljain/surface/docs/cli-architecture.md)
- [docs/provider-architecture.md](/Users/vishaljain/surface/docs/provider-architecture.md)
- [contracts/unread-mail-v1.schema.json](/Users/vishaljain/surface/contracts/unread-mail-v1.schema.json)

## Current State

- Outlook unread export is implemented.
- Outlook search export is implemented.
- Gmail unread export is implemented with the Gmail API and desktop OAuth.
- The canonical export artifact is JSON.
- CSV is optional and should be treated as a convenience export, not the source of truth.
- Provider session state and exports are sensitive and should not be committed.
- The root CLI stores state outside the repo by default under `~/.surface/`.
- `SURFACE_HOME` can override that root state directory.

## Current CLI

The public CLI shape is `surface`.

Current repo-local invocation:

```bash
python surface --help
```

### Outlook

Setup:

```bash
python surface account setup \
  --provider outlook \
  --account imperial \
  [--label "Imperial Outlook"] \
  [--mailbox-url https://outlook.office.com/mail/]
```

Export unread mail:

```bash
python surface unread export \
  --provider outlook \
  --account imperial \
  [--output /absolute/path/to/unread.json] \
  [--mailbox-url https://outlook.office.com/mail/] \
  [--headless]
```

Export search results:

```bash
python surface search export \
  --provider outlook \
  --account imperial \
  --query "josh" \
  [--max-results 50] \
  [--thread-depth all] \
  [--output /absolute/path/to/search.json] \
  [--mailbox-url https://outlook.office.com/mail/] \
  [--headless]
```

Current argument meanings:

- `--provider`: provider id such as `outlook`
- `--account`: local account slug such as `imperial`
- `--query`: mailbox search term for `surface search export`
- `--max-results`: optional cap on top-level search result rows
- `--thread-depth`: `all` or a positive integer limit for messages returned per thread
- `--mailbox-url`: provider mailbox entry URL
- `--output`: optional JSON output path
- `--headless`: run export without showing a browser window

Internally, Outlook still uses the provider-local implementation in `providers/outlook/export_unread_emails.py`.

### Gmail

Setup:

```bash
python surface account setup \
  --provider gmail \
  --account personal \
  [--client-secret-file /absolute/path/to/credentials.json]
```

Export unread mail:

```bash
python surface unread export \
  --provider gmail \
  --account personal \
  [--output /absolute/path/to/unread.json]
```

Current Gmail auth/state notes:

- `--client-secret-file` is only needed the first time to seed the shared Google OAuth desktop credentials JSON
- `SURFACE_GMAIL_CLIENT_SECRET_FILE` can provide that same first-run file
- shared OAuth client credentials are stored under `~/.surface/providers/gmail/client_secret.json`
- refreshable user token state is stored under `~/.surface/accounts/gmail/<account>/token.json`
- unread export is programmatic through the Gmail API, not browser automation

## Architecture Rules

- Use `account`, not `user`, as the mailbox identity term.
- An account is provider-scoped, for example `outlook/work` or `gmail/personal`.
- User auth tokens should be account-specific. Shared provider OAuth app credentials can live at provider scope.
- Root CLI state should live under `~/.surface/` by default, not inside the git repo.
- Provider quirks stay inside `providers/<provider>/`.
- Shared contracts stay in `contracts/`.
- Frontends and agents should target stable CLIs, not provider internals.
- Raw unread export is distinct from filtered/frontend view data.
- Raw search export is also distinct from filtered/frontend view data.

## Implementing A New Provider

When adding any future provider, use this insertion pattern:

- provider-specific logic belongs under `providers/<provider>/`
- root CLI dispatch belongs in `surface_cli/main.py`
- account state belongs under `~/.surface/accounts/<provider>/<account>/`
- raw exports belong under `~/.surface/exports/raw/`
- canonical unread output must match `contracts/unread-mail-v1.schema.json`
- search export should preserve the same `emails[]` and `threads[]` shape when possible
- JSON is required; CSV is optional and derived only

Gmail implementation notes:

- Gmail account setup lives behind `python surface account setup --provider gmail --account <account>`
- Gmail unread export lives behind `python surface unread export --provider gmail --account <account>`
- Gmail OAuth user tokens are account-specific under `~/.surface/accounts/gmail/<account>/`
- Gmail OAuth app credentials are shared under `~/.surface/providers/gmail/`
- do not make agents or frontends call provider-local modules directly
- update docs and the schema in the same change if the contract changes

## Action Boundaries

Provider actions:

- reply
- reply-all
- forward
- RSVP
- mark-read
- archive
- delete

App-local actions:

- dismiss
- block
- mute in UI
- local categorization

Do not treat `dismiss` as a mailbox provider action unless the intent is actually `mark-read` or `archive`.

## Recommended Target Structure

Keep provider implementations isolated, but converge on one top-level CLI surface.

Recommended long-term layout:

```text
surface/
  contracts/
  docs/
  providers/
    outlook/
      auth.py
      unread.py
      actions.py
      cli.py
    gmail/
      auth.py
      unread.py
      actions.py
      cli.py
  state/
    accounts/
    exports/
    views/
    rules/
  apps/
    menubar/
    ios/
```

## Recommended Target CLI

The repo should converge on a single root CLI, for example `surface` or `python -m surface`, with subcommands such as:

- `surface account setup`
- `surface account list`
- `surface unread export`
- `surface search export`
- `surface action reply`
- `surface action reply-all`
- `surface action forward`
- `surface action rsvp`
- `surface filter apply`

This now exists as the repo-local `surface` CLI entrypoint. Provider-local scripts remain implementation entrypoints behind it.

## Change Discipline

- If you change the unread contract, update the schema and docs in the same change.
- Do not commit files under profile, token, export, or cache directories.
- Prefer additive schema changes unless a breaking version bump is intentional.
- Keep JSON as the canonical interchange format for agents and frontends.
