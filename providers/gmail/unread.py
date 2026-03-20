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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from providers.gmail import auth

CONTRACT_VERSION = "surface.unread_mail.v1"
PROVIDER_NAME = "gmail"
SOURCE_NAME = "gmail_api"
UNREAD_LABEL = "UNREAD"
CALENDAR_PART_MIME_TYPES = {"text/calendar", "application/ics"}
INFERRED_RSVP_ACTIONS = ["AcceptItem", "TentativelyAcceptItem", "DeclineItem"]
WINDOWS_TIMEZONE_ALIASES = {
    "UTC": "UTC",
    "GMT Standard Time": "Europe/London",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Romance Standard Time": "Europe/Paris",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
}


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


def decode_base64url_bytes(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def decode_bytes(raw_bytes: bytes, charset: str) -> str:
    try:
        return raw_bytes.decode(charset, errors="replace")
    except LookupError:
        return raw_bytes.decode("utf-8", errors="replace")


def decode_part_data(part: dict[str, Any]) -> str:
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""

    return decode_bytes(decode_base64url_bytes(data), part_charset(part))


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


def is_calendar_part(part: dict[str, Any]) -> bool:
    mime_type = (part.get("mimeType") or "").lower()
    filename = (part.get("filename") or "").lower()
    return mime_type in CALENDAR_PART_MIME_TYPES or filename.endswith(".ics")


def fetch_attachment_text(service: Any, message_id: str, attachment_id: str, charset: str) -> str:
    attachment = execute(
        service.users().messages().attachments().get(
            userId="me",
            messageId=message_id,
            id=attachment_id,
        ),
        description=f"fetching Gmail attachment {attachment_id} for message {message_id}",
    )
    data = attachment.get("data")
    if not data:
        return ""
    return decode_bytes(decode_base64url_bytes(data), charset)


def extract_calendar_text(service: Any, message: dict[str, Any]) -> str:
    message_id = message.get("id")
    if not message_id:
        return ""

    for part in iter_parts(message.get("payload")):
        if not is_calendar_part(part):
            continue
        body = part.get("body") or {}
        data = body.get("data")
        if data:
            return decode_bytes(decode_base64url_bytes(data), part_charset(part))
        attachment_id = body.get("attachmentId")
        if attachment_id:
            text = fetch_attachment_text(service, message_id, attachment_id, part_charset(part))
            if text:
                return text
    return ""


def unfold_ics_lines(value: str) -> list[str]:
    lines: list[str] = []
    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.startswith((" ", "\t")) and lines:
            lines[-1] += raw_line[1:]
        else:
            lines.append(raw_line)
    return [line for line in lines if line]


def parse_ics_content_line(line: str) -> tuple[str, dict[str, str], str] | None:
    if ":" not in line:
        return None

    left, value = line.split(":", 1)
    segments = left.split(";")
    name = segments[0].upper()
    params: dict[str, str] = {}
    for segment in segments[1:]:
        if "=" in segment:
            key, param_value = segment.split("=", 1)
            params[key.upper()] = param_value.strip('"')
        else:
            params[segment.upper()] = ""
    return name, params, value


def unescape_ics_value(value: str | None) -> str | None:
    if value is None:
        return None
    return (
        value.replace("\\N", "\n")
        .replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def resolve_ics_timezone(tzid: str | None) -> str | None:
    if not tzid:
        return None
    return WINDOWS_TIMEZONE_ALIASES.get(tzid, tzid)


def parse_ics_datetime(value: str | None, params: dict[str, str]) -> str | None:
    if not value:
        return None

    raw_value = value.strip()
    if re.fullmatch(r"\d{8}", raw_value):
        try:
            return datetime.strptime(raw_value, "%Y%m%d").date().isoformat()
        except ValueError:
            return raw_value

    if raw_value.endswith("Z"):
        try:
            return normalize_datetime(datetime.strptime(raw_value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc))
        except ValueError:
            return raw_value

    format_string = "%Y%m%dT%H%M%S" if len(raw_value) == 15 else "%Y%m%dT%H%M"
    try:
        parsed = datetime.strptime(raw_value, format_string)
    except ValueError:
        return raw_value

    timezone_name = resolve_ics_timezone(params.get("TZID"))
    if timezone_name:
        try:
            return normalize_datetime(parsed.replace(tzinfo=ZoneInfo(timezone_name)))
        except ZoneInfoNotFoundError:
            return raw_value
    return parsed.isoformat()


def ics_mailbox(value: str | None, params: dict[str, str]) -> dict[str, str] | None:
    if not value and not params.get("CN"):
        return None
    email = (value or "").strip()
    if email.lower().startswith("mailto:"):
        email = email[7:]
    name = unescape_ics_value(params.get("CN")) or ""
    if not name and not email:
        return None
    return {"name": name, "email": email}


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def parse_calendar_invite(
    ics_text: str,
    *,
    mailbox_email: str | None,
    recipient_emails: list[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    method: str | None = None
    event_properties: dict[str, tuple[dict[str, str], str]] = {}
    attendees: list[tuple[dict[str, str], str]] = []
    in_event = False

    for line in unfold_ics_lines(ics_text):
        parsed = parse_ics_content_line(line)
        if parsed is None:
            continue
        name, params, value = parsed
        upper_value = value.upper()

        if name == "METHOD" and method is None:
            method = upper_value
            continue

        if name == "BEGIN" and upper_value == "VEVENT":
            in_event = True
            continue

        if name == "END" and upper_value == "VEVENT":
            break

        if not in_event:
            continue

        if name == "ATTENDEE":
            attendees.append((params, value))
        elif name not in event_properties:
            event_properties[name] = (params, value)

    if not event_properties and not attendees:
        return None, []

    organizer = ics_mailbox(*event_properties.get("ORGANIZER", ({}, ""))[::-1]) if "ORGANIZER" in event_properties else None
    attendee_targets = {normalize_email(mailbox_email), *(normalize_email(email) for email in recipient_emails)}
    attendee_targets.discard("")

    selected_attendee: dict[str, Any] | None = None
    for params, value in attendees:
        mailbox = ics_mailbox(value, params)
        email = normalize_email((mailbox or {}).get("email"))
        if email and email in attendee_targets:
            selected_attendee = {
                "mailbox": mailbox,
                "partstat": (params.get("PARTSTAT") or "").upper() or None,
                "rsvp": (params.get("RSVP") or "").upper() == "TRUE",
                "role": (params.get("ROLE") or "").upper() or None,
            }
            break

    event_status = (event_properties.get("STATUS", ({}, ""))[1] or "").upper() or None
    request_type = method or None
    available_rsvp_actions = (
        list(INFERRED_RSVP_ACTIONS)
        if request_type == "REQUEST" and event_status != "CANCELLED" and selected_attendee is not None
        else []
    )

    start_params, start_value = event_properties.get("DTSTART", ({}, ""))
    end_params, end_value = event_properties.get("DTEND", ({}, ""))
    timezone_id = start_params.get("TZID") or end_params.get("TZID")
    meeting = {
        "request_type": request_type,
        "response_type": selected_attendee.get("partstat") if selected_attendee else None,
        "organizer": organizer,
        "location": unescape_ics_value(event_properties.get("LOCATION", ({}, None))[1]),
        "start": parse_ics_datetime(start_value, start_params),
        "end": parse_ics_datetime(end_value, end_params),
        "uid": event_properties.get("UID", ({}, None))[1],
        "status": event_status,
        "timezone": timezone_id,
        "available_rsvp_actions": available_rsvp_actions,
    }
    if selected_attendee and selected_attendee.get("mailbox"):
        meeting["attendee"] = selected_attendee["mailbox"]
    if selected_attendee and selected_attendee.get("role"):
        meeting["attendee_role"] = selected_attendee["role"]

    return {key: value for key, value in meeting.items() if value is not None}, available_rsvp_actions


def extract_meeting_data(service: Any, message: dict[str, Any], *, mailbox_email: str | None) -> tuple[dict[str, Any] | None, list[str]]:
    ics_text = extract_calendar_text(service, message)
    if not ics_text:
        return None, []

    payload = message.get("payload", {})
    headers = header_index(payload.get("headers"))
    recipient_emails = [
        mailbox.get("email", "")
        for mailbox in (
            parse_mailboxes(headers.get("to"))
            + parse_mailboxes(headers.get("cc"))
            + parse_mailboxes(headers.get("bcc"))
        )
    ]
    return parse_calendar_invite(ics_text, mailbox_email=mailbox_email, recipient_emails=recipient_emails)


def build_message_record(service: Any, message: dict[str, Any], *, is_root_node: bool, mailbox_email: str | None) -> dict[str, Any]:
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
    meeting, available_rsvp_actions = extract_meeting_data(service, message, mailbox_email=mailbox_email)

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
        "available_actions": available_rsvp_actions,
        "can_rsvp": bool(available_rsvp_actions),
        "meeting": meeting,
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
            record = build_message_record(service, message, is_root_node=index == 0, mailbox_email=profile_email)
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
