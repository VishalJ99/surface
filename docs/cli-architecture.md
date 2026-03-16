# CLI And Architecture Plan

## Why This Doc Exists

Surface is not just a scraper repo. It is intended to become a mail automation backend that can be driven by:

- future agents
- a macOS menu bar app
- a future iOS app
- any other thin frontend that reads mailbox state and triggers actions

The backend therefore needs a stable CLI and clear boundaries between provider code, filtering code, and frontend code.

By default, the root CLI should persist account state outside the repo under `~/.surface/`. Use `SURFACE_HOME` only when you intentionally want a different root state directory.

## Core Terms

### Provider

Mailbox backend implementation such as `outlook` or `gmail`.

### Account

A configured mailbox identity under a provider.

Examples:

- `outlook/imperial`
- `outlook/personal`
- `gmail/work`

Use `account`, not `user`, throughout the codebase and CLI.

### Raw unread export

Canonical provider output containing unread mail plus thread metadata. This should be JSON-first.

### Derived view

Frontend-facing filtered/classified/summarized mailbox data built from the raw export.

### Provider action

Mailbox mutation or send operation performed against the provider, such as reply or archive.

### App-local action

Frontend or local-state behavior that should not mutate the remote mailbox, such as dismissing an item from the menu bar.

## Recommended Architectural Split

The codebase should have four layers.

### 1. Provider layer

Provider-specific auth, unread export, and actions.

Responsibilities:

- sign-in and token/profile setup
- unread mailbox fetch
- thread fetch
- reply/forward/RSVP/delete/archive/mark-read

This is the only layer that should know Outlook or Gmail implementation details.

### 2. Contract layer

Provider-neutral schemas and typed data models.

Responsibilities:

- raw unread mail contract
- future filtered/view contract
- future action request/result contracts

### 3. Pipeline layer

Transforms raw provider exports into frontend-facing data.

Responsibilities:

- sender regex blocking
- keyword blocking
- LLM-based classification
- summarization
- local UI state joins such as dismiss or mute

This layer should not talk to Outlook or Gmail directly. It should operate on exported data.

### 4. App layer

Frontend code for the menu bar app, future full mail client, and future iOS app.

Responsibilities:

- render unread mail and threads
- show summaries
- let the user trigger backend actions
- maintain lightweight local UI state

The app layer should call stable CLIs or a later shared backend library, not provider-specific scripts.

## Recommended Repo Shape

Current repo shape is backend-only and provider-heavy. That is fine for now, but the target should look more like this:

```text
surface/
  AGENTS.md
  README.md
  contracts/
    unread-mail-v1.schema.json
    filtered-mail-v1.schema.json
    action-request-v1.schema.json
    action-result-v1.schema.json
  docs/
    cli-architecture.md
    provider-architecture.md
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
  apps/
    menubar/
    ios/
```

Recommended default local state home:

```text
~/.surface/
  providers/
    gmail/
      client_secret.json
  accounts/
    outlook/
      imperial/
        config.json
        profile/
    gmail/
      personal/
        config.json
        token.json
  exports/
    raw/
    filtered/
  rules/
    senders.json
    keywords.json
    llm-classifiers.json
  ui/
    dismissals.json
    pinned.json
```

## CLI Strategy

### Recommendation

Do not grow many unrelated top-level scripts.

Use one root CLI with subcommands as the stable automation interface.

Example command family:

```bash
surface account setup
surface account list
surface unread export
surface action reply
surface action reply-all
surface action forward
surface action rsvp
surface filter apply
```

Provider-local scripts can still exist for development and debugging, but they should become implementation details over time.

This repo now has the first pass of that root CLI as the repo-local entrypoint:

```bash
python surface --help
```

### Why One Root CLI Is Better

- agents only need to learn one interface
- frontends can stay provider-agnostic
- provider switching does not change the external control surface
- account enumeration becomes consistent
- future action CLIs can share request parsing and validation

## Current CLI Reference

Today the repo has a working root CLI with Outlook and Gmail `account setup` and `unread export` wired through it.

### Outlook setup

```bash
python surface account setup \
  --provider outlook \
  --account imperial \
  [--label "Imperial Outlook"] \
  [--mailbox-url https://outlook.office.com/mail/]
```

Arguments:

- `--provider`: currently `outlook`
- `--account`: local account slug
- `--label`: optional display label stored in account config
- `--mailbox-url`: mailbox entry URL

Behavior:

- creates or updates local account config under `~/.surface/accounts/`
- opens Chrome with an account-scoped persistent profile
- waits for manual login and MFA
- saves session state for later exports
- if the same `provider/account` already exists, prints a warning and requires pressing Enter before continuing

### Outlook unread export

```bash
python surface unread export \
  --provider outlook \
  --account imperial \
  [--output /absolute/path/to/unread.json] \
  [--mailbox-url https://outlook.office.com/mail/] \
  [--headless]
```

Arguments:

- `--provider`: currently `outlook`
- `--account`: local account slug
- `--output`: optional path for the JSON unread export
- `--mailbox-url`: mailbox entry URL override
- `--headless`: run without displaying the browser

Behavior:

- resolves the account-scoped profile path from `provider + account`
- opens Outlook Web with the saved profile
- applies the `Unread` filter in the browser UI
- exhausts the filtered infinite-scroll list
- fetches structured thread contents from the authenticated Outlook session
- writes unread messages plus thread history into the shared JSON contract

### Outlook search export

```bash
python surface search export \
  --provider outlook \
  --account work \
  --query josh \
  [--max-results 50] \
  [--thread-depth all] \
  [--output /absolute/path/to/search.json] \
  [--mailbox-url https://outlook.office.com/mail/] \
  [--headless]
```

Arguments:

- `--provider`: currently `outlook`
- `--account`: local account slug
- `--query`: search term entered into the Outlook search box
- `--max-results`: optional cap on returned top-level search results
- `--thread-depth`: `all` or a positive integer limit for messages included per thread
- `--output`: optional path for the JSON search export
- `--mailbox-url`: mailbox entry URL override
- `--headless`: run without displaying the browser

Behavior:

- resolves the account-scoped profile path from `provider + account`
- opens Outlook Web with the saved profile
- enters the search term into the Outlook search box
- collects the returned top-level result rows in list order
- fetches structured thread contents from the authenticated Outlook session
- writes the same `emails[]` and `threads[]` shape as unread export, with extra top-level search metadata

### Outlook multi-account example

The current Outlook implementation should support multiple accounts by assigning each one a separate account slug and therefore a separate persistent Chrome profile.

Example:

```bash
python surface account setup --provider outlook --account work
python surface account setup --provider outlook --account personal

python surface unread export --provider outlook --account work --headless
python surface unread export --provider outlook --account personal --headless

python surface account inspect --provider outlook --account work
python surface account inspect --provider outlook --account personal
python surface account list
```

Expected local state layout:

```text
~/.surface/accounts/outlook/work/profile/
~/.surface/accounts/outlook/personal/profile/
~/.surface/exports/raw/outlook-work-unread.json
~/.surface/exports/raw/outlook-personal-unread.json
```

### Gmail setup

```bash
python surface account setup \
  --provider gmail \
  --account personal
```

Arguments:

- `--provider`: `gmail`
- `--account`: local account slug
- `--label`: optional display label stored in account config
- `--client-secret-file`: optional first-run bootstrap path for the shared Google OAuth desktop credentials JSON. You can also set `SURFACE_GMAIL_CLIENT_SECRET_FILE`.

Behavior:

- creates or updates local account config under `~/.surface/accounts/`
- on the first Gmail setup, copies OAuth desktop client credentials into `~/.surface/providers/gmail/client_secret.json`
- opens a browser for Google sign-in and consent
- saves a refreshable Gmail token under `~/.surface/accounts/gmail/<account>/token.json`
- if the same `provider/account` already exists, prints a warning and requires pressing Enter before continuing

First Gmail bootstrap example:

```bash
python surface account setup \
  --provider gmail \
  --account personal \
  --client-secret-file /absolute/path/to/credentials.json
```

Second Gmail account on the same machine:

```bash
python surface account setup \
  --provider gmail \
  --account work
```

### Gmail unread export

```bash
python surface unread export \
  --provider gmail \
  --account personal \
  [--output /absolute/path/to/unread.json]
```

Arguments:

- `--provider`: `gmail`
- `--account`: local account slug
- `--output`: optional path for the JSON unread export

Behavior:

- loads and refreshes the cached Gmail OAuth token when needed
- lists unread Gmail messages programmatically
- fetches full thread contents for threads containing unread mail
- writes unread messages plus thread history into the shared JSON contract

Expected local state layout:

```text
~/.surface/providers/gmail/client_secret.json
~/.surface/accounts/gmail/personal/token.json
~/.surface/exports/raw/gmail-personal-unread.json
```

### Reserved root CLI commands

The root shape is now fixed even though some commands are still stubs:

```bash
python surface action reply ...
python surface action reply-all ...
python surface action forward ...
python surface action rsvp ...
python surface action mark-read ...
python surface action archive ...
python surface action delete ...
python surface filter apply ...
```

These currently return explicit "not implemented yet" errors.

### CSV

CSV should remain optional.

Reason:

- thread history
- action metadata
- hierarchy
- future LLM annotations

all fit naturally in JSON and poorly in CSV.

Recommendation:

- JSON is canonical
- CSV is derived and optional for debugging, spreadsheet inspection, or manual review

## Recommended Target CLI

### Account CLI

These commands manage configured accounts and their auth state.

Examples:

```bash
surface account setup --provider outlook --account imperial
surface account setup --provider gmail --account personal
surface account list
surface account inspect --provider outlook --account imperial
```

Recommended arguments:

- `--provider`: required provider id such as `outlook` or `gmail`
- `--account`: required local account slug such as `imperial`
- `--label`: optional display label
- `--client-secret-file`: optional first-run Gmail OAuth desktop client credentials JSON

Behavior by provider:

- Outlook: launch browser profile setup and store profile under account state
- Gmail: seed one shared `client_secret.json` at provider scope, then store a refresh token per account

### Unread export CLI

These commands fetch raw unread mail for a specific account.

Examples:

```bash
surface unread export --provider outlook --account imperial --output state/exports/raw/outlook-imperial.json
surface unread export --provider gmail --account personal --output ~/.surface/exports/raw/gmail-personal.json
```

Recommended arguments:

- `--provider`
- `--account`
- `--output`
- `--headless` where relevant
- `--format json|csv` if CSV becomes first-class later

Recommended output fields:

- provider
- account
- unread emails
- thread history
- action affordances
- export timestamp

## Action CLI

This is the long-term automation surface for agents.

Recommended commands:

```bash
surface action reply
surface action reply-all
surface action forward
surface action rsvp
surface action mark-read
surface action archive
surface action delete
```

Recommended common arguments:

- `--provider`
- `--account`
- `--message-id`
- `--conversation-id`
- `--internet-message-id`

Recommended content arguments:

- `--body`
- `--body-file`
- `--to`
- `--cc`
- `--bcc`
- `--subject`
- `--attachments`

Recommended RSVP arguments:

- `--response accept|tentative|decline`
- `--comment`

Important distinction:

- `dismiss` should not live in the provider action CLI unless it maps to a real mailbox mutation.
- If `dismiss` only means "hide this from the Surface UI", store it in local app state or the filter/view pipeline.

## Filtering And View Pipeline

Do not mix filtering rules into provider export code.

Recommended pipeline:

1. Export raw unread mail from each account.
2. Merge exports if the frontend wants a unified inbox.
3. Apply sender regex blocking.
4. Apply keyword blocking.
5. Apply semantic/LLM classification.
6. Attach summaries if enabled.
7. Write a derived filtered view contract for the frontend.

Recommended future command:

```bash
surface filter apply \
  --input ~/.surface/exports/raw/outlook-imperial.json \
  --output ~/.surface/exports/filtered/outlook-imperial.json
```

Or for a merged inbox:

```bash
surface filter apply \
  --input-glob '~/.surface/exports/raw/*.json' \
  --output ~/.surface/exports/filtered/unified.json
```

## Menu Bar App Responsibilities

The menu bar app should:

- read the filtered view, not raw provider HTML/UI state
- show concise unread items across accounts
- support quick reply, forward, RSVP, and summary display
- support opening a richer mail client view

The menu bar app should not:

- contain provider auth logic
- scrape Outlook or Gmail itself
- directly own mailbox-specific action semantics

## Full Mail Client Responsibilities

The richer in-app client can:

- show the full thread history
- show one-step-back quoted context inline
- open an AI drafting pane with the thread preloaded
- trigger provider actions through the action CLI

## Recommended Refactor Path From Today

Do this incrementally.

### Step 1

Keep the current Outlook script working.

### Step 2

Split the current Outlook implementation internally into:

- `auth.py`
- `unread.py`
- `actions.py`

Keep the CLI entrypoint thin.

### Step 3

Introduce account-scoped state instead of the current single hard-coded Outlook profile path.

Example:

- `~/.surface/accounts/outlook/imperial/profile/`
- `~/.surface/accounts/outlook/personal/profile/`

### Step 4

Introduce a root CLI that dispatches to provider implementations.

### Step 5

Add Gmail under the same account/export/action interface.

### Step 6

Add filtered-view contracts and the frontend apps on top.
