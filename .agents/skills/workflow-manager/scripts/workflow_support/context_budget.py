"""Token-budget helpers and quality gates for workflow context packets."""

from __future__ import annotations

import json
from pathlib import Path

import workflow_manager_common as common
from workflow_support.context_contract import validate_context_packet
from workflow_support.context_paths import (
    approx_tokens,
    context_packet_paths,
    context_packet_relative_paths,
    read_optional_text,
)


def relative_file_token_estimate(root: Path, path: Path) -> dict[str, object]:
    byte_count = path.stat().st_size if path.is_file() else 0
    return {
        "path": common.relative(root, path),
        "exists": path.exists(),
        "bytes": byte_count,
        "chars": byte_count,
        "tokens_estimated": max(1, (byte_count + 3) // 4) if byte_count else 0,
    }


def compact_file_estimate(item: dict[str, object]) -> dict[str, object]:
    compact = {
        "path": item.get("path", ""),
        "tokens_estimated": item.get("tokens_estimated", 0),
    }
    if item.get("exists") is not True:
        compact["exists"] = item.get("exists", False)
    return compact


def serialize_context_packet(packet: dict[str, object]) -> str:
    """Return the exact deterministic JSON text persisted for a context packet."""

    return json.dumps(packet, sort_keys=True, separators=(",", ":")) + "\n"


def cap_file_estimates(
    estimates: list[dict[str, object]],
    *,
    limit: int,
    omitted_label: str,
) -> list[dict[str, object]]:
    if len(estimates) <= limit:
        return [compact_file_estimate(item) for item in estimates]
    selected = sorted(
        estimates,
        key=lambda item: int(item.get("tokens_estimated", 0)) if isinstance(item, dict) else 0,
        reverse=True,
    )[: max(1, limit - 1)]
    selected_paths = {str(item.get("path", "")) for item in selected}
    omitted = [item for item in estimates if str(item.get("path", "")) not in selected_paths]
    rows = sorted([compact_file_estimate(item) for item in selected], key=lambda item: str(item.get("path", "")))
    rows.append(
        {
            "path": f"... {len(omitted)} {omitted_label}(s) omitted; see run.json",
            "tokens_estimated": sum(int(item.get("tokens_estimated", 0)) for item in omitted),
        }
    )
    return rows


def context_budget_status(
    raw_tokens: int,
    packet_tokens: int,
    *,
    must_open_tokens: int = 0,
    must_open_budget_usage: list[dict[str, object]] | None = None,
    accounting_converged: bool = True,
    phase_budget_tokens: int | None = None,
    phase_budget_issue: str = "",
) -> dict[str, object]:
    packet_token_limit = common.project_policy_int("limits.workflow.context_packet_token_limit")
    minimum_savings_percent = common.project_policy_int(
        "limits.workflow.context_packet_min_savings_percent"
    )
    minimum_savings_ratio = minimum_savings_percent / 100
    minimum_savings_raw_tokens = common.project_policy_int(
        "limits.workflow.context_packet_min_savings_raw_tokens"
    )
    effective_load = packet_tokens + must_open_tokens
    saved = raw_tokens - effective_load
    savings_ratio = round(saved / raw_tokens, 4) if raw_tokens else 0.0
    packet_only_ratio = round(packet_tokens / raw_tokens, 4) if raw_tokens else 0.0
    effective_load_ratio = round(effective_load / raw_tokens, 4) if raw_tokens else 0.0
    savings_check_applies = raw_tokens >= minimum_savings_raw_tokens
    checks = [
        {
            "name": "packet-token-limit",
            "ok": packet_tokens <= packet_token_limit,
            "limit": packet_token_limit,
            "actual": packet_tokens,
        },
        {
            "name": "minimum-savings-ratio",
            "ok": not savings_check_applies or savings_ratio >= minimum_savings_ratio,
            "minimum": minimum_savings_ratio,
            "minimum_raw_tokens": minimum_savings_raw_tokens,
            "applies": savings_check_applies,
            "actual": savings_ratio,
        },
    ]
    usage = must_open_budget_usage or []
    budget_issues: list[str] = []
    for row in usage:
        reference_valid = row.get("valid") is True
        issue = str(row.get("issue", "")).strip()
        if issue and issue not in budget_issues:
            budget_issues.append(issue)
        checks.append(
            {
                "name": f"must-open-budget:{row.get('check_label') or row.get('budget_ref') or 'missing'}",
                "ok": reference_valid and int(row.get("actual", 0)) <= int(row.get("limit", 0)),
                "limit": int(row.get("limit", 0)),
                "actual": int(row.get("actual", 0)),
                "file_count": int(row.get("file_count", 0)),
                "budget_ref_valid": reference_valid,
                "issue": issue,
            }
        )
    effective_limit = packet_token_limit + sum(
        int(row.get("limit", 0))
        for row in usage
        if row.get("valid") is True
    )
    checks.append(
        {
            "name": "effective-load-limit",
            "ok": effective_load <= effective_limit,
            "limit": effective_limit,
            "actual": effective_load,
            "packet_tokens": packet_tokens,
            "must_open_tokens": must_open_tokens,
        }
    )
    phase_budget_applies = (
        isinstance(phase_budget_tokens, int)
        and not isinstance(phase_budget_tokens, bool)
        and phase_budget_tokens > 0
    )
    phase_margin = (
        int(phase_budget_tokens) - effective_load
        if phase_budget_applies
        else None
    )
    checks.append(
        {
            "name": "phase-budget-limit",
            "ok": (
                not phase_budget_issue
                and (not phase_budget_applies or effective_load <= int(phase_budget_tokens))
            ),
            "applies": phase_budget_applies,
            "limit": int(phase_budget_tokens) if phase_budget_applies else 0,
            "actual": effective_load,
            "remaining_margin_tokens": phase_margin if phase_margin is not None else 0,
            "issue": phase_budget_issue,
        }
    )
    checks.append(
        {
            "name": "context-accounting-converged",
            "ok": accounting_converged,
            "actual": 1 if accounting_converged else 0,
        }
    )
    failed_names = [str(check["name"]) for check in checks if check.get("ok") is not True]
    if failed_names:
        budget_issues.append("context budget checks failed: " + ", ".join(failed_names))
    return {
        "status": "ok" if all(bool(check["ok"]) for check in checks) else "needs-attention",
        "packet_token_limit": packet_token_limit,
        "minimum_savings_raw_tokens": minimum_savings_raw_tokens,
        "minimum_savings_ratio": minimum_savings_ratio,
        "packet_only_ratio": packet_only_ratio,
        "effective_load_ratio": effective_load_ratio,
        "savings_ratio": savings_ratio,
        "effective_load_tokens_estimated": effective_load,
        "effective_load_limit": effective_limit,
        "issues": budget_issues,
        "checks": checks,
    }


def must_open_budget_rows(
    context_sources: list[dict[str, object]],
    budgets: object,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Return unique must-open files and aggregate usage for each budget reference."""

    budget_values = budgets if isinstance(budgets, dict) else {}
    files_by_path: dict[str, dict[str, object]] = {}
    usage_paths: set[tuple[str, str]] = set()
    usage: dict[str, dict[str, object]] = {}
    for source in context_sources:
        if source.get("load_policy") != "must_open":
            continue
        budget_ref = str(source.get("budget_ref", "")).strip()
        raw_limit = budget_values.get(budget_ref)
        reference_valid = (
            bool(budget_ref)
            and budget_ref in budget_values
            and isinstance(raw_limit, int)
            and not isinstance(raw_limit, bool)
            and raw_limit > 0
        )
        if not budget_ref:
            issue = "must_open source budget_ref is missing"
        elif budget_ref not in budget_values:
            issue = f"must_open source budget_ref '{budget_ref}' is not declared"
        elif not reference_valid:
            issue = f"must_open source budget_ref '{budget_ref}' must resolve to a positive integer"
        else:
            issue = ""
        files = source.get("files") if isinstance(source.get("files"), list) else []
        for item in files:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", "")).strip()
            if not path:
                continue
            row = usage.setdefault(
                budget_ref,
                {
                    "budget_ref": budget_ref,
                    "check_label": budget_ref or "missing",
                    "valid": reference_valid,
                    "issue": issue,
                    "limit": int(raw_limit) if reference_valid else 0,
                    "actual": 0,
                    "file_count": 0,
                },
            )
            if path not in files_by_path:
                estimate = dict(item)
                estimate["budget_ref"] = budget_ref
                files_by_path[path] = estimate
            usage_key = (budget_ref, path)
            if usage_key in usage_paths:
                continue
            usage_paths.add(usage_key)
            row["actual"] = int(row["actual"]) + int(item.get("tokens_estimated", 0))
            row["file_count"] = int(row["file_count"]) + 1
    return list(files_by_path.values()), [usage[key] for key in sorted(usage)]


def context_packet_quality_gate(
    root: Path,
    run_dir: Path,
    packet: dict[str, object],
    *,
    existing: bool | None = None,
    fresh: bool | None = None,
    markdown_exists: bool | None = None,
) -> dict[str, object]:
    json_path, markdown_path = context_packet_paths(run_dir)
    expected_json, expected_markdown = context_packet_relative_paths(root, run_dir)
    budget = packet.get("context_budget") if isinstance(packet.get("context_budget"), dict) else {}
    budget_checks = budget.get("checks") if isinstance(budget.get("checks"), list) else []
    guidance = packet.get("guidance_savings") if isinstance(packet.get("guidance_savings"), dict) else {}
    required = packet.get("required_next_context") if isinstance(packet.get("required_next_context"), list) else []
    paths = packet.get("context_packet_paths") if isinstance(packet.get("context_packet_paths"), dict) else {}
    checks: list[dict[str, object]] = []

    def add(name: str, ok: bool, **extra: object) -> None:
        checks.append({"name": name, "ok": ok, **extra})

    actual_existing = json_path.exists() if existing is None else existing
    actual_markdown = markdown_path.exists() if markdown_exists is None else markdown_exists
    add("packet-json-exists", actual_existing, path=expected_json)
    add("packet-markdown-exists", actual_markdown, path=expected_markdown)
    if fresh is not None:
        add("packet-fresh", fresh)
    add("tool-id", packet.get("tool") == "workflow-manager.context-packet", actual=packet.get("tool", ""))
    schema_errors = validate_context_packet(packet)
    add("schema-valid", not schema_errors, errors=schema_errors[:10])
    add("context-paths", paths.get("json") == expected_json and paths.get("markdown") == expected_markdown)
    add("required-next-context-present", bool(required), count=len(required))
    add("self-reference", expected_json in [str(item) for item in required], path=expected_json)

    missing_required: list[str] = []
    for item in required:
        value = str(item).replace("\\", "/").strip().lstrip("/")
        if not value or value.startswith("http://") or value.startswith("https://") or value.startswith("... "):
            continue
        if not (root / value).exists():
            missing_required.append(value)
    add("required-next-context-files-exist", not missing_required, missing=missing_required[:10])
    add("budget-status", budget.get("status") == "ok", actual=budget.get("status", "missing"))
    add(
        "budget-checks",
        bool(budget_checks) and all(isinstance(item, dict) and item.get("ok") is True for item in budget_checks),
        failed=[item for item in budget_checks if isinstance(item, dict) and item.get("ok") is not True],
    )
    guidance_measurable = guidance.get("measurable") is True
    add("guidance-savings-present", bool(guidance), status=guidance.get("status", "missing"))
    add(
        "guidance-use-by-default",
        not guidance or guidance.get("use_by_default") is True,
        actual=guidance.get("use_by_default", "missing"),
    )
    add(
        "guidance-files-complete",
        not guidance or guidance.get("complete") is True,
        default_missing_count=guidance.get("default_missing_count", 0),
        baseline_missing_count=guidance.get("baseline_missing_count", 0),
    )
    add(
        "guidance-budget-config-valid",
        not guidance or not str(guidance.get("budget_issue", "")).strip(),
        source=guidance.get("budget_source", "missing"),
        issue=guidance.get("budget_issue", ""),
    )
    add(
        "guidance-absolute-budget",
        not guidance or guidance.get("within_absolute_budget") is True,
        budget_tokens=guidance.get("budget_tokens", 0),
        actual=guidance.get("default_guidance_tokens", 0),
    )
    add(
        "guidance-threshold-if-measurable",
        not guidance_measurable or guidance.get("meets_minimum") is True,
        status=guidance.get("status", "missing"),
        saved_percent_estimated=guidance.get("saved_percent_estimated", 0),
        min_saved_percent=guidance.get("min_saved_percent", 0),
    )
    failed = [item for item in checks if item.get("ok") is not True]
    return {
        "schema_version": 1,
        "tool": "workflow-manager.context-packet.quality-gate",
        "ok": not failed,
        "status": "ok" if not failed else "failed",
        "check_count": len(checks),
        "failed_count": len(failed),
        "failed_checks": failed,
        "checks": checks,
    }


def apply_token_estimates(
    packet: dict[str, object],
    *,
    raw_tokens: int,
    validation_tokens: int,
    compact_packet_tokens: int,
    raw_estimates: list[dict[str, object]],
    context_sources: list[dict[str, object]] | None = None,
    context_budgets: object = None,
) -> None:
    must_open_files, budget_usage = must_open_budget_rows(
        context_sources or [],
        context_budgets,
    )
    must_open_tokens = sum(
        int(item.get("tokens_estimated", 0)) for item in must_open_files
    )
    execution_profile = packet.get("execution_profile") if isinstance(packet.get("execution_profile"), dict) else {}
    phase_budget_tokens = execution_profile.get("budget_tokens", 0)
    if not isinstance(phase_budget_tokens, int) or isinstance(phase_budget_tokens, bool):
        phase_budget_tokens = 0
    phase_budget_issue = str(execution_profile.get("budget_issue", "")).strip()

    def update_execution_budget(effective_load: int) -> None:
        if not execution_profile or phase_budget_tokens <= 0:
            return
        execution_profile["effective_context_tokens"] = effective_load
        execution_profile["remaining_margin_tokens"] = phase_budget_tokens - effective_load
        execution_profile["within_budget"] = effective_load <= phase_budget_tokens
        execution_profile["context_measurement"] = "measured"

    estimates = {
        "method": "rough chars/4 estimate for context budgeting, not billing",
        "serialization_alignment": "",
        "raw_context_tokens_estimated": raw_tokens,
        "packet_tokens_estimated": compact_packet_tokens,
        "compact_packet_tokens_estimated": compact_packet_tokens,
        "estimated_tokens_saved": raw_tokens - (compact_packet_tokens + must_open_tokens),
        "raw_context_file_count": len(raw_estimates),
        "validation_tokens_estimated": validation_tokens,
        "must_open_file_count": len(must_open_files),
        "must_open_tokens_estimated": must_open_tokens,
        "effective_load_tokens_estimated": compact_packet_tokens + must_open_tokens,
    }
    packet["token_estimates"] = estimates
    update_execution_budget(compact_packet_tokens + must_open_tokens)
    base_ok = packet.get("ok") is True
    base_status = str(packet.get("status", "needs-attention"))
    base_issues = list(packet.get("issues", [])) if isinstance(packet.get("issues"), list) else []

    def apply_budget_status(packet_tokens: int, *, accounting_converged: bool) -> None:
        budget_status = context_budget_status(
            raw_tokens,
            packet_tokens,
            must_open_tokens=must_open_tokens,
            must_open_budget_usage=budget_usage,
            accounting_converged=accounting_converged,
            phase_budget_tokens=phase_budget_tokens if phase_budget_tokens > 0 else None,
            phase_budget_issue=phase_budget_issue,
        )
        packet["context_budget"] = budget_status
        budget_ok = budget_status.get("status") == "ok"
        packet["ok"] = base_ok and budget_ok
        packet["status"] = base_status if budget_ok or base_status != "ok" else "needs-attention"
        budget_messages = budget_status.get("issues") if isinstance(budget_status.get("issues"), list) else []
        packet["issues"] = [
            *base_issues,
            *[
                f"context budget: {message}"
                for message in budget_messages
                if f"context budget: {message}" not in base_issues
            ],
        ]

    def set_accounting(packet_tokens: int, *, accounting_converged: bool) -> None:
        estimates["packet_tokens_estimated"] = packet_tokens
        estimates["estimated_tokens_saved"] = raw_tokens - (packet_tokens + must_open_tokens)
        estimates["effective_load_tokens_estimated"] = packet_tokens + must_open_tokens
        update_execution_budget(packet_tokens + must_open_tokens)
        apply_budget_status(
            packet_tokens,
            accounting_converged=accounting_converged,
        )

    # The estimate is part of the serialized packet that it measures. A short,
    # declared alignment value lets the chars/4 estimator reach an exact fixed
    # point without changing semantic fixture content or under-reporting the
    # packet. Trying a bounded set is deterministic; exhaustion remains an
    # explicit fail-closed condition.
    observed_candidates: set[int] = {max(0, compact_packet_tokens)}
    converged = False
    for alignment_size in range(16):
        estimates["serialization_alignment"] = "." * alignment_size
        candidate = max(0, compact_packet_tokens)
        seen_candidates: set[int] = set()
        for _ in range(64):
            set_accounting(candidate, accounting_converged=True)
            actual = approx_tokens(serialize_context_packet(packet))
            observed_candidates.update((candidate, actual))
            if actual == candidate:
                converged = True
                break
            if actual in seen_candidates:
                break
            seen_candidates.add(candidate)
            candidate = actual
        if converged:
            break

    if not converged:
        # Keep the last attempted alignment and report a conservative estimate.
        # The packet is not eligible for an "ok" result even when the stored
        # value bounds the serialized size.
        conservative = max(observed_candidates)
        for _ in range(64):
            set_accounting(conservative, accounting_converged=False)
            serialized_tokens = approx_tokens(serialize_context_packet(packet))
            if conservative >= serialized_tokens:
                break
            conservative = serialized_tokens
