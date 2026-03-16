from __future__ import annotations

import argparse
import base64
import json
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator

from providers.gmail import auth

CONTRACT_VERSION = "surface.unread_mail.v1"
PROVIDER_NAME = "gmail"
SOURCE_NAME = "gmail_api"
UNREAD_LABEL = "UNREAD"


class HTMLTextExtractor(HTMLParser):
    BLOCK_TAGS = {"br", "div", "p", "li", "tr", "hr"}
    IGNORED_TAGS = {"head", "script", "style"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.IGNORED_TAGS:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.ignored_depth:
            return
        self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.IGNORED_TAGS:
            self.ignored_depth = max(0, self.ignored_depth - 1)
            return
        if self.ignored_depth:
            return
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def get_text(self) -> str:
        text = unescape("".join(self.parts))
        text = re.sub(r"\r", "", text)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        return text.strip()


def html_to_text(value: str) -> str:
    if not value:
        return ""
    parser = HTMLTextExtractor()
    parser.feed(value)
    parser.close()
    return parser.get_text()


def decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def header_index(headers: list[dict[str, str]] | None) -> dict[str, str]:
    indexed: dict[str, str] = {}
    for header in headers or []:
        name = (header.get("name") or "").lower()
        value = header.get("value") or ""
        if name and name not in indexed:
            indexed[name] = value
    return indexed


def parse_mailbox(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    decoded = decode_header_value(value)
    mailboxes = getaddresses([decoded])
    if not mailboxes:
        return None
    name, email = mailboxes[0]
    if not name and not email:
        return None
    return {"name": name or "", "email": email or ""}


def parse_mailboxes(value: str | None) -> list[dict[str, str]]:
    if not value:
        return []
    decoded = decode_header_value(value)
    result: list[dict[str, str]] = []
    for name, email in getaddresses([decoded]):
        if not name and not email:
            continue
        result.append({"name": name or "", "email": email or ""})
    return result


def normalize_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def internal_date_to_iso(internal_date_ms: str | None) -> str | None:
    if not internal_date_ms:
        return None
    try:
        return normalize_datetime(datetime.fromtimestamp(int(internal_date_ms) / 1000, tz=timezone.utc))
    except (TypeError, ValueError, OSError):
        return None


def header_date_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return normalize_datetime(parsed)


def iter_parts(payload: dict[str, Any] | None) -> Iterator[dict[str, Any]]:
    if not payload:
        return
    yield payload
    for child in payload.get("parts", []) or []:
        yield from iter_parts(child)


def part_charset(part: dict[str, Any]) -> str:
    content_type = header_index(part.get("headers")).get("content-type", "")
    match = re.search(r"charset=\"?([^\";]+)\"?", content_type, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return "utf-8"


def decode_part_data(part: dict[str, Any]) -> str:
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""

    padding = "=" * (-len(data) % 4)
    raw_bytes = base64.urlsafe_b64decode((data + padding).encode("ascii"))
    charset = part_charset(part)
    try:
        return raw_bytes.decode(charset, errors="replace")
    except LookupError:
        return raw_bytes.decode("utf-8", errors="replace")


def extract_message_bodies(payload: dict[str, Any] | None) -> tuple[str, str]:
    plain_body = ""
    html_body = ""
    for part in iter_parts(payload):
        mime_type = (part.get("mimeType") or "").lower()
        if mime_type not in {"text/plain", "text/html"}:
            continue
        if part.get("filename"):
            continue

        decoded = decode_part_data(part).strip()
        if not decoded:
            continue

        if mime_type == "text/plain" and not plain_body:
            plain_body = decoded
        if mime_type == "text/html" and not html_body:
            html_body = decoded

        if plain_body and html_body:
            break

    return plain_body, html_body


def build_message_record(message: dict[str, Any], *, is_root_node: bool) -> dict[str, Any]:
    payload = message.get("payload", {})
    headers = header_index(payload.get("headers"))
    plain_body, html_body = extract_message_bodies(payload)
    preview = message.get("snippet", "")
    received_at = internal_date_to_iso(message.get("internalDate"))
    sent_at = header_date_to_iso(headers.get("date")) or received_at

    body = plain_body or html_to_text(html_body) or preview
    subject = decode_header_value(headers.get("subject"))
    message_id = message.get("id")
    thread_id = message.get("threadId")
    is_read = UNREAD_LABEL not in set(message.get("labelIds", []))

    return {
        "message_id": message_id,
        "message_change_key": message.get("historyId"),
        "internet_message_id": decode_header_value(headers.get("message-id")) or None,
        "parent_internet_message_id": decode_header_value(headers.get("in-reply-to")) or None,
        "conversation_id": thread_id,
        "conversation_thread_id": thread_id,
        "instance_key": message_id,
        "item_class": payload.get("mimeType") or "gmail.message",
        "is_read": is_read,
        "received_at": received_at,
        "sent_at": sent_at,
        "from": parse_mailbox(headers.get("from")),
        "to": parse_mailboxes(headers.get("to")),
        "cc": parse_mailboxes(headers.get("cc")),
        "bcc": parse_mailboxes(headers.get("bcc")),
        "subject": subject,
        "body": body,
        "body_html": html_body,
        "body_scope": "full_message",
        "preview": preview,
        "available_actions": [],
        "can_rsvp": False,
        "meeting": None,
        "has_quoted_text": None,
        "is_root_node": is_root_node,
    }


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def execute(request: Any, *, description: str) -> dict[str, Any]:
    try:
        return request.execute()
    except Exception as exc:
        raise RuntimeError(f"Gmail API request failed while {description}: {exc}") from exc


def list_unread_message_refs(service: Any) -> list[dict[str, Any]]:
    unread_messages: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        response = execute(
            service.users().messages().list(
                userId="me",
                labelIds=[UNREAD_LABEL],
                includeSpamTrash=False,
                maxResults=500,
                pageToken=page_token,
            ),
            description="listing unread Gmail messages",
        )
        unread_messages.extend(response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return unread_messages


def fetch_thread(service: Any, thread_id: str) -> dict[str, Any]:
    return execute(
        service.users().threads().get(
            userId="me",
            id=thread_id,
            format="full",
        ),
        description=f"fetching Gmail thread {thread_id}",
    )


def run_export(args: argparse.Namespace) -> int:
    token_path = args.token_path.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    service = auth.load_gmail_service(token_path)
    profile_email = auth.fetch_profile_email(service)

    unread_refs = list_unread_message_refs(service)
    unread_order = {
        message.get("id"): index
        for index, message in enumerate(unread_refs)
        if message.get("id")
    }
    thread_ids = ordered_unique([message.get("threadId", "") for message in unread_refs])

    emails: list[dict[str, Any]] = []
    threads: list[dict[str, Any]] = []

    for thread_id in thread_ids:
        thread = fetch_thread(service, thread_id)
        thread_messages: list[dict[str, Any]] = []
        unread_thread_messages: list[tuple[str, dict[str, Any]]] = []

        for index, message in enumerate(thread.get("messages", [])):
            record = build_message_record(message, is_root_node=index == 0)
            thread_messages.append(record)

            message_id = message.get("id")
            if message_id in unread_order and record.get("is_read") is False:
                unread_thread_messages.append((message_id, dict(record)))

        threads.append(
            {
                "conversation_id": thread_id,
                "message_count": len(thread_messages),
                "messages": thread_messages,
            }
        )

        thread_message_index = {
            message.get("message_id"): index
            for index, message in enumerate(thread_messages)
            if message.get("message_id")
        }
        for message_id, record in unread_thread_messages:
            record["thread_message_index"] = thread_message_index.get(message_id)
            record["thread_message_count"] = len(thread_messages)
            emails.append(record)

    emails.sort(key=lambda message: unread_order.get(message.get("message_id"), len(unread_order)))

    payload: dict[str, Any] = {
        "contract": CONTRACT_VERSION,
        "provider": PROVIDER_NAME,
        "account": args.account,
        "source": SOURCE_NAME,
        "email_count": len(emails),
        "thread_count": len(threads),
        "emails": emails,
        "threads": threads,
    }
    if profile_email:
        payload["mailbox_email"] = profile_email

    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(emails)} unread emails to {output_path}")
    return 0
