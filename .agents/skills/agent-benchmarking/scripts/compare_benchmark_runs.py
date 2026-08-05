#!/usr/bin/env python3
"""Compare normalized benchmark result reports."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import benchmark_common as common
from support import token_measurement_v1 as token_v1
from support import provider_evidence_adapters


def load_result(path: Path) -> dict[str, Any]:
    report_path = common.run_report_path(path.expanduser().resolve())
    data = common.read_json(report_path)
    if not isinstance(data, dict):
        raise SystemExit(f"benchmark report must be an object: {report_path}")
    if data.get("schema_version") != common.SCHEMA_VERSION:
        raise SystemExit(
            f"benchmark report has incompatible schema_version {data.get('schema_version')!r}: {report_path}"
        )
    shape_issues = common.validate_benchmark_result_shape(data)
    if shape_issues:
        raise SystemExit(f"benchmark report is not comparable: {'; '.join(shape_issues)}")
    ledger_path = str(data.get("run_packet_path", "")).strip()
    if ledger_path:
        ledger = (report_path.parent / ledger_path).resolve()
        try:
            ledger.relative_to(report_path.parent.resolve())
        except ValueError:
            raise SystemExit(f"benchmark report run packet path escapes run folder: {ledger_path}") from None
        if not ledger.exists():
            raise SystemExit(f"benchmark report run packet is missing: {ledger_path}")
    data["_evidence_root"] = str(report_path.parent)
    return data


def score(report: dict[str, Any]) -> float:
    quality = report.get("quality")
    if isinstance(quality, dict):
        try:
            return float(quality.get("score", 0))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def quality_passed_value(report: dict[str, Any]) -> int:
    quality = report.get("quality")
    if isinstance(quality, dict) and quality.get("passed") is True:
        return 1
    return 0


def ok_value(report: dict[str, Any]) -> int:
    return 1 if report.get("ok") is True else 0


TOKEN_KEYS = (
    "input_tokens_estimated",
    "output_tokens_estimated",
    "total_tokens",
    "cacheable_static_tokens_estimated",
    "loaded_context_tokens_estimated",
)
DEFAULT_UNMEASURED_TOKEN_METHOD = "model benchmark metrics"


def token_value(report: dict[str, Any], key: str) -> int:
    measurement = report.get("token_measurement")
    measurement_key = {
        "input_tokens_estimated": "input_tokens",
        "output_tokens_estimated": "output_tokens",
        "total_tokens": "total_tokens",
    }.get(key)
    if isinstance(measurement, dict) and measurement_key:
        try:
            return int(measurement.get(measurement_key, 0))
        except (TypeError, ValueError):
            return 0
    tokens = report.get("advisory_token_estimates")
    if isinstance(tokens, dict):
        try:
            return int(tokens.get(key, 0))
        except (TypeError, ValueError):
            return 0
    return 0


def token_measured(
    report: dict[str, Any],
    key: str,
    *,
    gate_scope: str,
    trusted_codex_home: Path | None,
    trusted_host_capture_root: Path | None,
) -> bool:
    if key not in {"input_tokens_estimated", "output_tokens_estimated", "total_tokens"}:
        return False
    evidence_root_raw = report.get("_evidence_root")
    evidence_root = Path(evidence_root_raw) if isinstance(evidence_root_raw, str) and evidence_root_raw else None
    measurement = report.get("token_measurement")
    evidence = measurement.get("evidence") if isinstance(measurement, dict) else {}
    adapter_id = str(evidence.get("adapter_id", "")) if isinstance(evidence, dict) else ""
    gate = token_v1.gate_eligibility(
        measurement,
        gate_scope=gate_scope,
        evidence_root=evidence_root,
        trusted_host_capture_root=trusted_host_capture_root,
        expected_run_id=report.get("run_id"),
        expected_model_label=report.get("model_label"),
    )
    if gate["eligible"] is not True:
        return False
    if str(gate_scope).replace("-", "_") != "full_run":
        return True
    if adapter_id != "codex-rollout-v1":
        return adapter_id in {
            "claude-code-result-v1",
            "github-copilot-otel-v1",
            "openai-responses-usage-v1",
        }
    return not provider_evidence_adapters.verify_codex_ledger_receipt(
        measurement,
        report.get("token_measurement_receipt"),
        evidence_root=evidence_root,
        trusted_codex_home=trusted_codex_home,
    )


def token_measurement_boundary(report: dict[str, Any]) -> dict[str, str]:
    measurement = report.get("token_measurement")
    if not isinstance(measurement, dict):
        return {
            "provenance": "",
            "scope": "",
            "accounting_unit": "",
            "tokenizer_or_estimator": "",
            "host_surface": "",
            "model_provider": "",
            "model_label": str(report.get("model_label", "")),
        }
    return {
        "provenance": str(measurement.get("provenance", "")),
        "scope": str(measurement.get("scope", "")),
        "accounting_unit": str(measurement.get("accounting_unit", "")),
        "tokenizer_or_estimator": str(measurement.get("tokenizer_or_estimator", "")),
        "host_surface": str(measurement.get("host_surface", "")),
        "model_provider": str(measurement.get("model_provider", "")),
        "model_label": str(report.get("model_label", "")),
    }


def token_measurement_boundary_issues(
    first: dict[str, Any],
    last: dict[str, Any],
) -> list[str]:
    baseline = token_measurement_boundary(first)
    candidate = token_measurement_boundary(last)
    issues: list[str] = []
    for field in (
        "scope",
        "provenance",
        "accounting_unit",
        "tokenizer_or_estimator",
        "host_surface",
        "model_provider",
        "model_label",
    ):
        if baseline[field] != candidate[field]:
            issues.append(
                f"token measurement {field} differs: "
                f"{baseline[field] or 'missing'} vs {candidate[field] or 'missing'}"
            )
    return issues


def cost_value(report: dict[str, Any]) -> float:
    cost = report.get("cost_estimates")
    if isinstance(cost, dict):
        try:
            value = float(cost.get("total_estimated", 0))
            return value if math.isfinite(value) else 0.0
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def cost_measured(report: dict[str, Any]) -> bool:
    cost = report.get("cost_estimates")
    if not isinstance(cost, dict) or "total_estimated" not in cost:
        return False
    if cost.get("available") is not True or cost.get("measured") is not True:
        return False
    if cost.get("provenance") not in {"provider_telemetry", "provider_invoice"}:
        return False
    completeness = cost.get("completeness")
    if not isinstance(completeness, dict) or completeness.get("complete") is not True:
        return False
    missing = completeness.get("missing")
    if not isinstance(missing, list) or missing:
        return False
    currency = cost.get("currency")
    if not isinstance(currency, str) or not currency.strip():
        return False
    amount = cost.get("total_estimated")
    if isinstance(amount, bool):
        return False
    try:
        numeric_amount = float(amount)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(numeric_amount) or numeric_amount < 0:
        return False
    # Generic comparisons have no implemented invoice/telemetry cost adapter.
    # Provider-cost promotion remains available only in the specialized
    # three-arm validator, which binds raw invoice line items to a trial nonce.
    return False


def cost_currency(report: dict[str, Any]) -> str:
    cost = report.get("cost_estimates")
    if not isinstance(cost, dict):
        return ""
    currency = cost.get("currency")
    return currency.strip().upper() if isinstance(currency, str) else ""


def standard_metric_value(report: dict[str, Any], key: str) -> float:
    metrics = report.get("metrics_standard")
    if isinstance(metrics, dict):
        try:
            return float(metrics.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def standard_metric_measured(report: dict[str, Any], key: str) -> bool:
    metrics = report.get("metrics_standard")
    if not isinstance(metrics, dict) or key not in metrics or metrics.get(key) in {None, ""}:
        return False
    try:
        value = float(metrics.get(key))
    except (TypeError, ValueError):
        return False
    return value > 0


NEGATIVE_TRAJECTORY_SIGNAL_KEYS = tuple(
    key for key in sorted(common.TRAJECTORY_SIGNAL_COUNT_KEYS) if key != "satisfaction_count"
)


def trajectory_signal_value(report: dict[str, Any], key: str) -> int:
    signals = report.get("trajectory_signals")
    if isinstance(signals, dict):
        try:
            return int(signals.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def trajectory_negative_signal_total(report: dict[str, Any]) -> int:
    return sum(trajectory_signal_value(report, key) for key in NEGATIVE_TRAJECTORY_SIGNAL_KEYS)


def both_measured(
    first: dict[str, Any],
    last: dict[str, Any],
    kind: str,
    key: str,
    *,
    token_gate_scope: str,
    trusted_codex_home: Path | None,
    trusted_host_capture_root: Path | None,
) -> bool:
    if kind == "token":
        return (
            not token_measurement_boundary_issues(first, last)
            and token_measured(
                first,
                key,
                gate_scope=token_gate_scope,
                trusted_codex_home=trusted_codex_home,
                trusted_host_capture_root=trusted_host_capture_root,
            )
            and token_measured(
                last,
                key,
                gate_scope=token_gate_scope,
                trusted_codex_home=trusted_codex_home,
                trusted_host_capture_root=trusted_host_capture_root,
            )
        )
    if kind == "cost":
        first_currency = cost_currency(first)
        last_currency = cost_currency(last)
        return (
            cost_measured(first)
            and cost_measured(last)
            and bool(first_currency)
            and first_currency == last_currency
        )
    if kind == "standard":
        return standard_metric_measured(first, key) and standard_metric_measured(last, key)
    return False


def grounding_count(report: dict[str, Any], key: str) -> int:
    grounding = report.get("grounding")
    if not isinstance(grounding, dict):
        return 0
    value = grounding.get(key)
    if isinstance(value, list):
        return len(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def evidence_coverage_percent(report: dict[str, Any]) -> float:
    grounding = report.get("grounding")
    if not isinstance(grounding, dict):
        return 0.0
    coverage = grounding.get("evidence_coverage")
    if not isinstance(coverage, dict):
        return 0.0
    try:
        return float(coverage.get("coverage_percent", 0))
    except (TypeError, ValueError):
        return 0.0


def failed_check_count(report: dict[str, Any]) -> int:
    total = 0
    for item in report.get("checks", []):
        if isinstance(item, dict) and item.get("ok") is False:
            total += 1
    return total


def format_pattern_item(value: Any) -> str:
    if isinstance(value, dict):
        labels: list[str] = []
        for key in ("task", "mode", "category", "name", "path", "command"):
            text = str(value.get(key, "")).strip()
            if text:
                labels.append(text)
        detail = ""
        for key in ("reason", "detail", "message", "error"):
            detail = str(value.get(key, "")).strip()
            if detail:
                break
        if labels and detail:
            return f"{' / '.join(labels)}: {detail}"
        if labels:
            return " / ".join(labels)
        if detail:
            return detail
        return json.dumps(value, sort_keys=True)
    return str(value)


def optimization_gate_report(
    report: dict[str, Any],
    *,
    allow_quality_drop: float = 0.0,
    require_improvement: bool = True,
    optimization_scope: str = "full_run",
) -> dict[str, Any]:
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    rejections: list[str] = []
    improvements: list[str] = []
    eligible_efficiency_improvements: list[str] = []
    gate_mode = "savings" if require_improvement else "quality-floor-only"
    comparison_status = summary.get("comparison_status")
    if comparison_status == "insufficient-runs":
        rejections.append("optimization gate requires at least two comparable runs")
    elif comparison_status != "comparable":
        rejections.append("reports are not comparable; optimization gate requires comparable runs")
    quality_delta = float(summary.get("quality_delta", 0) or 0)
    if quality_delta < -allow_quality_drop:
        rejections.append(f"quality delta {quality_delta:g} is below floor {-allow_quality_drop:g}")
    if quality_delta > 0:
        improvements.append(f"quality score improved by {quality_delta:g}")
    quality_passed_delta = int(summary.get("quality_passed_delta", 0) or 0)
    if quality_passed_delta < 0:
        rejections.append("quality passed regressed from true to false")
    elif quality_passed_delta > 0:
        improvements.append("quality passed improved from false to true")
    ok_delta = int(summary.get("ok_delta", 0) or 0)
    if ok_delta < 0:
        rejections.append("run ok regressed from true to false")
    elif ok_delta > 0:
        improvements.append("run ok improved from false to true")
    guarded_deltas = (
        ("failed_check_delta", "failed check count"),
        ("failure_delta", "failure count"),
        ("skipped_delta", "skipped check count"),
        ("hallucination_delta", "hallucination count"),
    )
    for key, label in guarded_deltas:
        value = int(summary.get(key, 0) or 0)
        if value > 0:
            rejections.append(f"{label} increased by {value}")
        elif value < 0:
            improvements.append(f"{label} improved by {-value}")
    evidence_delta = float(summary.get("evidence_coverage_delta", 0) or 0)
    if evidence_delta < 0:
        rejections.append(f"evidence coverage dropped by {-evidence_delta:g} percentage point(s)")
    elif evidence_delta > 0:
        improvements.append(f"evidence coverage improved by {evidence_delta:g} percentage point(s)")
    lower_is_better = (
        ("input_token_delta", "input_tokens_estimated"),
        ("output_token_delta", "output_tokens_estimated"),
        ("cacheable_static_token_delta", "cacheable_static_tokens_estimated"),
        ("loaded_context_token_delta", "loaded_context_tokens_estimated"),
        ("cost_delta", "cost_estimated"),
        ("e2e_latency_ms_delta", "e2e_latency_ms"),
        ("tpot_ms_delta", "tpot_ms"),
        ("peak_memory_mib_delta", "peak_memory_mib"),
    )
    for key, label in lower_is_better:
        value = float(summary.get(key, 0) or 0)
        if value < 0 and bool(summary.get(f"{key}_measured")):
            delta = -value
            improvements.append(f"{label} improved by {delta:g}")
    total_token_delta = float(summary.get("total_token_delta", 0) or 0)
    total_token_measured = bool(summary.get("total_token_delta_measured"))
    if total_token_measured and total_token_delta < 0:
        total_improvement = f"total_tokens improved by {-total_token_delta:g}"
        improvements.append(total_improvement)
        eligible_efficiency_improvements.append(total_improvement)
    elif total_token_measured and total_token_delta > 0:
        rejections.append(f"total_tokens increased by {total_token_delta:g}")
    cost_delta = float(summary.get("cost_delta", 0) or 0)
    if optimization_scope == "full_run" and bool(summary.get("cost_delta_measured")) and cost_delta < 0:
        cost_improvement = f"measured provider cost improved by {-cost_delta:g}"
        eligible_efficiency_improvements.append(cost_improvement)
        if cost_improvement not in improvements:
            improvements.append(cost_improvement)
    if require_improvement and not eligible_efficiency_improvements:
        if optimization_scope == "artifact":
            rejections.append(
                "artifact optimization gate requires a complete tokenizer-artifact total-token improvement"
            )
        else:
            rejections.append(
                "full-run optimization gate requires a complete provider-backed token or measured-cost improvement"
            )
    accepted = not rejections
    return {
        "accepted": accepted,
        "status": "accepted" if accepted else "rejected",
        "policy": {
            "allow_quality_drop": allow_quality_drop,
            "require_comparable": True,
            "require_improvement": require_improvement,
            "gate_mode": gate_mode,
            "measurement_scope": optimization_scope,
            "reject_on_increased_failures_skips_hallucinations_or_failed_checks": True,
            "reject_on_evidence_coverage_drop": True,
        },
        "improvements": improvements,
        "eligible_efficiency_improvements": eligible_efficiency_improvements,
        "rejections": rejections,
    }


def apply_optimization_gate(
    report: dict[str, Any],
    *,
    allow_quality_drop: float = 0.0,
    require_improvement: bool = True,
    optimization_scope: str = "full_run",
) -> dict[str, Any]:
    gated = dict(report)
    gate = optimization_gate_report(
        gated,
        allow_quality_drop=allow_quality_drop,
        require_improvement=require_improvement,
        optimization_scope=optimization_scope,
    )
    gated["optimization_gate"] = gate
    gated["ok"] = bool(gated.get("ok", True)) and gate["accepted"]
    if not gate["accepted"]:
        gated["status"] = "optimization-rejected"
    return gated


CONTROL_SKILL_CONDITIONS = {"no-skill", "without-skill", "control"}
TREATMENT_SKILL_CONDITIONS = {"with-skill", "skill", "treatment"}


def run_config_value(report: dict[str, Any], key: str) -> str:
    config = report.get("run_config")
    if not isinstance(config, dict):
        return ""
    return str(config.get(key, "")).strip()


def skill_utility_gate_report(report: dict[str, Any], first: dict[str, Any], last: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    rejections: list[str] = []
    improvements: list[str] = []
    if summary.get("comparison_status") != "comparable":
        rejections.append("skill utility gate requires comparable paired runs")
    first_condition = run_config_value(first, "skill_condition").lower()
    last_condition = run_config_value(last, "skill_condition").lower()
    if first_condition not in CONTROL_SKILL_CONDITIONS:
        rejections.append("baseline run_config.skill_condition must be no-skill, without-skill, or control")
    if last_condition not in TREATMENT_SKILL_CONDITIONS:
        rejections.append("candidate run_config.skill_condition must be with-skill, skill, or treatment")
    first_skill = run_config_value(first, "skill_name")
    last_skill = run_config_value(last, "skill_name")
    skill_name = last_skill or first_skill
    if not first_skill:
        rejections.append("baseline run_config.skill_name is required")
    if not last_skill:
        rejections.append("candidate run_config.skill_name is required")
    if first_skill and last_skill and first_skill != last_skill:
        rejections.append(f"skill_name differs: {first_skill!r} vs {last_skill!r}")

    quality_delta = float(summary.get("quality_delta", 0) or 0)
    quality_passed_delta = int(summary.get("quality_passed_delta", 0) or 0)
    ok_delta = int(summary.get("ok_delta", 0) or 0)
    if quality_delta < 0:
        rejections.append(f"quality delta {quality_delta:g} is negative")
    elif quality_delta > 0:
        improvements.append(f"quality score improved by {quality_delta:g}")
    if quality_passed_delta < 0:
        rejections.append("quality passed regressed from true to false")
    elif quality_passed_delta > 0:
        improvements.append("quality passed improved from false to true")
    if ok_delta < 0:
        rejections.append("run ok regressed from true to false")
    elif ok_delta > 0:
        improvements.append("run ok improved from false to true")

    for key, label in (
        ("failed_check_delta", "failed check count"),
        ("failure_delta", "failure count"),
        ("skipped_delta", "skipped check count"),
        ("hallucination_delta", "hallucination count"),
        ("trajectory_negative_signal_delta", "negative trajectory signal count"),
    ):
        value = int(summary.get(key, 0) or 0)
        if value > 0:
            rejections.append(f"{label} increased by {value}")
        elif value < 0:
            improvements.append(f"{label} improved by {-value}")
    evidence_delta = float(summary.get("evidence_coverage_delta", 0) or 0)
    if evidence_delta < 0:
        rejections.append(f"evidence coverage dropped by {-evidence_delta:g} percentage point(s)")
    elif evidence_delta > 0:
        improvements.append(f"evidence coverage improved by {evidence_delta:g} percentage point(s)")

    # Component deltas remain diagnostics, but they cannot establish equal-
    # quality economics. Input can fall while output rises enough to make the
    # provider-backed total regress.
    for key, label in (
        ("input_token_delta", "input_tokens_estimated"),
        ("output_token_delta", "output_tokens_estimated"),
        ("loaded_context_token_delta", "loaded_context_tokens_estimated"),
        ("cacheable_static_token_delta", "cacheable_static_tokens_estimated"),
    ):
        value = float(summary.get(key, 0) or 0)
        if value < 0 and bool(summary.get(f"{key}_measured")):
            improvements.append(f"{label} improved by {-value:g}")

    eligible_efficiency_improvements: list[str] = []
    total_token_delta = float(summary.get("total_token_delta", 0) or 0)
    total_token_measured = bool(summary.get("total_token_delta_measured"))
    if total_token_measured and total_token_delta < 0:
        total_improvement = f"total_tokens improved by {-total_token_delta:g}"
        improvements.append(total_improvement)
        eligible_efficiency_improvements.append(total_improvement)
    elif total_token_measured and total_token_delta > 0:
        rejections.append(f"total_tokens increased by {total_token_delta:g}")

    cost_delta = float(summary.get("cost_delta", 0) or 0)
    if bool(summary.get("cost_delta_measured")) and cost_delta < 0:
        cost_improvement = f"measured provider cost improved by {-cost_delta:g}"
        improvements.append(cost_improvement)
        eligible_efficiency_improvements.append(cost_improvement)

    quality_or_pass_win = quality_delta > 0 or quality_passed_delta > 0
    token_or_cost_win = bool(eligible_efficiency_improvements)
    if not quality_or_pass_win and not token_or_cost_win:
        rejections.append("skill utility requires positive quality/pass delta or measured token/cost reduction at equal quality")

    # TokenMeasurementV1 total already includes input and output. Loaded context
    # is an input-like maintainability diagnostic and must not be added again.
    first_tokens = token_value(first, "total_tokens")
    last_tokens = token_value(last, "total_tokens")
    token_overhead_ratio = round((last_tokens - first_tokens) / first_tokens, 4) if first_tokens else None
    cost_efficiency = round(quality_delta / cost_delta, 6) if cost_delta > 0 and quality_delta > 0 else None
    interference_detected = any(
        int(summary.get(key, 0) or 0) > 0
        for key in ("failed_check_delta", "failure_delta", "skipped_delta", "hallucination_delta", "trajectory_negative_signal_delta")
    ) or quality_delta < 0 or quality_passed_delta < 0
    accepted = not rejections
    return {
        "accepted": accepted,
        "status": "accepted" if accepted else "rejected",
        "skill_name": skill_name,
        "policy": {
            "require_comparable": True,
            "require_control_then_treatment": True,
            "require_no_quality_failure_skip_hallucination_evidence_regression": True,
            "require_positive_quality_or_measured_equal_quality_token_cost_reduction": True,
            "canonical_equal_quality_token_metric": "total_tokens",
            "measured_cost_requires_same_currency": True,
        },
        "derived": {
            "skill_quality_delta": quality_delta,
            "skill_pass_delta": quality_passed_delta,
            "token_overhead_ratio": token_overhead_ratio,
            "cost_efficiency": cost_efficiency,
            "interference_detected": interference_detected,
        },
        "improvements": improvements,
        "eligible_efficiency_improvements": eligible_efficiency_improvements,
        "rejections": rejections,
    }


def apply_skill_utility_gate(report: dict[str, Any], first: dict[str, Any], last: dict[str, Any]) -> dict[str, Any]:
    gated = dict(report)
    gate = skill_utility_gate_report(gated, first, last)
    gated["skill_utility_gate"] = gate
    gated["ok"] = bool(gated.get("ok", True)) and gate["accepted"]
    if not gate["accepted"]:
        gated["status"] = "skill-utility-rejected"
    return gated


def compare_runs(
    paths: list[Path],
    *,
    require_comparable: bool = False,
    optimization_gate: bool = False,
    skill_utility_gate: bool = False,
    allow_quality_drop: float = 0.0,
    require_improvement: bool = True,
    optimization_scope: str = "full_run",
    trusted_codex_home: Path | None = None,
    trusted_host_capture_root: Path | None = None,
) -> dict[str, Any]:
    if len(paths) < 2:
        raise SystemExit("compare requires at least two benchmark reports or run folders.")
    reports = [load_result(path) for path in paths]
    first = reports[0]
    last = reports[-1]
    comparability: list[str] = []
    for report in reports[1:]:
        comparability.extend(common.comparability_issues(first, report))
    comparability = sorted(set(comparability))
    if require_comparable and comparability:
        raise SystemExit(f"benchmark reports are not comparable: {'; '.join(comparability)}")
    failure_counter: Counter[str] = Counter()
    taxonomy_counter: Counter[str] = Counter()
    failed_check_counter: Counter[str] = Counter()
    for report in reports:
        for item in report.get("failures", []):
            failure_counter[format_pattern_item(item)] += 1
        for item in report.get("failure_taxonomy", []):
            if isinstance(item, dict):
                taxonomy_counter[str(item.get("category", "other"))] += 1
            elif str(item).strip():
                taxonomy_counter["other"] += 1
        for check in report.get("checks", []):
            if isinstance(check, dict) and check.get("ok") is False:
                failed_check_counter[format_pattern_item(check.get("name", check))] += 1
    comparable = not comparability
    token_gate_scope = str(optimization_scope).replace("-", "_")
    token_boundary_issues = token_measurement_boundary_issues(first, last)
    token_measurement_comparability = {
        "ok": not token_boundary_issues,
        "issues": token_boundary_issues,
        "baseline": token_measurement_boundary(first),
        "candidate": token_measurement_boundary(last),
    }
    summary = {
        "runs": len(reports),
        "baseline_run": first.get("run_id", ""),
        "comparison_run": last.get("run_id", ""),
        "comparison_status": "comparable" if comparable else "not-comparable",
        "delta_interpretation": (
            "regression-signal"
            if comparable
            else "advisory-only; run configuration, suite, task, or workflow differs"
        ),
        "quality_delta": round(score(last) - score(first), 4),
        "quality_passed_delta": quality_passed_value(last) - quality_passed_value(first),
        "ok_delta": ok_value(last) - ok_value(first),
        "input_token_delta": token_value(last, "input_tokens_estimated") - token_value(first, "input_tokens_estimated"),
        "token_measurement_scope": token_gate_scope,
        "token_measurement_boundary_match": not token_boundary_issues,
        "token_measurement_boundary_issues": token_boundary_issues,
        "input_token_delta_measured": both_measured(first, last, "token", "input_tokens_estimated", token_gate_scope=token_gate_scope, trusted_codex_home=trusted_codex_home, trusted_host_capture_root=trusted_host_capture_root),
        "output_token_delta": token_value(last, "output_tokens_estimated") - token_value(first, "output_tokens_estimated"),
        "output_token_delta_measured": both_measured(first, last, "token", "output_tokens_estimated", token_gate_scope=token_gate_scope, trusted_codex_home=trusted_codex_home, trusted_host_capture_root=trusted_host_capture_root),
        "total_token_delta": token_value(last, "total_tokens") - token_value(first, "total_tokens"),
        "total_token_delta_measured": both_measured(first, last, "token", "total_tokens", token_gate_scope=token_gate_scope, trusted_codex_home=trusted_codex_home, trusted_host_capture_root=trusted_host_capture_root),
        "cacheable_static_token_delta": token_value(last, "cacheable_static_tokens_estimated") - token_value(first, "cacheable_static_tokens_estimated"),
        "cacheable_static_token_delta_measured": both_measured(first, last, "token", "cacheable_static_tokens_estimated", token_gate_scope=token_gate_scope, trusted_codex_home=trusted_codex_home, trusted_host_capture_root=trusted_host_capture_root),
        "loaded_context_token_delta": token_value(last, "loaded_context_tokens_estimated") - token_value(first, "loaded_context_tokens_estimated"),
        "loaded_context_token_delta_measured": both_measured(first, last, "token", "loaded_context_tokens_estimated", token_gate_scope=token_gate_scope, trusted_codex_home=trusted_codex_home, trusted_host_capture_root=trusted_host_capture_root),
        "hallucination_delta": grounding_count(last, "hallucination_count") - grounding_count(first, "hallucination_count"),
        "evidence_coverage_delta": round(evidence_coverage_percent(last) - evidence_coverage_percent(first), 2),
        "cost_delta": round(cost_value(last) - cost_value(first), 8),
        "cost_delta_measured": both_measured(first, last, "cost", "total_estimated", token_gate_scope=token_gate_scope, trusted_codex_home=trusted_codex_home, trusted_host_capture_root=trusted_host_capture_root),
        "baseline_cost_currency": cost_currency(first),
        "candidate_cost_currency": cost_currency(last),
        "cost_currency": cost_currency(first) if cost_currency(first) == cost_currency(last) else "",
        "cost_currency_match": bool(cost_currency(first)) and cost_currency(first) == cost_currency(last),
        "e2e_latency_ms_delta": round(standard_metric_value(last, "e2e_latency_ms") - standard_metric_value(first, "e2e_latency_ms"), 4),
        "e2e_latency_ms_delta_measured": both_measured(first, last, "standard", "e2e_latency_ms", token_gate_scope=token_gate_scope, trusted_codex_home=trusted_codex_home, trusted_host_capture_root=trusted_host_capture_root),
        "tpot_ms_delta": round(standard_metric_value(last, "tpot_ms") - standard_metric_value(first, "tpot_ms"), 4),
        "tpot_ms_delta_measured": both_measured(first, last, "standard", "tpot_ms", token_gate_scope=token_gate_scope, trusted_codex_home=trusted_codex_home, trusted_host_capture_root=trusted_host_capture_root),
        "peak_memory_mib_delta": round(standard_metric_value(last, "peak_memory_mib") - standard_metric_value(first, "peak_memory_mib"), 4),
        "peak_memory_mib_delta_measured": both_measured(first, last, "standard", "peak_memory_mib", token_gate_scope=token_gate_scope, trusted_codex_home=trusted_codex_home, trusted_host_capture_root=trusted_host_capture_root),
        "skipped_delta": len(last.get("skipped", [])) - len(first.get("skipped", [])),
        "failed_check_delta": failed_check_count(last) - failed_check_count(first),
        "failure_delta": len(last.get("failures", [])) - len(first.get("failures", [])),
        "trajectory_negative_signal_delta": trajectory_negative_signal_total(last) - trajectory_negative_signal_total(first),
    }
    report = {
        "schema_version": common.SCHEMA_VERSION,
        "tool": common.TOOL_NAME,
        "ok": True,
        "status": "compared" if comparable else "not-comparable",
        "summary": summary,
        "comparability": {
            "ok": not comparability,
            "issues": comparability,
        },
        "token_measurement_comparability": token_measurement_comparability,
        "not_comparable_reasons": comparability,
        "runs": [
            {
                "run_id": report.get("run_id", ""),
                "subject": report.get("subject", ""),
                "workflow_version": report.get("workflow_version", ""),
                "quality_score": score(report),
                "quality_passed": bool(quality_passed_value(report)),
                "ok": bool(ok_value(report)),
                "input_tokens_estimated": token_value(report, "input_tokens_estimated"),
                "output_tokens_estimated": token_value(report, "output_tokens_estimated"),
                "total_tokens": token_value(report, "total_tokens"),
                "loaded_context_tokens_estimated": token_value(report, "loaded_context_tokens_estimated"),
                "cacheable_static_tokens_estimated": token_value(report, "cacheable_static_tokens_estimated"),
                "hallucinations": grounding_count(report, "hallucination_count"),
                "evidence_coverage_percent": evidence_coverage_percent(report),
                "cost_estimated": cost_value(report),
                "e2e_latency_ms": standard_metric_value(report, "e2e_latency_ms"),
                "tpot_ms": standard_metric_value(report, "tpot_ms"),
                "peak_memory_mib": standard_metric_value(report, "peak_memory_mib"),
                "skipped": len(report.get("skipped", [])),
                "failures": len(report.get("failures", [])),
                "failed_checks": failed_check_count(report),
                "trajectory_negative_signals": trajectory_negative_signal_total(report),
            }
            for report in reports
        ],
        "recurring_patterns": {
            "failures": [item for item, _count in failure_counter.most_common(10)],
            "failure_taxonomy": [
                {"category": item, "count": count}
                for item, count in taxonomy_counter.most_common(10)
            ],
            "failed_checks": [item for item, _count in failed_check_counter.most_common(10)],
            "unsupported_claims": [
                item
                for item, _count in Counter(
                    format_pattern_item(claim)
                    for report in reports
                    for claim in (report.get("grounding", {}) if isinstance(report.get("grounding"), dict) else {}).get("unsupported_claims", [])
                ).most_common(10)
            ],
        },
        "quality_sections": {
            "model_quality": {
                "baseline": first.get("quality_sections", {}).get("model_quality", {}),
                "comparison": last.get("quality_sections", {}).get("model_quality", {}),
            },
            "agent_behavior": {
                "baseline": first.get("quality_sections", {}).get("agent_behavior", {}),
                "comparison": last.get("quality_sections", {}).get("agent_behavior", {}),
            },
            "tool_behavior": {
                "baseline": first.get("quality_sections", {}).get("tool_behavior", {}),
                "comparison": last.get("quality_sections", {}).get("tool_behavior", {}),
            },
            "workflow_quality": {
                "baseline": first.get("quality_sections", {}).get("workflow_quality", {}),
                "comparison": last.get("quality_sections", {}).get("workflow_quality", {}),
            },
        },
        "outliers": [
            {"run_id": report.get("run_id", ""), **item}
            for report in reports
            for item in (report.get("outliers", []) if isinstance(report.get("outliers"), list) else [])
            if isinstance(item, dict)
        ],
    }
    if optimization_gate:
        report = apply_optimization_gate(
            report,
            allow_quality_drop=allow_quality_drop,
            require_improvement=require_improvement,
            optimization_scope=token_gate_scope,
        )
    if skill_utility_gate:
        report = apply_skill_utility_gate(report, first, last)
    return report


def find_benchmark_reports(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(root.rglob("benchmark-result.json"), key=lambda path: path.stat().st_mtime)


def insufficient_compare_latest_report(root: Path, reports: list[Path]) -> dict[str, Any]:
    advisory = "compare-latest needs at least two benchmark-result.json files before a trend can be claimed."
    return {
        "schema_version": common.SCHEMA_VERSION,
        "tool": f"{common.TOOL_NAME}.compare-latest",
        "ok": True,
        "status": "insufficient-runs",
        "summary": {
            "run_count": len(reports),
            "required_run_count": 2,
            "runs_root": str(root),
            "comparison_status": "insufficient-runs",
            "delta_interpretation": "advisory-only; at least two benchmark-result.json files are required",
        },
        "issues": [],
        "advisories": [advisory],
        "skipped": ["benchmark comparison skipped because fewer than two retained benchmark results were available"],
        "report_paths": [str(path) for path in reports],
        "next_action": "Retain at least one more benchmark-result.json before claiming a trend.",
    }


def compare_latest(
    root: Path,
    *,
    require_comparable: bool = False,
    optimization_gate: bool = False,
    skill_utility_gate: bool = False,
    allow_quality_drop: float = 0.0,
    require_improvement: bool = True,
    optimization_scope: str = "full_run",
    trusted_codex_home: Path | None = None,
    trusted_host_capture_root: Path | None = None,
) -> dict[str, Any]:
    resolved_root = root.expanduser().resolve()
    reports = find_benchmark_reports(resolved_root)
    if len(reports) < 2:
        report = insufficient_compare_latest_report(resolved_root, reports)
        if optimization_gate:
            return apply_optimization_gate(
                report,
                allow_quality_drop=allow_quality_drop,
                require_improvement=require_improvement,
                optimization_scope=optimization_scope,
            )
        if skill_utility_gate:
            report["skill_utility_gate"] = {
                "accepted": False,
                "status": "rejected",
                "skill_name": "",
                "policy": {"require_comparable": True, "require_control_then_treatment": True},
                "derived": {},
                "improvements": [],
                "rejections": ["skill utility gate requires paired no-skill/with-skill runs"],
            }
            report["ok"] = False
            report["status"] = "skill-utility-rejected"
        return report
    loaded = [(path, load_result(path)) for path in reports]
    latest_path, _latest = loaded[-1]
    previous = loaded[:-1]
    comparable_previous = [
        (path, report)
        for path, report in previous
        if not common.comparability_issues(report, _latest)
    ]
    candidates = comparable_previous or previous
    best_path, _best = max(candidates, key=lambda item: (score(item[1]), -len(item[1].get("failures", []))))
    report = compare_runs(
        [best_path, latest_path],
        require_comparable=require_comparable,
        optimization_gate=optimization_gate,
        skill_utility_gate=skill_utility_gate,
        allow_quality_drop=allow_quality_drop,
        require_improvement=require_improvement,
        optimization_scope=optimization_scope,
        trusted_codex_home=trusted_codex_home,
        trusted_host_capture_root=trusted_host_capture_root,
    )
    report["summary"]["baseline_selection"] = (
        "previous-best-comparable-quality-score"
        if comparable_previous
        else "previous-best-quality-score-no-comparable-baseline"
    )
    report["summary"]["baseline_path"] = str(best_path)
    report["summary"]["comparison_path"] = str(latest_path)
    return report


def summarize_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    output: dict[str, Any] = {
        "schema_version": report.get("schema_version", common.SCHEMA_VERSION),
        "tool": report.get("tool", f"{common.TOOL_NAME}.compare"),
        "ok": bool(report.get("ok", True)),
        "status": report.get("status", summary.get("comparison_status", "")),
        "summary": {
            "comparison_status": summary.get("comparison_status", report.get("status", "")),
            "run_count": summary.get("run_count", summary.get("runs", 0)),
            "required_run_count": summary.get("required_run_count", 0),
            "quality_delta": summary.get("quality_delta", 0),
            "quality_passed_delta": summary.get("quality_passed_delta", 0),
            "ok_delta": summary.get("ok_delta", 0),
            "input_token_delta": summary.get("input_token_delta", 0),
            "loaded_context_token_delta": summary.get("loaded_context_token_delta", 0),
            "cacheable_static_token_delta": summary.get("cacheable_static_token_delta", 0),
            "skipped_delta": summary.get("skipped_delta", 0),
            "failed_check_delta": summary.get("failed_check_delta", 0),
            "trajectory_negative_signal_delta": summary.get("trajectory_negative_signal_delta", 0),
            "advisory_count": len(report.get("advisories", [])) if isinstance(report.get("advisories"), list) else 0,
            "issue_count": len(report.get("issues", [])) if isinstance(report.get("issues"), list) else 0,
            "skipped_count": len(report.get("skipped", [])) if isinstance(report.get("skipped"), list) else 0,
            "optimization_gate": report.get("optimization_gate", {}).get("status", ""),
            "skill_utility_gate": report.get("skill_utility_gate", {}).get("status", ""),
        },
    }
    if not compact:
        output["advisories"] = report.get("advisories", [])
        output["issues"] = report.get("issues", [])
        output["skipped"] = report.get("skipped", [])
        output["report_paths"] = report.get("report_paths", [])
        output["next_action"] = report.get("next_action", "")
    return output


def render_markdown(report: dict[str, Any]) -> str:
    if report.get("status") == "insufficient-runs":
        summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
        lines = [
            "# Benchmark Comparison",
            "",
            "- Status: insufficient-runs",
            f"- Runs found: {summary.get('run_count', 0)}",
            f"- Required runs: {summary.get('required_run_count', 2)}",
            f"- Delta interpretation: {summary.get('delta_interpretation', 'advisory-only')}",
            "",
            "## Advisories",
            "",
        ]
        lines.extend(f"- {item}" for item in report.get("advisories", []))
        lines.extend(["", f"Next action: {report.get('next_action', '')}"])
        return "\n".join(lines)
    summary = report["summary"]
    baseline_currency = str(summary.get("baseline_cost_currency", "")).strip()
    candidate_currency = str(summary.get("candidate_cost_currency", "")).strip()
    if summary.get("cost_currency_match") is not True and baseline_currency != candidate_currency:
        cost_delta_line = (
            "- Cost delta: incomparable "
            f"(baseline {baseline_currency or 'currency-unavailable'}; "
            f"candidate {candidate_currency or 'currency-unavailable'})"
        )
    elif summary.get("cost_delta_measured") is True:
        cost_delta_line = (
            f"- Cost delta (measured {baseline_currency or 'currency-unavailable'}): "
            f"{summary['cost_delta']}"
        )
    else:
        cost_delta_line = (
            f"- Cost delta (advisory {baseline_currency or 'currency-unavailable'}): "
            f"{summary['cost_delta']}"
        )
    lines = [
        "# Benchmark Comparison",
        "",
        f"- Runs compared: {summary['runs']}",
        f"- Status: {summary.get('comparison_status', 'unknown')}",
        f"- Delta interpretation: {summary.get('delta_interpretation', 'unknown')}",
        f"- Quality delta: {summary['quality_delta']}",
        f"- Input token delta: {summary['input_token_delta']}",
        f"- Output token delta: {summary['output_token_delta']}",
        f"- Cacheable static token delta: {summary['cacheable_static_token_delta']}",
        f"- Loaded-context token delta: {summary['loaded_context_token_delta']}",
        f"- Hallucination delta: {summary['hallucination_delta']}",
        f"- Evidence coverage delta: {summary['evidence_coverage_delta']}",
        cost_delta_line,
        f"- E2E latency delta (ms): {summary.get('e2e_latency_ms_delta', 0)}",
        f"- TPOT delta (ms): {summary.get('tpot_ms_delta', 0)}",
        f"- Peak memory delta (MiB): {summary.get('peak_memory_mib_delta', 0)}",
        f"- Skipped-check delta: {summary['skipped_delta']}",
        f"- Failed-check delta: {summary['failed_check_delta']}",
        f"- Negative trajectory signal delta: {summary.get('trajectory_negative_signal_delta', 0)}",
        "",
        "| Run | Subject | Quality | Input Tokens | Output Tokens | Cacheable | Hallucinations | Evidence | Cost | Skipped | Failures | Neg Signals |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in report["runs"]:
        lines.append(
            f"| `{item['run_id']}` | {item['subject']} | {item['quality_score']} | "
            f"{item['input_tokens_estimated']} | {item['output_tokens_estimated']} | "
            f"{item['cacheable_static_tokens_estimated']} | {item['hallucinations']} | "
            f"{item['evidence_coverage_percent']}% | {item['cost_estimated']} | "
            f"{item['skipped']} | {item['failures']} | {item.get('trajectory_negative_signals', 0)} |"
        )
    if not report.get("comparability", {}).get("ok", True):
        lines.extend(["", "## Not Comparable", ""])
        for issue in report.get("not_comparable_reasons", []):
            lines.append(f"- {issue}")
    gate = report.get("optimization_gate")
    if isinstance(gate, dict):
        lines.extend(["", "## Optimization Gate", ""])
        lines.append(f"- Status: {gate.get('status', 'unknown')}")
        improvements = gate.get("improvements", [])
        rejections = gate.get("rejections", [])
        lines.append("- Improvements: " + ("; ".join(improvements) if improvements else "None."))
        lines.append("- Rejections: " + ("; ".join(rejections) if rejections else "None."))
    skill_gate = report.get("skill_utility_gate")
    if isinstance(skill_gate, dict):
        lines.extend(["", "## Skill Utility Gate", ""])
        lines.append(f"- Status: {skill_gate.get('status', 'unknown')}")
        lines.append(f"- Skill: {skill_gate.get('skill_name', '')}")
        improvements = skill_gate.get("improvements", [])
        rejections = skill_gate.get("rejections", [])
        lines.append("- Improvements: " + ("; ".join(improvements) if improvements else "None."))
        lines.append("- Rejections: " + ("; ".join(rejections) if rejections else "None."))
    lines.extend(["", "## Recurring Patterns", ""])
    failures = report["recurring_patterns"]["failures"]
    lines.extend(f"- {item}" for item in failures) if failures else lines.append("- None.")
    taxonomy = report["recurring_patterns"].get("failure_taxonomy", [])
    lines.extend(["", "## Failure Taxonomy", ""])
    if taxonomy:
        for item in taxonomy:
            lines.append(f"- `{item['category']}`: {item['count']}")
    else:
        lines.append("- None.")
    sections = report.get("quality_sections", {})
    if isinstance(sections, dict):
        lines.extend(["", "## Quality Sections", ""])
        for name in ("model_quality", "agent_behavior", "tool_behavior", "workflow_quality"):
            lines.append(f"- `{name}`: {json.dumps(sections.get(name, {}), sort_keys=True)}")
    outliers = report.get("outliers", [])
    lines.extend(["", "## Outliers", ""])
    if outliers:
        for item in outliers:
            lines.append(f"- `{item.get('run_id', '')}` `{item.get('kind', 'outlier')}` {item.get('detail', '')}")
    else:
        lines.append("- None.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="*", help="run folders or benchmark-result.json files")
    parser.add_argument(
        "--compare-latest",
        metavar="RUNS_ROOT",
        help="compare newest benchmark-result.json under RUNS_ROOT against the previous best-quality run",
    )
    parser.add_argument(
        "--require-comparable",
        action="store_true",
        help="fail when suite, task, workflow, or run_config fields differ",
    )
    parser.add_argument(
        "--optimization-gate",
        action="store_true",
        help="accept only comparable comparisons with no quality/failure regression and at least one measured improvement",
    )
    parser.add_argument(
        "--optimization-scope",
        choices=("full-run", "artifact"),
        default="full-run",
        help="token measurement scope for optimization eligibility; full-run requires provider-backed usage",
    )
    parser.add_argument(
        "--trusted-codex-home",
        help="out-of-band Codex state root used to verify full-run Codex receipts; never read from a report",
    )
    parser.add_argument(
        "--trusted-host-capture-root",
        help="out-of-band coordinator capture root used to verify Claude Code or direct Responses receipts",
    )
    parser.add_argument(
        "--skill-utility-gate",
        action="store_true",
        help="accept only paired no-skill/with-skill runs with measurable utility and no interference",
    )
    parser.add_argument("--allow-quality-drop", type=float, default=0.0)
    parser.add_argument("--no-require-improvement", action="store_true")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    parser.add_argument("--compact", action="store_true", help="with --format json, emit counts and deltas only")
    return parser


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    if args.compare_latest:
        report = compare_latest(
            Path(args.compare_latest),
            require_comparable=args.require_comparable,
            optimization_gate=args.optimization_gate,
            skill_utility_gate=args.skill_utility_gate,
            allow_quality_drop=args.allow_quality_drop,
            require_improvement=not args.no_require_improvement,
            optimization_scope=args.optimization_scope,
            trusted_codex_home=(Path(args.trusted_codex_home) if args.trusted_codex_home else None),
            trusted_host_capture_root=(Path(args.trusted_host_capture_root) if args.trusted_host_capture_root else None),
        )
    else:
        report = compare_runs(
            [Path(item) for item in args.runs],
            require_comparable=args.require_comparable,
            optimization_gate=args.optimization_gate,
            skill_utility_gate=args.skill_utility_gate,
            allow_quality_drop=args.allow_quality_drop,
            require_improvement=not args.no_require_improvement,
            optimization_scope=args.optimization_scope,
            trusted_codex_home=(Path(args.trusted_codex_home) if args.trusted_codex_home else None),
            trusted_host_capture_root=(Path(args.trusted_host_capture_root) if args.trusted_host_capture_root else None),
        )
    if args.output_format == "json":
        if args.compact:
            report = summarize_report(report, compact=True)
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
