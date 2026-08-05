#!/usr/bin/env python3
"""Workflow review and context-budget helpers."""

from __future__ import annotations

import json
from pathlib import Path

import sync_automation_routing
import validate_automations
import workflow_manager_common as common
from workflow_run_support import context_workflow_run
from workflow_support.run_common import completed_run_status, normalized_run_health_status

WORKFLOW_BUDGET_POLICY_PATHS = {
    "entry": "limits.workflow.entry_warn_words",
    "contract": "limits.workflow.contract_warn_words",
    "instructions": "limits.workflow.instructions_warn_words",
}
OUT_OF_SCOPE_WORKFLOWS = {"user-story-workflow", "bug-ticket-workflow"}
OUT_OF_SCOPE_TEMPLATE_FILES = (
    "templates/ticket-info.md",
    "templates/plan.md",
    "templates/pr-description.md",
)


def workflow_budget_tier(path: Path, module_dir: Path) -> str:
    if path == common.workflow_start_path(module_dir):
        return "entry"
    if path.name == "module.json":
        return "contract"
    if path.name == "instructions.md":
        return "instructions"
    return "other"


def workflow_context_budget(root: Path, module_dir: Path) -> dict[str, object]:
    thresholds = {
        tier: common.project_policy_int(path, start=root)
        for tier, path in WORKFLOW_BUDGET_POLICY_PATHS.items()
    }
    aggregate_threshold = common.project_policy_int("limits.workflow.aggregate_warn_words", start=root)
    files = [
        common.workflow_start_path(module_dir),
        module_dir / "module.json",
        module_dir / "instructions.md",
    ]
    rows: list[dict[str, object]] = []
    total_words = 0
    for path in files:
        if not path.exists():
            continue
        words = len(common.read_text(path, limit=120_000).split())
        total_words += words
        tier = workflow_budget_tier(path, module_dir)
        threshold = thresholds.get(tier)
        status = "warning" if threshold is not None and words > threshold else "ok"
        rows.append(
            {
                "path": common.relative(root, path),
                "words": words,
                "tier": tier,
                "warning_threshold": threshold,
                "status": status,
            }
        )
    history = module_dir / "artifacts" / "context-budget-history.json"
    trend: dict[str, object] | None = None
    if history.exists():
        try:
            data = json.loads(history.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = []
        if isinstance(data, list) and len(data) >= 2:
            first = data[0] if isinstance(data[0], dict) else {}
            last = data[-1] if isinstance(data[-1], dict) else {}
            trend = {
                "first_words": first.get("total_words"),
                "latest_words": last.get("total_words"),
                "delta_words": (
                    int(last.get("total_words", 0)) - int(first.get("total_words", 0))
                    if str(first.get("total_words", "")).isdigit()
                    and str(last.get("total_words", "")).isdigit()
                    else None
                ),
            }
    tier_warnings = [row for row in rows if row.get("status") == "warning"]
    aggregate_warning: dict[str, object] | None = None
    if total_words > aggregate_threshold:
        aggregate_warning = {
            "tier": "aggregate",
            "path": "<workflow total>",
            "words": total_words,
            "warning_threshold": aggregate_threshold,
            "status": "warning",
        }
    warning_action = common.project_warning_action("workflow.context-budget", start=root)
    if warning_action == "off":
        tier_warnings = []
        aggregate_warning = None
    elif warning_action == "error":
        for warning in tier_warnings:
            warning["status"] = "error"
        if aggregate_warning:
            aggregate_warning["status"] = "error"
    warning_details = tier_warnings + ([aggregate_warning] if aggregate_warning else [])
    return {
        "total_words": total_words,
        "files": rows,
        "thresholds": {
            "entry_words": thresholds["entry"],
            "contract_words": thresholds["contract"],
            "instructions_words": thresholds["instructions"],
            "aggregate_words": aggregate_threshold,
        },
        "warning_action": warning_action,
        "tier_warnings": tier_warnings,
        "aggregate_warning": aggregate_warning,
        "warning_details": warning_details,
        "status": "error" if warning_details and warning_action == "error" else "warning" if warning_details else "ok",
        "trend": trend,
    }


def build_implementation_packet(
    root: Path,
    module_dir: Path,
    workflow_name: str,
    *,
    context_budget: dict[str, object],
    warnings: list[str],
) -> dict[str, object]:
    likely_files = [
        common.workflow_start_relative(module_dir),
        f"automations/{workflow_name}/module.json",
    ]
    instructions = module_dir / "instructions.md"
    if instructions.exists():
        likely_files.append(common.relative(root, instructions))
    has_runs = (module_dir / "runs").exists()
    expected_checks = [
        f"python -B .agents/manage.py validate-automations --name {workflow_name}",
        f"python -B .agents/manage.py review {workflow_name} --plan",
        "python -B .agents/manage.py sync-automation-routing",
        "python -B .agents/manage.py validate",
    ]
    if has_runs:
        expected_checks.append(
            f"python -B .agents/manage.py index-workflow-runs --name {workflow_name} --check"
        )
    plan_warnings = [warning for warning in warnings if "may overlap workflow" in warning]
    return {
        "purpose": "Execution-ready checklist for a substantial workflow change; read-only and advisory.",
        "likely_files": likely_files,
        "expected_checks": expected_checks,
        "generated_artifacts": [
            "automations/routing.md",
            "automations/registry.json",
        ],
        "completion_evidence": [
            "fresh validation command output",
            "generated routing sync or explicit stale-artifact blocker",
            "changed paths and workflow-owned outputs",
            "skipped or blocked checks with reasons",
        ],
        "two_stage_review": [
            "Stage 1: requested behavior/spec compliance against the user request and workflow contract.",
            "Stage 2: implementation quality, owner boundaries, validation coverage, and context budget.",
        ],
        "do_not_overbuild": [
            "Prefer extending this workflow when triggers, phases, outputs, or related skills overlap.",
            "Do not create a skill for workflow phase orchestration or repo policy.",
            "Add scripts only when deterministic orchestration is safer than prose.",
        ],
        "context_budget": context_budget,
        "overlap_warnings": plan_warnings,
    }


def workflow_declares_context_packet(module_dir: Path) -> bool:
    data, _error = common.read_json_file(module_dir / "module.json")
    if not isinstance(data, dict):
        return False
    outputs = data.get("outputs")
    if not isinstance(outputs, list):
        return False
    return any("artifacts/context/context-packet.json" in str(item).replace("\\", "/") for item in outputs)


def context_packet_health(root: Path, module_dir: Path, workflow_name: str, run_id: str) -> dict[str, object]:
    required = workflow_declares_context_packet(module_dir)
    if not required:
        return {"required": False, "status": "not-required", "fresh": None, "ok": True, "issues": []}
    try:
        report = context_workflow_run(root, workflow_name, run_id=run_id, check=True)
    except SystemExit as exc:
        return {
            "required": True,
            "status": "error",
            "fresh": False,
            "ok": False,
            "issues": [str(exc)],
            "next_command": f"python -B .agents/manage.py workflow context --name {workflow_name} --run-id {run_id} --write",
        }
    check = report.get("check") if isinstance(report.get("check"), dict) else {}
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    paths = report.get("context_packet_paths") if isinstance(report.get("context_packet_paths"), dict) else {}
    return {
        "required": True,
        "status": report.get("status", "unknown"),
        "fresh": check.get("fresh", False),
        "ok": report.get("ok") is True,
        "path": report.get("existing_packet_path") or paths.get("json", ""),
        "markdown_path": report.get("existing_markdown_path", ""),
        "issues": issues,
        "next_command": f"python -B .agents/manage.py workflow context --name {workflow_name} --run-id {run_id} --write",
    }


def out_of_scope_template_health(root: Path, module_dir: Path, workflow_name: str) -> dict[str, object]:
    if workflow_name not in OUT_OF_SCOPE_WORKFLOWS:
        return {"required": False, "status": "not-required", "missing": [], "present": []}
    missing: list[str] = []
    present: list[str] = []
    for relative_path in OUT_OF_SCOPE_TEMPLATE_FILES:
        path = module_dir / relative_path
        if path.exists() and "## Out Of Scope" in common.read_text(path, limit=20_000):
            present.append(common.relative(root, path))
        else:
            missing.append(common.relative(root, path))
    return {
        "required": True,
        "status": "ok" if not missing else "missing",
        "missing": missing,
        "present": present,
    }


def latest_run_summary(
    root: Path,
    module_dir: Path,
    workflow_name: str,
    local_ai_use_cases: list[str],
    *,
    include_completed: bool = False,
) -> dict[str, object]:
    runs_dir = module_dir / "runs"
    context_required = workflow_declares_context_packet(module_dir)
    if not runs_dir.exists():
        return {
            "status": "no-retained-runs",
            "runs": [],
            "run_count": 0,
            "blocking_count": 0,
            "advisory_count": 0,
            "completed_count": 0,
            "run_retention_policy": "none-retained",
            "current_phase": "",
            "context_packet": {
                "required": context_required,
                "status": "not-applicable" if context_required else "not-required",
                "fresh": None,
            },
            "next_command": f"python -B .agents/manage.py workflow start --name {workflow_name}",
            "expected_evidence": ["no retained run packets by default; start a run when workflow evidence is needed"],
        }
    runs: list[dict[str, object]] = []
    for child in sorted(runs_dir.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        data, error = common.read_json_file(child / "run.json")
        packet = data if isinstance(data, dict) else {}
        runs.append(
            {
                **packet,
                "path": common.relative(root, child),
                "run_id": child.name,
                "run_health_status": normalized_run_health_status(packet, error),
            }
        )
    if not runs:
        has_run_dirs = any(child.is_dir() for child in runs_dir.iterdir())
        if not has_run_dirs:
            return {
                "status": "no-retained-runs",
                "runs": [],
                "run_count": 0,
                "blocking_count": 0,
                "advisory_count": 0,
                "completed_count": 0,
                "run_retention_policy": "none-retained",
                "current_phase": "",
                "context_packet": {
                    "required": context_required,
                    "status": "not-applicable" if context_required else "not-required",
                    "fresh": None,
                },
                "next_command": f"python -B .agents/manage.py workflow start --name {workflow_name}",
                "expected_evidence": ["no retained run packets by default; start a run when workflow evidence is needed"],
            }
        return {
            "status": "unindexed",
            "runs": [],
            "run_count": 0,
            "blocking_count": 1,
            "advisory_count": 0,
            "completed_count": 0,
            "run_retention_policy": "retained-runs-missing-indexable-packet",
            "current_phase": "",
            "context_packet": {
                "required": context_required,
                "status": "unindexed" if context_required else "not-required",
                "fresh": None,
            },
            "next_command": f"python -B .agents/manage.py workflow start --name {workflow_name}",
            "expected_evidence": ["runs/<run-id>/run.json and REPORT.md"],
        }
    health_rows: list[dict[str, object]] = []
    for item in runs:
        run_id = str(item.get("run_id") or Path(str(item.get("path", ""))).name)
        run_status = str(item.get("run_health_status") or "unknown")
        completed = completed_run_status(run_status)
        context_packet = context_packet_health(
            root,
            module_dir,
            workflow_name,
            run_id,
        )
        failed = run_status in {"missing", "invalid", "unknown"} or (
            context_packet.get("required") is True
            and context_packet.get("ok") is not True
        )
        advisory = failed and completed and not include_completed
        blocking = failed and not advisory
        health_rows.append(
            {
                "run_id": run_id,
                "run_path": item.get("path", ""),
                "run_status": run_status,
                "updated_at": item.get("updated_at", ""),
                "completed": completed,
                "blocking": blocking,
                "advisory": advisory,
                "context_packet_status": context_packet.get("status", "unknown"),
                "context_packet_fresh": context_packet.get("fresh"),
                "context_packet": context_packet,
                "next_command": context_packet.get("next_command")
                or f"python -B .agents/manage.py workflow context --name {workflow_name} --run-id {run_id} --write",
            }
        )
    active_runs = [item for item in runs if not completed_run_status(item.get("run_health_status"))]
    candidates = active_runs or runs
    selected = sorted(
        candidates,
        key=lambda item: (str(item.get("updated_at", "")), str(item.get("run_id", ""))),
        reverse=True,
    )[0]
    expected_evidence = [
        "run.json",
        "REPORT.md",
        "fresh validation output or explicit blocked-check reason",
    ]
    local_ai_fallback = ""
    if "validation-triage" in local_ai_use_cases:
        local_ai_fallback = "python -B .agents/manage.py local-ai task --task validation-triage --input <deterministic-evidence-file>"
    selected_run_id = str(selected.get("run_id") or Path(str(selected.get("path", ""))).name)
    selected_status = str(selected.get("run_health_status") or "unknown")
    selected_health = next(
        row for row in health_rows if row.get("run_id") == selected_run_id
    )
    return {
        "status": selected_status,
        "advisory": selected_health.get("advisory") is True,
        "blocking": selected_health.get("blocking") is True,
        "completed": completed_run_status(selected_status),
        "runs": health_rows,
        "run_count": len(health_rows),
        "blocking_count": sum(1 for row in health_rows if row.get("blocking") is True),
        "advisory_count": sum(1 for row in health_rows if row.get("advisory") is True),
        "completed_count": sum(1 for row in health_rows if row.get("completed") is True),
        "run_retention_policy": "retained",
        "external_validation_status": selected.get("external_validation_status", "unknown"),
        "run_id": selected_run_id,
        "current_phase": selected.get("current_phase", ""),
        "last_completed_step": (selected.get("handoff") or {}).get("last_completed_step", "") if isinstance(selected.get("handoff"), dict) else "",
        "last_command": (selected.get("handoff") or {}).get("last_command", "") if isinstance(selected.get("handoff"), dict) else "",
        "blockers": selected.get("blocked", []),
        "loaded_context": (selected.get("handoff") or {}).get("loaded_context", []) if isinstance(selected.get("handoff"), dict) else [],
        "next_action": selected.get("next_action", ""),
        "next_command": f"python -B .agents/manage.py index-workflow-runs --name {workflow_name} --check",
        "expected_evidence": expected_evidence,
        "local_ai_fallback_command": local_ai_fallback,
        "context_packet": selected_health.get("context_packet", {}),
    }


def review_workflow(
    root: Path,
    workflow_name: str,
    *,
    include_plan: bool = False,
    include_completed: bool = False,
) -> dict[str, object]:
    module_dir = root / "automations" / workflow_name
    errors, warnings, modules = validate_automations.validate_automations(
        root, workflow_name=workflow_name
    )
    if not modules:
        return {
            "tool": "workflow-manager.review-workflow",
            "ok": False,
            "workflow": workflow_name,
            "errors": errors,
            "warnings": warnings,
        }
    registry = sync_automation_routing.build_registry_data(root, use_local_ai=False)
    entry = next(
        (item for item in registry["automations"] if item["id"] == workflow_name),
        {},
    )
    context_budget = workflow_context_budget(root, module_dir)
    out_of_scope = out_of_scope_template_health(root, module_dir, workflow_name)
    local_ai_use_cases = []
    local_ai = entry.get("local_ai") if isinstance(entry, dict) else None
    if isinstance(local_ai, dict):
        local_ai_use_cases = list(local_ai.get("use_cases") or [])
    suggestions = ["python -B .agents/manage.py local-ai integrations --target workflow"]
    if "validation-triage" in local_ai_use_cases:
        suggestions.append(
            "python -B .agents/manage.py local-ai task --task validation-triage --input <workflow-evidence-file>"
        )
    if "vision-pdf" in local_ai_use_cases:
        suggestions.append(
            "python -B .agents/manage.py local-ai vision pdf --pdf <file> --pages 1-5"
        )
    run_summary = latest_run_summary(
        root,
        module_dir,
        workflow_name,
        local_ai_use_cases,
        include_completed=include_completed,
    )
    report: dict[str, object] = {
        "tool": "workflow-manager.review-workflow",
        "ok": not errors and int(run_summary.get("blocking_count", 0)) == 0,
        "workflow": workflow_name,
        "path": common.relative(root, module_dir),
        "start_file": common.workflow_start_relative(module_dir),
        "validation": {"errors": errors, "warnings": warnings},
        "routing": {"summary": entry.get("summary"), "start_file": entry.get("start_file")},
        "context_budget": context_budget,
        "out_of_scope": out_of_scope,
        "run_summary": run_summary,
        "overlap_warnings": [warning for warning in warnings if "may overlap workflow" in warning],
        "decision_table": [
            {"decision": "keep", "when": "valid and distinct", "next": "sync routing"},
            {"decision": "extend", "when": "overlap warning is accepted", "next": "patch existing workflow"},
            {"decision": "rewrite-first", "when": "imported or overly broad", "next": "reduce to minimal workflow shape"},
            {"decision": "reject", "when": "not workflow-shaped", "next": "use a skill or docs instead"},
        ],
        "local_ai": {
            "declared_use_cases": local_ai_use_cases,
            "suggestions": suggestions,
            "fallback": "If local AI is unavailable, use the validation and routing sections above directly.",
        },
    }
    if include_plan:
        report["implementation_packet"] = build_implementation_packet(
            root,
            module_dir,
            workflow_name,
            context_budget=context_budget,
            warnings=warnings,
        )
    return report


def render_review(report: dict[str, object]) -> str:
    lines = [
        "# Workflow Review",
        "",
        f"- Workflow: `{report.get('workflow')}`",
        f"- Status: {'passed' if report.get('ok') else 'failed'}",
        f"- Start: `{report.get('start_file', 'unknown')}`",
    ]
    validation = report.get("validation") if isinstance(report.get("validation"), dict) else {}
    errors = validation.get("errors", []) if isinstance(validation, dict) else []
    warnings = validation.get("warnings", []) if isinstance(validation, dict) else []
    context_budget = report.get("context_budget") if isinstance(report.get("context_budget"), dict) else {}
    lines.extend(
        [
            "",
            "## Context Budget",
            "",
            f"- Total words: {context_budget.get('total_words', 0)}",
            f"- Status: {context_budget.get('status', 'unknown')}",
        ]
    )
    thresholds = context_budget.get("thresholds") if isinstance(context_budget.get("thresholds"), dict) else {}
    if thresholds:
        lines.append(
            "- Thresholds: "
            f"entry {thresholds.get('entry_words')}, "
            f"contract {thresholds.get('contract_words')}, "
            f"instructions {thresholds.get('instructions_words')}, "
            f"aggregate {thresholds.get('aggregate_words')} words"
        )
    warning_details = (
        context_budget.get("warning_details")
        if isinstance(context_budget.get("warning_details"), list)
        else []
    )
    if warning_details:
        lines.append("- Tier warnings:")
        for item in warning_details:
            if isinstance(item, dict):
                lines.append(
                    f"  - {item.get('path')} ({item.get('tier')}): "
                    f"{item.get('words')} words, threshold {item.get('warning_threshold')}"
                )
    else:
        lines.append("- Tier warnings: none")
    if report.get("overlap_warnings"):
        lines.extend(["", "## Overlap", ""])
        lines.extend(f"- {warning}" for warning in report["overlap_warnings"])
    out_of_scope = report.get("out_of_scope") if isinstance(report.get("out_of_scope"), dict) else {}
    if out_of_scope and out_of_scope.get("required"):
        lines.extend(["", "## Out Of Scope Templates", ""])
        lines.append(f"- Status: {out_of_scope.get('status')}")
        missing = out_of_scope.get("missing") if isinstance(out_of_scope.get("missing"), list) else []
        if missing:
            lines.append("- Missing:")
            lines.extend(f"  - `{item}`" for item in missing)
    run_summary = report.get("run_summary") if isinstance(report.get("run_summary"), dict) else {}
    if run_summary:
        lines.extend(["", "## Run Summary", ""])
        lines.append(f"- Status: {run_summary.get('status')}")
        if run_summary.get("external_validation_status"):
            lines.append(f"- External validation: {run_summary.get('external_validation_status')}")
        if run_summary.get("current_phase"):
            lines.append(f"- Current phase: {run_summary.get('current_phase')}")
        if run_summary.get("next_action"):
            lines.append(f"- Next action: {run_summary.get('next_action')}")
        context_packet = run_summary.get("context_packet") if isinstance(run_summary.get("context_packet"), dict) else {}
        if context_packet:
            lines.append(
                f"- Context packet: {context_packet.get('status')} "
                f"(fresh: {context_packet.get('fresh')})"
            )
        lines.append(f"- Next command: `{run_summary.get('next_command')}`")
        expected = run_summary.get("expected_evidence", [])
        if expected:
            lines.append("- Expected evidence:")
            for item in expected:
                lines.append(f"  - {item}")
        if run_summary.get("local_ai_fallback_command"):
            lines.append(f"- Optional local AI fallback: `{run_summary.get('local_ai_fallback_command')}`")
    if errors:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {error}" for error in errors)
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(["", "## Decision Table", "", "| Decision | When | Next |", "|---|---|---|"])
    for row in report.get("decision_table", []):
        if isinstance(row, dict):
            lines.append(f"| {row.get('decision')} | {row.get('when')} | {row.get('next')} |")
    local_ai = report.get("local_ai") if isinstance(report.get("local_ai"), dict) else {}
    suggestions = local_ai.get("suggestions", []) if isinstance(local_ai, dict) else []
    if suggestions:
        lines.extend(["", "## Local AI", ""])
        lines.extend(f"- `{item}`" for item in suggestions)
        lines.append(f"- Fallback: {local_ai.get('fallback')}")
    packet = report.get("implementation_packet")
    if isinstance(packet, dict):
        lines.extend(["", "## Implementation Packet", ""])
        lines.append(f"- Purpose: {packet.get('purpose')}")
        lines.append("- Likely files:")
        for item in packet.get("likely_files", []):
            lines.append(f"  - `{item}`")
        lines.append("- Expected checks:")
        for item in packet.get("expected_checks", []):
            lines.append(f"  - `{item}`")
        lines.append("- Generated artifacts:")
        for item in packet.get("generated_artifacts", []):
            lines.append(f"  - `{item}`")
        lines.append("- Completion evidence:")
        for item in packet.get("completion_evidence", []):
            lines.append(f"  - {item}")
        lines.append("- Two-stage review:")
        for item in packet.get("two_stage_review", []):
            lines.append(f"  - {item}")
    return "\n".join(lines) + "\n"
