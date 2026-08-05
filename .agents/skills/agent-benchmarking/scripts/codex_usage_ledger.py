#!/usr/bin/env python3
"""Aggregate Codex Desktop rollout token usage for telemetry-visible benchmark runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from support import token_measurement_v1 as token_v1
from support import execution_prompt_marker
from support import provider_evidence_adapters

sys.dont_write_bytecode = True

import benchmark_common as common


USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
OPTIONAL_USAGE_FIELDS = ("cache_write_input_tokens",)
MAX_ROLLOUT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class RunRef:
    label: str
    thread_id: str


def parse_run_ref(raw: str) -> RunRef:
    if "=" not in raw:
        raise SystemExit("--run must use label=thread-id")
    label, thread_id = raw.split("=", 1)
    label = label.strip()
    thread_id = thread_id.strip()
    if not label or not thread_id:
        raise SystemExit("--run must use non-empty label=thread-id")
    return RunRef(label=label, thread_id=thread_id)


def parse_labeled_value(raw: str, option: str) -> tuple[str, str]:
    if "=" not in raw:
        raise SystemExit(f"{option} must use label=value")
    label, value = (part.strip() for part in raw.split("=", 1))
    if not label or not value:
        raise SystemExit(f"{option} must use non-empty label=value")
    return label, value


def validate_run_refs(runs: list[RunRef]) -> None:
    labels: set[str] = set()
    thread_ids: set[str] = set()
    for run in runs:
        if run.label in labels:
            raise SystemExit(f"duplicate run label: {run.label}")
        if run.thread_id in thread_ids:
            raise SystemExit(f"duplicate thread id: {run.thread_id}")
        labels.add(run.label)
        thread_ids.add(run.thread_id)


def state_db_path(codex_home: Path) -> Path:
    return codex_home / "state_5.sqlite"


def session_index_title(codex_home: Path, thread_id: str) -> str:
    path = codex_home / "session_index.jsonl"
    if not path.exists():
        return ""
    title = ""
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("id") == thread_id:
                title = str(obj.get("thread_name", "") or "")
    return title


def rollout_path_from_sessions(codex_home: Path, thread_id: str) -> Path | None:
    sessions_dir = codex_home / "sessions"
    if not sessions_dir.exists():
        return None
    matches = sorted(
        sessions_dir.rglob(f"rollout-*-{thread_id}.jsonl"),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def fallback_thread_row(codex_home: Path, thread_id: str) -> dict[str, Any] | None:
    rollout_path = rollout_path_from_sessions(codex_home, thread_id)
    if rollout_path is None:
        return None
    return {
        "id": thread_id,
        "title": session_index_title(codex_home, thread_id),
        "model_provider": "",
        "cwd": "",
        "rollout_path": str(rollout_path),
        "tokens_used": 0,
        "source": "session-rollout",
    }


def thread_row(codex_home: Path, thread_id: str) -> dict[str, Any]:
    path = state_db_path(codex_home)
    if not path.exists():
        row = fallback_thread_row(codex_home, thread_id)
        if row is not None:
            return row
        raise SystemExit(f"Codex state database not found: {path}")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "select id, title, model_provider, cwd, rollout_path, tokens_used from threads where id=?",
            (thread_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        fallback = fallback_thread_row(codex_home, thread_id)
        if fallback is not None:
            return fallback
        raise SystemExit(f"thread id not found in Codex state or session rollouts: {thread_id}")
    result = dict(row)
    result["source"] = "state-sqlite"
    return result


def usage_events(rollout_path: Path) -> list[dict[str, Any]]:
    return list(scan_rollout(rollout_path)["events"])


def read_no_follow_bytes(
    path: Path,
    label: str,
    *,
    max_bytes: int,
) -> bytes:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        raise SystemExit(f"{label} file not found: {path}") from None
    reparse = bool(int(getattr(metadata, "st_file_attributes", 0)) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or reparse or not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(f"{label} must be a no-follow regular file: {path}")
    if metadata.st_size > max_bytes:
        raise SystemExit(f"{label} exceeds the {max_bytes}-byte evidence limit: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        opened_reparse = bool(int(getattr(opened, "st_file_attributes", 0)) & 0x400)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened_reparse
            or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise SystemExit(f"{label} changed while opening: {path}")
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise SystemExit(f"{label} exceeds the {max_bytes}-byte evidence limit: {path}")
    return data


def read_rollout_bytes(rollout_path: Path) -> bytes:
    return read_no_follow_bytes(
        rollout_path,
        "rollout",
        max_bytes=MAX_ROLLOUT_BYTES,
    )


def scan_rollout(rollout_path: Path) -> dict[str, Any]:
    data = read_rollout_bytes(rollout_path)
    rows: list[dict[str, Any]] = []
    malformed_line_count = 0
    for line_number, raw_line in enumerate(data.splitlines(), start=1):
        try:
            obj = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            malformed_line_count += 1
            continue
        if not isinstance(obj, dict):
            malformed_line_count += 1
            continue
        payload = obj.get("payload") if isinstance(obj, dict) else None
        info = payload.get("info") if isinstance(payload, dict) else None
        usage = info.get("last_token_usage") if isinstance(info, dict) else None
        if usage is None:
            continue
        if (
            not isinstance(usage, dict)
            or not all(
                isinstance(usage.get(field), int)
                and not isinstance(usage.get(field), bool)
                and int(usage.get(field)) >= 0
                for field in USAGE_FIELDS
            )
        ):
            malformed_line_count += 1
            continue
        input_tokens = int(usage["input_tokens"])
        output_tokens = int(usage["output_tokens"])
        if int(usage["total_tokens"]) != input_tokens + output_tokens:
            malformed_line_count += 1
            continue
        cache_read = int(usage["cached_input_tokens"])
        reasoning = int(usage["reasoning_output_tokens"])
        raw_cache_write = usage.get("cache_write_input_tokens")
        if raw_cache_write is not None and (
            not isinstance(raw_cache_write, int)
            or isinstance(raw_cache_write, bool)
            or raw_cache_write < 0
        ):
            malformed_line_count += 1
            continue
        cache_write = int(raw_cache_write) if raw_cache_write is not None else None
        if (
            cache_read > input_tokens
            or (cache_write is not None and cache_write > input_tokens)
            or cache_read + (cache_write or 0) > input_tokens
            or reasoning > output_tokens
        ):
            malformed_line_count += 1
            continue
        rows.append(
            {
                "line": line_number,
                "timestamp": obj.get("timestamp", ""),
                "usage": {
                    **{field: int(usage[field]) for field in USAGE_FIELDS},
                    "cache_write_input_tokens": cache_write,
                },
            }
        )
    read_errors = [] if not data or data.endswith(b"\n") else ["rollout_missing_terminal_newline"]
    return {
        "events": rows,
        "malformed_line_count": malformed_line_count,
        "read_errors": read_errors,
        "rollout_sha256": hashlib.sha256(data).hexdigest(),
        "raw_bytes": data,
    }


def aggregate_usage(events: list[dict[str, Any]]) -> dict[str, int | None]:
    totals: dict[str, int | None] = {field: 0 for field in USAGE_FIELDS}
    totals["cache_write_input_tokens"] = 0
    cache_write_available = True
    for event in events:
        usage = event["usage"]
        for field in USAGE_FIELDS:
            totals[field] = int(totals[field] or 0) + int(usage.get(field, 0) or 0)
        raw_cache_write = usage.get("cache_write_input_tokens")
        if raw_cache_write is None:
            cache_write_available = False
            totals["cache_write_input_tokens"] = None
        elif cache_write_available:
            totals["cache_write_input_tokens"] = int(totals["cache_write_input_tokens"] or 0) + int(raw_cache_write)
    return totals


def model_observation_bytes(data: bytes) -> dict[str, Any]:
    providers: set[str] = set()
    models: set[str] = set()
    reasoning_efforts: set[str] = set()
    incomplete_event_fields: set[str] = set()
    event_count = 0
    for raw_line in data.splitlines():
        try:
            obj = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict) or obj.get("type") != "turn_context":
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        event_count += 1
        provider = str(payload.get("model_provider") or payload.get("provider") or "").strip()
        model = str(payload.get("model") or "").strip()
        effort = str(payload.get("reasoning_effort") or payload.get("effort") or "").strip()
        if provider:
            providers.add(provider)
        else:
            incomplete_event_fields.add("provider_event")
        if model:
            models.add(model)
        else:
            incomplete_event_fields.add("model_event")
        if effort:
            reasoning_efforts.add(effort)
        else:
            incomplete_event_fields.add("reasoning_effort_event")

    missing: list[str] = []
    for name, values in (
        ("provider", providers),
        ("model", models),
        ("reasoning_effort", reasoning_efforts),
    ):
        if not values:
            missing.append(name)
        elif len(values) > 1:
            missing.append(f"ambiguous_{name}")
    missing.extend(sorted(incomplete_event_fields))
    complete = not missing
    return {
        "complete": complete,
        "source": "codex-rollout-turn-context",
        "event_count": event_count,
        "provider": next(iter(providers)) if len(providers) == 1 else "",
        "model": next(iter(models)) if len(models) == 1 else "",
        "reasoning_effort": next(iter(reasoning_efforts)) if len(reasoning_efforts) == 1 else "",
        "observed_providers": sorted(providers),
        "observed_models": sorted(models),
        "observed_reasoning_efforts": sorted(reasoning_efforts),
        "missing": missing,
        "requested_model_substitution": False,
    }


def model_observation(rollout_path: Path) -> dict[str, Any]:
    return model_observation_bytes(read_rollout_bytes(rollout_path))


def execution_prompt_count(data: bytes, expected_prompt: str) -> int:
    return execution_prompt_marker.occurrence_count(data, expected_prompt)


def execution_prompt_scope(data: bytes, expected_prompt: str) -> dict[str, Any]:
    return execution_prompt_marker.scope_observation(data, expected_prompt)


def pricing_rate(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"pricing {field} must be a finite non-negative number")
    try:
        rate = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"pricing {field} must be a finite non-negative number") from None
    if not math.isfinite(rate) or rate < 0:
        raise ValueError(f"pricing {field} must be a finite non-negative number")
    return rate


def estimate_cost(usage: dict[str, int], rates: dict[str, float]) -> dict[str, Any]:
    input_rate = pricing_rate(rates.get("input_per_million", 0.0), "input_per_million")
    cached_rate = pricing_rate(
        rates.get("cached_input_per_million", input_rate),
        "cached_input_per_million",
    )
    output_rate = pricing_rate(rates.get("output_per_million", 0.0), "output_per_million")
    cached_input = int(usage.get("cached_input_tokens", 0) or 0)
    total_input = int(usage.get("input_tokens", 0) or 0)
    uncached_input = max(0, total_input - cached_input)
    output = int(usage.get("output_tokens", 0) or 0)
    input_cost = (uncached_input / 1_000_000) * input_rate
    cached_cost = (cached_input / 1_000_000) * cached_rate
    output_cost = (output / 1_000_000) * output_rate
    return {
        "available": any(value > 0 for value in (input_rate, cached_rate, output_rate)),
        "provenance": "local_price_estimate",
        "measured": False,
        "completeness": {"complete": True, "missing": []},
        "currency": "USD",
        "input_per_million": input_rate,
        "cached_input_per_million": cached_rate,
        "output_per_million": output_rate,
        "uncached_input_tokens": uncached_input,
        "cached_input_tokens": cached_input,
        "output_tokens": output,
        "input_cost": round(input_cost, 8),
        "cached_input_cost": round(cached_cost, 8),
        "output_cost": round(output_cost, 8),
        "total_cost": round(input_cost + cached_cost + output_cost, 8),
        "note": "output_tokens already includes reasoning output in Codex usage events; reasoning_output_tokens is reported as a subset detail.",
    }


def build_report(
    *,
    codex_home: Path,
    runs: list[RunRef],
    rates: dict[str, float],
    execution_prompts: dict[str, str] | None = None,
) -> dict[str, Any]:
    validate_run_refs(runs)
    execution_prompts = dict(execution_prompts or {})
    unknown_prompt_labels = sorted(set(execution_prompts) - {run.label for run in runs})
    if unknown_prompt_labels:
        raise SystemExit("execution prompts name unknown run labels: " + ", ".join(unknown_prompt_labels))
    arms: dict[str, Any] = {}
    for run in runs:
        row = thread_row(codex_home, run.thread_id)
        rollout_path = Path(str(row["rollout_path"]))
        rollout_scan = scan_rollout(rollout_path)
        events = list(rollout_scan["events"])
        totals = aggregate_usage(events)
        observed_model = model_observation_bytes(bytes(rollout_scan["raw_bytes"]))
        state_tokens_used = int(row.get("tokens_used", 0) or 0)
        missing: list[str] = []
        if not events:
            missing.append("usage_events")
        if int(rollout_scan["malformed_line_count"]) > 0:
            missing.append("malformed_rollout_lines")
        missing.extend(str(value) for value in rollout_scan["read_errors"])
        if state_tokens_used != totals["total_tokens"]:
            missing.append("state_tokens_used_mismatch")
        state_model_provider = str(row.get("model_provider", "")).strip()
        observed_model_provider = str(observed_model.get("provider", "")).strip()
        raw_model_provider = observed_model_provider or "unknown"
        model_provider = provider_evidence_adapters.normalize_model_provider(
            raw_model_provider
        )
        if not state_model_provider:
            missing.append("state_model_provider")
        if raw_model_provider == "unknown":
            missing.append("model_provider")
        if state_model_provider and observed_model_provider and state_model_provider != observed_model_provider:
            missing.append("model_provider_mismatch")
        if observed_model.get("complete") is not True:
            missing.append("model_observation")
        expected_prompt = execution_prompts.get(run.label, "")
        prompt_scope = execution_prompt_scope(
            bytes(rollout_scan["raw_bytes"]),
            expected_prompt,
        )
        prompt_count = int(prompt_scope["occurrence_count"])
        if expected_prompt and prompt_count <= 0:
            missing.append("execution_prompt")
        if expected_prompt and prompt_scope["fresh_thread_scope"] is not True:
            missing.append("execution_prompt_scope")
        missing = sorted(dict.fromkeys(missing))
        token_measurement = token_v1.build_measurement(
            provenance="provider_telemetry",
            scope="full_run",
            tokenizer_or_estimator="codex-rollout-last-token-usage",
            input_tokens=totals["input_tokens"],
            cached_input_tokens=totals["cached_input_tokens"],
            cache_write_input_tokens=totals["cache_write_input_tokens"],
            output_tokens=totals["output_tokens"],
            reasoning_output_tokens=totals["reasoning_output_tokens"],
            total_tokens=totals["total_tokens"],
            host_surface="codex",
            model_provider=model_provider,
            complete=not missing,
            missing=missing,
            evidence=provider_evidence_adapters.codex_rollout_evidence(
                source_path=str(rollout_path),
                source_sha256=str(rollout_scan["rollout_sha256"]),
            ),
        )
        measurement_issues = token_v1.validate_measurement(token_measurement)
        if measurement_issues:
            raise RuntimeError(
                "codex usage ledger built an invalid TokenMeasurementV1: "
                + "; ".join(measurement_issues)
            )
        timestamps = [str(event.get("timestamp", "")) for event in events if str(event.get("timestamp", ""))]
        arms[run.label] = {
            "thread_id": run.thread_id,
            "title": str(row.get("title", "")),
            "model_provider": str(row.get("model_provider", "")),
            "cwd": str(row.get("cwd", "")),
            "rollout_path": str(rollout_path),
            "source": str(row.get("source", "")),
            "event_count": len(events),
            "malformed_line_count": int(rollout_scan["malformed_line_count"]),
            "read_errors": list(rollout_scan["read_errors"]),
            "rollout_sha256": str(rollout_scan["rollout_sha256"]),
            "execution_prompt": {
                "observed": bool(expected_prompt) and prompt_count > 0,
                "source": "structured-user-prompt-events",
                "binding": "exact-complete-user-prompt",
                "prompt_sha256": (
                    execution_prompt_marker.prompt_sha256(expected_prompt)
                    if expected_prompt
                    else ""
                ),
                "occurrence_count": prompt_count,
                "first_structured_user_message_observed": prompt_scope[
                    "first_structured_user_message_observed"
                ],
                "first_structured_user_message_matches": prompt_scope[
                    "first_structured_user_message_matches"
                ],
                "usage_events_before_first_prompt": prompt_scope[
                    "usage_events_before_first_prompt"
                ],
                "unsupported_user_context_before_or_with_prompt": prompt_scope[
                    "unsupported_user_context_before_or_with_prompt"
                ],
                "fresh_thread_scope": prompt_scope["fresh_thread_scope"],
            },
            "first_usage_timestamp": timestamps[0] if timestamps else "",
            "last_usage_timestamp": timestamps[-1] if timestamps else "",
            "state_tokens_used": state_tokens_used,
            "summed_last_token_usage": totals,
            "token_measurement": token_measurement,
            "model_observation": observed_model,
            "cost_estimate": estimate_cost(totals, rates),
        }
    labels = list(arms)
    baseline_label = labels[0] if labels else ""
    deltas: dict[str, Any] = {}
    if baseline_label:
        baseline = arms[baseline_label]["summed_last_token_usage"]
        baseline_cost = arms[baseline_label]["cost_estimate"]["total_cost"]
        for label in labels[1:]:
            current = arms[label]["summed_last_token_usage"]
            current_cost = arms[label]["cost_estimate"]["total_cost"]
            deltas[f"{label}_minus_{baseline_label}"] = {
                field: int(current.get(field, 0) or 0) - int(baseline.get(field, 0) or 0)
                for field in USAGE_FIELDS
            }
            deltas[f"{label}_minus_{baseline_label}"]["total_cost"] = round(current_cost - baseline_cost, 8)
    complete_for_threads = bool(arms) and all(
        arm.get("token_measurement", {}).get("completeness", {}).get("complete") is True
        for arm in arms.values()
    )
    complete_model_evidence = bool(arms) and all(
        arm.get("model_observation", {}).get("complete") is True
        for arm in arms.values()
    )
    complete_execution_prompts = bool(arms) and all(
        arm.get("execution_prompt", {}).get("observed") is True
        and arm.get("execution_prompt", {}).get("fresh_thread_scope") is True
        for arm in arms.values()
    )
    eligible_full_run_measurements = bool(arms) and all(
        token_v1.gate_eligibility(
            arm.get("token_measurement"), gate_scope="full_run"
        ).get("eligible")
        is True
        for arm in arms.values()
    )
    return {
        "schema_version": 1,
        "tool": "agent-benchmarking.codex-usage-ledger",
        "ok": True,
        "measurement_scope": {
            "scope": "codex-rollout-last-token-usage",
            "included": [
                "input_tokens from every rollout last_token_usage event",
                "cached_input_tokens from every rollout last_token_usage event",
                "output_tokens from every rollout last_token_usage event",
                "reasoning_output_tokens as output detail",
            ],
            "excluded": [
                "threads not listed with --run",
                "non-Codex processes",
                "provider invoice adjustments outside recorded usage events",
            ],
            "complete_for_listed_codex_threads": complete_for_threads,
            "complete_model_evidence_for_listed_threads": complete_model_evidence,
            "complete_execution_prompt_evidence_for_listed_threads": complete_execution_prompts,
            "complete_for_full_run_trials": (
                eligible_full_run_measurements
                and complete_model_evidence
                and complete_execution_prompts
            ),
            "eligible_provider_evidence_for_listed_threads": eligible_full_run_measurements,
            "requires_one_thread_per_benchmark_arm": True,
        },
        "pricing": {
            "provided": any(value > 0 for value in rates.values()),
            **rates,
        },
        "baseline_label": baseline_label,
        "arms": arms,
        "deltas": deltas,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Codex Usage Ledger",
        "",
        f"- OK: {str(report['ok']).lower()}",
        f"- Scope: {report['measurement_scope']['scope']}",
        "- Requirement: run each benchmark arm in its own telemetry-visible Codex thread.",
        "",
        "| Arm | Input | Cached Input | Output | Reasoning Output | Total | Events | Estimated Cost |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, arm in report["arms"].items():
        usage = arm["summed_last_token_usage"]
        cost = arm["cost_estimate"]
        lines.append(
            f"| {label} | {usage['input_tokens']} | {usage['cached_input_tokens']} | "
            f"{usage['output_tokens']} | {usage['reasoning_output_tokens']} | "
            f"{usage['total_tokens']} | {arm['event_count']} | {cost['total_cost']} |"
        )
    if report["deltas"]:
        lines.extend(["", "## Deltas", ""])
        for label, delta in report["deltas"].items():
            lines.append(
                f"- `{label}`: total `{delta['total_tokens']}`, input `{delta['input_tokens']}`, "
                f"cached input `{delta['cached_input_tokens']}`, output `{delta['output_tokens']}`, "
                f"cost `{delta['total_cost']}`."
            )
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex"))
    parser.add_argument("--run", action="append", default=[], help="benchmark arm as label=thread-id; repeatable")
    parser.add_argument(
        "--execution-marker",
        action="append",
        default=[],
        help="marker as label=value; requires --task-prompt and constructs the exact submitted prompt",
    )
    parser.add_argument(
        "--task-prompt",
        default="",
        help="immutable UTF-8 task prompt used with repeatable --execution-marker values",
    )
    parser.add_argument(
        "--execution-prompt-file",
        action="append",
        default=[],
        help="prepared complete user prompt as label=path; repeatable",
    )
    parser.add_argument("--input-per-million", type=float, default=0.0)
    parser.add_argument("--cached-input-per-million", type=float, default=0.0)
    parser.add_argument("--output-per-million", type=float, default=0.0)
    parser.add_argument("--output", default="", help="optional report JSON path")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format")
    return parser


def main(argv: list[str] | None = None) -> int:
    common.require_supported_python()
    args = build_parser().parse_args(argv)
    runs = [parse_run_ref(item) for item in args.run]
    if not runs:
        raise SystemExit("at least one --run label=thread-id is required")
    rates = {
        "input_per_million": args.input_per_million,
        "cached_input_per_million": args.cached_input_per_million,
        "output_per_million": args.output_per_million,
    }
    marker_pairs = [parse_labeled_value(item, "--execution-marker") for item in args.execution_marker]
    if len({label for label, _value in marker_pairs}) != len(marker_pairs):
        raise SystemExit("duplicate --execution-marker label")
    prompt_file_pairs = [
        parse_labeled_value(item, "--execution-prompt-file")
        for item in args.execution_prompt_file
    ]
    if len({label for label, _value in prompt_file_pairs}) != len(prompt_file_pairs):
        raise SystemExit("duplicate --execution-prompt-file label")
    execution_prompts: dict[str, str] = {}
    for label, raw_path in prompt_file_pairs:
        try:
            execution_prompts[label] = read_no_follow_bytes(
                Path(raw_path),
                "execution prompt",
                max_bytes=MAX_ROLLOUT_BYTES,
            ).decode("utf-8")
        except UnicodeDecodeError:
            raise SystemExit(f"execution prompt must be UTF-8: {raw_path}") from None
    if marker_pairs:
        if not args.task_prompt:
            raise SystemExit("--execution-marker requires --task-prompt or use --execution-prompt-file")
        try:
            task_text = read_no_follow_bytes(
                Path(args.task_prompt),
                "task prompt",
                max_bytes=MAX_ROLLOUT_BYTES,
            ).decode("utf-8-sig")
        except UnicodeDecodeError:
            raise SystemExit(f"task prompt must be UTF-8: {args.task_prompt}") from None
        for label, marker in marker_pairs:
            if label in execution_prompts:
                raise SystemExit(f"duplicate execution prompt label: {label}")
            try:
                execution_prompts[label] = execution_prompt_marker.build_prompt(task_text, marker)
            except ValueError as exc:
                raise SystemExit(f"invalid --execution-marker for {label}: {exc}") from None
    report = build_report(
        codex_home=Path(args.codex_home),
        runs=runs,
        rates=rates,
        execution_prompts=execution_prompts,
    )
    if args.output:
        common.write_json(Path(args.output), report)
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
