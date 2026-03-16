from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


load_env_file(ROOT_DIR / ".env")

DEFAULT_SURFACE_HOME = Path.home() / ".surface"
SURFACE_HOME = Path(os.environ.get("SURFACE_HOME", str(DEFAULT_SURFACE_HOME))).expanduser()
ACCOUNTS_DIR = SURFACE_HOME / "accounts"
EXPORTS_DIR = SURFACE_HOME / "exports"
RAW_EXPORTS_DIR = EXPORTS_DIR / "raw"
DERIVED_EXPORTS_DIR = EXPORTS_DIR / "derived"
PROVIDERS_DIR = SURFACE_HOME / "providers"

SUPPORTED_PROVIDERS = ("outlook", "gmail")
ACTION_COMMANDS = ("reply", "reply-all", "forward", "rsvp", "mark-read", "archive", "delete")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utc_filename_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def account_dir(provider: str, account: str) -> Path:
    return ACCOUNTS_DIR / provider / account


def account_config_path(provider: str, account: str) -> Path:
    return account_dir(provider, account) / "config.json"


def provider_state_dir(provider: str) -> Path:
    return PROVIDERS_DIR / provider


def default_raw_export_path(provider: str, account: str) -> Path:
    return RAW_EXPORTS_DIR / f"{provider}-{account}-unread.json"


def default_search_export_path(provider: str, account: str) -> Path:
    return RAW_EXPORTS_DIR / f"{provider}-{account}-search-{utc_filename_now()}.json"


def default_thread_summaries_path(raw_output_path: Path) -> Path:
    return DERIVED_EXPORTS_DIR / f"{raw_output_path.stem}-thread-summaries.json"


def outlook_profile_dir(provider: str, account: str) -> Path:
    return account_dir(provider, account) / "profile"


def gmail_token_path(provider: str, account: str) -> Path:
    return account_dir(provider, account) / "token.json"


def gmail_client_secret_path(provider: str) -> Path:
    return provider_state_dir(provider) / "client_secret.json"


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
        paths["client_secret_path"] = str(gmail_client_secret_path(provider).resolve())
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


def gmail_auth_module():
    from providers.gmail import auth as gmail_auth

    return gmail_auth


def gmail_unread_module():
    from providers.gmail import unread as gmail_unread

    return gmail_unread


def post_process_module():
    from surface_cli import post_process

    return post_process


def handle_account_setup(args: argparse.Namespace) -> int:
    confirm_setup_overwrite(args.provider, args.account)
    existing = load_account_config(args.provider, args.account)

    if args.provider == "gmail":
        gmail_auth = gmail_auth_module()

        pending_record = build_account_record(
            provider=args.provider,
            account=args.account,
            label=args.label,
            mailbox_url=args.mailbox_url or (existing or {}).get("mailbox_url"),
            status="pending",
            existing=existing,
        )
        write_account_config(args.provider, args.account, pending_record)

        provider_args = argparse.Namespace(
            token_path=gmail_token_path(args.provider, args.account),
            client_secret_path=gmail_client_secret_path(args.provider),
            source_client_secret_path=args.client_secret_file,
        )
        result = gmail_auth.run_setup(provider_args)

        final_record = build_account_record(
            provider=args.provider,
            account=args.account,
            label=args.label,
            mailbox_url=args.mailbox_url or (existing or {}).get("mailbox_url"),
            status="ready",
            existing=pending_record,
        )
        if result.email_address:
            final_record["email_address"] = result.email_address
        write_account_config(args.provider, args.account, final_record)

        print(f"Configured {args.provider}/{args.account}")
        print(json.dumps({"account": final_record, "paths": state_paths(args.provider, args.account)}, indent=2))
        return 0

    if args.provider != "outlook":
        raise SystemExit(f"Unsupported provider: {args.provider}")

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
    output_path = Path(args.output or default_raw_export_path(args.provider, args.account))

    if args.provider == "gmail":
        gmail_unread = gmail_unread_module()
        provider_args = argparse.Namespace(
            account=args.account,
            token_path=gmail_token_path(args.provider, args.account),
            output=output_path,
        )
        result = gmail_unread.run_export(provider_args)
        if result != 0:
            return result
        maybe_run_post_process_after_export(args, output_path)
        return 0

    if args.provider != "outlook":
        raise SystemExit(f"Unsupported provider: {args.provider}")

    outlook = outlook_module()
    mailbox_url = provider_mailbox_url(args, config, default=outlook.DEFAULT_OUTLOOK_URL)
    provider_args = argparse.Namespace(
        account=args.account,
        profile_dir=outlook_profile_dir(args.provider, args.account),
        outlook_url=mailbox_url,
        output=output_path,
        headless=args.headless,
    )
    result = outlook.run_export(provider_args)
    if result != 0:
        return result
    maybe_run_post_process_after_export(args, output_path)
    return 0


def handle_search_export(args: argparse.Namespace) -> int:
    config = ensure_account_exists(args.provider, args.account)

    if args.provider != "outlook":
        raise SystemExit("Search export is currently implemented only for Outlook.")

    outlook = outlook_module()
    output_path = Path(args.output or default_search_export_path(args.provider, args.account))
    mailbox_url = provider_mailbox_url(args, config, default=outlook.DEFAULT_OUTLOOK_URL)
    provider_args = argparse.Namespace(
        account=args.account,
        profile_dir=outlook_profile_dir(args.provider, args.account),
        outlook_url=mailbox_url,
        output=Path(output_path),
        query=args.query,
        max_results=args.max_results,
        thread_depth=args.thread_depth,
        headless=args.headless,
    )
    result = outlook.run_search_export(provider_args)
    if result != 0:
        return result
    maybe_run_post_process_after_export(args, output_path)
    return 0


def handle_action_stub(args: argparse.Namespace) -> int:
    raise SystemExit(
        f"`surface action {args.action_command}` is not implemented yet. "
        "The CLI shape is reserved; provider action backends will be slotted in next."
    )


def handle_filter_apply(args: argparse.Namespace) -> int:
    post_process = post_process_module()
    result = post_process.run_post_process(
        input_path=args.input,
        output_path=args.output,
        requested_backend=args.backend,
        requested_model=args.model,
        max_context_tokens=args.max_context_tokens,
        target_input_tokens=args.target_input_tokens,
        max_output_tokens=args.max_output_tokens,
        require_configured_backend=True,
    )
    print(
        f"Wrote {result.status} thread summaries to {result.output_path} "
        f"({result.summary_count} summaries across {result.chunk_count} chunks)"
    )
    return 0 if result.status == "complete" else 1


def add_post_process_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--skip-post-process", action="store_true")
    parser.add_argument("--post-process-output", type=Path)
    parser.add_argument("--post-process-backend", choices=("openrouter",))
    parser.add_argument("--post-process-model")


def maybe_run_post_process_after_export(args: argparse.Namespace, raw_output_path: Path) -> None:
    if getattr(args, "skip_post_process", False):
        return

    post_process = post_process_module()
    explicit_configuration = any(
        getattr(args, attribute_name, None)
        for attribute_name in ("post_process_output", "post_process_backend", "post_process_model")
    )
    output_path = Path(args.post_process_output or default_thread_summaries_path(raw_output_path))
    try:
        result = post_process.run_post_process(
            input_path=raw_output_path,
            output_path=output_path,
            requested_backend=args.post_process_backend,
            requested_model=args.post_process_model,
            require_configured_backend=explicit_configuration,
        )
    except RuntimeError as exc:
        print(f"Warning: post-processing skipped for {raw_output_path}: {exc}", file=sys.stderr)
        return

    if result.skipped:
        return

    print(
        f"Wrote {result.status} thread summaries to {result.output_path} "
        f"({result.summary_count} summaries across {result.chunk_count} chunks)"
    )
    if result.status != "complete":
        print(
            f"Warning: post-processing finished with status {result.status}. "
            f"See {result.output_path} for chunk-level details.",
            file=sys.stderr,
        )


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
    account_setup.add_argument(
        "--client-secret-file",
        type=Path,
        help=(
            "Optional Gmail OAuth desktop client credentials JSON used to bootstrap shared provider auth. "
            "Also read from SURFACE_GMAIL_CLIENT_SECRET_FILE."
        ),
    )
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
    account_reauth.add_argument(
        "--client-secret-file",
        type=Path,
        help=(
            "Optional Gmail OAuth desktop client credentials JSON used to bootstrap shared provider auth. "
            "Also read from SURFACE_GMAIL_CLIENT_SECRET_FILE."
        ),
    )
    account_reauth.set_defaults(handler=handle_account_reauth)

    unread_parser = subparsers.add_parser("unread", help="Export raw unread mail.")
    unread_subparsers = unread_parser.add_subparsers(dest="unread_command", required=True)

    unread_export = unread_subparsers.add_parser("export", help="Export unread mail for one account.")
    add_provider_account_args(unread_export)
    unread_export.add_argument("--output", type=Path)
    unread_export.add_argument("--mailbox-url")
    unread_export.add_argument("--headless", action="store_true")
    add_post_process_args(unread_export)
    unread_export.set_defaults(handler=handle_unread_export)

    search_parser = subparsers.add_parser("search", help="Export raw mail search results.")
    search_subparsers = search_parser.add_subparsers(dest="search_command", required=True)

    search_export = search_subparsers.add_parser("export", help="Export search results for one account.")
    add_provider_account_args(search_export)
    search_export.add_argument("--query", required=True)
    search_export.add_argument("--output", type=Path)
    search_export.add_argument("--max-results", type=positive_int)
    search_export.add_argument("--thread-depth", default="all")
    search_export.add_argument("--mailbox-url")
    search_export.add_argument("--headless", action="store_true")
    add_post_process_args(search_export)
    search_export.set_defaults(handler=handle_search_export)

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
    filter_apply.add_argument("--backend", choices=("openrouter",))
    filter_apply.add_argument("--model")
    filter_apply.add_argument("--max-context-tokens", type=positive_int, default=128000)
    filter_apply.add_argument("--target-input-tokens", type=positive_int, default=int(128000 * 0.85))
    filter_apply.add_argument("--max-output-tokens", type=positive_int, default=4096)
    filter_apply.set_defaults(handler=handle_filter_apply)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
