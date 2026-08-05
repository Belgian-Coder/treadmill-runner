#!/usr/bin/env python3
"""Record a normalized benchmark result for one prepared run."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import benchmark_common as common
import run_packet
from support import token_measurement_v1 as token_v1


def load_pricing(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    data = common.read_json(path.expanduser().resolve())
    if not isinstance(data, dict):
        raise SystemExit("pricing table must be a JSON object.")
    return data


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


def cost_estimates(model_label: str, tokens: dict[str, Any], pricing: dict[str, Any] | None) -> dict[str, Any]:
    if not pricing:
        return {
            "available": False,
            "provenance": "unavailable",
            "measured": False,
            "completeness": {"complete": False, "missing": ["pricing"]},
            "total_estimated": 0,
            "currency": "USD",
            "reason": "no local pricing table supplied",
        }
    models = pricing.get("models")
    if not isinstance(models, dict) or model_label not in models:
        return {
            "available": False,
            "provenance": "unavailable",
            "measured": False,
            "completeness": {"complete": False, "missing": ["model_pricing"]},
            "total_estimated": 0,
            "currency": "USD",
            "reason": f"model label not found in pricing table: {model_label}",
        }
    row = models[model_label]
    if not isinstance(row, dict):
        return {
            "available": False,
            "provenance": "unavailable",
            "measured": False,
            "completeness": {"complete": False, "missing": ["model_pricing"]},
            "total_estimated": 0,
            "currency": "USD",
            "reason": f"pricing row is not an object: {model_label}",
        }
    input_rate = pricing_rate(row.get("input_per_million", 0), "input_per_million")
    cached_rate = pricing_rate(
        row.get("cached_input_per_million", input_rate),
        "cached_input_per_million",
    )
    output_rate = pricing_rate(row.get("output_per_million", 0), "output_per_million")
    static_tokens = int(tokens.get("cacheable_static_tokens_estimated", 0))
    input_tokens = int(tokens.get("input_tokens_estimated", 0))
    output_tokens = int(tokens.get("output_tokens_estimated", 0))
    variable_input = max(input_tokens - static_tokens, 0)
    total = (
        (static_tokens * cached_rate)
        + (variable_input * input_rate)
        + (output_tokens * output_rate)
    ) / 1_000_000
    return {
        "available": True,
        "provenance": "local_price_estimate",
        "measured": False,
        "completeness": {"complete": True, "missing": []},
        "currency": str(row.get("currency", "USD")),
        "method": "local_price_per_million_times_estimated_tokens",
        "total_estimated": round(total, 8),
        "static_input_estimated": round((static_tokens * cached_rate) / 1_000_000, 8),
        "variable_input_estimated": round((variable_input * input_rate) / 1_000_000, 8),
        "output_estimated": round((output_tokens * output_rate) / 1_000_000, 8),
    }


def optional_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def estimate_loaded_context_tokens(raw: dict[str, Any]) -> int:
    loaded_context = raw.get("loaded_context")
    if loaded_context is None:
        loaded_context = raw.get("loaded_context_files")
    if loaded_context is None:
        return 0
    if isinstance(loaded_context, str):
        return common.estimate_tokens(loaded_context)
    return common.estimate_tokens(json.dumps(loaded_context, sort_keys=True))


def grounding_report(raw: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    unsupported_claims = optional_string_list(raw.get("unsupported_claims"))
    invented_paths = optional_string_list(raw.get("invented_paths"))
    invented_commands = optional_string_list(raw.get("invented_commands"))
    false_validation_claims = optional_string_list(raw.get("false_validation_claims"))
    abstentions = optional_string_list(raw.get("abstentions"))
    hallucination_count = (
        len(unsupported_claims)
        + len(invented_paths)
        + len(invented_commands)
        + len(false_validation_claims)
    )
    return {
        "unsupported_claims": unsupported_claims,
        "invented_paths": invented_paths,
        "invented_commands": invented_commands,
        "false_validation_claims": false_validation_claims,
        "abstentions": abstentions,
        "hallucination_count": hallucination_count,
        "evidence_coverage": packet.get("coverage", {}),
        "run_packet_status": packet.get("status", "unknown"),
        "unsupported_or_invalid_evidence": packet.get("warnings", []),
    }


def failure_category_from_result(
    raw: dict[str, Any],
    *,
    failures: list[str],
    checks: list[Any],
    grounding: dict[str, Any],
) -> str:
    explicit = str(raw.get("failure_category", "")).strip()
    if explicit:
        return explicit
    if int(grounding.get("hallucination_count", 0) or 0) > 0:
        return "assertion-mismatch"
    failed_checks = [check for check in checks if isinstance(check, dict) and check.get("ok") is False]
    if failures:
        category = common.classify_process_failure(returncode=1, stderr="\n".join(failures))
        return category if category != "none" else "other"
    if failed_checks:
        return "tool-failure"
    return "none"


def evidence_tier_report(raw: dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw.get("evidence_tiers"), dict):
        return raw["evidence_tiers"]
    evidence = raw.get("evidence")
    rows = evidence if isinstance(evidence, list) else []
    if not rows:
        rows = [
            {"tier": "primary", "path": "benchmark-result.json", "claim": "normalized benchmark result"},
            {"tier": "primary", "path": run_packet.RUN_PACKET_FILENAME, "claim": "workflow run packet"},
        ]
    return common.normalize_evidence_tiers(rows)


def baseline_comparison(raw: dict[str, Any]) -> dict[str, Any]:
    baseline = raw.get("baseline") if isinstance(raw.get("baseline"), dict) else {}
    if not baseline:
        return {"available": False, "reason": "no baseline supplied"}
    return {
        "available": True,
        "baseline_run_id": str(baseline.get("run_id", "")),
        "baseline_path": str(baseline.get("path", "")),
        "comparison": str(baseline.get("comparison", "advisory")),
    }


def normalize_result(raw: dict[str, Any], task: dict[str, Any], pricing: dict[str, Any] | None) -> dict[str, Any]:
    quality = common.normalize_quality(raw.get("quality"))
    commands = common.require_list(raw, "commands")
    files_changed = [str(item) for item in common.require_list(raw, "files_changed")]
    checks = common.require_list(raw, "checks")
    skipped = [str(item) for item in common.require_list(raw, "skipped")]
    failures = [str(item) for item in common.require_list(raw, "failures")]
    notes = [str(item) for item in common.require_list(raw, "notes")]
    task_tokens = task.get("advisory_token_estimates", {})
    if not isinstance(task_tokens, dict):
        task_tokens = {}
    output_text = str(raw.get("output_text", ""))
    output_source = "\n".join([output_text, *notes, *failures])
    input_tokens = int(task_tokens.get("input_tokens_estimated", 0))
    static_tokens = int(task_tokens.get("static_navigation_context", 0))
    task_specific_tokens = int(task_tokens.get("task_specific_context", 0))
    loaded_context_tokens = estimate_loaded_context_tokens(raw)
    token_counter = common.token_count_metadata()
    packet = run_packet.build_run_packet(
        run_dir=Path(str(task.get("run_dir", "."))),
        run_id=str(task.get("run_id", "")),
        raw_entries=raw.get("evidence") if "evidence" in raw else raw.get("run_packet"),
        unsupported_claims=optional_string_list(raw.get("unsupported_claims")),
        workflow=str(task.get("workflow_name") or "agent-benchmarking"),
        current_phase="record-result",
        status="passed" if bool(quality["passed"]) and not failures else "failed",
        commands=commands,
        checks=checks,
        skipped=skipped,
        failed=failures,
        decisions=[str(item) for item in raw.get("decisions", [])] if isinstance(raw.get("decisions"), list) else [],
        evidence_paths=["benchmark-result.json", "REPORT.md"],
    )
    grounding = grounding_report(raw, packet)
    advisory_token_estimates = {
        "estimates": not token_counter["exact"],
        "exact": token_counter["exact"],
        "method": common.TOKEN_ESTIMATION_METHOD,
        "token_counter": token_counter,
        "input_tokens_estimated": input_tokens,
        "output_tokens_estimated": common.estimate_tokens(output_source),
        "static_navigation_context": static_tokens,
        "task_specific_context": task_specific_tokens,
        "cacheable_static_tokens_estimated": int(task_tokens.get("cacheable_static_tokens_estimated", static_tokens)),
        "loaded_context_tokens_estimated": loaded_context_tokens,
        "dynamic_context_tokens_estimated": task_specific_tokens,
        "context_saved_tokens_estimated": int(task_tokens.get("context_saved_tokens_estimated", 0) or 0),
    }
    raw_token_measurement = raw.get("token_measurement")
    if "token_measurement" in raw:
        if raw_token_measurement is None:
            raise ValueError("invalid explicit token_measurement: token_measurement must be an object")
        token_measurement = token_v1.normalize_measurement(raw_token_measurement)
    else:
        token_measurement = token_v1.build_measurement(
            provenance="tokenizer_artifact" if token_counter["exact"] else "heuristic_estimate",
            scope="artifact",
            tokenizer_or_estimator=(
                f"tiktoken:{token_counter.get('encoding', '')}"
                if token_counter["exact"]
                else "estimated_chars_div_4"
            ),
            input_tokens=input_tokens,
            output_tokens=int(advisory_token_estimates["output_tokens_estimated"]),
            complete=True,
        )
    elapsed_seconds = float(raw.get("elapsed_seconds", 0) or 0)
    metrics_standard = common.normalize_metrics_standard(
        raw.get("metrics_standard")
        or common.metrics_standard_from_timings(
            wall_seconds=elapsed_seconds if elapsed_seconds else None,
            peak_memory_mib=raw.get("peak_memory_mib"),
            cpu_utilization_percent=raw.get("cpu_utilization_percent"),
            cold_start=raw.get("cold_start"),
            warm_cache=raw.get("warm_cache"),
            repetitions=int(raw.get("repetitions", 1) or 1),
        )
    )
    run_config = common.normalize_run_config(raw.get("run_config") or task.get("run_config") or {})
    agent_task_metrics = common.normalize_agent_task_metrics(
        raw.get("agent_task_metrics"),
        grounding=grounding,
        commands=commands,
        checks=checks,
        failures=failures,
    )
    trajectory_signals = common.normalize_trajectory_signals(
        raw.get("trajectory_signals"),
        quality=quality,
        commands=commands,
        checks=checks,
        skipped=skipped,
        failures=failures,
    )
    ok = bool(quality["passed"]) and not failures and grounding["hallucination_count"] == 0
    determinism = common.normalize_determinism(
        raw.get("determinism") or task.get("determinism"),
        run_id=str(task.get("run_id", "")),
        task_id=str(task.get("task_id", "")),
        artifact_dir=Path(str(task.get("run_dir", "."))).name,
    )
    failure_category = failure_category_from_result(
        raw,
        failures=failures,
        checks=checks,
        grounding=grounding,
    )
    mismatch_kind = common.classify_mismatch(
        quality=quality,
        grounding=grounding,
        failures=failures,
        checks=checks,
        raw=raw,
    )
    evidence_tiers = evidence_tier_report(raw)
    routing_determinism = {
        "batch_run_id": determinism["batch_run_id"],
        "unit_run_id": determinism["unit_run_id"],
        "artifact_dir": determinism["artifact_dir"],
        "failure_category": failure_category,
        "mismatch_kind": mismatch_kind,
        "failure_fingerprint": "" if failure_category == "none" else common.failure_fingerprint(failure_category, failures, checks),
        "evidence_primary_available": evidence_tiers.get("primary_available", False),
    }
    report = {
        "schema_version": common.SCHEMA_VERSION,
        "tool": common.TOOL_NAME,
        "ok": ok,
        "status": "passed" if ok else "failed",
        "run_id": str(task.get("run_id", "")),
        "suite": str(task.get("suite", "")),
        "task_id": str(task.get("task_id", "")),
        "subject": str(raw.get("subject") or task.get("subject") or ""),
        "agent_tool": str(task.get("agent_tool", "")),
        "model_label": str(task.get("model_label", "")),
        "workflow_name": str(task.get("workflow_name", "")),
        "workflow_version": str(task.get("workflow_version", "")),
        "started_at": str(raw.get("started_at", "")),
        "finished_at": str(raw.get("finished_at", "")),
        "elapsed_seconds": elapsed_seconds,
        "quality": quality,
        "advisory_token_estimates": advisory_token_estimates,
        "token_measurement": token_measurement,
        "context_savings": task.get("context_savings", {"estimated_tokens_saved": 0, "packets": []}),
        "cost_estimates": cost_estimates(str(task.get("model_label", "")), advisory_token_estimates, pricing),
        "grounding": grounding,
        "metrics_standard": metrics_standard,
        "run_config": run_config,
        "agent_task_metrics": agent_task_metrics,
        "trajectory_signals": trajectory_signals,
        "determinism": determinism,
        "routing_determinism": routing_determinism,
        "evidence_tiers": evidence_tiers,
        "baseline_comparison": baseline_comparison(raw),
        "run_packet": packet,
        "run_packet_path": run_packet.RUN_PACKET_FILENAME,
        "commands": commands,
        "files_changed": files_changed,
        "checks": checks,
        "skipped": skipped,
        "failures": failures,
        "failure_taxonomy": common.normalize_failure_taxonomy(
            raw.get("failure_taxonomy")
            or taxonomy_from_result(
                grounding=grounding,
                skipped=skipped,
                failures=failures,
                checks=checks,
            )
        ),
        "quality_sections": common.quality_section_summary(
            quality,
            commands,
            checks,
            skipped,
            failures,
            grounding,
        ),
        "outliers": common.detect_outliers(
            {
                "advisory_token_estimates": advisory_token_estimates,
                "grounding": grounding,
                "run_packet_path": run_packet.RUN_PACKET_FILENAME,
                "skipped": skipped,
                "checks": checks,
            }
        ),
        "notes": notes,
    }
    return report


def taxonomy_from_result(
    *,
    grounding: dict[str, Any],
    skipped: list[str],
    failures: list[str],
    checks: list[Any],
) -> list[dict[str, str]]:
    taxonomy: list[dict[str, str]] = []
    for claim in grounding.get("unsupported_claims", []) or []:
        taxonomy.append({"category": "unsupported-claim", "detail": str(claim), "evidence": ""})
    for path in grounding.get("invented_paths", []) or []:
        taxonomy.append({"category": "invented-path", "detail": str(path), "evidence": ""})
    for command in grounding.get("invented_commands", []) or []:
        taxonomy.append({"category": "invented-command", "detail": str(command), "evidence": ""})
    for claim in grounding.get("false_validation_claims", []) or []:
        taxonomy.append({"category": "false-validation-claim", "detail": str(claim), "evidence": ""})
    for item in skipped:
        taxonomy.append({"category": "skipped-validation", "detail": str(item), "evidence": ""})
    for item in failures:
        taxonomy.append({"category": "other", "detail": str(item), "evidence": ""})
    for check in checks:
        if isinstance(check, dict) and check.get("ok") is False:
            taxonomy.append(
                {
                    "category": "tool-failure",
                    "detail": str(check.get("name", "failed check")),
                    "evidence": str(check.get("summary", ""))[:240],
                }
            )
    return taxonomy


def record_result(
    *,
    run_dir: Path,
    result_path: Path,
    pricing_path: Path | None = None,
    write: bool = False,
) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    task_path = run_dir / "benchmark-task.json"
    task = common.read_json(task_path)
    if not isinstance(task, dict):
        raise SystemExit("benchmark-task.json must contain an object.")
    raw = common.read_json(result_path.expanduser().resolve())
    if not isinstance(raw, dict):
        raise SystemExit("result report must be a JSON object.")
    pricing = load_pricing(pricing_path)
    task["run_dir"] = str(run_dir)
    try:
        report = normalize_result(raw, task, pricing)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    shape_issues = common.validate_benchmark_result_shape(report)
    if shape_issues:
        raise SystemExit("normalized benchmark result is invalid: " + "; ".join(shape_issues))
    if write:
        common.write_json(run_dir / "benchmark-result.json", report)
        common.write_json(run_dir / run_packet.RUN_PACKET_FILENAME, report["run_packet"])
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="prepared benchmark run folder")
    parser.add_argument("--result", required=True, help="raw result JSON")
    parser.add_argument("--pricing", help="optional local pricing table JSON")
    parser.add_argument("--write", action="store_true", help="write benchmark-result.json")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    return parser


def render_markdown(report: dict[str, Any]) -> str:
    signals = report.get("trajectory_signals") if isinstance(report.get("trajectory_signals"), dict) else {}
    negative_signals = sum(
        int(signals.get(key, 0) or 0)
        for key in common.TRAJECTORY_SIGNAL_COUNT_KEYS
        if key != "satisfaction_count"
    )
    lines = [
        "# Benchmark Result",
        "",
        f"- Run: `{report['run_id']}`",
        f"- Subject: {report['subject']}",
        f"- Status: {report['status']}",
        f"- Quality score: {report['quality'].get('score')}",
        f"- Estimated input tokens: {report['advisory_token_estimates']['input_tokens_estimated']}",
        f"- Estimated output tokens: {report['advisory_token_estimates']['output_tokens_estimated']}",
        f"- Estimated context tokens saved: {report['advisory_token_estimates'].get('context_saved_tokens_estimated', 0)}",
        f"- Hallucination signals: {report['grounding']['hallucination_count']}",
        f"- Negative trajectory signals: {negative_signals}",
        f"- Evidence coverage: {report['grounding']['evidence_coverage'].get('coverage_percent', 0)}%",
        f"- Cost estimate available: {report['cost_estimates']['available']}",
        "",
        "## Quality Sections",
        "",
    ]
    sections = report.get("quality_sections", {})
    if isinstance(sections, dict):
        for name in ("model_quality", "agent_behavior", "tool_behavior", "workflow_quality"):
            lines.append(f"- `{name}`: {json.dumps(sections.get(name, {}), sort_keys=True)}")
    else:
        lines.append("- None.")
    outliers = report.get("outliers", [])
    if outliers:
        lines.extend(["", "## Outliers", ""])
        for item in outliers:
            lines.append(f"- `{item.get('kind', 'outlier')}` {item.get('detail', '')}")
    return "\n".join(lines)


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    report = record_result(
        run_dir=Path(args.run_dir),
        result_path=Path(args.result),
        pricing_path=Path(args.pricing) if args.pricing else None,
        write=args.write,
    )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
