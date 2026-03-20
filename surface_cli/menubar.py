from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

RAW_UNREAD_CONTRACT = "surface.unread_mail.v1"
MENUBAR_VIEW_CONTRACT = "surface.filtered_menubar.v1"


@dataclass
class MenubarViewBuildResult:
    output_path: Path
    item_count: int
    mailbox_count: int


def default_sync_status_payload() -> dict[str, Any]:
    return {
        "state": "idle",
        "last_attempt_at": None,
        "last_success_at": None,
        "next_scheduled_at": None,
        "error": None,
        "account_error_count": 0,
        "accounts": [],
    }


def load_sync_status_payload(path: Path) -> dict[str, Any]:
    payload = default_sync_status_payload()
    if not path.exists():
        return payload

    loaded = json.loads(path.read_text(encoding="utf-8"))
    for key in payload:
        if key in loaded:
            payload[key] = loaded[key]
    return payload


def write_sync_status_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_menubar_view(
    *,
    raw_exports_dir: Path,
    accounts_dir: Path,
    output_path: Path,
    sync_status_path: Path,
) -> MenubarViewBuildResult:
    now = datetime.now(timezone.utc)
    sync_status = _view_sync_status(load_sync_status_payload(sync_status_path))

    mailboxes: list[dict[str, Any]] = []
    for raw_path in sorted(raw_exports_dir.glob("*-unread.json")):
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        if payload.get("contract") != RAW_UNREAD_CONTRACT:
            raise RuntimeError(
                f"Unsupported raw export contract in {raw_path}: {payload.get('contract')!r}"
            )

        provider = payload.get("provider")
        account = payload.get("account")
        if not isinstance(provider, str) or not isinstance(account, str):
            raise RuntimeError(f"Raw export {raw_path} is missing provider/account metadata.")

        items = _build_items(payload=payload, now=now)
        if not items:
            continue

        account_config = _load_account_config(accounts_dir, provider, account)
        email_address = account_config.get("email_address") or payload.get("mailbox_email")
        mailboxes.append(
            {
                "provider": provider,
                "account": account,
                "label": account_config.get("label") or account,
                "email_address": email_address,
                "unread_count": len(items),
                "items": items,
            }
        )

    mailboxes.sort(key=lambda mailbox: (mailbox["label"].lower(), mailbox["provider"], mailbox["account"]))
    item_count = sum(mailbox["unread_count"] for mailbox in mailboxes)

    payload = {
        "contract": MENUBAR_VIEW_CONTRACT,
        "generated_at": _utc_now(),
        "selection_mode": "unread",
        "item_count": item_count,
        "mailbox_count": len(mailboxes),
        "sync_status": sync_status,
        "mailboxes": mailboxes,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return MenubarViewBuildResult(
        output_path=output_path,
        item_count=item_count,
        mailbox_count=len(mailboxes),
    )


def _load_account_config(accounts_dir: Path, provider: str, account: str) -> dict[str, Any]:
    config_path = accounts_dir / provider / account / "config.json"
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def _build_items(*, payload: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    threads = payload.get("threads") or []
    thread_counts = {
        thread.get("conversation_id"): thread.get("message_count")
        for thread in threads
        if thread.get("conversation_id")
    }

    items: list[dict[str, Any]] = []
    for email in sorted(payload.get("emails") or [], key=lambda value: _email_sort_key(value), reverse=True):
        sender = email.get("from") or {}
        sender_name = sender.get("name")
        sender_email = sender.get("email")
        items.append(
            {
                "provider": payload["provider"],
                "account": payload["account"],
                "message_id": email.get("message_id"),
                "conversation_id": email.get("conversation_id"),
                "conversation_thread_id": email.get("conversation_thread_id"),
                "internet_message_id": email.get("internet_message_id"),
                "sender_primary": sender_name or sender_email or "Unknown sender",
                "sender_email": sender_email,
                "subject": email.get("subject") or "",
                "preview": email.get("preview") or "",
                "received_at": email.get("received_at"),
                "relative_time": _format_relative_time(email.get("received_at"), now=now),
                "thread_message_count": _thread_message_count(email=email, thread_counts=thread_counts),
                "can_rsvp": bool(email.get("can_rsvp")),
                "available_actions": list(email.get("available_actions") or []),
                "meeting": _project_meeting(email.get("meeting")),
            }
        )
    return items


def _email_sort_key(email: dict[str, Any]) -> tuple[datetime, str]:
    parsed = _parse_datetime(email.get("received_at")) or datetime.min.replace(tzinfo=timezone.utc)
    message_id = email.get("message_id") or ""
    return (parsed, message_id)


def _thread_message_count(*, email: dict[str, Any], thread_counts: dict[str, Any]) -> int:
    count = email.get("thread_message_count")
    if isinstance(count, int) and count >= 0:
        return count

    conversation_id = email.get("conversation_id")
    thread_count = thread_counts.get(conversation_id)
    if isinstance(thread_count, int) and thread_count >= 0:
        return thread_count
    return 1


def _project_meeting(meeting: Any) -> dict[str, Any] | None:
    if not isinstance(meeting, dict):
        return None

    organizer = meeting.get("organizer")
    if isinstance(organizer, dict):
        projected_organizer: dict[str, Any] | None = {
            "name": organizer.get("name"),
            "email": organizer.get("email"),
        }
    else:
        projected_organizer = None

    return {
        "request_type": meeting.get("request_type"),
        "response_type": meeting.get("response_type"),
        "organizer": projected_organizer,
        "location": meeting.get("location"),
        "start": meeting.get("start"),
        "end": meeting.get("end"),
        "available_rsvp_actions": list(meeting.get("available_rsvp_actions") or []),
    }


def _view_sync_status(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": payload.get("state", "idle"),
        "last_attempt_at": payload.get("last_attempt_at"),
        "last_success_at": payload.get("last_success_at"),
        "next_scheduled_at": payload.get("next_scheduled_at"),
        "error": payload.get("error"),
        "account_error_count": payload.get("account_error_count", 0),
    }


def _format_relative_time(value: str | None, *, now: datetime) -> str | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None

    delta = now - parsed
    if delta < timedelta(seconds=0):
        delta = timedelta(seconds=0)

    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return "now"
    if total_seconds < 3600:
        return f"{total_seconds // 60}m"
    if total_seconds < 86400:
        return f"{total_seconds // 3600}h"
    if total_seconds < 604800:
        return f"{total_seconds // 86400}d"
    if total_seconds < 2592000:
        return f"{total_seconds // 604800}w"
    if total_seconds < 31536000:
        return f"{total_seconds // 2592000}mo"
    return f"{total_seconds // 31536000}y"


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
