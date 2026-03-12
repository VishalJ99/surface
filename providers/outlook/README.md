# Outlook Provider

This provider exports unread Outlook Web mail into the shared Surface contract.

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
python providers/outlook/export_unread_emails.py setup
```

Export unread mail:

```bash
python providers/outlook/export_unread_emails.py export \
  --output providers/outlook/exports/unread.json \
  --headless
```

## Notes

- the browser profile is stored under `providers/outlook/.profiles/`
- exports are intended to be written outside git or under `providers/outlook/exports/`
- the output contract is `surface.unread_mail.v1`
- `can_rsvp` indicates whether Outlook currently exposes RSVP actions for that message
