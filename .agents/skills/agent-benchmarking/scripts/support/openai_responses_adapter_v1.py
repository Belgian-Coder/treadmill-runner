"""Deterministic V1 request and sanitized evidence support for direct Responses API use.

This module never performs network I/O. The caller owns transport, protected
continuation state, credentials, approvals, and the coordinator capture root.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


SCHEMA_VERSION = 1
TOOL_ID = "agent-benchmarking.openai-responses-receipt"
ENDPOINT = "/v1/responses"
RECEIPT_FIELDS = {
    "schema_version",
    "tool",
    "run_id",
    "capture_nonce",
    "endpoint",
    "calls",
    "usage",
}
CALL_FIELDS = {"sequence", "request", "response"}
REQUEST_FIELDS = {
    "body_sha256",
    "model",
    "store",
    "prompt_cache_key_sha256",
    "cache_mode",
    "cache_ttl",
    "cache_breakpoint_count",
    "reasoning_context",
    "continuation_mode",
    "previous_response_id_sha256",
    "replay_items_sha256",
    "programmatic_tool_calling",
    "programmatic_tool_set_sha256",
    "input_relationships",
}
RESPONSE_FIELDS = {
    "response_id_sha256",
    "request_id_sha256",
    "next_replay_items_sha256",
    "model",
    "status",
    "effective_reasoning_context",
    "output_relationships",
    "usage",
}
USAGE_FIELDS = {
    "input_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
}
RELATIONSHIP_FIELDS = {
    "type",
    "call_id_sha256",
    "caller_type",
    "caller_id_sha256",
    "status",
}
REASONING_CONTEXTS = {"auto", "current_turn", "all_turns", "unavailable"}
CACHE_MODES = {"implicit", "explicit", "unavailable"}
RESPONSE_STATUSES = {"completed", "incomplete", "failed"}
CALLERS = {"direct", "programmatic"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: object) -> str:
    if value in (None, "", [], {}):
        return ""
    raw = value if isinstance(value, bytes) else _canonical_bytes(value)
    return hashlib.sha256(raw).hexdigest()


def _secret_sha(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _count(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= (2**63 - 1)
    )


def _breakpoint_count(value: object, *, issues: list[str] | None = None) -> int:
    if isinstance(value, list):
        return sum(_breakpoint_count(item, issues=issues) for item in value)
    if not isinstance(value, dict):
        return 0
    own = 0
    if "prompt_cache_breakpoint" in value:
        if (
            value.get("type") not in {"input_text", "input_image", "input_file"}
            or value.get("prompt_cache_breakpoint") != {"mode": "explicit"}
        ):
            if issues is not None:
                issues.append(
                    "Responses prompt cache breakpoints are allowed only on input_text, input_image, or input_file blocks"
                )
        else:
            own = 1
    return own + sum(_breakpoint_count(item, issues=issues) for item in value.values())


def _validate_tools(tools: list[dict[str, Any]]) -> bool:
    names: set[str] = set()
    program_marker_count = 0
    program_callable = False
    for tool in tools:
        if not isinstance(tool, dict):
            raise ValueError("Responses tools must be objects")
        if tool.get("type") == "programmatic_tool_calling":
            program_marker_count += 1
            continue
        if tool.get("type") != "function":
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("Responses function tool names must be unique non-empty strings")
        names.add(name)
        allowed = tool.get("allowed_callers", ["direct"])
        if (
            not isinstance(allowed, list)
            or not allowed
            or any(caller not in CALLERS for caller in allowed)
            or len(allowed) != len(set(allowed))
        ):
            raise ValueError("Responses function allowed_callers must be unique direct/programmatic values")
        if "programmatic" in allowed:
            program_callable = True
            if not isinstance(tool.get("output_schema"), dict):
                raise ValueError("program-callable Responses functions require an output_schema")
    if program_marker_count > 1:
        raise ValueError("Responses tools may contain one programmatic_tool_calling marker")
    if program_marker_count and not program_callable:
        raise ValueError("programmatic_tool_calling requires at least one program-callable function")
    return program_marker_count == 1


def build_request(
    *,
    model: str,
    stable_input: list[object],
    volatile_input: list[object],
    tools: list[dict[str, Any]] | None = None,
    store: bool = False,
    prompt_cache_key: str | None = None,
    cache_mode: str | None = None,
    cache_ttl: str | None = None,
    previous_response_id: str | None = None,
    replay_items: list[object] | None = None,
    reasoning_context: str | None = None,
) -> dict[str, Any]:
    """Build a direct Responses request with stable prefix items before volatile input."""

    if not isinstance(model, str) or not model.strip():
        raise ValueError("Responses model must be a non-empty string")
    if not isinstance(stable_input, list) or not isinstance(volatile_input, list):
        raise ValueError("Responses stable_input and volatile_input must be arrays")
    if type(store) is not bool:
        raise ValueError("Responses store must be boolean")
    if previous_response_id and replay_items is not None:
        raise ValueError("Responses continuation must use previous_response_id or stateless replay, not both")
    if previous_response_id is not None and (not isinstance(previous_response_id, str) or not previous_response_id):
        raise ValueError("Responses previous_response_id must be a non-empty string")
    if replay_items is not None and (not isinstance(replay_items, list) or not replay_items):
        raise ValueError("Responses replay_items must be a non-empty array")
    if reasoning_context is not None and reasoning_context not in REASONING_CONTEXTS - {"unavailable"}:
        raise ValueError("Responses reasoning context is invalid")
    if cache_mode is not None and cache_mode not in {"implicit", "explicit"}:
        raise ValueError("Responses prompt cache mode must be implicit or explicit")
    if cache_ttl is not None and cache_ttl != "30m":
        raise ValueError("Responses prompt cache ttl must be 30m")
    if cache_ttl and not cache_mode:
        raise ValueError("Responses prompt cache ttl requires a cache mode")
    if prompt_cache_key is not None and (not isinstance(prompt_cache_key, str) or not prompt_cache_key):
        raise ValueError("Responses prompt_cache_key must be a non-empty string")

    normalized_tools = list(tools or [])
    _validate_tools(normalized_tools)
    ordered_input = [*(replay_items or []), *stable_input, *volatile_input]
    breakpoint_issues: list[str] = []
    breakpoints = _breakpoint_count(ordered_input, issues=breakpoint_issues)
    breakpoints += _breakpoint_count(normalized_tools, issues=breakpoint_issues)
    if breakpoint_issues:
        raise ValueError("; ".join(sorted(set(breakpoint_issues))))
    maximum_breakpoints = 4 if cache_mode == "explicit" else 3
    if breakpoints > maximum_breakpoints:
        raise ValueError(
            f"Responses {cache_mode or 'implicit'} cache mode allows at most {maximum_breakpoints} explicit breakpoints"
        )

    request: dict[str, Any] = {
        "model": model,
        "store": store,
        "input": ordered_input,
    }
    if normalized_tools:
        request["tools"] = normalized_tools
    if prompt_cache_key:
        request["prompt_cache_key"] = prompt_cache_key
    if cache_mode:
        options: dict[str, str] = {"mode": cache_mode}
        if cache_ttl:
            options["ttl"] = cache_ttl
        request["prompt_cache_options"] = options
    if previous_response_id:
        request["previous_response_id"] = previous_response_id
    if reasoning_context:
        request["reasoning"] = {"context": reasoning_context}
    return request


def request_metadata(
    request: dict[str, Any],
    *,
    replay_items: list[object] | None = None,
) -> dict[str, Any]:
    request_input = request.get("input")
    if replay_items is not None:
        if (
            request.get("store") is not False
            or request.get("previous_response_id")
            or not isinstance(request_input, list)
            or request_input[: len(replay_items)] != replay_items
        ):
            raise ValueError(
                "stateless replay evidence requires store false and the exact replay items as the input prefix"
            )
    tools = request.get("tools") if isinstance(request.get("tools"), list) else []
    programmatic = any(
        isinstance(tool, dict) and tool.get("type") == "programmatic_tool_calling"
        for tool in tools
    )
    options = request.get("prompt_cache_options")
    options = options if isinstance(options, dict) else {}
    cache_mode = options.get("mode") or (
        "implicit"
        if request.get("prompt_cache_key") or _breakpoint_count(request) > 0
        else "unavailable"
    )
    cache_ttl = options.get("ttl", "")
    reasoning = request.get("reasoning")
    reasoning = reasoning if isinstance(reasoning, dict) else {}
    if request.get("previous_response_id"):
        continuation = "previous-response-id"
    elif request.get("input") and request.get("store") is False and replay_items is not None:
        continuation = "stateless-replay"
    else:
        continuation = "none"
    replay_digest = _sha(replay_items) if replay_items is not None else ""
    clean_request = dict(request)
    new_input = (
        request_input[len(replay_items) :]
        if isinstance(request_input, list) and replay_items is not None
        else request_input
    )
    return {
        "body_sha256": hashlib.sha256(_canonical_bytes(clean_request)).hexdigest(),
        "model": str(request.get("model", "")),
        "store": request.get("store"),
        "prompt_cache_key_sha256": _secret_sha(request.get("prompt_cache_key")),
        "cache_mode": str(cache_mode),
        "cache_ttl": str(cache_ttl),
        "cache_breakpoint_count": _breakpoint_count(clean_request),
        "reasoning_context": str(reasoning.get("context", "unavailable")),
        "continuation_mode": continuation,
        "previous_response_id_sha256": _secret_sha(request.get("previous_response_id")),
        "replay_items_sha256": replay_digest,
        "programmatic_tool_calling": programmatic,
        "programmatic_tool_set_sha256": _sha(tools) if programmatic else "",
        "input_relationships": _relationships(new_input),
    }


def _usage(response: dict[str, Any]) -> dict[str, int | None]:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("Responses response.usage must be an object")
    input_details = usage.get("input_tokens_details")
    input_details = input_details if isinstance(input_details, dict) else {}
    output_details = usage.get("output_tokens_details")
    output_details = output_details if isinstance(output_details, dict) else {}
    result = {
        "input_tokens": usage.get("input_tokens"),
        "cached_tokens": input_details.get("cached_tokens"),
        "cache_write_tokens": input_details.get("cache_write_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": output_details.get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        if not _count(result[field]):
            raise ValueError(f"Responses usage.{field} must be an exact non-negative integer")
    for field in ("cached_tokens", "cache_write_tokens", "reasoning_tokens"):
        if result[field] is not None and not _count(result[field]):
            raise ValueError(f"Responses usage.{field} must be null or an exact non-negative integer")
    if result["total_tokens"] != result["input_tokens"] + result["output_tokens"]:
        raise ValueError("Responses usage total must equal input plus output")
    cache_values = [result["cached_tokens"], result["cache_write_tokens"]]
    if all(value is not None for value in cache_values) and sum(int(value) for value in cache_values) > int(result["input_tokens"]):
        raise ValueError("Responses cache read plus write tokens must not exceed input tokens")
    if result["reasoning_tokens"] is not None and result["reasoning_tokens"] > result["output_tokens"]:
        raise ValueError("Responses reasoning tokens must not exceed output tokens")
    return result


def _relationships(output: object) -> list[dict[str, str]]:
    if not isinstance(output, list):
        return []
    rows: list[dict[str, str]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if item_type not in {
            "program",
            "function_call",
            "function_call_output",
            "program_output",
            "message",
        }:
            continue
        caller = item.get("caller")
        caller_id = caller.get("caller_id") if isinstance(caller, dict) else ""
        caller_type = caller.get("type") if isinstance(caller, dict) else ""
        rows.append(
            {
                "type": item_type,
                "call_id_sha256": _secret_sha(item.get("call_id")),
                "caller_type": str(caller_type or ""),
                "caller_id_sha256": _secret_sha(caller_id),
                "status": str(item.get("status", "")),
            }
        )
    return rows


def validate_program_relationships(rows: object) -> list[str]:
    if not isinstance(rows, list):
        return ["OpenAI receipt program relationships must be an array"]
    issues: list[str] = []
    programs: set[str] = set()
    function_calls: dict[str, str] = {}
    function_outputs: set[str] = set()
    program_outputs: dict[str, str] = {}
    message_observed = False
    exact_observations: set[tuple[str, str, str, str, str]] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != RELATIONSHIP_FIELDS:
            issues.append(f"OpenAI receipt program relationships[{index}] has an invalid shape")
            continue
        item_type = row.get("type")
        call_id = row.get("call_id_sha256")
        caller_type = row.get("caller_type")
        caller_id = row.get("caller_id_sha256")
        status = row.get("status")
        if item_type not in {
            "program",
            "function_call",
            "function_call_output",
            "program_output",
            "message",
        }:
            issues.append(f"OpenAI receipt program relationships[{index}].type is invalid")
        if item_type in {"program", "program_output", "function_call", "function_call_output"} and not isinstance(call_id, str):
            issues.append(f"OpenAI receipt program relationships[{index}] call id is invalid")
        if isinstance(call_id, str) and call_id and not SHA256_RE.fullmatch(call_id):
            issues.append(f"OpenAI receipt program relationships[{index}] call id must be a SHA-256")
        if not isinstance(caller_type, str):
            issues.append(f"OpenAI receipt program relationships[{index}] caller type is invalid")
        elif caller_type not in {"", "program"}:
            issues.append(f"OpenAI receipt program relationships[{index}] caller type is unsupported")
        if not isinstance(status, str):
            issues.append(f"OpenAI receipt program relationships[{index}] status is invalid")
        elif item_type == "program_output" and status not in {"completed", "incomplete"}:
            issues.append("OpenAI receipt program output status must be completed or incomplete")
        elif item_type != "program_output" and status:
            issues.append(f"OpenAI receipt program relationships[{index}] status is allowed only for program_output")
        if item_type in {"function_call", "function_call_output"} and caller_id and caller_type != "program":
            issues.append("OpenAI receipt nested function relationship caller type must be program")
        observation = (
            str(item_type),
            str(call_id),
            str(caller_type),
            str(caller_id),
            str(status),
        )
        if observation in exact_observations:
            continue
        exact_observations.add(observation)
        if item_type == "program":
            if not call_id or call_id in programs:
                issues.append("OpenAI receipt program call ids must be unique and non-empty")
            programs.add(str(call_id))
        elif item_type == "function_call" and caller_id:
            if caller_id not in programs:
                issues.append("OpenAI receipt function call has an unknown program caller")
            if not call_id or (
                call_id in function_calls
                and function_calls.get(str(call_id)) != str(caller_id)
            ):
                issues.append("OpenAI receipt nested function call ids must be unique and non-empty")
            function_calls[str(call_id)] = str(caller_id)
        elif item_type == "function_call_output":
            if call_id not in function_calls:
                issues.append("OpenAI receipt function output has no prior nested function call")
            elif caller_id != function_calls.get(str(call_id)):
                issues.append("OpenAI receipt function output caller does not match its nested call")
            if not call_id or call_id in function_outputs:
                issues.append("OpenAI receipt function output ids must be unique and non-empty")
            function_outputs.add(str(call_id))
        elif item_type == "program_output":
            if call_id not in programs:
                issues.append("OpenAI receipt program output has no prior program call")
            if not call_id or (
                call_id in program_outputs
                and program_outputs.get(str(call_id)) != str(status)
            ):
                issues.append("OpenAI receipt program output call ids must be unique and non-empty")
            program_outputs[str(call_id)] = str(status)
        elif item_type == "message":
            message_observed = True
        if not isinstance(caller_id, str):
            issues.append(f"OpenAI receipt program relationships[{index}] caller id is invalid")
        elif caller_id and not SHA256_RE.fullmatch(caller_id):
            issues.append(f"OpenAI receipt program relationships[{index}] caller id must be a SHA-256")
    if programs and programs != set(program_outputs):
        issues.append("OpenAI receipt program outputs must match every program call exactly")
    if any(status != "completed" for status in program_outputs.values()):
        issues.append("OpenAI receipt completed program flow requires completed program outputs")
    if function_calls and set(function_calls) != function_outputs:
        issues.append("OpenAI receipt function outputs must match every nested function call exactly")
    if programs and not message_observed:
        issues.append("OpenAI receipt completed program flow requires a final assistant message")
    return issues


def _response_metadata(
    response: dict[str, Any],
    *,
    request_id: str,
    request_input: object,
) -> dict[str, Any]:
    usage = _usage(response)
    reasoning = response.get("reasoning")
    reasoning = reasoning if isinstance(reasoning, dict) else {}
    return {
        "response_id_sha256": _secret_sha(response.get("id")),
        "request_id_sha256": _secret_sha(request_id),
        "next_replay_items_sha256": _sha(
            [
                *(request_input if isinstance(request_input, list) else []),
                *(response.get("output") if isinstance(response.get("output"), list) else []),
            ]
        ),
        "model": str(response.get("model", "")),
        "status": str(response.get("status", "")),
        "effective_reasoning_context": str(reasoning.get("context", "unavailable")),
        "output_relationships": _relationships(response.get("output")),
        "usage": usage,
    }


def _aggregate_usage(rows: list[dict[str, int | None]]) -> dict[str, int | None]:
    aggregate: dict[str, int | None] = {}
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        aggregate[field] = sum(int(row[field] or 0) for row in rows)
    for field in ("cached_tokens", "cache_write_tokens", "reasoning_tokens"):
        aggregate[field] = (
            sum(int(row[field] or 0) for row in rows)
            if all(row[field] is not None for row in rows)
            else None
        )
    return aggregate


def build_run_receipt(
    *,
    run_id: str,
    capture_nonce: str,
    exchanges: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a sanitized receipt covering every Responses call in one benchmark run."""

    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("OpenAI receipt run_id must be a non-empty string")
    if not isinstance(capture_nonce, str) or not capture_nonce.strip():
        raise ValueError("OpenAI receipt capture_nonce must be a non-empty string")
    if not isinstance(exchanges, list) or not exchanges:
        raise ValueError("OpenAI run receipt requires every Responses exchange in order")
    calls: list[dict[str, Any]] = []
    for sequence, exchange in enumerate(exchanges, start=1):
        if not isinstance(exchange, dict) or set(exchange) - {
            "request",
            "response",
            "request_id",
            "replay_items",
        }:
            raise ValueError("OpenAI run receipt exchanges have an invalid shape")
        request = exchange.get("request")
        response = exchange.get("response")
        if not isinstance(request, dict) or not isinstance(response, dict):
            raise ValueError("OpenAI run receipt exchanges require request and response objects")
        request_id = exchange.get("request_id", "")
        if not isinstance(request_id, str):
            raise ValueError("OpenAI run receipt request_id must be a string")
        replay_items = exchange.get("replay_items")
        if replay_items is not None and not isinstance(replay_items, list):
            raise ValueError("OpenAI run receipt replay_items must be an array")
        calls.append(
            {
                "sequence": sequence,
                "request": request_metadata(request, replay_items=replay_items),
                "response": _response_metadata(
                    response,
                    request_id=request_id,
                    request_input=request.get("input"),
                ),
            }
        )
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_ID,
        "run_id": run_id,
        "capture_nonce": capture_nonce,
        "endpoint": ENDPOINT,
        "calls": calls,
        "usage": _aggregate_usage([call["response"]["usage"] for call in calls]),
    }
    issues = validate_receipt(receipt, expected_run_id=run_id)
    if issues:
        raise ValueError("invalid OpenAI Responses receipt: " + "; ".join(issues))
    return receipt


def _validate_request_metadata(request: object, *, label: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
        return [f"{label} has an invalid shape"]
    request = dict(request)
    for field in (
        "body_sha256",
        "model",
        "prompt_cache_key_sha256",
        "cache_ttl",
        "previous_response_id_sha256",
        "replay_items_sha256",
        "programmatic_tool_set_sha256",
    ):
        if not isinstance(request.get(field), str):
            issues.append(f"{label}.{field} must be a string")
    if not SHA256_RE.fullmatch(str(request.get("body_sha256", ""))):
        issues.append(f"{label}.body_sha256 must be a lowercase SHA-256")
    for field in (
        "prompt_cache_key_sha256",
        "previous_response_id_sha256",
        "replay_items_sha256",
        "programmatic_tool_set_sha256",
    ):
        digest = str(request.get(field, ""))
        if digest and not SHA256_RE.fullmatch(digest):
            issues.append(f"{label}.{field} must be empty or a lowercase SHA-256")
    if not isinstance(request.get("store"), bool):
        issues.append(f"{label}.store must be boolean")
    if request.get("cache_mode") not in CACHE_MODES:
        issues.append(f"{label}.cache_mode is invalid")
    if request.get("cache_ttl") not in {"", "30m"}:
        issues.append(f"{label}.cache_ttl must be empty or 30m")
    if request.get("cache_mode") == "unavailable" and (
        request.get("cache_ttl") or request.get("prompt_cache_key_sha256")
    ):
        issues.append(f"{label} unavailable cache mode must not claim cache controls")
    if request.get("reasoning_context") not in REASONING_CONTEXTS:
        issues.append(f"{label}.reasoning_context is invalid")
    continuation = request.get("continuation_mode")
    if continuation not in {"none", "previous-response-id", "stateless-replay"}:
        issues.append(f"{label}.continuation_mode is invalid")
    elif continuation == "previous-response-id" and (
        not request.get("previous_response_id_sha256") or request.get("replay_items_sha256")
    ):
        issues.append(f"{label} previous-response continuation requires only its response-id hash")
    elif continuation == "stateless-replay" and (
        not request.get("replay_items_sha256") or request.get("previous_response_id_sha256")
    ):
        issues.append(f"{label} stateless continuation requires only its replay-items hash")
    elif continuation == "none" and (
        request.get("previous_response_id_sha256") or request.get("replay_items_sha256")
    ):
        issues.append(f"{label} non-continuation must not contain continuation hashes")
    breakpoint_limit = 4 if request.get("cache_mode") == "explicit" else 3
    if (
        not _count(request.get("cache_breakpoint_count"))
        or int(request.get("cache_breakpoint_count", 0)) > breakpoint_limit
    ):
        issues.append(
            f"{label}.cache_breakpoint_count must be an integer from 0 through {breakpoint_limit}"
        )
    if not isinstance(request.get("programmatic_tool_calling"), bool):
        issues.append(f"{label}.programmatic_tool_calling must be boolean")
    elif request.get("programmatic_tool_calling") is not bool(request.get("programmatic_tool_set_sha256")):
        issues.append(f"{label} programmatic tool flag must match its tool-set hash")
    relationships = request.get("input_relationships")
    if not isinstance(relationships, list):
        issues.append(f"{label}.input_relationships must be an array")
    return issues


def _validate_response_metadata(response: object, *, label: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(response, dict) or set(response) != RESPONSE_FIELDS:
        return [f"{label} has an invalid shape"]
    response = dict(response)
    for field in (
        "response_id_sha256",
        "request_id_sha256",
        "next_replay_items_sha256",
        "model",
    ):
        if not isinstance(response.get(field), str):
            issues.append(f"{label}.{field} must be a string")
    if not SHA256_RE.fullmatch(str(response.get("response_id_sha256", ""))):
        issues.append(f"{label}.response_id_sha256 must be a lowercase SHA-256")
    request_id_digest = str(response.get("request_id_sha256", ""))
    if request_id_digest and not SHA256_RE.fullmatch(request_id_digest):
        issues.append(f"{label}.request_id_sha256 must be empty or a lowercase SHA-256")
    if not SHA256_RE.fullmatch(str(response.get("next_replay_items_sha256", ""))):
        issues.append(f"{label}.next_replay_items_sha256 must be a lowercase SHA-256")
    if not isinstance(response.get("model"), str) or not str(response.get("model", "")).strip():
        issues.append(f"{label}.model must be a non-empty string")
    if response.get("status") not in RESPONSE_STATUSES:
        issues.append(f"{label}.status is invalid")
    elif response.get("status") != "completed":
        issues.append(f"{label} must be completed for full-run evidence")
    if response.get("effective_reasoning_context") not in REASONING_CONTEXTS:
        issues.append(f"{label}.effective_reasoning_context is invalid")
    if not isinstance(response.get("output_relationships"), list):
        issues.append(f"{label}.output_relationships must be an array")
    usage = response.get("usage")
    if not isinstance(usage, dict) or set(usage) != USAGE_FIELDS:
        issues.append(f"{label}.usage has an invalid shape")
    else:
        try:
            _usage(
                {
                    "usage": {
                        "input_tokens": usage["input_tokens"],
                        "input_tokens_details": {
                            "cached_tokens": usage["cached_tokens"],
                            "cache_write_tokens": usage["cache_write_tokens"],
                        },
                        "output_tokens": usage["output_tokens"],
                        "output_tokens_details": {"reasoning_tokens": usage["reasoning_tokens"]},
                        "total_tokens": usage["total_tokens"],
                    }
                }
            )
        except ValueError as exc:
            issues.append(str(exc))
    return issues


def validate_receipt(value: object, *, expected_run_id: str | None) -> list[str]:
    if not isinstance(value, dict):
        return ["OpenAI Responses receipt must be an object"]
    issues: list[str] = []
    if set(value) != RECEIPT_FIELDS:
        for field in sorted(RECEIPT_FIELDS - set(value)):
            issues.append(f"OpenAI Responses receipt.{field} is required")
        for field in sorted(set(value) - RECEIPT_FIELDS):
            issues.append(f"OpenAI Responses receipt.{field} is not allowed")
    if type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
        issues.append("OpenAI Responses receipt.schema_version must be the integer 1")
    if value.get("tool") != TOOL_ID or value.get("endpoint") != ENDPOINT:
        issues.append("OpenAI Responses receipt tool or endpoint is invalid")
    if expected_run_id is None or not expected_run_id.strip():
        issues.append("OpenAI Responses evidence requires an out-of-band expected benchmark run id")
    elif value.get("run_id") != expected_run_id:
        issues.append("OpenAI Responses receipt.run_id does not match the benchmark report")
    for field in ("run_id", "capture_nonce"):
        if not isinstance(value.get(field), str) or not str(value.get(field)).strip():
            issues.append(f"OpenAI Responses receipt.{field} must be a non-empty string")
    calls = value.get("calls")
    if not isinstance(calls, list) or not calls or len(calls) > 1000:
        issues.append("OpenAI Responses receipt.calls must contain 1 through 1000 calls")
        calls = []
    usages: list[dict[str, int | None]] = []
    relationships: list[dict[str, str]] = []
    previous_response_hash = ""
    previous_replay_hash = ""
    previous_store = False
    response_hashes: set[str] = set()
    program_requested = False
    for index, call in enumerate(calls, start=1):
        label = f"OpenAI Responses receipt.calls[{index - 1}]"
        if not isinstance(call, dict) or set(call) != CALL_FIELDS:
            issues.append(f"{label} has an invalid shape")
            continue
        if type(call.get("sequence")) is not int or call.get("sequence") != index:
            issues.append(f"{label}.sequence must be the exact one-based call order")
        request = call.get("request")
        response = call.get("response")
        issues.extend(_validate_request_metadata(request, label=f"{label}.request"))
        issues.extend(_validate_response_metadata(response, label=f"{label}.response"))
        if not isinstance(request, dict) or not isinstance(response, dict):
            continue
        if index == 1 and request.get("continuation_mode") != "none":
            issues.append("OpenAI Responses full-run receipt must start with an uncontinued request")
        if request.get("continuation_mode") == "previous-response-id":
            if not previous_store:
                issues.append(
                    f"{label} previous_response_id requires the immediately preceding call to be stored"
                )
            if request.get("previous_response_id_sha256") != previous_response_hash:
                issues.append(
                    f"{label} previous_response_id does not bind the immediately preceding call"
                )
        if (
            request.get("continuation_mode") == "stateless-replay"
            and request.get("replay_items_sha256") != previous_replay_hash
        ):
            issues.append(
                f"{label} stateless replay does not bind the immediately preceding complete history"
            )
        previous_response_hash = str(response.get("response_id_sha256", ""))
        previous_store = request.get("store") is True
        if previous_response_hash in response_hashes:
            issues.append("OpenAI Responses receipt response ids must be unique")
        response_hashes.add(previous_response_hash)
        previous_replay_hash = str(response.get("next_replay_items_sha256", ""))
        program_requested = program_requested or request.get("programmatic_tool_calling") is True
        input_rows = request.get("input_relationships")
        output_rows = response.get("output_relationships")
        if isinstance(input_rows, list):
            relationships.extend(row for row in input_rows if isinstance(row, dict))
        if isinstance(output_rows, list):
            relationships.extend(row for row in output_rows if isinstance(row, dict))
        usage = response.get("usage")
        if isinstance(usage, dict) and set(usage) == USAGE_FIELDS:
            usages.append(usage)
    issues.extend(validate_program_relationships(relationships))
    program_observed = any(row.get("type") == "program" for row in relationships)
    if program_observed and not program_requested:
        issues.append("OpenAI Responses program output requires requested programmatic tool calling")
    receipt_usage = value.get("usage")
    if not isinstance(receipt_usage, dict) or set(receipt_usage) != USAGE_FIELDS:
        issues.append("OpenAI Responses receipt.usage has an invalid shape")
    elif len(usages) == len(calls) and receipt_usage != _aggregate_usage(usages):
        issues.append("OpenAI Responses receipt usage does not equal every recorded call")
    return sorted(set(issues))


def receipt_usage(receipt: dict[str, Any]) -> dict[str, int | None]:
    usage = receipt["usage"]
    return {
        "input_tokens": usage["input_tokens"],
        "cache_read_input_tokens": usage["cached_tokens"],
        "cache_write_input_tokens": usage["cache_write_tokens"],
        "output_tokens": usage["output_tokens"],
        "reasoning_output_tokens": usage["reasoning_tokens"],
        "total_tokens": usage["total_tokens"],
    }


def attested_capabilities(receipt: dict[str, Any]) -> list[str]:
    """Return only capabilities demonstrated across the complete direct run."""

    if validate_receipt(receipt, expected_run_id=str(receipt.get("run_id", ""))):
        return []
    calls = receipt["calls"]
    capabilities = ["per-call-usage"]
    usage = receipt["usage"]
    if usage["cached_tokens"] is not None and usage["cache_write_tokens"] is not None:
        capabilities.append("prompt-cache-telemetry")
    if any(call["request"]["cache_mode"] != "unavailable" for call in calls):
        capabilities.append("prompt-cache-control")
    continued = [call for call in calls if call["request"]["continuation_mode"] != "none"]
    if continued and all(
        call["response"]["effective_reasoning_context"]
        == call["request"]["reasoning_context"]
        and call["request"]["reasoning_context"] != "unavailable"
        for call in continued
    ):
        capabilities.append("reasoning-continuation")
    relationships = [
        row
        for call in calls
        for field in ("input_relationships",)
        for row in call["request"][field]
    ] + [
        row
        for call in calls
        for row in call["response"]["output_relationships"]
    ]
    if any(row["type"] == "program" for row in relationships):
        capabilities.append("hosted-program-orchestration")
    return capabilities
