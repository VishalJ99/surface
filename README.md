# Surface App

Surface App is a provider-first mail backend for a future macOS menu bar app. Providers export raw mailbox data into a shared JSON shape so the UI layer can stay simple.

Primary docs:

- `docs/cli-architecture.md`: current CLI usage plus the recommended long-term provider/account/action architecture
- `docs/menubar-popover-architecture.md`: menu bar popover product boundaries, sync model, and frontend/backend interaction plan
- `docs/provider-architecture.md`: provider-facing design principles
- `AGENTS.md`: repo operating guide for future agents and automation

The current focus is backend only:

- stabilize the unread-mail export contract
- support Outlook and Gmail
- build the read-only menu bar data path before UI iteration

## Repo Shape

```text
surface-app/
  contracts/
  docs/
  providers/
    gmail/
    outlook/
```

## Public CLI

The public automation surface is now the repo-local `surface` CLI.

Current invocation in this repo:

```bash
python surface --help
```

Implemented today:

```bash
python surface account setup --provider outlook --account imperial
python surface account setup --provider gmail --account personal --client-secret-file /absolute/path/to/credentials.json
python surface account list
python surface account inspect --provider outlook --account imperial
python surface unread export --provider outlook --account imperial --headless
python surface search export --provider outlook --account work --query josh --max-results 50 --headless
python surface unread export --provider gmail --account personal
python surface sync run
python surface view build --view menubar
python surface filter apply --input ~/.surface/exports/raw/outlook-work-search.json --output ~/.surface/exports/derived/outlook-work-search-thread-summaries.json --backend openrouter
```

Reserved for the next phase:

```bash
python surface action ...
```

The unread export contract lives in `contracts/unread-mail-v1.schema.json`.
Outlook search export currently reuses the same `emails[]` and `threads[]` shape, with top-level search metadata added for query-specific exports.
Derived thread summaries live in `contracts/thread-summaries-v1.schema.json`.
The current read-only menu bar view contract lives in `contracts/filtered-menubar-v1.schema.json`.

By default, the root CLI stores account state, browser profiles, tokens, and default exports under `~/.surface/`. You can override that location with the `SURFACE_HOME` environment variable.

For the current menu bar phase:

- `python surface sync run` refreshes ready accounts and rebuilds the menubar view
- `python surface view build --view menubar` reshapes existing raw unread exports into `~/.surface/exports/filtered/menubar-inbox.json`
- no blocking, semantic filtering, or summaries are attached to the menubar view yet

## Optional LLM Post-Processing

Surface can now build a derived per-thread summary artifact from a raw unread or search export.

Current implementation notes:

- only OpenRouter is implemented today as the LLM backend
- the raw export stays canonical and unchanged
- the derived summary artifact is written separately under `~/.surface/exports/derived/` by default
- `python surface unread export ...` and `python surface search export ...` will auto-run post-processing when `OPENROUTER_API_KEY` is configured unless `--skip-post-process` is set
- the canonical manual entrypoint is `python surface filter apply --input ... --output ...`

You can configure the backend through environment variables or a repo-local `.env` file. See `.env.example` for the supported keys.

Example `.env`:

```bash
OPENROUTER_API_KEY=your_key_here
SURFACE_POST_PROCESS_BACKEND=openrouter
SURFACE_POST_PROCESS_MODEL=qwen/qwen3.5-397b-a17b
```

Example manual run:

```bash
python surface filter apply \
  --input ~/.surface/exports/raw/outlook-work-search.json \
  --output ~/.surface/exports/derived/outlook-work-search-thread-summaries.json \
  --backend openrouter
```

Example export-time override:

```bash
python surface search export \
  --provider outlook \
  --account work \
  --query pizza \
  --post-process-model qwen/qwen3.5-397b-a17b \
  --post-process-output ~/.surface/exports/derived/outlook-work-search-thread-summaries.json \
  --headless
```

## Outlook Today

Outlook uses browser automation because the target school account does not allow the simpler programmatic routes.

Quick start:

```bash
conda env create -f environment.yml
conda activate surface-app
python -m playwright install chrome
python surface account setup --provider outlook --account imperial
```

If you rerun `account setup` with the same `--provider` and `--account`, the CLI now warns that an existing setup was found and requires pressing Enter before it reuses or overwrites that account-scoped state.

After the one-time login bootstrap, export unread mail with:

```bash
python surface unread export \
  --provider outlook \
  --account imperial \
  --output ~/.surface/exports/raw/outlook-imperial-unread.json \
  --headless
```

Search mail with the same message and thread shape:

```bash
python surface search export \
  --provider outlook \
  --account work \
  --query josh \
  --max-results 50 \
  --thread-depth all \
  --output ~/.surface/exports/raw/outlook-work-search.json \
  --headless
```

To test multiple Outlook accounts, use different `--account` values. Each account gets its own persistent browser profile:

```bash
python surface account setup --provider outlook --account work
python surface account setup --provider outlook --account personal

python surface unread export --provider outlook --account work --headless
python surface unread export --provider outlook --account personal --headless

python surface account list
```

The Outlook exporter:

- reuses an account-scoped Chrome profile
- applies the `Unread` filter in Outlook Web
- exhausts the filtered unread list
- can also execute a mailbox search query and export the returned top-level result rows
- fetches structured message and thread data from the authenticated Outlook session
- writes JSON in the shared unread-mail contract shape
- can optionally auto-run derived thread summarization after raw export through the root CLI
- includes whether a message currently exposes RSVP actions

## Gmail Today

Gmail uses the Gmail API with desktop OAuth sign-in and a cached refresh token. It does not use browser automation for mailbox fetches.

Quick start:

1. create a Google Cloud project
2. enable the Gmail API
3. create OAuth desktop credentials
4. seed Surface with that app credential once
5. run Gmail account setup for each Gmail account you want to connect

First machine/app bootstrap:

```bash
python surface account setup \
  --provider gmail \
  --account personal \
  --client-secret-file /absolute/path/to/credentials.json
```

Later Gmail accounts on the same machine:

```bash
python surface account setup \
  --provider gmail \
  --account work
```

Export:

```bash
python surface unread export \
  --provider gmail \
  --account personal \
  --output ~/.surface/exports/raw/gmail-personal-unread.json
```

The Gmail provider:

- stores account config under `~/.surface/accounts/gmail/<account>/config.json`
- stores the shared OAuth client credentials under `~/.surface/providers/gmail/client_secret.json`
- stores the refreshable user token under `~/.surface/accounts/gmail/<account>/token.json`
- fetches unread mail programmatically from the Gmail API
- expands each unread message into thread history in the shared unread-mail contract

For local testing with unverified OAuth credentials, add the Gmail accounts you want to use as Google OAuth test users.

## Notes

- `.env` is ignored by git; use `.env.example` as the documented template
- `providers/*/.profiles/` is ignored because it contains browser session state.
- `providers/*/exports/` is ignored because exports can contain sensitive mail data.
- the frontend is intentionally not in this repo yet; the contract comes first
