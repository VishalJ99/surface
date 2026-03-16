# Gmail Provider

This provider exports unread Gmail mail into the shared Surface contract with the Gmail API.

It uses desktop OAuth sign-in for setup, then refreshable tokens for later exports. Mailbox fetch is programmatic; there is no browser automation for unread export.

User token state is stored under `~/.surface/accounts/gmail/<account>/` by default when you use the root CLI. The OAuth client credentials for the app are stored once per machine under `~/.surface/providers/gmail/client_secret.json`.

## Setup

Create Google OAuth desktop credentials first:

1. create a Google Cloud project
2. enable the Gmail API
3. create OAuth desktop app credentials
4. download the client credentials JSON

Bootstrap Surface with that app credential once:

```bash
python surface account setup \
  --provider gmail \
  --account personal \
  --client-secret-file /absolute/path/to/credentials.json
```

That copies the OAuth client credentials into Surface's shared Gmail provider state and completes sign-in for the `personal` account.

After that, additional Gmail accounts do not need the JSON file again:

```bash
python surface account setup \
  --provider gmail \
  --account work
```

You can also provide the initial credentials file through `SURFACE_GMAIL_CLIENT_SECRET_FILE`.

The setup flow will:

- copy the shared OAuth client credentials into `~/.surface/providers/gmail/client_secret.json`
- open the browser for Google sign-in and consent
- cache the refreshable token in `~/.surface/accounts/gmail/<account>/token.json`
- store the configured account record in `config.json`

## Export unread mail

```bash
python surface unread export \
  --provider gmail \
  --account personal \
  --output ~/.surface/exports/raw/gmail-personal-unread.json
```

The exporter:

- lists unread Gmail messages with the Gmail API
- fetches full thread history for those unread messages
- extracts message headers and plain-text/HTML bodies
- writes JSON in `surface.unread_mail.v1`

## Local State

```text
~/.surface/accounts/gmail/<account>/config.json
~/.surface/accounts/gmail/<account>/token.json
~/.surface/providers/gmail/client_secret.json
~/.surface/exports/raw/gmail-<account>-unread.json
```

No API key-only flow is used here because mailbox read access requires user OAuth scopes.

Because this provider requests `https://www.googleapis.com/auth/gmail.readonly`, expect Google OAuth testing and verification rules to apply. For local testing, add the Gmail accounts you want to use as test users in the Google Auth Platform if the app is still unverified.
