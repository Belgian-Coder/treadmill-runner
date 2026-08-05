"""Benchmark metric, quality, and comparison helpers."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from benchmark_determinism import (
    classify_mismatch,
    normalize_determinism,
    normalize_evidence_tiers,
)
from .benchmark_common_contracts import (
    EXECUTION_TRACE_V1_ACTOR_PATTERN,
    EXECUTION_TRACE_V1_CONTEXT_INHERITANCE,
    EXECUTION_TRACE_V1_EVENT_FIELDS,
    EXECUTION_TRACE_V1_EVENT_KINDS,
    EXECUTION_TRACE_V1_FIELDS,
    EXECUTION_TRACE_V1_MAX_EVENTS,
    EXECUTION_TRACE_V1_NEGATIVE_COUNT_KEYS,
    EXECUTION_TRACE_V1_NEUTRAL_COUNT_KEYS,
    EXECUTION_TRACE_V1_OPERATION_PATTERN,
    EXECUTION_TRACE_V1_SCOPE_STATES,
    EXECUTION_TRACE_V1_SHA256_PATTERN,
    EXECUTION_TRACE_V1_SUMMARY_FIELDS,
    EXECUTION_TRACE_V1_SUMMARY_TOOL,
    EXECUTION_TRACE_V1_TOOL,
    FAILURE_TAXONOMY_CATEGORIES,
    RUN_CONFIG_COMPARE_KEYS,
    SCHEMA_VERSION,
    STANDARD_AGENT_BOOL_METRICS,
    STANDARD_AGENT_NUMERIC_METRICS,
    STANDARD_BOOL_METRICS,
    STANDARD_NUMERIC_METRICS,
    TOOL_NAME,
    TRAJECTORY_SIGNAL_COUNT_KEYS,
)
from . import token_measurement_v1

def percentile(values: list[float], percent: float) -> float | None:
    clean = sorted(value for value in values if not math.isnan(value))
    if not clean:
        return None
    if len(clean) == 1:
        return round(clean[0], 4)
    rank = (len(clean) - 1) * percent
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(clean[int(rank)], 4)
    weight = rank - lower
    return round(clean[lower] * (1 - weight) + clean[upper] * weight, 4)


def metric_distribution(values: list[float]) -> dict[str, Any]:
    clean = [float(value) for value in values if not math.isnan(float(value))]
    if not clean:
        return {"samples": 0, "min": None, "mean": None, "p50": None, "p95": None, "max": None}
    return {
        "samples": len(clean),
        "min": round(min(clean), 4),
        "mean": round(sum(clean) / len(clean), 4),
        "p50": percentile(clean, 0.50),
        "p95": percentile(clean, 0.95),
        "max": round(max(clean), 4),
    }


def metrics_standard_from_timings(
    *,
    wall_seconds: float | None = None,
    prompt_eval_ms: float | None = None,
    decode_ms: float | None = None,
    model_load_ms: float | None = None,
    prompt_tokens: int | None = None,
    generated_tokens: int | None = None,
    peak_memory_mib: float | None = None,
    cpu_utilization_percent: float | None = None,
    cold_start: bool | None = None,
    warm_cache: bool | None = None,
    repetitions: int = 1,
) -> dict[str, Any]:
    e2e_ms = wall_seconds * 1000 if wall_seconds is not None else None
    generated = int(generated_tokens or 0)
    prompt = int(prompt_tokens or 0)
    tpot_ms = (decode_ms / generated) if decode_ms is not None and generated > 0 else None
    output_tps = (generated / (decode_ms / 1000)) if decode_ms and generated > 0 else None
    prompt_tps = (prompt / (prompt_eval_ms / 1000)) if prompt_eval_ms and prompt > 0 else None
    ttft_parts = [value for value in (model_load_ms, prompt_eval_ms) if value is not None]
    return normalize_metrics_standard(
        {
            "ttft_ms": sum(ttft_parts) if ttft_parts else None,
            "tpot_ms": tpot_ms,
            "itl_ms": tpot_ms,
            "e2e_latency_ms": e2e_ms,
            "model_load_ms": model_load_ms,
            "prompt_eval_ms": prompt_eval_ms,
            "decode_ms": decode_ms,
            "request_throughput_rps": (1000 / e2e_ms) if e2e_ms and e2e_ms > 0 else None,
            "output_throughput_tps": output_tps,
            "prompt_throughput_tps": prompt_tps,
            "peak_memory_mib": peak_memory_mib,
            "cpu_utilization_percent": cpu_utilization_percent,
            "cold_start": cold_start,
            "warm_cache": warm_cache,
            "repetitions": repetitions,
        }
    )


def normalize_metrics_standard(value: Any | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key in STANDARD_NUMERIC_METRICS or key == "prompt_throughput_tps":
            normalized[key] = None if item is None or item == "" else round(float(item), 4)
        elif key in STANDARD_BOOL_METRICS:
            normalized[key] = None if item is None else bool(item)
        elif key == "repetitions":
            normalized[key] = max(1, int(item or 1))
        elif key in {"p50", "p95"} and isinstance(item, dict):
            normalized[key] = {
                sub_key: (None if sub_value is None or sub_value == "" else round(float(sub_value), 4))
                for sub_key, sub_value in item.items()
            }
        else:
            normalized[key] = item
    return normalized


def aggregate_standard_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate: dict[str, Any] = {"repetitions": len(samples)}
    for key in sorted(STANDARD_NUMERIC_METRICS | {"prompt_throughput_tps"}):
        values: list[float] = []
        for sample in samples:
            value = sample.get(key)
            if isinstance(value, (int, float)):
                values.append(float(value))
        if values:
            aggregate[key] = round(sum(values) / len(values), 4)
            aggregate.setdefault("p50", {})[key] = metric_distribution(values)["p50"]
            aggregate.setdefault("p95", {})[key] = metric_distribution(values)["p95"]
    aggregate["cold_start"] = any(sample.get("cold_start") is True for sample in samples)
    aggregate["warm_cache"] = any(sample.get("warm_cache") is True for sample in samples)
    return aggregate


def validate_metrics_standard(value: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(value, dict):
        return ["metrics_standard must be an object"]
    for key in STANDARD_NUMERIC_METRICS | {"prompt_throughput_tps"}:
        if key in value and value[key] is not None:
            try:
                number = float(value[key])
            except (TypeError, ValueError):
                issues.append(f"metrics_standard.{key} must be numeric or null")
                continue
            if number < 0:
                issues.append(f"metrics_standard.{key} must be non-negative")
    for key in STANDARD_BOOL_METRICS:
        if key in value and value[key] is not None and not isinstance(value[key], bool):
            issues.append(f"metrics_standard.{key} must be boolean or null")
    if "repetitions" in value:
        try:
            if int(value["repetitions"]) < 1:
                issues.append("metrics_standard.repetitions must be at least 1")
        except (TypeError, ValueError):
            issues.append("metrics_standard.repetitions must be an integer")
    for key in ("p50", "p95"):
        if key in value and value[key] is not None and not isinstance(value[key], dict):
            issues.append(f"metrics_standard.{key} must be an object or null")
    return issues


def normalize_run_config(value: Any | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    normalized = {str(key): item for key, item in value.items()}
    for key in ("threads", "context_size", "batch_size", "seed", "output_cap"):
        if key in normalized and normalized[key] not in ("", None):
            normalized[key] = int(normalized[key])
    for key in ("temperature",):
        if key in normalized and normalized[key] not in ("", None):
            normalized[key] = float(normalized[key])
    return normalized


def validate_run_config(value: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(value, dict):
        return ["run_config must be an object"]
    for key in ("threads", "context_size", "batch_size", "output_cap"):
        if key in value and value[key] is not None:
            try:
                if int(value[key]) < 0:
                    issues.append(f"run_config.{key} must be non-negative")
            except (TypeError, ValueError):
                issues.append(f"run_config.{key} must be an integer")
    for key in (
        "model_hash",
        "runtime_hash",
        "prompt_version",
        "suite_version",
        "verifier_version",
        "embedding_profile",
        "retrieval_backend",
        "vector_state",
        "hybrid_weight_preset",
        "chunking_version",
        "query_scope",
        "git_ref",
        "dirty_state",
    ):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            issues.append(f"run_config.{key} must be a string")
    return issues


def normalize_agent_task_metrics(
    value: Any | None,
    *,
    grounding: dict[str, Any] | None = None,
    commands: list[Any] | None = None,
    checks: list[Any] | None = None,
    failures: list[Any] | None = None,
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    grounding = grounding or {}
    coverage = grounding.get("evidence_coverage") if isinstance(grounding.get("evidence_coverage"), dict) else {}
    return {
        "pass_at_1": float(raw.get("pass_at_1", 1.0 if not failures else 0.0)),
        "attempts": int(raw.get("attempts", 1)),
        "verifier_passed": bool(raw.get("verifier_passed", not failures)),
        "tool_call_count": int(raw.get("tool_call_count", len(commands or []))),
        "tool_retry_count": int(raw.get("tool_retry_count", 0)),
        "trajectory_complete": bool(raw.get("trajectory_complete", True)),
        "unsupported_claim_count": int(
            raw.get("unsupported_claim_count", len(grounding.get("unsupported_claims", []) if isinstance(grounding.get("unsupported_claims"), list) else []))
        ),
        "evidence_coverage_percent": float(raw.get("evidence_coverage_percent", coverage.get("coverage_percent", 0))),
        **{str(key): item for key, item in raw.items() if key not in STANDARD_AGENT_BOOL_METRICS | STANDARD_AGENT_NUMERIC_METRICS},
    }


def validate_agent_task_metrics(value: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(value, dict):
        return ["agent_task_metrics must be an object"]
    for key in STANDARD_AGENT_BOOL_METRICS:
        if key in value and not isinstance(value[key], bool):
            issues.append(f"agent_task_metrics.{key} must be boolean")
    for key in STANDARD_AGENT_NUMERIC_METRICS:
        if key not in value:
            continue
        try:
            number = float(value[key])
        except (TypeError, ValueError):
            issues.append(f"agent_task_metrics.{key} must be numeric")
            continue
        if number < 0:
            issues.append(f"agent_task_metrics.{key} must be non-negative")
        if key in {"pass_at_1"} and number > 1:
            issues.append(f"agent_task_metrics.{key} must be between 0 and 1")
        if key == "evidence_coverage_percent" and number > 100:
            issues.append("agent_task_metrics.evidence_coverage_percent must be between 0 and 100")
    return issues


def _compact_row_text(rows: list[Any]) -> str:
    try:
        return json.dumps(rows, sort_keys=True, default=str).lower()
    except TypeError:
        return str(rows).lower()


def _count_text_hits(rows: list[Any], terms: tuple[str, ...]) -> int:
    total = 0
    for row in rows:
        text = _compact_row_text([row])
        if any(term in text for term in terms):
            total += 1
    return total


def _trace_v1_non_negative_integer(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= (2**63 - 1)
    )


def _trace_v1_fingerprint(value: object, *, required: bool) -> bool:
    if not isinstance(value, str):
        return False
    if not value:
        return not required
    return EXECUTION_TRACE_V1_SHA256_PATTERN.fullmatch(value) is not None


def validate_execution_trace_v1(value: Any) -> list[str]:
    """Validate a portable, content-free execution trace before deriving metrics."""

    if not isinstance(value, dict):
        return ["execution_trace_v1 must be an object"]
    issues: list[str] = []
    missing = EXECUTION_TRACE_V1_FIELDS - set(value)
    unknown = set(value) - EXECUTION_TRACE_V1_FIELDS
    issues.extend(
        f"execution_trace_v1.{field} is required" for field in sorted(missing)
    )
    issues.extend(
        f"execution_trace_v1.{field} is not allowed"
        for field in sorted(unknown, key=str)
    )
    if type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
        issues.append("execution_trace_v1.schema_version must be the integer 1")
    if value.get("tool") != EXECUTION_TRACE_V1_TOOL:
        issues.append(f"execution_trace_v1.tool must be {EXECUTION_TRACE_V1_TOOL}")
    root_actor_id = value.get("root_actor_id")
    if (
        not isinstance(root_actor_id, str)
        or EXECUTION_TRACE_V1_ACTOR_PATTERN.fullmatch(root_actor_id) is None
    ):
        issues.append("execution_trace_v1.root_actor_id is invalid")
        root_actor_id = ""
    events = value.get("events")
    if not isinstance(events, list):
        return [*issues, "execution_trace_v1.events must be an array"]
    if len(events) > EXECUTION_TRACE_V1_MAX_EVENTS:
        issues.append(
            "execution_trace_v1.events must contain at most "
            f"{EXECUTION_TRACE_V1_MAX_EVENTS} events"
        )

    known_actors = {root_actor_id} if root_actor_id else set()
    actor_depths = {root_actor_id: 0} if root_actor_id else {}
    previous_elapsed_ms = -1
    previous_round = 0
    for index, raw_event in enumerate(events, start=1):
        label = f"execution_trace_v1.events[{index - 1}]"
        if not isinstance(raw_event, dict) or set(raw_event) != EXECUTION_TRACE_V1_EVENT_FIELDS:
            issues.append(f"{label} has an invalid shape")
            continue
        event = raw_event
        if type(event.get("sequence")) is not int or event.get("sequence") != index:
            issues.append(f"{label}.sequence must be the exact one-based event order")
        elapsed_ms = event.get("elapsed_ms")
        if not _trace_v1_non_negative_integer(elapsed_ms):
            issues.append(f"{label}.elapsed_ms must be an exact non-negative integer")
        elif int(elapsed_ms) < previous_elapsed_ms:
            issues.append(f"{label}.elapsed_ms must be non-decreasing")
        else:
            previous_elapsed_ms = int(elapsed_ms)
        round_number = event.get("round")
        if (
            not _trace_v1_non_negative_integer(round_number)
            or int(round_number) < 1
        ):
            issues.append(f"{label}.round must be an exact positive integer")
        elif previous_round == 0 and int(round_number) != 1:
            issues.append(f"{label}.round must start at 1")
            previous_round = int(round_number)
        elif previous_round and int(round_number) not in {previous_round, previous_round + 1}:
            issues.append(f"{label}.round must be non-decreasing without gaps")
            previous_round = int(round_number)
        else:
            previous_round = int(round_number)
        raw_kind = event.get("kind")
        kind = raw_kind if isinstance(raw_kind, str) else ""
        if kind not in EXECUTION_TRACE_V1_EVENT_KINDS:
            issues.append(f"{label}.kind is invalid")
        actor_id = event.get("actor_id")
        actor_valid = (
            isinstance(actor_id, str)
            and EXECUTION_TRACE_V1_ACTOR_PATTERN.fullmatch(actor_id) is not None
        )
        if not actor_valid:
            issues.append(f"{label}.actor_id is invalid")
        elif actor_id not in known_actors:
            issues.append(f"{label}.actor_id must be known before the event")
        operation = event.get("operation")
        if (
            not isinstance(operation, str)
            or EXECUTION_TRACE_V1_OPERATION_PATTERN.fullmatch(operation) is None
        ):
            issues.append(f"{label}.operation is invalid")
        input_required = kind in {"command", "read", "validation"}
        result_required = kind in {"read", "validation"}
        if not _trace_v1_fingerprint(
            event.get("input_fingerprint"), required=input_required
        ):
            issues.append(
                f"{label}.input_fingerprint must be "
                + ("a lowercase SHA-256" if input_required else "empty or a lowercase SHA-256")
            )
        if not _trace_v1_fingerprint(
            event.get("result_fingerprint"), required=result_required
        ):
            issues.append(
                f"{label}.result_fingerprint must be "
                + ("a lowercase SHA-256" if result_required else "empty or a lowercase SHA-256")
            )
        scope = event.get("scope")
        if not isinstance(scope, str) or scope not in EXECUTION_TRACE_V1_SCOPE_STATES:
            issues.append(f"{label}.scope is invalid")
        if type(event.get("material")) is not bool:
            issues.append(f"{label}.material must be boolean")

        target_actor_id = event.get("target_actor_id")
        authorized = event.get("authorized")
        inheritance = event.get("context_inheritance")
        if kind == "spawn":
            target_valid = (
                isinstance(target_actor_id, str)
                and EXECUTION_TRACE_V1_ACTOR_PATTERN.fullmatch(target_actor_id) is not None
            )
            if not target_valid:
                issues.append(f"{label}.target_actor_id is invalid for a spawn")
            elif target_actor_id in known_actors:
                issues.append(f"{label}.target_actor_id must be a new actor")
            if type(authorized) is not bool:
                issues.append(f"{label}.authorized must be boolean for a spawn")
            if (
                not isinstance(inheritance, str)
                or inheritance
                not in EXECUTION_TRACE_V1_CONTEXT_INHERITANCE - {"not-applicable"}
            ):
                issues.append(f"{label}.context_inheritance is invalid for a spawn")
            if (
                target_valid
                and target_actor_id not in known_actors
                and actor_valid
                and actor_id in actor_depths
            ):
                known_actors.add(target_actor_id)
                actor_depths[target_actor_id] = actor_depths[actor_id] + 1
        else:
            if target_actor_id is not None:
                issues.append(f"{label}.target_actor_id is allowed only for a spawn")
            if authorized is not None:
                issues.append(f"{label}.authorized is allowed only for a spawn")
            if inheritance != "not-applicable":
                issues.append(
                    f"{label}.context_inheritance must be not-applicable outside a spawn"
                )
    return sorted(set(issues))


def summarize_execution_trace_v1(value: Any) -> dict[str, Any]:
    """Return deterministic overthinking and neutral metrics for a valid V1 trace."""

    issues = validate_execution_trace_v1(value)
    if issues:
        raise ValueError("invalid execution_trace_v1: " + "; ".join(issues))
    assert isinstance(value, dict)
    events = value["events"]
    root_actor_id = value["root_actor_id"]
    actor_depths: dict[str, int] = {root_actor_id: 0}
    command_inputs: set[tuple[str, str, str]] = set()
    read_results: set[tuple[str, str, str, str]] = set()
    validation_results: set[tuple[str, str, str, str]] = set()
    kind_counts = {kind: 0 for kind in EXECUTION_TRACE_V1_EVENT_KINDS}
    negative_counts = {
        key: 0 for key in sorted(EXECUTION_TRACE_V1_NEGATIVE_COUNT_KEYS)
    }
    material_action_count = 0
    time_to_first_material_action_ms: int | None = None
    rounds: set[int] = set()
    for event in events:
        kind = str(event["kind"])
        kind_counts[kind] += 1
        rounds.add(int(event["round"]))
        if event["material"] is True:
            material_action_count += 1
            if time_to_first_material_action_ms is None:
                time_to_first_material_action_ms = int(event["elapsed_ms"])
        if event["scope"] == "excess":
            negative_counts["scope_excess_count"] += 1
        operation = str(event["operation"])
        actor_id = str(event["actor_id"])
        input_fingerprint = str(event["input_fingerprint"])
        result_fingerprint = str(event["result_fingerprint"])
        if kind == "command":
            command_key = (actor_id, operation, input_fingerprint)
            if command_key in command_inputs:
                negative_counts["duplicate_command_count"] += 1
            command_inputs.add(command_key)
        elif kind == "read":
            read_key = (actor_id, operation, input_fingerprint, result_fingerprint)
            if read_key in read_results:
                negative_counts["unchanged_read_count"] += 1
            read_results.add(read_key)
        elif kind == "validation":
            validation_key = (
                actor_id,
                operation,
                input_fingerprint,
                result_fingerprint,
            )
            if validation_key in validation_results:
                negative_counts["unchanged_validation_count"] += 1
            validation_results.add(validation_key)
        elif kind == "spawn":
            target_actor_id = str(event["target_actor_id"])
            if event["authorized"] is False:
                negative_counts["unauthorized_spawn_count"] += 1
            if actor_depths[actor_id] > 0:
                negative_counts["recursive_spawn_count"] += 1
            if event["context_inheritance"] == "unknown":
                negative_counts["unknown_context_inheritance_count"] += 1
            actor_depths[target_actor_id] = actor_depths[actor_id] + 1
    summary = {
        "schema_version": 1,
        "tool": EXECUTION_TRACE_V1_SUMMARY_TOOL,
        "method": "trace-derived-v1",
        "event_count": len(events),
        "action_count": kind_counts["action"],
        "command_count": kind_counts["command"],
        "read_count": kind_counts["read"],
        "validation_count": kind_counts["validation"],
        "spawn_count": kind_counts["spawn"],
        "compaction_count": kind_counts["compaction"],
        "observation_count": kind_counts["observation"],
        "material_action_count": material_action_count,
        "round_count": len(rounds),
        "max_depth": max(actor_depths.values(), default=0),
        "time_to_first_material_action_ms": time_to_first_material_action_ms,
        **negative_counts,
    }
    summary_issues = validate_execution_trace_summary_v1(summary)
    if summary_issues:
        raise AssertionError("invalid derived execution trace summary: " + "; ".join(summary_issues))
    return summary


def validate_execution_trace_summary_v1(value: Any) -> list[str]:
    """Validate a persisted summary without trusting self-authored counts."""

    if not isinstance(value, dict):
        return ["execution trace summary must be an object"]
    issues: list[str] = []
    if set(value) != EXECUTION_TRACE_V1_SUMMARY_FIELDS:
        issues.extend(
            f"execution trace summary.{field} is required"
            for field in sorted(EXECUTION_TRACE_V1_SUMMARY_FIELDS - set(value))
        )
        issues.extend(
            f"execution trace summary.{field} is not allowed"
            for field in sorted(
                set(value) - EXECUTION_TRACE_V1_SUMMARY_FIELDS, key=str
            )
        )
    if type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
        issues.append("execution trace summary.schema_version must be the integer 1")
    if value.get("tool") != EXECUTION_TRACE_V1_SUMMARY_TOOL:
        issues.append(
            f"execution trace summary.tool must be {EXECUTION_TRACE_V1_SUMMARY_TOOL}"
        )
    if value.get("method") != "trace-derived-v1":
        issues.append("execution trace summary.method must be trace-derived-v1")
    for key in sorted(
        EXECUTION_TRACE_V1_NEGATIVE_COUNT_KEYS
        | EXECUTION_TRACE_V1_NEUTRAL_COUNT_KEYS
    ):
        if not _trace_v1_non_negative_integer(value.get(key)):
            issues.append(f"execution trace summary.{key} must be a non-negative integer")
    first_action_ms = value.get("time_to_first_material_action_ms")
    if first_action_ms is not None and not _trace_v1_non_negative_integer(first_action_ms):
        issues.append(
            "execution trace summary.time_to_first_material_action_ms must be null or a non-negative integer"
        )
    if issues:
        return sorted(set(issues))
    event_count = int(value["event_count"])
    kind_total = sum(
        int(value[key])
        for key in (
            "action_count",
            "command_count",
            "read_count",
            "validation_count",
            "spawn_count",
            "compaction_count",
            "observation_count",
        )
    )
    if event_count != kind_total:
        issues.append("execution trace summary event count must equal its kind counts")
    if int(value["material_action_count"]) > event_count:
        issues.append("execution trace summary material actions must not exceed events")
    if (int(value["material_action_count"]) == 0) is not (first_action_ms is None):
        issues.append(
            "execution trace summary first material action time must be available exactly when a material action exists"
        )
    expected_round_constraint = (
        int(value["round_count"]) == 0 if event_count == 0 else 1 <= int(value["round_count"]) <= event_count
    )
    if not expected_round_constraint:
        issues.append("execution trace summary round count is inconsistent with events")
    if int(value["max_depth"]) > int(value["spawn_count"]):
        issues.append("execution trace summary max depth must not exceed spawn count")
    upper_bounds = {
        "duplicate_command_count": max(int(value["command_count"]) - 1, 0),
        "unchanged_read_count": max(int(value["read_count"]) - 1, 0),
        "unchanged_validation_count": max(int(value["validation_count"]) - 1, 0),
        "unauthorized_spawn_count": int(value["spawn_count"]),
        "recursive_spawn_count": int(value["spawn_count"]),
        "unknown_context_inheritance_count": int(value["spawn_count"]),
        "scope_excess_count": event_count,
    }
    for key, upper_bound in upper_bounds.items():
        if int(value[key]) > upper_bound:
            issues.append(f"execution trace summary.{key} exceeds its event bound")
    return sorted(set(issues))


def normalize_trajectory_signals(
    value: Any | None,
    *,
    quality: dict[str, Any] | None = None,
    commands: list[Any] | None = None,
    checks: list[Any] | None = None,
    skipped: list[Any] | None = None,
    failures: list[Any] | None = None,
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    trace_summary: dict[str, Any] | None = None
    if raw.get("execution_trace_v1") is not None:
        trace_summary = summarize_execution_trace_v1(raw["execution_trace_v1"])
    command_rows = commands or []
    check_rows = checks or []
    skipped_rows = skipped or []
    failure_rows = failures or []
    quality_passed = bool((quality or {}).get("passed", not failure_rows))
    failed_check_count = sum(1 for item in check_rows if isinstance(item, dict) and item.get("ok") is False)
    failed_command_count = sum(
        1
        for item in command_rows
        if isinstance(item, dict) and str(item.get("status", "")).lower() in {"failed", "error", "timeout", "blocked"}
    )
    derived = {
        "misalignment_count": _count_text_hits(failure_rows + skipped_rows, ("misalign", "wrong owner", "wrong tool", "unsupported claim")),
        "stagnation_count": _count_text_hits(failure_rows + skipped_rows, ("stagnat", "no progress", "repeated", "same failure")),
        "redundant_verification_count": _count_text_hits(
            failure_rows + skipped_rows + command_rows,
            ("redundant verification", "duplicate validation", "proof of proof", "repeated validation"),
        ),
        "unchanged_evidence_cycle_count": _count_text_hits(
            failure_rows + skipped_rows + command_rows,
            ("unchanged evidence", "same evidence", "same verdict", "no material delta"),
        ),
        "scope_expansion_count": _count_text_hits(
            failure_rows + skipped_rows,
            ("scope expansion", "scope-expanded", "outside authorized scope"),
        ),
        "overbuild_count": _count_text_hits(
            failure_rows + skipped_rows,
            ("overbuild", "premature abstraction", "unnecessary layer", "duplicate skill"),
        ),
        "non_material_review_count": _count_text_hits(
            failure_rows + skipped_rows + command_rows,
            ("non-material review", "non material review", "no material delta"),
        ),
        "disengagement_count": _count_text_hits(failure_rows + skipped_rows, ("gave up", "abandoned", "stopped early", "incomplete")),
        "satisfaction_count": 1 if quality_passed and not failure_rows and not skipped_rows else 0,
        "execution_failure_count": len(failure_rows) + failed_check_count + failed_command_count,
        "loop_count": _count_text_hits(failure_rows + skipped_rows + command_rows, ("loop", "cycle", "repeated command")),
        "environment_exhaustion_count": _count_text_hits(
            failure_rows + skipped_rows,
            ("context", "token", "budget", "out of memory", "oom", "disk full", "quota"),
        ),
        "timeout_count": _count_text_hits(failure_rows + skipped_rows + command_rows, ("timeout", "timed out")),
        "tool_error_count": failed_command_count,
    }
    if trace_summary is not None:
        for key in EXECUTION_TRACE_V1_NEGATIVE_COUNT_KEYS:
            derived[key] = int(trace_summary[key])
            if key not in raw:
                continue
            if not _trace_v1_non_negative_integer(raw[key]):
                raise ValueError(
                    f"trajectory_signals.{key} must be an exact non-negative integer"
                )
            supplied = int(raw[key])
            if supplied != derived[key]:
                raise ValueError(
                    f"trajectory_signals.{key} conflicts with execution_trace_v1"
                )
    signals: dict[str, Any] = {
        "taxonomy": "interaction/execution/environment",
        "method": (
            "trace-derived-v1"
            if trace_summary is not None
            else str(raw.get("method", "cheap-local-signals-no-model-calls"))
        ),
        "llm_calls": 0,
    }
    for key in sorted(TRAJECTORY_SIGNAL_COUNT_KEYS):
        try:
            signals[key] = max(0, int(raw.get(key, derived.get(key, 0))))
        except (TypeError, ValueError):
            signals[key] = 0
    negative_total = sum(
        int(signals.get(key, 0) or 0)
        for key in TRAJECTORY_SIGNAL_COUNT_KEYS
        if key != "satisfaction_count"
    )
    derived_informative = negative_total > 0 or not quality_passed or bool(skipped_rows)
    if trace_summary is not None and "informative" in raw:
        if type(raw["informative"]) is not bool:
            raise ValueError("trajectory_signals.informative must be boolean")
        if raw["informative"] is not derived_informative:
            raise ValueError(
                "trajectory_signals.informative conflicts with execution_trace_v1"
            )
    signals["informative"] = (
        derived_informative
        if trace_summary is not None
        else bool(raw.get("informative", derived_informative))
    )
    if trace_summary is not None:
        signals["trace_summary"] = trace_summary
    return signals


def validate_trajectory_signals(value: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(value, dict):
        return ["trajectory_signals must be an object"]
    for key in TRAJECTORY_SIGNAL_COUNT_KEYS:
        if key not in value:
            continue
        try:
            number = int(value[key])
        except (TypeError, ValueError):
            issues.append(f"trajectory_signals.{key} must be an integer")
            continue
        if number < 0:
            issues.append(f"trajectory_signals.{key} must be non-negative")
    if "informative" in value and not isinstance(value["informative"], bool):
        issues.append("trajectory_signals.informative must be boolean")
    if "llm_calls" in value:
        try:
            llm_calls = int(value["llm_calls"])
        except (TypeError, ValueError):
            issues.append("trajectory_signals.llm_calls must be an integer")
        else:
            if llm_calls < 0:
                issues.append("trajectory_signals.llm_calls must be non-negative")
    if "trace_summary" in value:
        issues.extend(validate_execution_trace_summary_v1(value["trace_summary"]))
        if value.get("method") != "trace-derived-v1":
            issues.append(
                "trajectory_signals.method must be trace-derived-v1 when trace_summary is present"
            )
        summary = value.get("trace_summary")
        if isinstance(summary, dict):
            for key in EXECUTION_TRACE_V1_NEGATIVE_COUNT_KEYS:
                if value.get(key) != summary.get(key):
                    issues.append(
                        f"trajectory_signals.{key} must match trace_summary.{key}"
                    )
    return issues


def normalize_quality(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit("result report must include quality as an object.")
    score = value.get("score", 1.0 if value.get("passed") is True else 0.0)
    try:
        numeric_score = float(score)
    except (TypeError, ValueError):
        raise SystemExit("quality.score must be numeric when provided.") from None
    return {
        "passed": bool(value.get("passed", numeric_score >= 0.7)),
        "score": round(numeric_score, 4),
        **{key: item for key, item in value.items() if key not in {"passed", "score"}},
    }


def require_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise SystemExit(f"result report must include {key} as a list.")
    return value


def run_report_path(path: Path) -> Path:
    if path.is_dir():
        return path / "benchmark-result.json"
    return path


def validate_benchmark_result_shape(data: dict[str, Any], *, require_ledger_local: bool = True) -> list[str]:
    issues: list[str] = []
    required = {
        "schema_version",
        "tool",
        "ok",
        "status",
        "run_id",
        "task_id",
        "subject",
        "agent_tool",
        "model_label",
        "workflow_name",
        "workflow_version",
        "quality",
        "advisory_token_estimates",
        "cost_estimates",
        "grounding",
        "run_packet_path",
        "commands",
        "files_changed",
        "checks",
        "skipped",
        "failures",
        "notes",
    }
    missing = sorted(required - set(data))
    issues.extend(f"missing required field: {key}" for key in missing)
    for key in ("run_id", "task_id", "agent_tool", "model_label"):
        if key in data and (
            not isinstance(data.get(key), str) or not str(data.get(key)).strip()
        ):
            issues.append(f"{key} must be a non-empty string")
    quality = data.get("quality")
    if not isinstance(quality, dict):
        issues.append("quality must be an object")
    else:
        try:
            score = float(quality.get("score"))
        except (TypeError, ValueError):
            issues.append("quality.score must be numeric")
        else:
            if score < 0 or score > 1:
                issues.append("quality.score must be between 0 and 1")
        if not isinstance(quality.get("passed"), bool):
            issues.append("quality.passed must be boolean")
    tokens = data.get("advisory_token_estimates")
    if not isinstance(tokens, dict):
        issues.append("advisory_token_estimates must be an object")
    else:
        for key in (
            "input_tokens_estimated",
            "output_tokens_estimated",
            "cacheable_static_tokens_estimated",
            "loaded_context_tokens_estimated",
        ):
            if key not in tokens:
                issues.append(f"advisory_token_estimates.{key} is required")
            else:
                try:
                    if int(tokens.get(key, 0)) < 0:
                        issues.append(f"advisory_token_estimates.{key} must be non-negative")
                except (TypeError, ValueError):
                    issues.append(f"advisory_token_estimates.{key} must be an integer")
    if "token_measurement" in data:
        issues.extend(token_measurement_v1.validate_measurement(data.get("token_measurement")))
    costs = data.get("cost_estimates")
    if not isinstance(costs, dict):
        issues.append("cost_estimates must be an object")
    else:
        if "total_estimated" not in costs:
            issues.append("cost_estimates.total_estimated is required")
        else:
            amount = costs.get("total_estimated")
            if isinstance(amount, bool):
                issues.append("cost_estimates.total_estimated must be a finite non-negative number")
            else:
                try:
                    numeric_amount = float(amount)
                except (TypeError, ValueError):
                    issues.append("cost_estimates.total_estimated must be a finite non-negative number")
                else:
                    if not math.isfinite(numeric_amount) or numeric_amount < 0:
                        issues.append("cost_estimates.total_estimated must be a finite non-negative number")
        completeness = costs.get("completeness")
        if completeness is not None:
            if not isinstance(completeness, dict):
                issues.append("cost_estimates.completeness must be an object")
            else:
                complete = completeness.get("complete")
                missing = completeness.get("missing")
                if not isinstance(complete, bool):
                    issues.append("cost_estimates.completeness.complete must be boolean")
                if not isinstance(missing, list) or not all(
                    isinstance(item, str) and item.strip() for item in (missing or [])
                ):
                    issues.append("cost_estimates.completeness.missing must be a list of non-empty strings")
                elif isinstance(complete, bool) and complete is not (len(missing) == 0):
                    issues.append(
                        "cost_estimates.completeness.complete must be true exactly when missing is empty"
                    )
        if costs.get("measured") is True:
            if costs.get("available") is not True:
                issues.append("cost_estimates.available must be true for measured cost")
            if costs.get("provenance") not in {"provider_telemetry", "provider_invoice"}:
                issues.append("cost_estimates.provenance must be provider_telemetry or provider_invoice when measured")
            if not isinstance(costs.get("currency"), str) or not str(costs.get("currency", "")).strip():
                issues.append("cost_estimates.currency must be a non-empty string for measured cost")
            if not isinstance(completeness, dict):
                issues.append("cost_estimates.completeness is required for measured cost")
    grounding = data.get("grounding")
    if not isinstance(grounding, dict):
        issues.append("grounding must be an object")
    else:
        if not isinstance(grounding.get("unsupported_claims", []), list):
            issues.append("grounding.unsupported_claims must be a list")
        if not isinstance(grounding.get("evidence_coverage", {}), dict):
            issues.append("grounding.evidence_coverage must be an object")
    for key in ("commands", "files_changed", "checks", "skipped", "failures", "notes"):
        if not isinstance(data.get(key), list):
            issues.append(f"{key} must be a list")
    ledger_path = data.get("run_packet_path")
    if not isinstance(ledger_path, str) or not ledger_path.strip():
        issues.append("run_packet_path must be a non-empty string")
    elif require_ledger_local and (Path(ledger_path).is_absolute() or ".." in Path(ledger_path).parts):
        issues.append("run_packet_path must be run-local")
    if "metrics_standard" in data:
        issues.extend(validate_metrics_standard(data["metrics_standard"]))
    if "run_config" in data:
        issues.extend(validate_run_config(data["run_config"]))
    if "agent_task_metrics" in data:
        issues.extend(validate_agent_task_metrics(data["agent_task_metrics"]))
    if "trajectory_signals" in data:
        issues.extend(validate_trajectory_signals(data["trajectory_signals"]))
    determinism = data.get("determinism")
    if determinism is not None:
        if not isinstance(determinism, dict):
            issues.append("determinism must be an object")
        else:
            for key in ("batch_run_id", "unit_run_id", "artifact_dir"):
                if not isinstance(determinism.get(key, ""), str):
                    issues.append(f"determinism.{key} must be a string")
    evidence_tiers = data.get("evidence_tiers")
    if evidence_tiers is not None:
        if not isinstance(evidence_tiers, dict):
            issues.append("evidence_tiers must be an object")
        elif not isinstance(evidence_tiers.get("summary", {}), dict):
            issues.append("evidence_tiers.summary must be an object")
    routing_determinism = data.get("routing_determinism")
    if routing_determinism is not None and not isinstance(routing_determinism, dict):
        issues.append("routing_determinism must be an object")
    return issues


def normalized_model_benchmark_report(
    *,
    run_id: str,
    task_id: str,
    subject: str,
    agent_tool: str,
    model_label: str,
    workflow_name: str = "",
    workflow_version: str = "",
    quality: dict[str, Any] | None = None,
    result_summary: dict[str, Any] | None = None,
    artifacts: dict[str, Any] | None = None,
    run_packet_path: str = "run.json",
    commands: list[Any] | None = None,
    files_changed: list[Any] | None = None,
    checks: list[Any] | None = None,
    skipped: list[Any] | None = None,
    failures: list[Any] | None = None,
    notes: list[Any] | None = None,
    failure_taxonomy: list[Any] | None = None,
    quality_sections: dict[str, Any] | None = None,
    grounding: dict[str, Any] | None = None,
    advisory_token_estimates: dict[str, Any] | None = None,
    token_measurement: dict[str, Any] | None = None,
    cost_estimates: dict[str, Any] | None = None,
    metrics_standard: dict[str, Any] | None = None,
    run_config: dict[str, Any] | None = None,
    agent_task_metrics: dict[str, Any] | None = None,
    trajectory_signals: dict[str, Any] | None = None,
    determinism: dict[str, Any] | None = None,
    evidence_tiers: dict[str, Any] | None = None,
    routing_determinism: dict[str, Any] | None = None,
    baseline_comparison: dict[str, Any] | None = None,
    ok: bool = True,
    status: str = "completed",
) -> dict[str, Any]:
    normalized_quality = normalize_quality(quality or {"passed": ok, "score": 1.0 if ok else 0.0})
    normalized_artifacts = dict(artifacts or {})
    output_files = normalized_artifacts.get("output_files")
    if not isinstance(output_files, list):
        output_files = []
        normalized_artifacts["output_files"] = output_files
    normalized_grounding = grounding or {
        "hallucination_count": 0,
        "unsupported_claims": [],
        "evidence_coverage": {"coverage_percent": 100},
    }
    sections = quality_sections or quality_section_summary(normalized_quality, commands or [], checks or [], skipped or [], failures or [], normalized_grounding)
    normalized_agent_metrics = normalize_agent_task_metrics(
        agent_task_metrics,
        grounding=normalized_grounding,
        commands=commands or [],
        checks=checks or [],
        failures=failures or [],
    )
    normalized_trajectory_signals = normalize_trajectory_signals(
        trajectory_signals,
        quality=normalized_quality,
        commands=commands or [],
        checks=checks or [],
        skipped=skipped or [],
        failures=failures or [],
    )
    normalized_determinism = normalize_determinism(
        determinism,
        run_id=run_id,
        task_id=task_id,
        artifact_dir=run_id,
    )
    normalized_evidence_tiers = evidence_tiers or normalize_evidence_tiers(normalized_grounding.get("evidence", []))
    mismatch_kind = classify_mismatch(
        quality=normalized_quality,
        grounding=normalized_grounding,
        failures=failures or [],
        checks=checks or [],
    )
    normalized_routing = routing_determinism or {
        "failure_category": "none" if ok else ("other" if failures else "none"),
        "mismatch_kind": mismatch_kind,
        "batch_run_id": normalized_determinism["batch_run_id"],
        "unit_run_id": normalized_determinism["unit_run_id"],
    }
    normalized_advisory_token_estimates = advisory_token_estimates or {
        "input_tokens_estimated": 0,
        "output_tokens_estimated": 0,
        "cacheable_static_tokens_estimated": 0,
        "loaded_context_tokens_estimated": 0,
        "method": "model benchmark metrics",
    }
    if token_measurement is None:
        input_tokens = normalized_advisory_token_estimates.get("input_tokens_estimated", 0)
        output_tokens = normalized_advisory_token_estimates.get("output_tokens_estimated", 0)
        normalized_token_measurement = token_measurement_v1.build_measurement(
            provenance="heuristic_estimate",
            scope="artifact",
            tokenizer_or_estimator=str(
                normalized_advisory_token_estimates.get("method", "model benchmark metrics")
            ),
            input_tokens=input_tokens if isinstance(input_tokens, int) else 0,
            output_tokens=output_tokens if isinstance(output_tokens, int) else 0,
            complete=True,
        )
    else:
        normalized_token_measurement = token_measurement_v1.normalize_measurement(
            token_measurement
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "ok": bool(ok),
        "status": status,
        "run_id": run_id,
        "task_id": task_id,
        "subject": subject,
        "agent_tool": agent_tool,
        "model_label": model_label,
        "workflow_name": workflow_name,
        "workflow_version": workflow_version,
        "quality": normalized_quality,
        "advisory_token_estimates": normalized_advisory_token_estimates,
        "token_measurement": normalized_token_measurement,
        "cost_estimates": cost_estimates
        or {
            "available": False,
            "provenance": "unavailable",
            "measured": False,
            "completeness": {"complete": False, "missing": ["provider_cost"]},
            "total_estimated": 0,
            "currency": "USD",
            "reason": "Local model benchmark has no API cost.",
        },
        "grounding": normalized_grounding,
        "metrics_standard": normalize_metrics_standard(metrics_standard),
        "run_config": normalize_run_config(run_config),
        "agent_task_metrics": normalized_agent_metrics,
        "trajectory_signals": normalized_trajectory_signals,
        "determinism": normalized_determinism,
        "routing_determinism": normalized_routing,
        "evidence_tiers": normalized_evidence_tiers,
        "baseline_comparison": baseline_comparison or {"available": False, "reason": "no baseline supplied"},
        "run_packet_path": run_packet_path,
        "commands": commands or [],
        "files_changed": files_changed or [],
        "checks": checks or [],
        "skipped": skipped or [],
        "failures": failures or [],
        "failure_taxonomy": normalize_failure_taxonomy(failure_taxonomy or []),
        "quality_sections": sections,
        "outliers": detect_outliers(
            {
                "advisory_token_estimates": advisory_token_estimates or {},
                "grounding": normalized_grounding,
                "run_packet_path": run_packet_path,
                "skipped": skipped or [],
                "checks": checks or [],
            }
        ),
        "notes": notes or [],
        "result_summary": result_summary or {},
        "artifacts": normalized_artifacts,
        "output_files": output_files,
        "unsupported_claims": (grounding or {}).get("unsupported_claims", []),
        "skipped_checks": skipped or [],
    }


def comparability_issues(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in ("suite", "task_id", "workflow_name", "workflow_version", "model_label"):
        left_value = str(left.get(key, ""))
        right_value = str(right.get(key, ""))
        if left_value and right_value and left_value != right_value:
            issues.append(f"{key} differs: {left_value!r} vs {right_value!r}")
    left_config = left.get("run_config") if isinstance(left.get("run_config"), dict) else {}
    right_config = right.get("run_config") if isinstance(right.get("run_config"), dict) else {}
    for key in sorted(RUN_CONFIG_COMPARE_KEYS):
        if key in left_config and key in right_config and left_config.get(key) != right_config.get(key):
            issues.append(f"run_config.{key} differs: {left_config.get(key)!r} vs {right_config.get(key)!r}")
    left_signals = left.get("trajectory_signals") if isinstance(left.get("trajectory_signals"), dict) else {}
    right_signals = right.get("trajectory_signals") if isinstance(right.get("trajectory_signals"), dict) else {}
    left_trace = isinstance(left_signals.get("trace_summary"), dict)
    right_trace = isinstance(right_signals.get("trace_summary"), dict)
    if left_trace != right_trace:
        issues.append("trajectory_signals trace instrumentation availability differs")
    elif left_trace and left_signals.get("method") != right_signals.get("method"):
        issues.append(
            "trajectory_signals method differs: "
            f"{left_signals.get('method')!r} vs {right_signals.get('method')!r}"
        )
    return issues


def retrieval_score(evidence: list[dict[str, Any]], expected_paths: list[str], *, top_k: int = 5) -> dict[str, Any]:
    expected = [path.replace("\\", "/") for path in expected_paths if path]
    retrieved = [str(item.get("path", "")).replace("\\", "/") for item in evidence[: max(1, top_k)]]
    if not expected:
        return {
            "recall_at_k": 1.0,
            "precision_at_k": 1.0 if not retrieved else 0.0,
            "mrr": 0.0,
            "ndcg_at_k": 0.0,
            "hits": 0,
            "expected": [],
            "retrieved": retrieved,
            "no_evidence_expected": True,
            "no_evidence_correct": not retrieved,
            "no_evidence_precision": 1.0 if not retrieved else 0.0,
            "false_positive_count": len(retrieved),
        }
    matched_expected: set[str] = set()
    hits: list[int] = []
    for index, path in enumerate(retrieved, start=1):
        for target in expected:
            if path == target or path.endswith(target):
                hits.append(index)
                matched_expected.add(target)
                break
    recall = len(matched_expected) / len(expected)
    precision = len(hits) / max(len(retrieved), 1)
    mrr = 1 / hits[0] if hits else 0.0
    dcg = sum(1 / math.log2(rank + 1) for rank in hits)
    ideal_hits = min(len(expected), len(retrieved), top_k)
    idcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return {
        "recall_at_k": round(recall, 4),
        "precision_at_k": round(precision, 4),
        "mrr": round(mrr, 4),
        "ndcg_at_k": round((dcg / idcg) if idcg else 0.0, 4),
        "hits": len(hits),
        "expected": expected,
        "retrieved": retrieved,
        "no_evidence_expected": False,
        "no_evidence_correct": False,
        "no_evidence_precision": None,
        "false_positive_count": 0,
    }


def trajectory_score(
    tool_events: list[dict[str, Any]],
    *,
    required_tools: list[str] | None = None,
    forbidden_tools: list[str] | None = None,
    final_verifier_passed: bool = False,
) -> dict[str, Any]:
    required = required_tools or []
    forbidden = set(forbidden_tools or [])
    called = [str(event.get("tool", "")) for event in tool_events]
    missing = [tool for tool in required if tool not in called]
    forbidden_hits = [tool for tool in called if tool in forbidden]
    retries = sum(1 for event in tool_events if str(event.get("status", "")).lower() in {"retry", "retried"})
    path_escapes = 0
    for event in tool_events:
        args = event.get("args") if isinstance(event.get("args"), dict) else {}
        raw_path = str(args.get("path", "") or args.get("target", ""))
        if raw_path and (Path(raw_path).is_absolute() or ".." in Path(raw_path).parts):
            path_escapes += 1
    passed = not missing and not forbidden_hits and path_escapes == 0 and bool(final_verifier_passed)
    return {
        "passed": passed,
        "required_tools": required,
        "called_tools": called,
        "missing_required_tools": missing,
        "forbidden_tool_calls": forbidden_hits,
        "tool_retry_count": retries,
        "path_escape_count": path_escapes,
        "final_verifier_passed": bool(final_verifier_passed),
    }


def document_vision_score(output_text: str, expected_facts: list[str], *, unreadable_expected: bool = False) -> dict[str, Any]:
    text = output_text.lower()
    hits = [fact for fact in expected_facts if fact.lower() in text]
    missed = [fact for fact in expected_facts if fact not in hits]
    uncertainty_terms = ("unclear", "unreadable", "not legible", "cannot read", "uncertain")
    explicit_uncertainty = any(term in text for term in uncertainty_terms)
    return {
        "fact_coverage": round(len(hits) / max(len(expected_facts), 1), 4),
        "hits": hits,
        "missed": missed,
        "explicit_uncertainty": explicit_uncertainty,
        "accepted": len(hits) == len(expected_facts) or (unreadable_expected and explicit_uncertainty),
    }


def normalize_failure_taxonomy(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    rows = value if isinstance(value, list) else [value]
    normalized: list[dict[str, str]] = []
    for row in rows:
        if isinstance(row, dict):
            category = str(row.get("category", "other")).strip() or "other"
            detail = str(row.get("detail", row.get("message", ""))).strip()
            evidence = str(row.get("evidence", "")).strip()
        else:
            category = "other"
            detail = str(row).strip()
            evidence = ""
        if category not in FAILURE_TAXONOMY_CATEGORIES:
            category = "other"
        if detail or evidence:
            normalized.append({"category": category, "detail": detail, "evidence": evidence})
    return normalized


def detect_outliers(report: dict[str, Any]) -> list[dict[str, str]]:
    outliers: list[dict[str, str]] = []
    tokens = report.get("advisory_token_estimates")
    if isinstance(tokens, dict):
        for key, threshold in (
            ("input_tokens_estimated", 120_000),
            ("output_tokens_estimated", 20_000),
            ("loaded_context_tokens_estimated", 80_000),
        ):
            try:
                value = int(tokens.get(key, 0))
            except (TypeError, ValueError):
                value = 0
            if value > threshold:
                outliers.append(
                    {
                        "kind": "high-token-use",
                        "detail": f"{key}={value} is above review threshold {threshold}.",
                    }
                )
    grounding = report.get("grounding")
    coverage = {}
    if isinstance(grounding, dict):
        coverage = grounding.get("evidence_coverage") if isinstance(grounding.get("evidence_coverage"), dict) else {}
    try:
        coverage_percent = float(coverage.get("coverage_percent", 100))
    except (TypeError, ValueError):
        coverage_percent = 0.0
    if coverage_percent < 75:
        outliers.append(
            {
                "kind": "missing-evidence",
                "detail": f"evidence coverage is {coverage_percent}%.",
            }
        )
    skipped = report.get("skipped")
    if isinstance(skipped, list) and len(skipped) >= 3:
        outliers.append(
            {
                "kind": "skipped-validations",
                "detail": f"{len(skipped)} validation item(s) were skipped.",
            }
        )
    checks = report.get("checks")
    if isinstance(checks, list):
        missing = [
            item
            for item in checks
            if isinstance(item, dict) and str(item.get("status", "")).lower() in {"skipped", "missing"}
        ]
        if missing:
            outliers.append(
                {
                    "kind": "skipped-validations",
                    "detail": f"{len(missing)} check row(s) report skipped or missing status.",
                }
            )
    if report.get("run_packet_path") and not isinstance(report.get("run_packet_path"), str):
        outliers.append({"kind": "missing-evidence", "detail": "run_packet_path is not a string."})
    return outliers


def quality_section_summary(
    quality: dict[str, Any],
    commands: list[Any],
    checks: list[Any],
    skipped: list[Any],
    failures: list[Any],
    grounding: dict[str, Any],
) -> dict[str, Any]:
    return {
        "model_quality": {
            "score": quality.get("score", 0),
            "passed": quality.get("passed", False),
            "failure_count": len(failures),
        },
        "agent_behavior": {
            "unsupported_claims": len(grounding.get("unsupported_claims", []))
            if isinstance(grounding.get("unsupported_claims"), list)
            else 0,
            "hallucination_count": grounding.get("hallucination_count", 0),
        },
        "tool_behavior": {
            "commands": len(commands),
            "failed_checks": len(
                [item for item in checks if isinstance(item, dict) and item.get("ok") is False]
            ),
        },
        "workflow_quality": {
            "skipped": len(skipped),
            "evidence_coverage": grounding.get("evidence_coverage", {}),
        },
    }
