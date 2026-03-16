# Provider Architecture

Surface App is being built contract-first.

The rule is simple: every mail provider should export raw mailbox data into a provider-neutral JSON shape before any frontend work begins.

The development environment is shared across providers through the repo-level `environment.yml`.

Provider account state for the root CLI should live outside the repo under `~/.surface/` by default.
Provider-shared OAuth client configuration can also live there when a provider needs one app identity across multiple accounts.

## Current Direction

- `providers/outlook` uses browser automation because the target Outlook account does not allow the preferred programmatic access path.
- `providers/gmail` uses the Gmail API with OAuth desktop sign-in rather than browser automation.
- the future menu bar app should consume the shared contract rather than provider-specific payloads.

## Provider Interface

The public interface should be the root `surface` CLI.

Providers should plug into commands shaped like:

```bash
python surface account setup --provider <provider> --account <account>
python surface unread export --provider <provider> --account <account> --output /absolute/path/to/unread.json
python surface search export --provider <provider> --account <account> --query "term" --output /absolute/path/to/search.json
```

Provider-local scripts are still acceptable as internal entrypoints while the repo is being refactored.

`setup` is allowed to be provider-specific:

- Outlook: sign in once with an account-scoped dedicated browser profile
- Gmail: seed one shared `client_secret.json` for the provider, then complete desktop OAuth per account and cache each refresh token in `token.json`

`export` should always produce the same core message and thread shape:

- unread export remains unread-only
- no attachments in v1
- include enough metadata for the future UI to show quick actions such as RSVP

Search export can add top-level query metadata, but should keep the same `emails[]` and `threads[]` structure so downstream consumers can reuse the same parsing logic.

Derived LLM summaries should not be produced inside provider code.

- mail providers remain things like `outlook` and `gmail`
- LLM backends such as OpenRouter belong to the pipeline layer, not the provider layer
- post-processing should consume exported JSON and emit a separate derived contract

## New Provider Checklist

When implementing Gmail or another provider:

- put provider-specific code under `providers/<provider>/`
- wire public command handling through `surface_cli/main.py`
- keep account state under `~/.surface/accounts/<provider>/<account>/`
- emit JSON that matches `contracts/unread-mail-v1.schema.json`
- treat CSV as optional derived output only, not the source of truth
- keep LLM/post-processing logic outside `providers/<provider>/`
- update docs and contract files in the same change if the unread shape changes

For Gmail specifically, the current root CLI setup flow is:

```bash
python surface account setup \
  --provider gmail \
  --account personal \
  --client-secret-file /absolute/path/to/credentials.json
```

That first run seeds `~/.surface/providers/gmail/client_secret.json`. Later Gmail accounts can use:

```bash
python surface account setup \
  --provider gmail \
  --account work
```

`SURFACE_GMAIL_CLIENT_SECRET_FILE` can provide the initial client credentials file without passing the flag explicitly.

## Why This Structure

- keeps provider quirks isolated
- lets the future frontend stay thin
- makes adding new providers incremental instead of invasive
- avoids coupling the UI to Outlook or Gmail internals
