from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

THREAD_SUMMARIES_CONTRACT = "surface.thread_summaries.v1"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "qwen/qwen3.5-397b-a17b"
DEFAULT_MAX_CONTEXT_TOKENS = 128_000
# We intentionally stay well below the advertised limit because prompt framing,
# response tokens, and local token estimates are not exact.
DEFAULT_TARGET_INPUT_TOKENS = int(DEFAULT_MAX_CONTEXT_TOKENS * 0.85)
DEFAULT_MAX_OUTPUT_TOKENS = 4_096
SUPPORTED_LLM_BACKENDS = ("openrouter",)


@dataclass(frozen=True)
class ResolvedBackend:
    name: str
    model: str
    api_key: str


@dataclass(frozen=True)
class ChunkSpec:
    chunk_index: int
    thread_keys: list[str]
    thread_units: list[dict[str, Any]]
    estimated_input_tokens: int
    oversized_single_thread: bool = False


@dataclass(frozen=True)
class PostProcessRunResult:
    output_path: Path
    status: str
    summary_count: int
    chunk_count: int
    failed_chunk_count: int
    skipped: bool = False


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_backend(
    *,
    requested_backend: str | None = None,
    requested_model: str | None = None,
    require_configured: bool = True,
) -> ResolvedBackend | None:
    backend_name = requested_backend or os.environ.get("SURFACE_POST_PROCESS_BACKEND")
    if backend_name:
        if backend_name not in SUPPORTED_LLM_BACKENDS:
            raise RuntimeError(f"Unsupported LLM backend: {backend_name}")
    elif os.environ.get("OPENROUTER_API_KEY"):
        backend_name = "openrouter"

    if backend_name is None:
        if require_configured:
            raise RuntimeError(
                "No post-processing backend is configured. Set OPENROUTER_API_KEY or pass --skip-post-process."
            )
        return None

    if backend_name != "openrouter":
        raise RuntimeError(f"Unsupported LLM backend: {backend_name}")

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        if require_configured:
            raise RuntimeError("OPENROUTER_API_KEY is required for OpenRouter post-processing.")
        return None

    model = requested_model or os.environ.get("SURFACE_POST_PROCESS_MODEL") or DEFAULT_OPENROUTER_MODEL
    return ResolvedBackend(name=backend_name, model=model, api_key=api_key)


def compact_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\n{3,}", "\n\n", value.strip())


def project_mailbox(mailbox: dict[str, Any] | None) -> dict[str, str] | None:
    if not mailbox:
        return None
    name = (mailbox.get("name") or "").strip()
    email = (mailbox.get("email") or "").strip()
    if not name and not email:
        return None
    return {"name": name, "email": email}


def project_recipients(recipients: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    projected: list[dict[str, str]] = []
    for mailbox in recipients or []:
        item = project_mailbox(mailbox)
        if item is not None:
            projected.append(item)
    return projected


def message_identity(message: dict[str, Any]) -> str | None:
    for key in ("message_id", "internet_message_id", "message_change_key"):
        value = message.get(key)
        if value:
            return str(value)
    return None


def thread_key_for(thread: dict[str, Any], index: int) -> str:
    conversation_id = thread.get("conversation_id")
    if conversation_id:
        return str(conversation_id)
    for message in thread.get("messages") or []:
        identity = message_identity(message)
        if identity:
            return f"thread:{identity}"
    return f"thread-index-{index}"


def project_thread_message(message: dict[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {
        "message_id": message.get("message_id"),
        "internet_message_id": message.get("internet_message_id"),
        "subject": message.get("subject") or "",
        "from": project_mailbox(message.get("from")),
        "to": project_recipients(message.get("to")),
        "cc": project_recipients(message.get("cc")),
        "sent_at": message.get("sent_at"),
        "received_at": message.get("received_at"),
        "preview": compact_text(message.get("preview")),
        "body": compact_text(message.get("body")),
        "can_rsvp": bool(message.get("can_rsvp")),
        "available_actions": list(message.get("available_actions") or []),
    }
    if message.get("meeting") is not None:
        projected["meeting"] = message.get("meeting")
    return projected


def project_top_level_email(email: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_id": email.get("message_id"),
        "internet_message_id": email.get("internet_message_id"),
        "subject": email.get("subject") or "",
        "from": project_mailbox(email.get("from")),
        "sent_at": email.get("sent_at"),
        "received_at": email.get("received_at"),
        "preview": compact_text(email.get("preview")),
    }


def build_thread_units(payload: dict[str, Any]) -> list[dict[str, Any]]:
    thread_units: list[dict[str, Any]] = []
    thread_unit_by_key: dict[str, dict[str, Any]] = {}
    thread_key_by_conversation_id: dict[str, str] = {}
    thread_key_by_message_identity: dict[str, str] = {}

    for index, thread in enumerate(payload.get("threads") or []):
        thread_key = thread_key_for(thread, index)
        conversation_id = thread.get("conversation_id")
        projected_messages = [project_thread_message(message) for message in thread.get("messages") or []]
        message_ids = [identity for message in thread.get("messages") or [] if (identity := message_identity(message))]
        thread_unit = {
            "thread_key": thread_key,
            "conversation_id": conversation_id,
            "message_count": thread.get("message_count", len(projected_messages)),
            "message_ids": message_ids,
            "matched_top_level_messages": [],
            "messages": projected_messages,
        }
        thread_units.append(thread_unit)
        thread_unit_by_key[thread_key] = thread_unit

        if conversation_id:
            thread_key_by_conversation_id[str(conversation_id)] = thread_key
        for identity in message_ids:
            thread_key_by_message_identity[identity] = thread_key

    for email in payload.get("emails") or []:
        thread_key: str | None = None
        conversation_id = email.get("conversation_id")
        if conversation_id:
            thread_key = thread_key_by_conversation_id.get(str(conversation_id))
        if thread_key is None:
            identity = message_identity(email)
            if identity:
                thread_key = thread_key_by_message_identity.get(identity)
        if thread_key is None:
            continue
        thread_unit_by_key[thread_key]["matched_top_level_messages"].append(project_top_level_email(email))

    return thread_units


def build_chunk_payload(payload: dict[str, Any], thread_units: list[dict[str, Any]]) -> dict[str, Any]:
    chunk_payload: dict[str, Any] = {
        "provider": payload.get("provider"),
        "account": payload.get("account"),
        "selection_mode": payload.get("selection_mode") or "unread",
        "source_contract": payload.get("contract"),
        "thread_count": len(thread_units),
        "threads": thread_units,
    }
    if payload.get("search_query"):
        chunk_payload["search_query"] = payload["search_query"]
    return chunk_payload


def build_summary_messages(payload: dict[str, Any], thread_units: list[dict[str, Any]]) -> list[dict[str, str]]:
    keys = [thread_unit["thread_key"] for thread_unit in thread_units]
    instructions = {
        "expected_thread_keys": keys,
        "output_shape": {"thread_summaries": {"<thread_key>": "summary text"}},
        "requirements": [
            "Return only valid JSON.",
            "Use exactly the thread keys provided in expected_thread_keys.",
            "Each summary must describe the full thread, not just one matched message.",
            "Mention key asks, decisions, deadlines, blockers, and unresolved questions when present.",
            "Do not add markdown, commentary, or extra keys outside thread_summaries.",
        ],
        "input": build_chunk_payload(payload, thread_units),
    }
    return [
        {
            "role": "system",
            "content": (
                "You summarize email threads for downstream agents. "
                "Return strict JSON only, with no markdown fences or explanatory prose."
            ),
        },
        {"role": "user", "content": json.dumps(instructions, ensure_ascii=False, separators=(",", ":"))},
    ]


def build_json_repair_messages(raw_text: str, expected_keys: list[str]) -> list[dict[str, str]]:
    payload = {
        "expected_thread_keys": expected_keys,
        "output_shape": {"thread_summaries": {"<thread_key>": "summary text"}},
        "rules": [
            "Return only valid JSON.",
            "Repair syntax only and preserve the underlying summaries.",
            "Do not invent thread keys that are not already present in the malformed response.",
        ],
        "malformed_response": raw_text,
    }
    return [
        {
            "role": "system",
            "content": "You repair malformed JSON. Return valid JSON only with no markdown or commentary.",
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]


def estimate_tokens_for_messages(messages: list[dict[str, str]]) -> int:
    rendered = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    return max(1, math.ceil(len(rendered.encode("utf-8")) / 3))


def pack_thread_units(
    payload: dict[str, Any],
    thread_units: list[dict[str, Any]],
    *,
    target_input_tokens: int,
    max_context_tokens: int,
    max_output_tokens: int,
) -> tuple[list[ChunkSpec], list[dict[str, Any]]]:
    chunks: list[ChunkSpec] = []
    preflight_results: list[dict[str, Any]] = []
    hard_input_limit = max(1, max_context_tokens - max_output_tokens)

    current_units: list[dict[str, Any]] = []
    current_estimate = 0
    next_chunk_index = 0

    def append_chunk(units: list[dict[str, Any]], estimate: int, *, oversized_single_thread: bool = False) -> None:
        nonlocal next_chunk_index
        chunks.append(
            ChunkSpec(
                chunk_index=next_chunk_index,
                thread_keys=[unit["thread_key"] for unit in units],
                thread_units=units,
                estimated_input_tokens=estimate,
                oversized_single_thread=oversized_single_thread,
            )
        )
        next_chunk_index += 1

    for thread_unit in thread_units:
        candidate_units = current_units + [thread_unit]
        candidate_estimate = estimate_tokens_for_messages(build_summary_messages(payload, candidate_units))
        if current_units and candidate_estimate <= target_input_tokens:
            current_units = candidate_units
            current_estimate = candidate_estimate
            continue

        single_thread_estimate = estimate_tokens_for_messages(build_summary_messages(payload, [thread_unit]))
        if current_units:
            append_chunk(current_units, current_estimate)
            current_units = []
            current_estimate = 0

        if single_thread_estimate > hard_input_limit:
            preflight_results.append(
                {
                    "chunk_index": next_chunk_index,
                    "status": "failed",
                    "conversation_ids": [thread_unit["thread_key"]],
                    "summary_count": 0,
                    "estimated_input_tokens": single_thread_estimate,
                    "missing_conversation_ids": [thread_unit["thread_key"]],
                    "error": "thread_exceeds_input_budget",
                }
            )
            next_chunk_index += 1
            continue

        current_units = [thread_unit]
        current_estimate = single_thread_estimate
        if single_thread_estimate > target_input_tokens:
            append_chunk(current_units, current_estimate, oversized_single_thread=True)
            current_units = []
            current_estimate = 0

    if current_units:
        append_chunk(current_units, current_estimate)

    return chunks, preflight_results


def strip_markdown_fences(raw_text: str) -> str:
    fenced_match = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", raw_text, flags=re.DOTALL)
    if fenced_match:
        return fenced_match.group(1).strip()
    return raw_text.strip()


def parse_thread_summaries(raw_text: str) -> dict[str, str]:
    parsed = json.loads(strip_markdown_fences(raw_text))
    summaries_payload: Any
    if isinstance(parsed, dict) and isinstance(parsed.get("thread_summaries"), dict):
        summaries_payload = parsed["thread_summaries"]
    elif isinstance(parsed, dict):
        summaries_payload = parsed
    else:
        raise ValueError("LLM response is not a JSON object.")

    normalized: dict[str, str] = {}
    for key, value in summaries_payload.items():
        if isinstance(value, str):
            normalized[str(key)] = value.strip()
            continue
        if isinstance(value, dict) and isinstance(value.get("summary"), str):
            normalized[str(key)] = value["summary"].strip()
            continue
        raise ValueError(f"Thread summary for key {key!r} is not a string.")
    return normalized


def select_expected_summaries(
    parsed_summaries: dict[str, str], expected_keys: list[str]
) -> tuple[dict[str, str], list[str], list[str]]:
    expected_key_set = set(expected_keys)
    found = {key: value for key, value in parsed_summaries.items() if key in expected_key_set and value}
    missing = [key for key in expected_keys if key not in found]
    unexpected = [key for key in parsed_summaries if key not in expected_key_set]
    return found, missing, unexpected


class OpenRouterBackend:
    def __init__(self, *, api_key: str, model: str, max_output_tokens: int) -> None:
        self.api_key = api_key
        self.model = model
        self.max_output_tokens = max_output_tokens

    def complete(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.max_output_tokens,
        }
        request_payload = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            OPENROUTER_API_URL,
            data=request_payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=120) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenRouter request failed with HTTP {exc.code}: {response_body}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"OpenRouter request failed: {exc.reason}") from exc

        choices = response_payload.get("choices") or []
        if not choices:
            raise RuntimeError("OpenRouter response did not include any choices.")

        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            raw_text = content
        elif isinstance(content, list):
            raw_text = "".join(
                item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
            )
        else:
            raise RuntimeError("OpenRouter response did not include textual content.")

        return raw_text, response_payload.get("usage") or {}


def raw_response_path_for(output_path: Path, chunk_index: int) -> Path:
    return output_path.with_name(f"{output_path.stem}.chunk-{chunk_index}.raw.txt")


def write_raw_response_debug(output_path: Path, chunk_index: int, raw_text: str) -> str:
    raw_path = raw_response_path_for(output_path, chunk_index)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw_text, encoding="utf-8")
    return str(raw_path.resolve())


def build_empty_output_payload(
    *,
    input_path: Path,
    output_path: Path,
    source_payload: dict[str, Any],
    backend_name: str,
    model: str,
    status: str,
    chunk_results: list[dict[str, Any]],
    thread_summaries: dict[str, str],
) -> dict[str, Any]:
    return {
        "contract": THREAD_SUMMARIES_CONTRACT,
        "status": status,
        "generated_at": utc_now(),
        "source_contract": source_payload.get("contract"),
        "source_path": str(input_path.resolve()),
        "provider": source_payload.get("provider"),
        "account": source_payload.get("account"),
        "selection_mode": source_payload.get("selection_mode") or "unread",
        "search_query": source_payload.get("search_query"),
        "llm_backend": backend_name,
        "llm_model": model,
        "source_email_count": source_payload.get("email_count", len(source_payload.get("emails") or [])),
        "source_thread_count": source_payload.get("thread_count", len(source_payload.get("threads") or [])),
        "thread_summary_count": len(thread_summaries),
        "thread_summaries": thread_summaries,
        "chunk_count": len(chunk_results),
        "failed_chunk_count": sum(1 for chunk in chunk_results if chunk.get("status") != "ok"),
        "chunk_results": chunk_results,
        "output_path": str(output_path.resolve()),
    }


def write_output_payload(output_path: Path, payload: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_post_process(
    *,
    input_path: Path,
    output_path: Path,
    requested_backend: str | None = None,
    requested_model: str | None = None,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    target_input_tokens: int = DEFAULT_TARGET_INPUT_TOKENS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    require_configured_backend: bool = True,
) -> PostProcessRunResult:
    source_payload = json.loads(input_path.read_text(encoding="utf-8"))
    thread_units = build_thread_units(source_payload)
    backend = resolve_backend(
        requested_backend=requested_backend,
        requested_model=requested_model,
        require_configured=require_configured_backend and bool(thread_units),
    )
    if backend is None:
        return PostProcessRunResult(
            output_path=output_path,
            status="skipped",
            summary_count=0,
            chunk_count=0,
            failed_chunk_count=0,
            skipped=True,
        )

    if not thread_units:
        empty_payload = build_empty_output_payload(
            input_path=input_path,
            output_path=output_path,
            source_payload=source_payload,
            backend_name=backend.name,
            model=backend.model,
            status="complete",
            chunk_results=[],
            thread_summaries={},
        )
        write_output_payload(output_path, empty_payload)
        return PostProcessRunResult(
            output_path=output_path,
            status="complete",
            summary_count=0,
            chunk_count=0,
            failed_chunk_count=0,
        )

    openrouter_backend = OpenRouterBackend(
        api_key=backend.api_key,
        model=backend.model,
        max_output_tokens=max_output_tokens,
    )

    chunks, preflight_results = pack_thread_units(
        source_payload,
        thread_units,
        target_input_tokens=target_input_tokens,
        max_context_tokens=max_context_tokens,
        max_output_tokens=max_output_tokens,
    )
    thread_summaries: dict[str, str] = {}
    chunk_results: list[dict[str, Any]] = list(preflight_results)

    for chunk in chunks:
        messages = build_summary_messages(source_payload, chunk.thread_units)
        raw_response_text = ""
        try:
            raw_response_text, usage = openrouter_backend.complete(messages)
            try:
                parsed_summaries = parse_thread_summaries(raw_response_text)
            except (ValueError, json.JSONDecodeError):
                repair_messages = build_json_repair_messages(raw_response_text, chunk.thread_keys)
                repaired_response_text, repair_usage = openrouter_backend.complete(repair_messages)
                usage = {
                    "prompt_tokens": (usage.get("prompt_tokens") or 0) + (repair_usage.get("prompt_tokens") or 0),
                    "completion_tokens": (usage.get("completion_tokens") or 0)
                    + (repair_usage.get("completion_tokens") or 0),
                    "total_tokens": (usage.get("total_tokens") or 0) + (repair_usage.get("total_tokens") or 0),
                }
                parsed_summaries = parse_thread_summaries(repaired_response_text)
                raw_response_text = repaired_response_text

            found_summaries, missing_keys, unexpected_keys = select_expected_summaries(parsed_summaries, chunk.thread_keys)
            thread_summaries.update(found_summaries)

            chunk_status = "ok" if not missing_keys else "partial"
            chunk_result: dict[str, Any] = {
                "chunk_index": chunk.chunk_index,
                "status": chunk_status,
                "conversation_ids": chunk.thread_keys,
                "summary_count": len(found_summaries),
                "estimated_input_tokens": chunk.estimated_input_tokens,
                "oversized_single_thread": chunk.oversized_single_thread,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
            if missing_keys:
                chunk_result["missing_conversation_ids"] = missing_keys
                chunk_result["raw_response_path"] = write_raw_response_debug(output_path, chunk.chunk_index, raw_response_text)
            if unexpected_keys:
                chunk_result["unexpected_conversation_ids"] = unexpected_keys
            chunk_results.append(chunk_result)
        except RuntimeError as exc:
            chunk_result = {
                "chunk_index": chunk.chunk_index,
                "status": "failed",
                "conversation_ids": chunk.thread_keys,
                "summary_count": 0,
                "estimated_input_tokens": chunk.estimated_input_tokens,
                "oversized_single_thread": chunk.oversized_single_thread,
                "missing_conversation_ids": chunk.thread_keys,
                "error": str(exc),
            }
            if raw_response_text:
                chunk_result["raw_response_path"] = write_raw_response_debug(output_path, chunk.chunk_index, raw_response_text)
            chunk_results.append(chunk_result)

    failed_chunk_count = sum(1 for chunk_result in chunk_results if chunk_result.get("status") != "ok")
    if failed_chunk_count == 0:
        status = "complete"
    elif thread_summaries:
        status = "partial"
    else:
        status = "failed"

    output_payload = build_empty_output_payload(
        input_path=input_path,
        output_path=output_path,
        source_payload=source_payload,
        backend_name=backend.name,
        model=backend.model,
        status=status,
        chunk_results=chunk_results,
        thread_summaries=thread_summaries,
    )
    write_output_payload(output_path, output_payload)

    return PostProcessRunResult(
        output_path=output_path,
        status=status,
        summary_count=len(thread_summaries),
        chunk_count=len(chunk_results),
        failed_chunk_count=failed_chunk_count,
    )
