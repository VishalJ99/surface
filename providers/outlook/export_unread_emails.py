#!/usr/bin/env python3
"""Export Outlook Web mail into the shared Surface contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from time import monotonic
from typing import Any, Callable
from urllib.parse import urlparse

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
OWA_REQUIRED_HEADER_KEYS = (
    "authorization",
    "x-anchormailbox",
    "x-owa-hosted-ux",
    "x-owa-sessionid",
    "x-req-source",
    "prefer",
    "user-agent",
)


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
        description="Export Outlook Web mail into the shared Surface JSON contract."
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
        "--account",
        required=True,
        help="Logical account slug for the export artifact, for example `work` or `personal`.",
    )
    export_parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without opening a browser window. Keep this off until login bootstrap is finished.",
    )

    search_export_parser = subparsers.add_parser(
        "search-export",
        help="Export Outlook Web search results into the shared JSON contract shape.",
    )
    add_common_browser_args(search_export_parser)
    search_export_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to the JSON output file.",
    )
    search_export_parser.add_argument(
        "--account",
        required=True,
        help="Logical account slug for the export artifact, for example `work` or `personal`.",
    )
    search_export_parser.add_argument(
        "--query",
        required=True,
        help="Search term to enter into the Outlook search box.",
    )
    search_export_parser.add_argument(
        "--max-results",
        type=int,
        help="Maximum number of top-level search results to export.",
    )
    search_export_parser.add_argument(
        "--thread-depth",
        default="all",
        help="How many messages per returned thread to include. Use `all` or a positive integer.",
    )
    search_export_parser.add_argument(
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


def wait_for_message_list(page: Page, *, timeout: float = 30_000) -> None:
    page.locator('[role="listbox"]').first.wait_for(timeout=timeout)


def wait_for_mailbox_ready(page: Page, *, timeout: float = 30_000) -> None:
    deadline = monotonic() + (timeout / 1000)
    last_error: Error | None = None
    while monotonic() < deadline:
        if maybe_advance_account_picker(page):
            page.wait_for_timeout(1_500)
            continue

        remaining_ms = max(500, int((deadline - monotonic()) * 1000))
        try:
            wait_for_message_list(page, timeout=min(2_000, remaining_ms))
            return
        except Error as exc:
            last_error = exc
            page.wait_for_timeout(750)

    if last_error is not None:
        raise last_error
    wait_for_message_list(page, timeout=timeout)


def maybe_advance_account_picker(page: Page) -> bool:
    try:
        body_text = page.locator("body").inner_text(timeout=2_000)
    except Error:
        return False

    if "Pick an account" not in body_text:
        return False

    # Microsoft's picker usually shows one remembered account plus "Use another account".
    # Prefer clicking the remembered account tile and skip the "use another account" affordance.
    for selector in (
        '[data-bind="text: session.tileDisplayName"]',
        '[data-bind="text: session.signInName"]',
    ):
        locator = page.locator(selector)
        if locator.count() == 1:
            locator.first.click(no_wait_after=True)
            return True

    candidates = page.locator("div, button")
    for index in range(min(candidates.count(), 30)):
        candidate = candidates.nth(index)
        try:
            text = candidate.inner_text(timeout=500).strip()
        except Error:
            continue
        if not text:
            continue
        lowered = text.lower()
        if "use another account" in lowered or "terms of use" in lowered or "privacy" in lowered:
            continue
        if "\n" not in text and "@" not in text:
            continue
        candidate.click(no_wait_after=True)
        return True

    return False


def has_complete_owa_headers(headers: dict[str, str]) -> bool:
    return all(headers.get(key) for key in OWA_REQUIRED_HEADER_KEYS)


def infer_service_url_from_mailbox_url(mailbox_url: str) -> str | None:
    parsed = urlparse(mailbox_url)
    if not parsed.scheme or not parsed.netloc:
        return None

    path = parsed.path.strip("/")
    if not path.startswith("mail"):
        return f"{parsed.scheme}://{parsed.netloc}/owa/service.svc"

    segments = path.split("/")
    mailbox_suffix = ""
    if len(segments) > 1 and segments[1]:
        mailbox_suffix = segments[1]

    if mailbox_suffix:
        return f"{parsed.scheme}://{parsed.netloc}/owa/{mailbox_suffix}/service.svc"
    return f"{parsed.scheme}://{parsed.netloc}/owa/service.svc"


def capture_owa_session(context: BrowserContext, page: Page, outlook_url: str) -> tuple[str, dict[str, str]]:
    headers: dict[str, str] = {}
    candidate_urls: list[str] = []
    observed_service_urls: list[str] = []
    preferred_service_url: str | None = None
    fallback_service_url: str | None = None

    def on_request(request: Any) -> None:
        if request.resource_type in {"fetch", "xhr"} and len(candidate_urls) < 8:
            candidate_urls.append(request.url)
        if request.resource_type not in {"fetch", "xhr"}:
            return
        if "/service.svc" not in request.url:
            return

        nonlocal fallback_service_url, preferred_service_url
        raw_service_url = request.url.split("?", 1)[0]
        if raw_service_url not in observed_service_urls and len(observed_service_urls) < 8:
            observed_service_urls.append(raw_service_url)
        if fallback_service_url is None:
            fallback_service_url = raw_service_url
        if "/published/service.svc" not in raw_service_url:
            preferred_service_url = raw_service_url

        source = request.headers
        for key in OWA_REQUIRED_HEADER_KEYS:
            value = source.get(key)
            if value and not headers.get(key):
                headers[key] = value

    context.on("request", on_request)
    page.goto(outlook_url, wait_until="domcontentloaded")
    wait_for_mailbox_ready(page)

    for attempt in range(4):
        page.wait_for_timeout(3_000)
        service_url = preferred_service_url or infer_service_url_from_mailbox_url(page.url) or fallback_service_url
        if service_url and has_complete_owa_headers(headers):
            return service_url, headers

        if attempt == 0:
            page.reload(wait_until="domcontentloaded")
            wait_for_mailbox_ready(page)
            continue

        if attempt == 1:
            apply_unread_filter(page)
            continue

        if attempt == 2:
            page.wait_for_timeout(3_000)
            continue

    if not headers:
        raise RuntimeError(
            "Could not capture Outlook service request headers from the browser session. "
            "The profile reached Outlook, but no authenticated OWA request was observed. "
            "If Outlook showed an account picker or login interstitial, rerun setup and wait "
            "until the inbox list is visible before pressing Enter. "
            f"Observed fetch/XHR URLs: {candidate_urls}"
        )

    raise RuntimeError(
        "Could not determine the Outlook service endpoint for the browser session. "
        f"Observed service URLs: {observed_service_urls or candidate_urls}; "
        f"mailbox_url={page.url}"
    )


def apply_unread_filter(page: Page) -> None:
    filter_button = page.get_by_role("button", name="Filter").first
    filter_button.wait_for(timeout=20_000)
    filter_button.click()
    page.wait_for_timeout(800)
    page.locator("text=/^Unread$/").first.click()
    page.wait_for_timeout(2_000)


def locate_search_box(page: Page) -> Any:
    candidates = (
        page.get_by_role("searchbox", name=re.compile("search", re.IGNORECASE)),
        page.get_by_role("combobox", name=re.compile("search", re.IGNORECASE)),
        page.get_by_role("textbox", name=re.compile("search", re.IGNORECASE)),
        page.locator('[role="searchbox"]'),
        page.locator('[role="combobox"][aria-label*="Search" i]'),
        page.locator('input[aria-label*="Search" i]'),
        page.locator('input[placeholder*="Search" i]'),
        page.locator('textarea[aria-label*="Search" i]'),
    )

    for locator in candidates:
        if not locator.count():
            continue
        candidate = locator.first
        try:
            candidate.wait_for(timeout=2_000)
            return candidate
        except Error:
            continue

    debug_fields = page.locator('input, textarea, [role="searchbox"], [role="combobox"], [role="textbox"]').evaluate_all(
        """
        elements => elements.slice(0, 20).map(element => ({
            tag: element.tagName,
            role: element.getAttribute('role') || '',
            ariaLabel: element.getAttribute('aria-label') || '',
            placeholder: element.getAttribute('placeholder') || '',
            title: element.getAttribute('title') || '',
        }))
        """
    )
    raise RuntimeError(f"Could not locate the Outlook search box. Candidate fields: {debug_fields}")


def apply_search_query(page: Page, query: str) -> None:
    search_box = locate_search_box(page)
    search_box.click()
    page.wait_for_timeout(500)
    try:
        search_box.press("Meta+A")
    except Error:
        try:
            search_box.press("Control+A")
        except Error:
            pass
    search_box.fill(query)
    search_box.press("Enter")
    page.wait_for_timeout(3_000)
    wait_for_message_list(page, timeout=30_000)
    reset_message_list_to_top(page)


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


def reset_message_list_to_top(page: Page) -> None:
    listbox = page.locator('[role="listbox"]').first
    listbox.evaluate(
        """
        element => {
            const candidates = [element, ...element.querySelectorAll('*')];
            const target = candidates.find(node => node.scrollHeight > node.clientHeight + 5) || element;
            target.scrollTop = 0;
        }
        """
    )
    page.wait_for_timeout(500)


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


def maybe_expand_filtered_results(page: Page) -> bool:
    search_link = page.get_by_text("run a search for all filtered items", exact=False)
    if not search_link.count():
        return False

    search_link.first.click()
    page.wait_for_timeout(2_000)
    reset_message_list_to_top(page)
    return True


def conversation_row_key(row: dict[str, str]) -> str:
    return row.get("conversation_id", "")


def search_result_row_key(row: dict[str, str]) -> str:
    instance_key = row.get("instance_key", "")
    if instance_key:
        return instance_key
    conversation_id = row.get("conversation_id", "")
    aria_label = row.get("aria_label", "")
    text = row.get("text", "")
    if conversation_id or aria_label or text:
        return f"{conversation_id}|{aria_label}|{text}"
    return ""


def collect_rows(
    page: Page,
    *,
    key_fn: Callable[[dict[str, str]], str],
    max_results: int | None = None,
    allow_server_search_expansion: bool = False,
) -> tuple[list[dict[str, str]], bool]:
    seen: dict[str, dict[str, str]] = {}
    stagnant_rounds = 0
    expanded_server_results = False

    reset_message_list_to_top(page)

    while stagnant_rounds < 4:
        grew = False
        for row in collect_visible_rows(page):
            row_key = key_fn(row)
            if not row_key:
                continue

            if row_key not in seen:
                seen[row_key] = row
                grew = True
                if max_results is not None and len(seen) >= max_results:
                    return list(seen.values()), True

        scroll_state = scroll_message_list(page)
        page.wait_for_timeout(800)
        moved = scroll_state.get("after", 0) > scroll_state.get("before", 0)

        if grew or moved:
            stagnant_rounds = 0
            continue

        if allow_server_search_expansion and not expanded_server_results and maybe_expand_filtered_results(page):
            expanded_server_results = True
            stagnant_rounds = 0
            continue

        stagnant_rounds += 1

    return list(seen.values()), False


def collect_filtered_conversations(page: Page) -> list[dict[str, str]]:
    rows, _ = collect_rows(page, key_fn=conversation_row_key, allow_server_search_expansion=True)
    return rows


def collect_search_result_rows(page: Page, *, max_results: int | None = None) -> tuple[list[dict[str, str]], bool]:
    return collect_rows(page, key_fn=search_result_row_key, max_results=max_results)


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


def fetch_conversation_nodes(
    context: BrowserContext,
    service_url: str,
    headers: dict[str, str],
    conversation_id: str,
) -> list[dict[str, Any]]:
    payload = build_conversation_payload(conversation_id)
    response = context.request.post(
        f"{service_url}?action=GetConversationItems&app=Mail&n=999",
        headers=build_owa_headers(headers, "GetConversationItems"),
        data=json.dumps(payload),
    )
    if not response.ok:
        raise RuntimeError(
            f"GetConversationItems failed with status {response.status} for conversation {conversation_id}."
        )
    data = response.json()

    response_messages = data.get("Body", {}).get("ResponseMessages", {}).get("Items", [])
    if not response_messages:
        return []

    conversation = response_messages[0].get("Conversation", {})
    return conversation.get("ConversationNodes", [])


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


def message_identity(item: dict[str, Any], conversation_id: str) -> str:
    return (
        item.get("ItemId", {}).get("Id")
        or item.get("InternetMessageId")
        or item.get("InstanceKey")
        or f"{conversation_id}:{item.get('DateTimeReceived') or item.get('Subject') or 'unknown'}"
    )


def parse_thread_depth(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        if value <= 0:
            raise RuntimeError("`--thread-depth` must be `all` or a positive integer.")
        return value

    raw_value = str(value).strip().lower()
    if raw_value in {"", "all"}:
        return None

    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise RuntimeError("`--thread-depth` must be `all` or a positive integer.") from exc

    if parsed <= 0:
        raise RuntimeError("`--thread-depth` must be `all` or a positive integer.")
    return parsed


def thread_depth_label(thread_depth: int | None) -> str:
    return "all" if thread_depth is None else str(thread_depth)


def build_message_record(
    item: dict[str, Any],
    conversation_id: str,
    *,
    instance_key: str = "",
    row_aria_label: str | None = None,
    parent_internet_message_id: str | None = None,
    has_quoted_text: bool | None = None,
    is_root_node: bool | None = None,
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
        "internet_message_id": item.get("InternetMessageId"),
        "parent_internet_message_id": parent_internet_message_id,
        "conversation_id": conversation_id,
        "conversation_thread_id": (item.get("ConversationThreadId") or {}).get("Id"),
        "instance_key": item.get("InstanceKey") or instance_key,
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
        "body_scope": "unique_fragment",
        "preview": item.get("Preview", ""),
        "available_actions": response_objects,
        "can_rsvp": is_meeting_request and bool(meeting and meeting["available_rsvp_actions"]),
        "meeting": meeting,
        "has_quoted_text": has_quoted_text,
        "is_root_node": is_root_node,
        **({"row_aria_label": row_aria_label} if row_aria_label is not None else {}),
    }


def build_thread_bundle(
    context: BrowserContext,
    service_url: str,
    headers: dict[str, str],
    conversation_id: str,
    *,
    thread_depth: int | None,
) -> dict[str, Any]:
    full_item_entries: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for node in fetch_conversation_nodes(context, service_url, headers, conversation_id):
        node_metadata = {
            "parent_internet_message_id": node.get("ParentInternetMessageId"),
            "has_quoted_text": node.get("HasQuotedText"),
            "is_root_node": node.get("IsRootNode"),
        }
        for item in node.get("Items", []):
            full_item_entries.append((item, node_metadata))

    included_item_entries = full_item_entries if thread_depth is None else full_item_entries[:thread_depth]
    thread_messages = [
        build_message_record(
            item,
            conversation_id,
            parent_internet_message_id=node_metadata["parent_internet_message_id"],
            has_quoted_text=node_metadata["has_quoted_text"],
            is_root_node=node_metadata["is_root_node"],
        )
        for item, node_metadata in included_item_entries
    ]
    thread_index_by_identity = {
        (
            message.get("message_id")
            or message.get("internet_message_id")
            or message.get("instance_key")
            or f"{conversation_id}:{index}"
        ): index
        for index, message in enumerate(thread_messages)
    }

    return {
        "conversation_id": conversation_id,
        "full_item_entries": full_item_entries,
        "included_item_entries": included_item_entries,
        "thread_messages": thread_messages,
        "thread_index_by_identity": thread_index_by_identity,
        "thread": {
            "conversation_id": conversation_id,
            "message_count": len(thread_messages),
            "messages": thread_messages,
        },
    }


def select_search_result_item(
    row: dict[str, str],
    conversation_id: str,
    full_item_entries: list[tuple[dict[str, Any], dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    row_instance_key = row.get("instance_key", "")
    if row_instance_key:
        for item, node_metadata in full_item_entries:
            if item.get("InstanceKey") == row_instance_key:
                return item, node_metadata

    for item, node_metadata in full_item_entries:
        if message_identity(item, conversation_id) == row_instance_key:
            return item, node_metadata

    if full_item_entries:
        return full_item_entries[0]
    return None


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
        service_url, owa_headers = capture_owa_session(context, page, args.outlook_url)
        apply_unread_filter(page)
        filtered_rows = collect_filtered_conversations(page)

        emails: list[dict[str, Any]] = []
        threads: list[dict[str, Any]] = []
        seen_messages: set[str] = set()
        for row in filtered_rows:
            conversation_id = row.get("conversation_id", "")
            if not conversation_id:
                continue

            thread_bundle = build_thread_bundle(
                context,
                service_url,
                owa_headers,
                conversation_id,
                thread_depth=None,
            )
            threads.append(thread_bundle["thread"])

            for item, node_metadata in thread_bundle["full_item_entries"]:
                if item.get("IsRead") is True:
                    continue
                identity = message_identity(item, conversation_id)
                if identity in seen_messages:
                    continue
                seen_messages.add(identity)

                record = build_message_record(
                    item,
                    conversation_id,
                    instance_key=row.get("instance_key", ""),
                    row_aria_label=row.get("aria_label", ""),
                    parent_internet_message_id=node_metadata["parent_internet_message_id"],
                    has_quoted_text=node_metadata["has_quoted_text"],
                    is_root_node=node_metadata["is_root_node"],
                )
                record["thread_message_index"] = thread_bundle["thread_index_by_identity"].get(identity)
                record["thread_message_count"] = thread_bundle["thread"]["message_count"]
                emails.append(record)

        payload = {
            "contract": CONTRACT_VERSION,
            "provider": PROVIDER_NAME,
            "account": args.account,
            "source": "outlook_web",
            "service_url": service_url,
            "profile_dir": str(profile_dir.resolve()),
            "email_count": len(emails),
            "thread_count": len(threads),
            "emails": emails,
            "threads": threads,
        }
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote {len(emails)} unread emails to {output_path}")
        return 0
    finally:
        context.close()
        playwright.stop()


def run_search_export(args: argparse.Namespace) -> int:
    profile_dir = args.profile_dir.expanduser()
    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    thread_depth = parse_thread_depth(args.thread_depth)

    playwright, context, page = launch_context(profile_dir, headless=args.headless)
    try:
        service_url, owa_headers = capture_owa_session(context, page, args.outlook_url)
        apply_search_query(page, args.query)
        search_rows, truncated = collect_search_result_rows(page, max_results=args.max_results)

        emails: list[dict[str, Any]] = []
        threads: list[dict[str, Any]] = []
        thread_bundle_by_conversation: dict[str, dict[str, Any]] = {}

        for row in search_rows:
            conversation_id = row.get("conversation_id", "")
            if not conversation_id:
                continue

            if conversation_id not in thread_bundle_by_conversation:
                thread_bundle_by_conversation[conversation_id] = build_thread_bundle(
                    context,
                    service_url,
                    owa_headers,
                    conversation_id,
                    thread_depth=thread_depth,
                )
                threads.append(thread_bundle_by_conversation[conversation_id]["thread"])

            thread_bundle = thread_bundle_by_conversation[conversation_id]
            selected_item = select_search_result_item(row, conversation_id, thread_bundle["full_item_entries"])
            if selected_item is None:
                continue

            item, node_metadata = selected_item
            identity = message_identity(item, conversation_id)
            record = build_message_record(
                item,
                conversation_id,
                instance_key=row.get("instance_key", ""),
                row_aria_label=row.get("aria_label", ""),
                parent_internet_message_id=node_metadata["parent_internet_message_id"],
                has_quoted_text=node_metadata["has_quoted_text"],
                is_root_node=node_metadata["is_root_node"],
            )
            record["thread_message_index"] = thread_bundle["thread_index_by_identity"].get(identity)
            record["thread_message_count"] = thread_bundle["thread"]["message_count"]
            emails.append(record)

        payload = {
            "contract": CONTRACT_VERSION,
            "provider": PROVIDER_NAME,
            "account": args.account,
            "source": "outlook_web",
            "service_url": service_url,
            "profile_dir": str(profile_dir.resolve()),
            "selection_mode": "search",
            "search_query": args.query,
            "thread_depth": thread_depth_label(thread_depth),
            "truncated": truncated,
            "email_count": len(emails),
            "thread_count": len(threads),
            "emails": emails,
            "threads": threads,
        }
        if args.max_results is not None:
            payload["max_results"] = args.max_results

        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote {len(emails)} search results across {len(threads)} threads to {output_path}")
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
        if args.command == "search-export":
            return run_search_export(args)
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
