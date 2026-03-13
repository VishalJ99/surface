# Gmail Provider

This provider is not implemented yet.

The expected approach is the Gmail API with OAuth desktop sign-in, not browser automation.

When the root CLI supports Gmail, account state should be stored under `~/.surface/accounts/gmail/<account>/` by default.

## Target CLI

```bash
python surface account setup --provider gmail --account personal
python surface unread export --provider gmail --account personal --output /absolute/path/to/unread.json
```

## Expected Auth Model

- create a Google Cloud project
- enable the Gmail API
- create OAuth desktop credentials
- run `setup` once to sign in
- cache the refresh token locally for later exports

No API key-only flow is expected here because mailbox read access should use user OAuth scopes.
