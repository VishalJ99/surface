# Outlook Provider

This provider exports Outlook Web unread mail and search results into the shared Surface contract shape.

## Why Browser Automation

This Outlook account is constrained enough that standard programmatic access is not the reliable path, so this provider uses Playwright with a dedicated Chrome profile.

## Commands

Create the environment and install Chrome support:

```bash
conda env create -f environment.yml
conda activate surface-app
python -m playwright install chrome
```

Run the one-time login bootstrap:

```bash
python surface account setup --provider outlook --account imperial
```

If you run the same setup command again for an existing account slug, the root CLI warns before continuing and requires pressing Enter to confirm reuse/overwrite of that account's saved state.

Export unread mail:

```bash
python surface unread export \
  --provider outlook \
  --account imperial \
  --output ~/.surface/exports/raw/outlook-imperial-unread.json \
  --headless
```

Export search results with the same message and thread shape:

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

Multiple accounts are supported by using distinct account slugs through the root CLI:

```bash
python surface account setup --provider outlook --account work
python surface account setup --provider outlook --account personal

python surface unread export --provider outlook --account work --headless
python surface unread export --provider outlook --account personal --headless

python surface account list
```

The provider-local script remains available as the implementation entrypoint for now:

```bash
python providers/outlook/export_unread_emails.py setup
python providers/outlook/export_unread_emails.py export --account work --output /absolute/path/to/unread.json --headless
python providers/outlook/export_unread_emails.py search-export --account work --query josh --output /absolute/path/to/search.json --headless
```

## Notes

- the account-scoped browser profile is stored under `~/.surface/accounts/outlook/<account>/profile/` when using the root CLI
- exports are intended to be written outside git, for example under `~/.surface/exports/`
- set `SURFACE_HOME` if you want the root CLI to use a different state home
- the output contract is `surface.unread_mail.v1`
- `can_rsvp` indicates whether Outlook currently exposes RSVP actions for that message
