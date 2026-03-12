#!/usr/bin/env python3
"""Export unread Outlook Web mail into the shared Surface contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

try:
    from playwright.sync_api import BrowserContext, Error, Page, Playwright, sync_playwright
except ModuleNotFoundError:
    BrowserContext = Page = Playwright = object
    Error = Exception
    sync_playwright = None

APP_DIR = Path(__file__).resolve().parent
CONTRACT_VERSION = "surface.unread_mail.v1"
PROVIDER_NAME = "outlook"
DEFAULT_OUTLOOK_URL = "https://outlook.office.com/mail/"
DEFAULT_PROFILE_DIR = APP_DIR / ".profiles" / "outlook"
DEFAULT_PROFILE_DIR_LABEL = "providers/outlook/.profiles/outlook"
OWA_SERVICE_URL = "https://outlook.office.com/owa/service.svc"


class HTMLTextExtractor(HTMLParser):
    """Collapse simple HTML into readable text for JSON export."""

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

    def handle_comment(self, data: str) -> None:
        return

    def get_text(self) -> str:
        text = unescape("".join(self.parts))
        text = re.sub(r"\r", "", text)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        return text.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export unread Outlook Web mail into the shared Surface unread-mail contract."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser(
        "setup",
        help="Open Chrome with a dedicated persistent profile and wait for manual Outlook login.",
    )
    add_common_browser_args(setup_parser)

    export_parser = subparsers.add_parser(
        "export",
        help="Export unread Outlook Web messages into the shared unread-mail JSON contract.",
    )
    add_common_browser_args(export_parser)
    export_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to the JSON output file.",
    )
    export_parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without opening a browser window. Keep this off until login bootstrap is finished.",
    )

    return parser


def add_common_browser_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
        help=f"Persistent Chrome profile directory. Default: {DEFAULT_PROFILE_DIR_LABEL}",
    )
    parser.add_argument(
        "--outlook-url",
        default=DEFAULT_OUTLOOK_URL,
        help=f"Outlook Web entry URL. Default: {DEFAULT_OUTLOOK_URL}",
    )


def html_to_text(value: str) -> str:
    if not value:
        return ""

    parser = HTMLTextExtractor()
    parser.feed(value)
    parser.close()
    return parser.get_text()


def capture_owa_headers(page: Page, outlook_url: str) -> dict[str, str]:
    headers: dict[str, str] = {}

    def on_request(request: Any) -> None:
        if headers:
            return
        if request.resource_type not in {"fetch", "xhr"}:
            return
        if "/owa/service.svc" not in request.url:
            return

        source = request.headers
        for key in (
            "authorization",
            "x-anchormailbox",
            "x-owa-hosted-ux",
            "x-owa-sessionid",
            "x-req-source",
            "prefer",
            "user-agent",
        ):
            value = source.get(key)
            if value:
                headers[key] = value

    page.on("request", on_request)
    page.goto(outlook_url, wait_until="domcontentloaded")
    page.locator('[role="listbox"]').first.wait_for(timeout=30_000)
    page.wait_for_timeout(3_000)

    if not headers:
        raise RuntimeError("Could not capture Outlook service request headers from the browser session.")

    return headers


def apply_unread_filter(page: Page) -> None:
    if page.get_by_role("button", name="Unread").count():
        return

    filter_button = page.get_by_role("button", name="Filter").first
    filter_button.wait_for(timeout=20_000)
    filter_button.click()
    page.wait_for_timeout(800)
    page.locator("text=/^Unread$/").first.click()
    page.wait_for_timeout(2_000)


def collect_visible_rows(page: Page) -> list[dict[str, str]]:
    rows = page.locator('[role="option"]')
    return rows.evaluate_all(
        """
        elements => elements.map(element => ({
            instance_key: element.id || '',
            conversation_id: element.getAttribute('data-convid') || '',
            aria_label: element.getAttribute('aria-label') || '',
            text: (element.innerText || '').trim(),
        }))
        """
    )


def scroll_message_list(page: Page) -> dict[str, int]:
    listbox = page.locator('[role="listbox"]').first
    return listbox.evaluate(
        """
        element => {
            const candidates = [element, ...element.querySelectorAll('*')];
            const target = candidates.find(node => node.scrollHeight > node.clientHeight + 5) || element;
            const before = target.scrollTop;
            const delta = Math.max(Math.floor(target.clientHeight * 0.85), 600);
            target.scrollTop = Math.min(target.scrollTop + delta, target.scrollHeight);
            return {
                before,
                after: target.scrollTop,
                clientHeight: target.clientHeight,
                scrollHeight: target.scrollHeight,
            };
        }
        """
    )


def collect_unread_conversations(page: Page) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    stagnant_rounds = 0

    while stagnant_rounds < 3:
        grew = False
        for row in collect_visible_rows(page):
            conversation_id = row.get("conversation_id", "")
            if not conversation_id:
                continue

            is_unread = row.get("aria_label", "").startswith("Unread")
            if is_unread and conversation_id not in seen:
                seen[conversation_id] = row
                grew = True

        scroll_state = scroll_message_list(page)
        page.wait_for_timeout(800)

        if grew:
            stagnant_rounds = 0
            continue

        stagnant_rounds += 1

    return list(seen.values())


def build_owa_headers(base_headers: dict[str, str], action: str) -> dict[str, str]:
    headers = dict(base_headers)
    headers["action"] = action
    headers["content-type"] = "application/json; charset=utf-8"
    return headers


def build_conversation_payload(conversation_id: str) -> dict[str, Any]:
    return {
        "__type": "GetConversationItemsJsonRequest:#Exchange",
        "Header": {
            "__type": "JsonRequestHeaders:#Exchange",
            "RequestServerVersion": "V2017_08_18",
            "TimeZoneContext": {
                "__type": "TimeZoneContext:#Exchange",
                "TimeZoneDefinition": {
                    "__type": "TimeZoneDefinitionType:#Exchange",
                    "Id": "GMT Standard Time",
                },
            },
        },
        "Body": {
            "__type": "GetConversationItemsRequest:#Exchange",
            "Conversations": [
                {
                    "__type": "ConversationRequestType:#Exchange",
                    "ConversationId": {"__type": "ItemId:#Exchange", "Id": conversation_id},
                    "SyncState": "",
                }
            ],
            "ItemShape": {
                "__type": "ItemResponseShape:#Exchange",
                "BaseShape": "IdOnly",
                "AddBlankTargetToLinks": True,
                "BlockContentFromUnknownSenders": False,
                "BlockExternalImagesIfSenderUntrusted": True,
                "ClientSupportsIrm": True,
                "CssScopeClassName": "rps_export",
                "FilterHtmlContent": True,
                "FilterInlineSafetyTips": True,
                "InlineImageCustomDataTemplate": "{id}",
                "InlineImageUrlTemplate": (
                    "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAEALAAAAAABAAEAAAIBTAA7"
                ),
                "MaximumBodySize": 2_097_152,
                "MaximumRecipientsToReturn": 100,
                "ImageProxyCapability": "OwaAndConnectorsProxy",
                "AdditionalProperties": [
                    {"__type": "PropertyUri:#Exchange", "FieldURI": "CanDelete"},
                ],
                "InlineImageUrlOnLoadTemplate": "",
                "ExcludeBindForInlineAttachments": True,
                "CalculateOnlyFirstBody": True,
                "BodyShape": "UniqueFragment",
            },
            "ShapeName": "ItemPart",
            "SortOrder": "DateOrderDescending",
            "MaxItemsToReturn": 100,
            "Action": "ReturnRootNode",
            "FoldersToIgnore": [],
            "ReturnSubmittedItems": True,
            "ReturnDeletedItems": True,
        },
    }


def fetch_conversation_items(
    context: BrowserContext,
    headers: dict[str, str],
    conversation_id: str,
) -> list[dict[str, Any]]:
    payload = build_conversation_payload(conversation_id)
    response = context.request.post(
        f"{OWA_SERVICE_URL}?action=GetConversationItems&app=Mail&n=999",
        headers=build_owa_headers(headers, "GetConversationItems"),
        data=json.dumps(payload),
    )
    if not response.ok:
        raise RuntimeError(
            f"GetConversationItems failed with status {response.status} for conversation {conversation_id}."
        )
    data = response.json()

    items: list[dict[str, Any]] = []
    response_messages = data.get("Body", {}).get("ResponseMessages", {}).get("Items", [])
    if not response_messages:
        return items

    conversation = response_messages[0].get("Conversation", {})
    for node in conversation.get("ConversationNodes", []):
        items.extend(node.get("Items", []))
    return items


def mailbox_from_exchange(value: dict[str, Any] | None) -> dict[str, str] | None:
    mailbox = (value or {}).get("Mailbox", {})
    email = mailbox.get("EmailAddress")
    name = mailbox.get("Name")
    if not email and not name:
        return None
    return {"name": name or "", "email": email or ""}


def mailboxes_from_exchange(values: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for value in values or []:
        mailbox = value.get("Mailbox", value)
        email = mailbox.get("EmailAddress")
        name = mailbox.get("Name")
        if not email and not name:
            continue
        result.append({"name": name or "", "email": email or ""})
    return result


def normalize_response_objects(values: list[dict[str, Any]] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        raw_type = value.get("__type", "")
        normalized = raw_type.split(":", 1)[0]
        if normalized:
            result.append(normalized)
    return result


def item_id_data(value: dict[str, Any] | None) -> dict[str, str] | None:
    if not value:
        return None
    item_id = value.get("Id")
    change_key = value.get("ChangeKey")
    if not item_id and not change_key:
        return None
    return {"id": item_id or "", "change_key": change_key or ""}


def export_record(
    item: dict[str, Any],
    conversation_id: str,
    row: dict[str, str],
) -> dict[str, Any]:
    body_html = (
        item.get("UniqueBody", {}).get("Value")
        or item.get("Body", {}).get("Value")
        or ""
    )
    body_text = html_to_text(body_html) if body_html else item.get("Preview", "")
    response_objects = normalize_response_objects(item.get("ResponseObjects"))
    is_meeting_request = item.get("ItemClass") == "IPM.Schedule.Meeting.Request"
    meeting = None
    if is_meeting_request:
        meeting = {
            "request_type": item.get("MeetingRequestType"),
            "response_type": item.get("ResponseType"),
            "organizer": mailbox_from_exchange(item.get("Organizer")) or mailbox_from_exchange(item.get("Sender")),
            "location": (item.get("Location") or {}).get("DisplayName"),
            "start": item.get("Start"),
            "end": item.get("End"),
            "associated_calendar_item": item_id_data(item.get("AssociatedCalendarItemId")),
            "available_rsvp_actions": [
                action
                for action in response_objects
                if action in {"AcceptItem", "TentativelyAcceptItem", "DeclineItem", "ProposeNewTime"}
            ],
        }

    return {
        "message_id": item.get("ItemId", {}).get("Id"),
        "message_change_key": item.get("ItemId", {}).get("ChangeKey"),
        "conversation_id": conversation_id,
        "instance_key": item.get("InstanceKey") or row.get("instance_key"),
        "item_class": item.get("ItemClass"),
        "is_read": item.get("IsRead"),
        "received_at": item.get("DateTimeReceived") or item.get("ReceivedOrRenewTime"),
        "sent_at": item.get("DateTimeSent"),
        "from": mailbox_from_exchange(item.get("From")) or mailbox_from_exchange(item.get("Sender")),
        "to": mailboxes_from_exchange(item.get("ToRecipients")),
        "cc": mailboxes_from_exchange(item.get("CcRecipients")),
        "bcc": mailboxes_from_exchange(item.get("BccRecipients")),
        "subject": item.get("Subject", ""),
        "body": body_text,
        "body_html": body_html,
        "preview": item.get("Preview", ""),
        "available_actions": response_objects,
        "can_rsvp": is_meeting_request and bool(meeting and meeting["available_rsvp_actions"]),
        "meeting": meeting,
        "row_aria_label": row.get("aria_label", ""),
    }


def launch_context(profile_dir: Path, *, headless: bool) -> tuple[Playwright, BrowserContext, Page]:
    if sync_playwright is None:
        raise RuntimeError(
            "Playwright is not installed. Create the Conda environment with "
            "`conda env create -f environment.yml`, activate it, and then run "
            "`python -m playwright install chrome`."
        )

    profile_dir.mkdir(parents=True, exist_ok=True)

    playwright = sync_playwright().start()
    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir.resolve()),
            channel="chrome",
            headless=headless,
            viewport={"width": 1440, "height": 960},
        )
    except Exception:
        playwright.stop()
        raise

    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(15_000)
    return playwright, context, page


def run_setup(args: argparse.Namespace) -> int:
    profile_dir = args.profile_dir.expanduser()
    playwright, context, page = launch_context(profile_dir, headless=False)
    try:
        page.goto(args.outlook_url, wait_until="domcontentloaded")
        print()
        print("One-time Outlook profile bootstrap")
        print(f"Profile directory: {profile_dir.resolve()}")
        print("1. Sign in to Outlook in the opened Chrome window.")
        print("2. Complete MFA if prompted.")
        print("3. Wait until the inbox is fully loaded.")
        print("4. Optional but recommended: disable conversation view for easier scraping.")
        input("Press Enter here once Outlook is ready and logged in...")
        print("Profile bootstrap complete.")
        return 0
    finally:
        context.close()
        playwright.stop()


def run_export(args: argparse.Namespace) -> int:
    profile_dir = args.profile_dir.expanduser()
    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    playwright, context, page = launch_context(profile_dir, headless=args.headless)
    try:
        owa_headers = capture_owa_headers(page, args.outlook_url)
        apply_unread_filter(page)
        unread_rows = collect_unread_conversations(page)

        emails: list[dict[str, Any]] = []
        seen_message_ids: set[str] = set()
        for row in unread_rows:
            conversation_id = row.get("conversation_id", "")
            if not conversation_id:
                continue

            for item in fetch_conversation_items(context, owa_headers, conversation_id):
                if item.get("IsRead") is True:
                    continue
                message_id = item.get("ItemId", {}).get("Id")
                if message_id and message_id in seen_message_ids:
                    continue
                if message_id:
                    seen_message_ids.add(message_id)
                emails.append(export_record(item, conversation_id, row))

        payload = {
            "contract": CONTRACT_VERSION,
            "provider": PROVIDER_NAME,
            "source": "outlook_web",
            "mailbox_url": args.outlook_url,
            "profile_dir": str(profile_dir.resolve()),
            "email_count": len(emails),
            "emails": emails,
        }
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote {len(emails)} unread emails to {output_path}")
        return 0
    finally:
        context.close()
        playwright.stop()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "setup":
            return run_setup(args)
        if args.command == "export":
            return run_export(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Error as exc:
        print(f"Playwright error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
