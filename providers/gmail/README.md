# Gmail Provider

This provider is not implemented yet.

The expected approach is the Gmail API with OAuth desktop sign-in, not browser automation.

## Target CLI

```bash
python providers/gmail/export_unread_emails.py setup
python providers/gmail/export_unread_emails.py export --output /absolute/path/to/unread.json
```

## Expected Auth Model

- create a Google Cloud project
- enable the Gmail API
- create OAuth desktop credentials
- run `setup` once to sign in
- cache the refresh token locally for later exports

No API key-only flow is expected here because mailbox read access should use user OAuth scopes.
