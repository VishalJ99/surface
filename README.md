# Surface App

Surface App is a provider-first mail backend for a future macOS menu bar app. Each provider exports unread email into the same JSON contract so the UI layer can stay simple.

Primary docs:

- `docs/cli-architecture.md`: current CLI usage plus the recommended long-term provider/account/action architecture
- `docs/provider-architecture.md`: provider-facing design principles
- `AGENTS.md`: repo operating guide for future agents and automation

The current focus is backend only:

- stabilize the unread-mail export contract
- support Outlook first
- add Gmail next
- build the menu bar frontend after both providers can emit the same shape

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
python surface account list
python surface account inspect --provider outlook --account imperial
python surface unread export --provider outlook --account imperial --headless
```

Reserved for the next phase:

```bash
python surface action ...
python surface filter apply ...
```

The output contract lives in `contracts/unread-mail-v1.schema.json`.

By default, the root CLI stores account state, browser profiles, tokens, and default exports under `~/.surface/`. You can override that location with the `SURFACE_HOME` environment variable.

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
- fetches structured message and thread data from the authenticated Outlook session
- writes JSON in the shared unread-mail contract
- includes whether a message currently exposes RSVP actions

## Gmail Next

Gmail should use the Gmail API with OAuth desktop sign-in and a cached refresh token. It should not need browser automation unless a specific account restriction forces it.

That means the likely Gmail setup flow is:

1. create a Google Cloud project
2. enable the Gmail API
3. create OAuth desktop credentials
4. run `setup` once to sign in and cache the token locally
5. use `export` normally after that

## Notes

- `providers/*/.profiles/` is ignored because it contains browser session state.
- `providers/*/exports/` is ignored because exports can contain sensitive mail data.
- the frontend is intentionally not in this repo yet; the contract comes first
