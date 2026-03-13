# Provider Architecture

Surface App is being built contract-first.

The rule is simple: every mail provider should export unread email into the same JSON shape before any frontend work begins.

The development environment is shared across providers through the repo-level `environment.yml`.

Provider account state for the root CLI should live outside the repo under `~/.surface/` by default.

## Current Direction

- `providers/outlook` uses browser automation because the target Outlook account does not allow the preferred programmatic access path.
- `providers/gmail` should use the Gmail API with OAuth desktop sign-in rather than browser automation.
- the future menu bar app should consume the shared contract rather than provider-specific payloads.

## Provider Interface

The public interface should be the root `surface` CLI.

Providers should plug into commands shaped like:

```bash
python surface account setup --provider <provider> --account <account>
python surface unread export --provider <provider> --account <account> --output /absolute/path/to/unread.json
```

Provider-local scripts are still acceptable as internal entrypoints while the repo is being refactored.

`setup` is allowed to be provider-specific:

- Outlook: sign in once with an account-scoped dedicated browser profile
- Gmail: complete OAuth once and cache the refresh token

`export` should always produce the same contract:

- unread messages only
- no attachments in v1
- include enough metadata for the future UI to show quick actions such as RSVP

## Why This Structure

- keeps provider quirks isolated
- lets the future frontend stay thin
- makes adding new providers incremental instead of invasive
- avoids coupling the UI to Outlook or Gmail internals
