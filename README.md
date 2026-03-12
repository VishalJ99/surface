# Surface App

Surface App is a provider-first mail backend for a future macOS menu bar app. Each provider exports unread email into the same JSON contract so the UI layer can stay simple.

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

## Provider Contract

Every provider should eventually expose the same CLI shape:

```bash
python providers/<provider>/export_unread_emails.py setup
python providers/<provider>/export_unread_emails.py export --output /absolute/path/to/unread.json
```

The output contract lives in `contracts/unread-mail-v1.schema.json`.

## Outlook Today

Outlook uses browser automation because the target school account does not allow the simpler programmatic routes.

Quick start:

```bash
conda env create -f environment.yml
conda activate surface-app
python -m playwright install chrome
python providers/outlook/export_unread_emails.py setup
```

After the one-time login bootstrap, export unread mail with:

```bash
python providers/outlook/export_unread_emails.py export \
  --output providers/outlook/exports/unread.json \
  --headless
```

The Outlook exporter:

- reuses a dedicated Chrome profile
- applies the `Unread` filter in Outlook Web
- fetches structured message data from the authenticated Outlook session
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
