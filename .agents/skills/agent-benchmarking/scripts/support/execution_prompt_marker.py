"""Build and observe benchmark markers carried by real user prompts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


NONCE_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MARKER_PREFIX = "[agent-benchmarking.execution-nonce:"
MARKER_PATTERN = re.compile(r"^\[agent-benchmarking\.execution-nonce:[0-9a-f]{64}\]$")


def build_marker(execution_nonce: str) -> str:
    """Return the exact final prompt line bound to one protocol trial."""

    if not isinstance(execution_nonce, str) or NONCE_PATTERN.fullmatch(execution_nonce) is None:
        raise ValueError("execution nonce must be a lowercase SHA-256")
    return f"{MARKER_PREFIX}{execution_nonce}]"


def build_prompt(task_text: str, marker: str) -> str:
    """Build the only complete user prompt accepted for a benchmark trial."""

    if not isinstance(task_text, str):
        raise ValueError("task text must be a string")
    if not isinstance(marker, str) or MARKER_PATTERN.fullmatch(marker) is None:
        raise ValueError("execution marker is invalid")
    return f"{task_text}\n{marker}"


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


CONTEXT_FIELD_TERMS = ("attachment", "context", "file", "image")


def _nonempty(value: object) -> bool:
    if value is None or value is False or value == "":
        return False
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return True


def _has_undeclared_context(payload: dict[str, Any], allowed: set[str]) -> bool:
    return any(
        key not in allowed
        and any(term in str(key).lower() for term in CONTEXT_FIELD_TERMS)
        and _nonempty(value)
        for key, value in payload.items()
    )


def _user_message_observations(value: object) -> list[dict[str, Any]]:
    """Extract text and reject undeclared content from parsed user events."""

    if not isinstance(value, dict):
        return []
    payload = value.get("payload")
    if not isinstance(payload, dict):
        return []
    if value.get("type") == "event_msg" and payload.get("type") == "user_message":
        message = payload.get("message")
        return [
            {
                "text": message if isinstance(message, str) else "",
                "unsupported_context": (
                    not isinstance(message, str)
                    or _has_undeclared_context(payload, {"type", "message"})
                    or _has_undeclared_context(value, {"type", "payload"})
                ),
            }
        ]
    if (
        value.get("type") == "response_item"
        and payload.get("type") == "message"
        and payload.get("role") == "user"
    ):
        content = payload.get("content")
        if not isinstance(content, list):
            return [{"text": "", "unsupported_context": True}]
        input_texts: list[str] = []
        unsupported_context = _has_undeclared_context(
            payload,
            {"type", "role", "content"},
        ) or _has_undeclared_context(value, {"type", "payload"})
        for item in content:
            if (
                not isinstance(item, dict)
                or item.get("type") != "input_text"
                or not isinstance(item.get("text"), str)
            ):
                unsupported_context = True
                continue
            if _has_undeclared_context(item, {"type", "text"}):
                unsupported_context = True
            input_texts.append(str(item["text"]))
        if not input_texts:
            unsupported_context = True
        return [
            {
                "text": "".join(input_texts),
                "unsupported_context": unsupported_context,
            }
        ]
    return []


def scope_observation(data: bytes, expected_prompt: str) -> dict[str, Any]:
    """Observe exact prompt presence and the fresh-thread usage boundary."""

    if not isinstance(expected_prompt, str) or not expected_prompt:
        return {
            "occurrence_count": 0,
            "first_structured_user_message_observed": False,
            "first_structured_user_message_matches": False,
            "usage_events_before_first_prompt": 0,
            "unsupported_user_context_before_or_with_prompt": False,
            "fresh_thread_scope": False,
        }
    count = 0
    first_user_observed = False
    first_user_matches = False
    prompt_observed = False
    usage_before_prompt = 0
    unsupported_context = False
    for raw_line in data.splitlines():
        try:
            value: Any = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        user_observations = _user_message_observations(value)
        for observation in user_observations:
            text = str(observation["text"])
            event_has_unsupported_context = observation["unsupported_context"] is True
            if event_has_unsupported_context and (
                not prompt_observed or text == expected_prompt
            ):
                unsupported_context = True
            canonical_prompt = text == expected_prompt and not event_has_unsupported_context
            if not first_user_observed:
                first_user_observed = True
                first_user_matches = canonical_prompt
            if canonical_prompt:
                count += 1
                prompt_observed = True
        payload = value.get("payload") if isinstance(value, dict) else None
        info = payload.get("info") if isinstance(payload, dict) else None
        usage = info.get("last_token_usage") if isinstance(info, dict) else None
        if isinstance(usage, dict) and not prompt_observed:
            usage_before_prompt += 1
    return {
        "occurrence_count": count,
        "first_structured_user_message_observed": first_user_observed,
        "first_structured_user_message_matches": first_user_matches,
        "usage_events_before_first_prompt": usage_before_prompt,
        "unsupported_user_context_before_or_with_prompt": unsupported_context,
        "fresh_thread_scope": (
            count > 0
            and first_user_matches
            and usage_before_prompt == 0
            and not unsupported_context
        ),
    }


def occurrence_count(data: bytes, expected_prompt: str) -> int:
    """Count exact complete prompts in structured user-message events only."""

    return int(scope_observation(data, expected_prompt)["occurrence_count"])
