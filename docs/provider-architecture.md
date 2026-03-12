# Provider Architecture

Surface App is being built contract-first.

The rule is simple: every mail provider should export unread email into the same JSON shape before any frontend work begins.

The development environment is shared across providers through the repo-level `environment.yml`.

## Current Direction

- `providers/outlook` uses browser automation because the target Outlook account does not allow the preferred programmatic access path.
- `providers/gmail` should use the Gmail API with OAuth desktop sign-in rather than browser automation.
- the future menu bar app should consume the shared contract rather than provider-specific payloads.

## Provider Interface

Each provider should expose:

```bash
python providers/<provider>/export_unread_emails.py setup
python providers/<provider>/export_unread_emails.py export --output /absolute/path/to/unread.json
```

`setup` is allowed to be provider-specific:

- Outlook: sign in once with a dedicated browser profile
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
