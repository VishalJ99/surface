from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SURFACE_HOME = Path.home() / ".surface"
SURFACE_HOME = Path(os.environ.get("SURFACE_HOME", str(DEFAULT_SURFACE_HOME))).expanduser()
ACCOUNTS_DIR = SURFACE_HOME / "accounts"
EXPORTS_DIR = SURFACE_HOME / "exports"
RAW_EXPORTS_DIR = EXPORTS_DIR / "raw"

SUPPORTED_PROVIDERS = ("outlook", "gmail")
ACTION_COMMANDS = ("reply", "reply-all", "forward", "rsvp", "mark-read", "archive", "delete")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def account_dir(provider: str, account: str) -> Path:
    return ACCOUNTS_DIR / provider / account


def account_config_path(provider: str, account: str) -> Path:
    return account_dir(provider, account) / "config.json"


def default_raw_export_path(provider: str, account: str) -> Path:
    return RAW_EXPORTS_DIR / f"{provider}-{account}-unread.json"


def outlook_profile_dir(provider: str, account: str) -> Path:
    return account_dir(provider, account) / "profile"


def gmail_token_path(provider: str, account: str) -> Path:
    return account_dir(provider, account) / "token.json"


def state_paths(provider: str, account: str) -> dict[str, str]:
    paths: dict[str, str] = {
        "surface_home": str(SURFACE_HOME.resolve()),
        "account_dir": str(account_dir(provider, account).resolve()),
        "config_path": str(account_config_path(provider, account).resolve()),
        "default_raw_export_path": str(default_raw_export_path(provider, account).resolve()),
    }
    if provider == "outlook":
        paths["profile_dir"] = str(outlook_profile_dir(provider, account).resolve())
    if provider == "gmail":
        paths["token_path"] = str(gmail_token_path(provider, account).resolve())
    return paths


def load_account_config(provider: str, account: str) -> dict[str, Any] | None:
    path = account_config_path(provider, account)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_account_config(provider: str, account: str, payload: dict[str, Any]) -> None:
    path = account_config_path(provider, account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_account_record(
    *,
    provider: str,
    account: str,
    label: str | None,
    mailbox_url: str | None,
    status: str,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    created_at = (existing or {}).get("created_at") or utc_now()
    record = {
        "provider": provider,
        "account": account,
        "label": label or (existing or {}).get("label") or account,
        "mailbox_url": mailbox_url or (existing or {}).get("mailbox_url"),
        "status": status,
        "created_at": created_at,
        "updated_at": utc_now(),
    }
    return record


def existing_setup_paths(provider: str, account: str) -> list[Path]:
    paths: list[Path] = []
    for path in (account_dir(provider, account), account_config_path(provider, account)):
        if path.exists():
            paths.append(path)

    if provider == "outlook":
        profile_dir = outlook_profile_dir(provider, account)
        if profile_dir.exists():
            paths.append(profile_dir)
    if provider == "gmail":
        token_path = gmail_token_path(provider, account)
        if token_path.exists():
            paths.append(token_path)

    unique_paths: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(resolved)
    return unique_paths


def confirm_setup_overwrite(provider: str, account: str) -> None:
    paths = existing_setup_paths(provider, account)
    if not paths:
        return

    print()
    print(f"Warning: setup already exists for account tag {provider}/{account}.")
    print("Existing state paths:")
    for path in paths:
        print(f"- {path}")
    input("Press Enter to continue and overwrite/reuse this setup, or Ctrl+C to cancel...")


def ensure_account_exists(provider: str, account: str) -> dict[str, Any]:
    config = load_account_config(provider, account)
    if config is None:
        raise SystemExit(
            f"Account {provider}/{account} is not configured. Run "
            f"`python surface account setup --provider {provider} --account {account}` first."
        )
    return config


def provider_mailbox_url(args: argparse.Namespace, config: dict[str, Any] | None, *, default: str | None) -> str | None:
    return args.mailbox_url or (config or {}).get("mailbox_url") or default


def outlook_module():
    from providers.outlook import export_unread_emails as outlook

    return outlook


def handle_account_setup(args: argparse.Namespace) -> int:
    if args.provider == "gmail":
        raise SystemExit("Gmail account setup is not implemented yet.")

    if args.provider != "outlook":
        raise SystemExit(f"Unsupported provider: {args.provider}")

    confirm_setup_overwrite(args.provider, args.account)
    existing = load_account_config(args.provider, args.account)
    outlook = outlook_module()
    mailbox_url = provider_mailbox_url(args, existing, default=outlook.DEFAULT_OUTLOOK_URL)

    pending_record = build_account_record(
        provider=args.provider,
        account=args.account,
        label=args.label,
        mailbox_url=mailbox_url,
        status="pending",
        existing=existing,
    )
    write_account_config(args.provider, args.account, pending_record)

    provider_args = argparse.Namespace(
        profile_dir=outlook_profile_dir(args.provider, args.account),
        outlook_url=mailbox_url,
    )
    result = outlook.run_setup(provider_args)

    final_status = "ready" if result == 0 else "pending"
    final_record = build_account_record(
        provider=args.provider,
        account=args.account,
        label=args.label,
        mailbox_url=mailbox_url,
        status=final_status,
        existing=pending_record,
    )
    write_account_config(args.provider, args.account, final_record)
    print(f"Configured {args.provider}/{args.account}")
    print(json.dumps({"account": final_record, "paths": state_paths(args.provider, args.account)}, indent=2))
    return result


def handle_account_list(args: argparse.Namespace) -> int:
    records: list[dict[str, Any]] = []
    providers = [args.provider] if args.provider else list(SUPPORTED_PROVIDERS)
    for provider in providers:
        provider_dir = ACCOUNTS_DIR / provider
        if not provider_dir.exists():
            continue
        for config_path in sorted(provider_dir.glob("*/config.json")):
            record = json.loads(config_path.read_text(encoding="utf-8"))
            record["paths"] = state_paths(provider, record["account"])
            records.append(record)

    print(json.dumps(records, indent=2))
    return 0


def handle_account_inspect(args: argparse.Namespace) -> int:
    config = ensure_account_exists(args.provider, args.account)
    payload = {
        "account": config,
        "paths": state_paths(args.provider, args.account),
    }
    print(json.dumps(payload, indent=2))
    return 0


def handle_account_reauth(args: argparse.Namespace) -> int:
    return handle_account_setup(args)


def handle_unread_export(args: argparse.Namespace) -> int:
    config = ensure_account_exists(args.provider, args.account)

    if args.provider == "gmail":
        raise SystemExit("Gmail unread export is not implemented yet.")

    if args.provider != "outlook":
        raise SystemExit(f"Unsupported provider: {args.provider}")

    outlook = outlook_module()
    output_path = args.output or default_raw_export_path(args.provider, args.account)
    mailbox_url = provider_mailbox_url(args, config, default=outlook.DEFAULT_OUTLOOK_URL)
    provider_args = argparse.Namespace(
        profile_dir=outlook_profile_dir(args.provider, args.account),
        outlook_url=mailbox_url,
        output=Path(output_path),
        headless=args.headless,
    )
    return outlook.run_export(provider_args)


def handle_action_stub(args: argparse.Namespace) -> int:
    raise SystemExit(
        f"`surface action {args.action_command}` is not implemented yet. "
        "The CLI shape is reserved; provider action backends will be slotted in next."
    )


def handle_filter_apply(args: argparse.Namespace) -> int:
    raise SystemExit("`surface filter apply` is not implemented yet.")


def add_provider_account_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", choices=SUPPORTED_PROVIDERS, required=True)
    parser.add_argument("--account", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="surface",
        description="Surface mail automation CLI.",
    )
    subparsers = parser.add_subparsers(dest="command_group", required=True)

    account_parser = subparsers.add_parser("account", help="Manage configured provider accounts.")
    account_subparsers = account_parser.add_subparsers(dest="account_command", required=True)

    account_setup = account_subparsers.add_parser("setup", help="Run one-time setup for an account.")
    add_provider_account_args(account_setup)
    account_setup.add_argument("--label")
    account_setup.add_argument("--mailbox-url")
    account_setup.set_defaults(handler=handle_account_setup)

    account_list = account_subparsers.add_parser("list", help="List configured accounts.")
    account_list.add_argument("--provider", choices=SUPPORTED_PROVIDERS)
    account_list.set_defaults(handler=handle_account_list)

    account_inspect = account_subparsers.add_parser("inspect", help="Inspect one configured account.")
    add_provider_account_args(account_inspect)
    account_inspect.set_defaults(handler=handle_account_inspect)

    account_reauth = account_subparsers.add_parser("reauth", help="Re-run interactive auth/setup for an account.")
    add_provider_account_args(account_reauth)
    account_reauth.add_argument("--label")
    account_reauth.add_argument("--mailbox-url")
    account_reauth.set_defaults(handler=handle_account_reauth)

    unread_parser = subparsers.add_parser("unread", help="Export raw unread mail.")
    unread_subparsers = unread_parser.add_subparsers(dest="unread_command", required=True)

    unread_export = unread_subparsers.add_parser("export", help="Export unread mail for one account.")
    add_provider_account_args(unread_export)
    unread_export.add_argument("--output", type=Path)
    unread_export.add_argument("--mailbox-url")
    unread_export.add_argument("--headless", action="store_true")
    unread_export.set_defaults(handler=handle_unread_export)

    action_parser = subparsers.add_parser("action", help="Run provider-backed mail actions.")
    action_subparsers = action_parser.add_subparsers(dest="action_command", required=True)
    for command in ACTION_COMMANDS:
        action_command = action_subparsers.add_parser(command, help=f"Run the `{command}` action.")
        add_provider_account_args(action_command)
        action_command.add_argument("--message-id")
        action_command.add_argument("--conversation-id")
        action_command.add_argument("--internet-message-id")
        action_command.add_argument("--body")
        action_command.add_argument("--body-file", type=Path)
        action_command.add_argument("--subject")
        action_command.add_argument("--to", action="append", default=[])
        action_command.add_argument("--cc", action="append", default=[])
        action_command.add_argument("--bcc", action="append", default=[])
        if command == "rsvp":
            action_command.add_argument("--response", choices=("accept", "tentative", "decline"))
        action_command.set_defaults(handler=handle_action_stub)

    filter_parser = subparsers.add_parser("filter", help="Build frontend-facing filtered views.")
    filter_subparsers = filter_parser.add_subparsers(dest="filter_command", required=True)

    filter_apply = filter_subparsers.add_parser("apply", help="Apply blocking/classification rules to raw exports.")
    filter_apply.add_argument("--input", type=Path, required=True)
    filter_apply.add_argument("--output", type=Path, required=True)
    filter_apply.set_defaults(handler=handle_filter_apply)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)
