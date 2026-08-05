#!/usr/bin/env python3
"""Workflow-manager command dispatcher."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import create_workflow
import eval_workflow
import index_workflow_runs
import sync_automation_routing
import validate_automations
import workflow_manager_common as common
import workflow_plan_check
import workflow_context_evidence
from validation_support.reporting import render_json_report, render_markdown_report
from workflow_support.cli_parser import build_parser
from workflow_run_support import (
    WORKFLOW_HOOK_EVENTS,
    canonical_workflow_run_id,
    checkpoint_workflow_run,
    context_audit_workflow_run,
    context_workflow_run,
    finish_workflow_run,
    handoff_workflow_run,
    hooks_workflow_run,
    latest_or_selected_run_dir,
    load_runtime_observation_packet,
    normalized_run_state,
    read_json_object,
    render_checkpoint_markdown,
    render_context_audit,
    render_context_packet_markdown,
    render_finish_run,
    render_handoff_markdown,
    render_hook_audit_packet,
    render_hooks_run,
    render_recover_markdown,
    render_resume_run,
    render_start_run,
    recover_workflow_run,
    resume_workflow_run,
    start_workflow_run,
    write_hook_audit_packet,
)

from workflow_support.review import (
    render_review,
    review_workflow,
    workflow_declares_context_packet,
)
from workflow_support.run_common import completed_run_status, normalized_run_health_status
from workflow_support.analytics import compact_analytics, render_analytics, workflow_analytics
from workflow_support.intent_builder import (
    adjust_plan_report,
    create_from_request,
    proposal_report,
    recipes_report,
    render_report as render_builder_report,
)
from workflow_support.scorecard import compact_scorecards, render_scorecards, scorecards
from workflow_support.smoke import render_smoke_markdown, smoke_workflows
from workflow_support.template_layers import (
    branch_policy_check,
    integration_check,
    lint_templates,
    managed_section_diff,
    metadata_inspect,
    render_simple_report,
    resolve_template,
    template_gate_check,
)
from workflow_support.validation_packets import render_validation_packet, validate_packet
from workflow_support.workers import (
    profile_catalog_report,
    render_workers_markdown,
    verify_persisted_runtime_observation,
    workflow_workers_report,
)
from workflow_support.orchestration import render_orchestration_markdown, resolve_orchestration

EVAL_FILENAME_RE = re.compile(r"(^|[-_])evals?([-_.]|$)", re.IGNORECASE)
MAX_WORKFLOW_EVAL_WORKERS = 4
CANONICAL_GLOBAL_HOOK_COMMAND = (
    "python -B .agents/manage.py workflow hook-audit "
    "--name {workflow} --run-id {run_id} --run-dir {run_dir} "
    "--event {event} --hook-id {hook_id} --format json"
)
CANONICAL_GLOBAL_HOOK_EVIDENCE = "validation/hooks/{event}-{hook_id}.json"
CANONICAL_GLOBAL_HOOK_KEYS = {
    "id",
    "event",
    "command",
    "required",
    "timeout_seconds",
    "evidence_path",
}


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def root_from_args(args: argparse.Namespace) -> Path:
    return Path(args.root).expanduser().resolve() if args.root else default_root()


def accepted_workflow_names(root: Path) -> list[str]:
    automations = root / "automations"
    names: list[str] = []
    if not automations.exists():
        return names
    for module_dir in sorted(automations.iterdir(), key=lambda item: item.name):
        if not module_dir.is_dir():
            continue
        if common.workflow_start_path(module_dir).exists():
            names.append(module_dir.name)
    return names


def discover_workflow_eval_suites(root: Path) -> list[tuple[str, Path]]:
    suites: list[tuple[str, Path]] = []
    for workflow_name in accepted_workflow_names(root):
        suite_dir = root / "automations" / workflow_name / "suites"
        if not suite_dir.exists():
            continue
        for suite in sorted(suite_dir.glob("*.json"), key=lambda item: item.name):
            if suite.is_file() and EVAL_FILENAME_RE.search(suite.stem):
                suites.append((workflow_name, suite))
    return suites


def summarize_eval_all_report(report: dict[str, object]) -> dict[str, object]:
    results = report.get("results") if isinstance(report.get("results"), list) else []
    failed_results = [item for item in results if isinstance(item, dict) and not item.get("ok")]
    return {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "eval-workflows"),
        "ok": bool(report.get("ok")),
        "status": report.get("status", ""),
        "summary": report.get("summary", {}),
        "execution": report.get("execution", {}),
        "results": failed_results,
        "next_command": "python -B .agents/manage.py workflow eval --all --summary --compact --format json",
    }


def suite_parallel_safety(suite: Path, workflow_name: str) -> list[str]:
    data, error = common.read_json_file(suite)
    if error or not isinstance(data, dict):
        return [f"{suite.name}: suite could not be inspected safely"]
    cases = data.get("evals") or data.get("cases")
    if not isinstance(cases, list):
        return [f"{suite.name}: suite cases are malformed"]
    reasons: list[str] = []
    for case in cases:
        if not isinstance(case, dict):
            reasons.append(f"{suite.name}: suite case is malformed")
            continue
        assertions = case.get("assertions")
        if not isinstance(assertions, list):
            reasons.append(f"{suite.name}: case assertions are malformed")
            continue
        for assertion in assertions:
            if not isinstance(assertion, dict):
                reasons.append(f"{suite.name}: assertion is malformed")
                continue
            if assertion.get("type") != "repo_command_succeeds":
                continue
            if not eval_workflow.repo_command_is_allowed(assertion.get("command"), workflow_name):
                reasons.append(f"{suite.name}: repository command is not parallel-safe")
    return reasons


def global_hooks_parallel_safety(root: Path) -> list[str]:
    path = root / "automations" / "hooks.json"
    if not path.exists():
        return []
    data, error = common.read_json_file(path)
    if error or not isinstance(data, dict):
        return ["global workflow hooks could not be inspected safely"]
    hooks = data.get("hooks")
    if not isinstance(hooks, list):
        return ["global workflow hooks are malformed"]
    reasons: list[str] = []
    for hook in hooks:
        if not isinstance(hook, dict):
            reasons.append("global workflow hook is malformed")
            continue
        command = str(hook.get("command", ""))
        evidence_path = str(hook.get("evidence_path", "")).replace("\\", "/")
        if (
            set(hook) != CANONICAL_GLOBAL_HOOK_KEYS
            or command != CANONICAL_GLOBAL_HOOK_COMMAND
            or evidence_path != CANONICAL_GLOBAL_HOOK_EVIDENCE
            or hook.get("required") is not True
            or hook.get("timeout_seconds") != 30
            or hook.get("event") not in WORKFLOW_HOOK_EVENTS
            or not isinstance(hook.get("id"), str)
            or not str(hook.get("id")).strip()
        ):
            reasons.append(f"global workflow hook {hook.get('id', '<unknown>')} is not parallel-safe")
    return reasons


def workflow_groups_parallel_safety(
    root: Path,
    groups: list[tuple[str, list[Path]]],
) -> tuple[bool, list[str]]:
    reasons = global_hooks_parallel_safety(root)
    resolved_workflows: dict[Path, str] = {}
    for workflow_name, suites in groups:
        manifest_path = root / "automations" / workflow_name / "module.json"
        workflow_dir = manifest_path.parent
        resolved_workflow_dir = workflow_dir.resolve()
        previous_name = resolved_workflows.get(resolved_workflow_dir)
        if workflow_dir.is_symlink():
            reasons.append(f"{workflow_name}: workflow directory aliases require serial evaluation")
        if previous_name is not None:
            reasons.append(
                f"{workflow_name}: workflow directory aliases {previous_name} and {workflow_name} "
                "require serial evaluation"
            )
        else:
            resolved_workflows[resolved_workflow_dir] = workflow_name
        manifest, error = common.read_json_file(manifest_path)
        if error or not isinstance(manifest, dict):
            reasons.append(f"{workflow_name}: module contract could not be inspected safely")
        elif manifest.get("hooks"):
            reasons.append(f"{workflow_name}: workflow-local hooks require serial evaluation")
        for suite in suites:
            reasons.extend(
                f"{workflow_name}: {reason}"
                for reason in suite_parallel_safety(suite, workflow_name)
            )
    return not reasons, sorted(set(reasons))


def evaluate_workflow_group(
    root: Path,
    workflow_name: str,
    suites: list[Path],
) -> list[tuple[dict[str, object], int, int]]:
    rows: list[tuple[dict[str, object], int, int]] = []
    for suite in suites:
        try:
            report = eval_workflow.run_eval(
                eval_workflow.Args(root=root, workflow_name=workflow_name, suite=suite, output_format="json")
            )
            suite_summary = report.get("summary", {}) if isinstance(report, dict) else {}
            results = report.get("results", []) if isinstance(report.get("results"), list) else []
            failed_results = [item for item in results if isinstance(item, dict) and item.get("ok") is not True]
            suite_passed = int(suite_summary.get("passed", 0) or 0)
            suite_failed = int(suite_summary.get("failed", 0) or 0)
            row: dict[str, object] = {
                "workflow": workflow_name,
                "suite": common.relative(root, suite),
                "ok": suite_failed == 0,
                "summary": suite_summary,
            }
            if failed_results:
                row["failed_results"] = failed_results
            rows.append((row, suite_passed, suite_failed))
        except SystemExit as exc:
            rows.append(
                (
                    {
                        "workflow": workflow_name,
                        "suite": common.relative(root, suite),
                        "ok": False,
                        "summary": {"passed": 0, "failed": 1, "total": 1},
                        "error": str(exc),
                    },
                    0,
                    1,
                )
            )
    return rows


def eval_all_workflows(root: Path, *, summary: bool = False) -> dict[str, object]:
    root = root.expanduser().resolve()
    suites = discover_workflow_eval_suites(root)
    accepted_names = accepted_workflow_names(root)
    suites_by_workflow: dict[str, list[Path]] = {name: [] for name in accepted_names}
    for workflow_name, suite in suites:
        suites_by_workflow.setdefault(workflow_name, []).append(suite)
    groups = [(name, suites_by_workflow[name]) for name in accepted_names if suites_by_workflow.get(name)]
    missing_suite_names = [name for name in accepted_names if not suites_by_workflow.get(name)]

    rows: list[dict[str, object]] = []
    passed = 0
    failed = 0
    parallel_safe, fallback_reasons = workflow_groups_parallel_safety(root, groups)
    worker_count = min(MAX_WORKFLOW_EVAL_WORKERS, len(groups)) if parallel_safe and len(groups) > 1 else 1
    execution_strategy = "parallel-by-workflow" if worker_count > 1 else "serial"

    if worker_count > 1:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="workflow-eval",
        ) as executor:
            grouped_rows = list(
                executor.map(
                    lambda group: evaluate_workflow_group(root, group[0], group[1]),
                    groups,
                )
            )
    else:
        grouped_rows = [
            evaluate_workflow_group(root, workflow_name, group_suites)
            for workflow_name, group_suites in groups
        ]

    for group_rows in grouped_rows:
        for row, suite_passed, suite_failed in group_rows:
            passed += suite_passed
            failed += suite_failed
            rows.append(row)

    for workflow_name in missing_suite_names:
        failed += 1
        rows.append(
            {
                "workflow": workflow_name,
                "suite": "",
                "ok": False,
                "summary": {"passed": 0, "failed": 1, "total": 1},
                "error": "accepted workflow has no discovered eval suite",
            }
        )
    if not accepted_names:
        failed += 1
        rows.append(
            {
                "workflow": "",
                "suite": "",
                "ok": False,
                "summary": {"passed": 0, "failed": 1, "total": 1},
                "error": "no accepted workflows were discovered",
            }
        )

    ok = failed == 0
    report = {
        "schema_version": 1,
        "tool": "eval-workflows",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "summary": {
            "workflows": len(accepted_names),
            "suites": len(suites),
            "passed": passed,
            "failed": failed,
            "cases": passed + failed,
        },
        "execution": {
            "strategy": execution_strategy,
            "workers": worker_count,
            "max_workers": MAX_WORKFLOW_EVAL_WORKERS,
            "workflow_groups": len(groups),
            "parallel_safe": parallel_safe,
            "fallback_reasons": fallback_reasons,
        },
        "results": rows,
        "next_command": "python -B .agents/manage.py workflow eval --all --format json",
    }
    return summarize_eval_all_report(report) if summary else report


def render_eval_all(report: dict[str, object]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = ["# Workflow Eval Summary", ""]
    lines.append(f"- Status: {report.get('status')}")
    lines.append(f"- Workflows: {summary.get('workflows', 0)}")
    lines.append(f"- Suites: {summary.get('suites', 0)}")
    lines.append(f"- Cases: {summary.get('passed', 0)}/{summary.get('cases', 0)} passed")
    results = report.get("results") if isinstance(report.get("results"), list) else []
    if results:
        lines.extend(["", "## Suites", ""])
        for row in results:
            if not isinstance(row, dict):
                continue
            row_summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
            status = "pass" if row.get("ok") else "fail"
            lines.append(
                f"- `{row.get('workflow')}` `{row.get('suite')}`: {status} "
                f"({row_summary.get('passed', 0)}/{row_summary.get('total', 0)})"
            )
            if row.get("error"):
                lines.append(f"  - {row.get('error')}")
    elif report.get("status") == "passed":
        lines.append("- Suite detail omitted in summary mode.")
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"


def context_all_workflow_runs(root: Path, *, include_completed: bool = False) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    skipped_rows: list[dict[str, object]] = []
    for workflow_name in accepted_workflow_names(root):
        module_dir = root / "automations" / workflow_name
        if not workflow_declares_context_packet(module_dir):
            continue
        runs_dir = module_dir / "runs"
        run_dirs = sorted(
            [child for child in runs_dir.iterdir() if child.is_dir()] if runs_dir.exists() else [],
            key=lambda item: item.name.lower(),
        )
        if not run_dirs:
            skipped_rows.append(
                {
                    "workflow": workflow_name,
                    "status": "skipped",
                    "reason": f"workflow has no runs folder: automations/{workflow_name}/runs",
                }
            )
            continue
        for run_dir in run_dirs:
            run_data, read_error = common.read_json_file(run_dir / "run.json")
            run_status = normalized_run_health_status(run_data, read_error)
            packet: dict[str, object] = {}
            issues: list[object] = []
            if read_error:
                issues = [read_error]
                packet = {"ok": False, "status": run_status, "run_id": run_dir.name}
            else:
                try:
                    packet = context_workflow_run(root, workflow_name, run_id=run_dir.name, check=True)
                except SystemExit as exc:
                    packet = {"ok": False, "status": "error", "run_id": run_dir.name}
                    issues = [str(exc)]
            check = packet.get("check") if isinstance(packet.get("check"), dict) else {}
            if not issues:
                issues = packet.get("issues") if isinstance(packet.get("issues"), list) else []
            quality_gate = packet.get("quality_gate") if isinstance(packet.get("quality_gate"), dict) else {}
            context_ok = packet.get("ok") is True
            completed = completed_run_status(run_status)
            advisory = completed and not include_completed and not context_ok
            blocking = not context_ok and not advisory
            rows.append(
                {
                    "workflow": workflow_name,
                    "ok": not blocking,
                    "run_id": run_dir.name,
                    "run_status": run_status,
                    "completed": completed,
                    "advisory": advisory,
                    "blocking": blocking,
                    "run_path": packet.get("run_path", common.relative(root, run_dir)),
                    "context_packet_status": packet.get("status", "unknown"),
                    "context_packet_fresh": check.get("fresh", False),
                    "quality_gate_status": quality_gate.get("status", "unknown"),
                    "quality_gate_failed_count": quality_gate.get("failed_count", 0),
                    "context_packet_path": packet.get("existing_packet_path") or "",
                    "issues": issues,
                    "next_command": (
                        f"python -B .agents/manage.py workflow context --name {workflow_name} "
                        f"--run-id {run_dir.name} --write"
                    ),
                }
            )
    blocking_rows = [row for row in rows if row.get("blocking") is True]
    advisory_rows = [row for row in rows if row.get("advisory") is True]
    ok = not blocking_rows
    return {
        "schema_version": 1,
        "tool": "workflow-manager.context-all",
        "ok": ok,
        "status": "failed" if not ok else ("advisory" if advisory_rows else "ok"),
        "checked_count": len(rows),
        "workflow_count": len({str(row.get("workflow", "")) for row in rows}),
        "blocking_count": len(blocking_rows),
        "advisory_count": len(advisory_rows),
        "completed_count": sum(1 for row in rows if row.get("completed") is True),
        "include_completed": include_completed,
        "skipped_count": len(skipped_rows),
        "skipped_workflows": skipped_rows,
        "workflows": rows,
        "next_command": (
            str(blocking_rows[0].get("next_command", ""))
            if blocking_rows
            else "python -B .agents/manage.py workflow doctor --all --summary"
        ),
    }


def compact_context_all_report(report: dict[str, object]) -> dict[str, object]:
    workflows = report.get("workflows") if isinstance(report.get("workflows"), list) else []
    issue_rows = [
        row
        for row in workflows
        if isinstance(row, dict)
        and (
            row.get("ok") is not True
            or row.get("blocking") is True
            or row.get("advisory") is True
        )
    ]
    skipped_workflows = [
        row
        for row in (report.get("skipped_workflows") if isinstance(report.get("skipped_workflows"), list) else [])
        if isinstance(row, dict)
    ]
    compact_skipped = [
        {
            "workflow": row.get("workflow", ""),
            "status": row.get("status", "skipped"),
            "reason": row.get("reason", ""),
        }
        for row in skipped_workflows[:5]
    ]
    return {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "workflow-manager.context-all"),
        "ok": report.get("ok", False),
        "status": report.get("status", "unknown"),
        "checked_count": report.get("checked_count", 0),
        "workflow_count": report.get("workflow_count", 0),
        "blocking_count": report.get("blocking_count", 0),
        "advisory_count": report.get("advisory_count", 0),
        "completed_count": report.get("completed_count", 0),
        "include_completed": report.get("include_completed", False),
        "skipped_count": report.get("skipped_count", 0),
        "issue_count": len(issue_rows),
        "missing_count": sum(1 for row in issue_rows if row.get("context_packet_status") == "missing"),
        "stale_count": sum(1 for row in issue_rows if row.get("context_packet_status") == "stale"),
        "quality_failed_count": sum(1 for row in issue_rows if row.get("quality_gate_status") == "failed"),
        "skipped_workflows": compact_skipped,
        "omitted_skipped_count": max(0, len(skipped_workflows) - len(compact_skipped)),
        "workflows": issue_rows,
        "next_command": report.get("next_command", ""),
    }


def compact_context_run_report(report: dict[str, object]) -> dict[str, object]:
    check = report.get("check") if isinstance(report.get("check"), dict) else {}
    budget = report.get("context_budget") if isinstance(report.get("context_budget"), dict) else {}
    estimates = report.get("token_estimates") if isinstance(report.get("token_estimates"), dict) else {}
    quality_gate = report.get("quality_gate") if isinstance(report.get("quality_gate"), dict) else {}
    checks = budget.get("checks") if isinstance(budget.get("checks"), list) else []
    failed_budget_checks = [
        row for row in checks if isinstance(row, dict) and row.get("ok") is not True
    ]
    failed_quality_checks = (
        quality_gate.get("failed_checks")
        if isinstance(quality_gate.get("failed_checks"), list)
        else []
    )
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "workflow-manager.context-packet"),
        "ok": report.get("ok", False),
        "status": report.get("status", "unknown"),
        "workflow": report.get("workflow", ""),
        "run_id": report.get("run_id", ""),
        "status": report.get("status", "unknown"),
        "run_path": report.get("run_path", ""),
        "current_phase": report.get("current_phase", ""),
        "context_packet_path": report.get("existing_packet_path") or report.get("context_packet_path", ""),
        "context_packet_fresh": bool(check.get("fresh", False)),
        "markdown_exists": bool(check.get("markdown_exists", False)),
        "raw_tokens": estimates.get("raw_context_tokens_estimated", budget.get("raw_tokens", 0)),
        "packet_tokens": estimates.get("packet_tokens_estimated", budget.get("packet_tokens", 0)),
        "effective_load_tokens": estimates.get("effective_load_tokens_estimated", 0),
        "compression_ratio": budget.get("packet_only_ratio", budget.get("compression_ratio", 0)),
        "effective_load_ratio": budget.get("effective_load_ratio", 0),
        "savings_ratio": budget.get("savings_ratio", 0),
        "failed_budget_checks": failed_budget_checks,
        "quality_gate_status": quality_gate.get("status", "not-run"),
        "quality_gate_failed_count": quality_gate.get("failed_count", 0),
        "failed_quality_checks": failed_quality_checks,
        "issue_count": len(report.get("issues", [])) if isinstance(report.get("issues"), list) else 0,
        "issues": report.get("issues", []),
        "next_action": report.get("next_action", ""),
    }
    execution_profile = compact_execution_profile_report(report.get("execution_profile"))
    if execution_profile:
        output["execution_profile"] = execution_profile
    return output


def _estimated_compact_json_tokens(value: dict[str, object]) -> int:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return (len(data) + 3) // 4 if data else 0


def _print_json_report(report: dict[str, object], *, compact: bool = False) -> None:
    if compact:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return
    print(json.dumps(report, indent=2, sort_keys=True))


def _attach_lifecycle_output_budget(output: dict[str, object], command: str, *, budget_tokens: int = 2000) -> dict[str, object]:
    output["output_budget"] = {
        "command": command,
        "estimated_output_tokens": 0,
        "budget_tokens": budget_tokens,
        "tokens_over_budget": 0,
        "status": "within-budget",
        "summary": f"0/{budget_tokens} estimated output tokens",
    }
    for _index in range(6):
        estimated = _estimated_compact_json_tokens(output)
        over = max(0, estimated - budget_tokens)
        report = {
            "command": command,
            "estimated_output_tokens": estimated,
            "budget_tokens": budget_tokens,
            "tokens_over_budget": over,
            "status": "within-budget" if over == 0 else "over-budget",
            "summary": f"{estimated}/{budget_tokens} estimated output tokens",
        }
        if output.get("output_budget") == report:
            break
        output["output_budget"] = report
    return output


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _unique_strings(values: list[object]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def _first_json_path(values: list[object]) -> str:
    for value in values:
        text = str(value or "")
        if text.endswith(".json"):
            return text
    return ""


def _context_budget_summary(packet: dict[str, object]) -> dict[str, object]:
    estimates = packet.get("token_estimates") if isinstance(packet.get("token_estimates"), dict) else {}
    budget = packet.get("context_budget") if isinstance(packet.get("context_budget"), dict) else {}
    return {
        "packet_tokens_estimated": estimates.get("packet_tokens_estimated", 0),
        "compact_packet_tokens_estimated": estimates.get("compact_packet_tokens_estimated", 0),
        "raw_context_tokens_estimated": estimates.get("raw_context_tokens_estimated", 0),
        "estimated_tokens_saved": estimates.get("estimated_tokens_saved", 0),
        "savings_ratio": budget.get("savings_ratio", 0),
        "status": budget.get("status", ""),
    }


def _checkpoint_budget_summary(packet: dict[str, object]) -> dict[str, object]:
    budget = packet.get("context_budget") if isinstance(packet.get("context_budget"), dict) else {}
    return {
        "checkpoint_tokens_estimated": budget.get("checkpoint_tokens_estimated", 0),
        "raw_tokens_estimated": budget.get("raw_tokens_estimated", 0),
        "estimated_tokens_saved": budget.get("estimated_tokens_saved", 0),
    }


def _check_counts(report: dict[str, object]) -> dict[str, int]:
    return {
        "blocked": len(_as_list(report.get("blockers") or report.get("blocked"))),
        "skipped": len(_as_list(report.get("skipped"))),
        "failed": len(_as_list(report.get("failed"))),
    }


def compact_start_run_report(report: dict[str, object]) -> dict[str, object]:
    preflight = report.get("workflow_preflight") if isinstance(report.get("workflow_preflight"), dict) else {}
    context_packet = report.get("context_packet") if isinstance(report.get("context_packet"), dict) else {}
    checkpoint = report.get("checkpoint") if isinstance(report.get("checkpoint"), dict) else {}
    context_evidence = report.get("context_evidence") if isinstance(report.get("context_evidence"), dict) else {}
    context_paths = context_packet.get("context_packet_paths") if isinstance(context_packet.get("context_packet_paths"), dict) else {}
    checkpoint_written = _as_list(report.get("checkpoint_written"))
    checkpoint_path = str(checkpoint.get("checkpoint_path") or _first_json_path(checkpoint_written))
    context_path = str(report.get("context_packet_path") or context_paths.get("json") or "")
    read_first = _unique_strings([*(_as_list(preflight.get("read_first"))), context_path])
    evidence_paths = _unique_strings(
        [
            report.get("run_path") and f"{report.get('run_path')}/run.json",
            report.get("run_path") and f"{report.get('run_path')}/REPORT.md",
            context_path,
            checkpoint_path,
        ]
    )
    raw_detail_paths = {
        "run": str(report.get("run_path") or ""),
        "preflight": str(report.get("run_path") or "") + "/run.json",
        "context_packet": context_path,
        "checkpoint": checkpoint_path,
        "context_evidence": _first_json_path(_as_list(context_evidence.get("written"))),
    }
    output = {
        "schema_version": report.get("schema_version", 2),
        "tool": report.get("tool", "workflow-manager.start-run"),
        "ok": report.get("ok", False),
        "status": report.get("status", "partial"),
        "workflow": report.get("workflow", ""),
        "run_id": report.get("run_id", ""),
        "run_path": report.get("run_path", ""),
        "current_phase": "orientation",
        "phase_status": "not-started",
        "next_action": report.get("next_action", ""),
        "next_command": str(report.get("next_command", "")) + " --summary --compact --format json",
        "read_first": read_first,
        "evidence_paths": evidence_paths,
        "raw_detail_paths": raw_detail_paths,
        "tool_only_inputs": _unique_strings(_as_list(preflight.get("tool_only_inputs"))),
        "stop_conditions": _unique_strings(_as_list(preflight.get("stop_conditions"))),
        "context_budget": _context_budget_summary(context_packet),
        "checkpoint_budget": _checkpoint_budget_summary(checkpoint),
        "check_counts": _check_counts(report),
    }
    return _attach_lifecycle_output_budget(output, "workflow-start")


def compact_execution_profile_report(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not value:
        return {}
    profile_id = str(value.get("profile_id", "")).strip()
    if not profile_id:
        return {}
    overlay = value.get("prompt_overlay") if isinstance(value.get("prompt_overlay"), dict) else {}
    header = value.get("instruction_header") if isinstance(value.get("instruction_header"), list) else []
    compact: dict[str, object] = {
        "profile_id": profile_id,
        "endpoint_status": value.get("endpoint_status", value.get("status", "unknown")),
        "capability_status": value.get("capability_status", "unknown"),
        "effective_execution_mode": value.get("effective_execution_mode", "serial-active-model"),
        "semantic_instruction": str(header[0]) if header else "",
        "prompt_overlay": {
            "id": overlay.get("id", "generic-v1"),
            "version": overlay.get("version", 1),
            "delivery_directive": overlay.get("delivery_directive", ""),
        },
    }
    for field in (
        "observed_host_surface",
        "observed_model_provider",
        "observed_model",
        "host_observation_source",
        "model_observation_source",
    ):
        if str(value.get(field, "")).strip():
            compact[field] = value[field]
    surface = value.get("surface_adapter") if isinstance(value.get("surface_adapter"), dict) else {}
    if surface:
        compact["surface_adapter"] = {
            key: surface.get(key)
            for key in (
                "id",
                "orchestration_mode",
                "available_orchestration_mode",
                "continuation_mode",
                "cache_mode",
            )
            if key in surface
        }
        enabled = surface.get("enabled_optimizations")
        if isinstance(enabled, dict) and enabled:
            compact["surface_adapter"]["enabled_optimization_ids"] = sorted(enabled)
    reason = " ".join(str(value.get("fallback_reason", "")).split())
    if reason:
        compact["fallback_reason"] = reason[:397].rstrip() + "..." if len(reason) > 400 else reason
    return compact


def compact_resume_run_report(report: dict[str, object]) -> dict[str, object]:
    context_path = str(report.get("context_handoff_json_path") or report.get("context_handoff_path") or "")
    checkpoint_path = str(report.get("checkpoint_path") or "")
    context_evidence = report.get("context_evidence") if isinstance(report.get("context_evidence"), dict) else {}
    required_context = _as_list(report.get("required_next_context"))
    read_first = _unique_strings([context_path, "automations/navigation/artifacts/maps/HANDOFF.md", *required_context[:4]])
    evidence_paths = _unique_strings(
        [
            report.get("run_path") and f"{report.get('run_path')}/run.json",
            report.get("run_path") and f"{report.get('run_path')}/REPORT.md",
            context_path,
            checkpoint_path,
        ]
    )
    raw_detail_paths = {
        "run": str(report.get("run_path") or ""),
        "context_packet": context_path,
        "checkpoint": checkpoint_path,
        "context_evidence": _first_json_path(_as_list(context_evidence.get("written"))),
    }
    execution_profile = compact_execution_profile_report(report.get("execution_profile"))
    output = {
        "schema_version": report.get("schema_version", 2),
        "tool": report.get("tool", "workflow-manager.resume-run"),
        "ok": report.get("ok", False),
        "status": report.get("status", "unknown"),
        "workflow": report.get("workflow", ""),
        "run_id": report.get("run_id", ""),
        "run_path": report.get("run_path", ""),
        "current_phase": report.get("current_phase", ""),
        "phase_status": report.get("phase_status", ""),
        "next_action": report.get("next_action", ""),
        "next_command": report.get("next_command", ""),
        "read_first": read_first,
        "evidence_paths": evidence_paths,
        "raw_detail_paths": raw_detail_paths,
        "context_budget": report.get("context_budget", {}),
        "stop_conditions": _unique_strings(
            [
                "read_first file is missing",
                "context or checkpoint auto-refresh failed",
                "required context evidence is missing",
                "plan-check fails before implementation",
                "raw navigation JSON would be needed for model context",
            ]
        ),
        "required_next_context_count": len(required_context),
        "context_auto_refreshed": bool(report.get("context_auto_refreshed", False)),
        "checkpoint_auto_refreshed": bool(report.get("checkpoint_auto_refreshed", False)),
        "external_validation_status": report.get("external_validation_status", ""),
        "evidence_count": report.get("evidence_count", 0),
        "unsupported_claim_count": report.get("unsupported_claim_count", 0),
        "check_counts": _check_counts(report),
    }
    if execution_profile:
        output["execution_profile"] = execution_profile
    return _attach_lifecycle_output_budget(output, "workflow-resume")


def compact_context_audit_report(report: dict[str, object]) -> dict[str, object]:
    resume_contract = report.get("resume_contract") if isinstance(report.get("resume_contract"), dict) else {}
    compact_resume_contract = {}
    if resume_contract:
        compact_resume_contract = {
            "schema_version": resume_contract.get("schema_version", 1),
            "status": resume_contract.get("status", "unknown"),
            "can_resume": bool(resume_contract.get("can_resume", False)),
            "next_command_mode": resume_contract.get("next_command_mode", ""),
            "read_first": resume_contract.get("read_first", []),
            "blocking_reasons": resume_contract.get("blocking_reasons", []),
            "reason_counts": resume_contract.get("reason_counts", {}),
        }
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "workflow-manager.context-audit"),
        "ok": report.get("ok", False),
        "status": report.get("status", "unknown"),
        "workflow": report.get("workflow", ""),
        "run_id": report.get("run_id", ""),
        "context_packet_status": report.get("context_packet_status", "unknown"),
        "context_packet_fresh": bool(report.get("context_packet_fresh", False)),
        "context_packet_path": report.get("context_packet_path", ""),
        "required_next_context_count": report.get("required_next_context_count", 0),
        "missing_required_context_count": len(report.get("missing_required_context", []) if isinstance(report.get("missing_required_context"), list) else []),
        "missing_evidence_path_count": len(report.get("missing_evidence_paths", []) if isinstance(report.get("missing_evidence_paths"), list) else []),
        "quality_gate_status": report.get("quality_gate_status", "unknown"),
        "quality_gate_failed_count": report.get("quality_gate_failed_count", 0),
        "issue_count": report.get("issue_count", 0),
        "issues": report.get("issues", []),
        "resume_contract": compact_resume_contract,
        "next_command": report.get("next_command", ""),
    }
    if not output["issues"]:
        output.pop("issues", None)
    return output


def compact_checkpoint_run_report(report: dict[str, object]) -> dict[str, object]:
    check = report.get("check") if isinstance(report.get("check"), dict) else {}
    budget = report.get("context_budget") if isinstance(report.get("context_budget"), dict) else {}
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    return {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "workflow-manager.checkpoint"),
        "ok": report.get("ok", False),
        "status": report.get("status", "unknown"),
        "workflow": report.get("workflow", ""),
        "run_id": report.get("run_id", ""),
        "run_path": report.get("run_path", ""),
        "checkpoint_path": report.get("existing_checkpoint_path") or report.get("checkpoint_path", ""),
        "checkpoint_fresh": bool(check.get("fresh", False)),
        "markdown_exists": bool(check.get("markdown_exists", False)),
        "fingerprint": report.get("fingerprint", ""),
        "raw_tokens": budget.get("raw_tokens_estimated", 0),
        "checkpoint_tokens": budget.get("checkpoint_tokens_estimated", 0),
        "estimated_tokens_saved": budget.get("estimated_tokens_saved", 0),
        "issue_count": len(issues),
        "issues": issues,
        "next_command": report.get("next_command", ""),
    }


def compact_finish_run_report(report: dict[str, object]) -> dict[str, object]:
    """Return bounded finish evidence without repeating full proof/checkpoint packets."""

    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    advisories = report.get("advisories") if isinstance(report.get("advisories"), list) else []
    missing_proof = report.get("missing_proof") if isinstance(report.get("missing_proof"), list) else []
    completeness = report.get("evidence_completeness") if isinstance(report.get("evidence_completeness"), dict) else {}
    return {
        "schema_version": report.get("schema_version", 2),
        "tool": report.get("tool", "workflow-manager.finish-run"),
        "ok": report.get("ok") is True,
        "workflow": report.get("workflow", ""),
        "run_id": report.get("run_id", ""),
        "run_path": report.get("run_path", ""),
        "external_validation_status": report.get("external_validation_status", "not-recorded"),
        "issue_count": len(issues),
        "issues": issues[:12],
        "advisory_count": len(advisories),
        "advisories": advisories[:12],
        "missing_proof_count": len(missing_proof),
        "evidence_completeness": {
            "status": completeness.get("status", ""),
            "missing_count": completeness.get("missing_count", 0),
            "optional_missing_count": completeness.get("optional_missing_count", 0),
            "evidence_entry_count": completeness.get("evidence_entry_count", 0),
            "evidence_path_count": completeness.get("evidence_path_count", 0),
            "unsupported_claim_count": completeness.get("unsupported_claim_count", 0),
        },
        "state_path": report.get("state_path", ""),
        "final_report_path": report.get("final_report_path", ""),
        "next_command": report.get("next_command", ""),
    }


def render_context_all(report: dict[str, object]) -> str:
    lines = ["# Workflow Context Packet Checks", ""]
    lines.append(f"- Status: {report.get('status')}")
    lines.append(f"- Checked workflows: {report.get('checked_count', 0)}")
    if report.get("skipped_count"):
        lines.append(f"- Skipped clean workflows: {report.get('skipped_count', 0)}")
    workflows = report.get("workflows") if isinstance(report.get("workflows"), list) else []
    if workflows:
        lines.extend(["", "## Workflows", ""])
        for row in workflows:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- `{row.get('workflow')}`: {row.get('context_packet_status')} "
                f"(fresh: {row.get('context_packet_fresh')})"
            )
            if row.get("quality_gate_status") == "failed":
                lines.append(f"  - quality gate failed checks: {row.get('quality_gate_failed_count', 0)}")
            issues = row.get("issues") if isinstance(row.get("issues"), list) else []
            for issue in issues:
                lines.append(f"  - {issue}")
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"


def hooks_all_workflow_runs(root: Path, *, event: str | None = None, check: bool = False) -> dict[str, object]:
    _ = check
    rows: list[dict[str, object]] = []
    for workflow_name in accepted_workflow_names(root):
        try:
            packet = hooks_workflow_run(root, workflow_name, event=event)
        except SystemExit as exc:
            rows.append(
                {
                    "workflow": workflow_name,
                    "ok": False,
                    "hook_count": 0,
                    "required_count": 0,
                    "unsafe_count": 1,
                    "events": [event] if event else sorted(WORKFLOW_HOOK_EVENTS),
                    "issues": [str(exc)],
                    "next_command": f"python -B .agents/manage.py workflow hooks --name {workflow_name} --format json",
                }
            )
            continue
        hooks = packet.get("hooks") if isinstance(packet.get("hooks"), list) else []
        unsafe_hooks = [hook for hook in hooks if isinstance(hook, dict) and hook.get("safe") is not True]
        rows.append(
            {
                "workflow": workflow_name,
                "ok": packet.get("ok") is True,
                "hook_count": packet.get("hook_count", 0),
                "required_count": packet.get("required_count", 0),
                "unsafe_count": packet.get("unsafe_count", 0),
                "events": packet.get("events", []),
                "unsafe_hooks": [
                    {
                        "id": hook.get("id", ""),
                        "event": hook.get("event", ""),
                        "scope": hook.get("scope", ""),
                        "source": hook.get("source", ""),
                        "command": hook.get("command", ""),
                    }
                    for hook in unsafe_hooks
                ],
                "issues": ["unsafe workflow hooks resolved"] if unsafe_hooks else [],
                "next_command": f"python -B .agents/manage.py workflow hooks --name {workflow_name} --format json",
            }
        )
    unsafe_count = sum(int(row.get("unsafe_count", 0)) for row in rows)
    hook_count = sum(int(row.get("hook_count", 0)) for row in rows)
    required_count = sum(int(row.get("required_count", 0)) for row in rows)
    ok = all(row.get("ok") is True for row in rows)
    return {
        "schema_version": 1,
        "tool": "workflow-manager.hooks-all",
        "ok": ok,
        "status": "ok" if ok else "failed",
        "checked_count": len(rows),
        "hook_count": hook_count,
        "required_count": required_count,
        "unsafe_count": unsafe_count,
        "events": [event] if event else sorted(WORKFLOW_HOOK_EVENTS),
        "workflows": rows,
        "next_command": "python -B .agents/manage.py validate-automations --strict-phase-quality",
    }


def compact_hooks_all_report(report: dict[str, object]) -> dict[str, object]:
    workflows = report.get("workflows") if isinstance(report.get("workflows"), list) else []
    issue_rows = [
        row
        for row in workflows
        if isinstance(row, dict) and (row.get("ok") is not True or int(row.get("unsafe_count", 0) or 0) > 0)
    ]
    events = report.get("events") if isinstance(report.get("events"), list) else []
    return {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "workflow-manager.hooks-all"),
        "ok": report.get("ok", False),
        "status": report.get("status", "unknown"),
        "checked_count": report.get("checked_count", 0),
        "hook_count": report.get("hook_count", 0),
        "required_count": report.get("required_count", 0),
        "unsafe_count": report.get("unsafe_count", 0),
        "event_count": len(events),
        "workflows": issue_rows,
        "next_command": report.get("next_command", ""),
    }


def compact_hooks_run_report(report: dict[str, object]) -> dict[str, object]:
    hooks = report.get("hooks") if isinstance(report.get("hooks"), list) else []
    unsafe_hooks = [
        {
            "id": hook.get("id", ""),
            "event": hook.get("event", ""),
            "scope": hook.get("scope", ""),
            "source": hook.get("source", ""),
            "command": hook.get("command", ""),
        }
        for hook in hooks
        if isinstance(hook, dict) and hook.get("safe") is not True
    ]
    output: dict[str, object] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "workflow-manager.hooks"),
        "ok": report.get("ok", False),
        "workflow": report.get("workflow", ""),
        "run_id": report.get("run_id", ""),
        "run_exists": bool(report.get("run_exists", False)),
        "hook_count": report.get("hook_count", 0),
        "required_count": report.get("required_count", 0),
        "unsafe_count": report.get("unsafe_count", 0),
        "event_count": len(report.get("events", []) if isinstance(report.get("events"), list) else []),
        "unsafe_hooks": unsafe_hooks,
        "issues": report.get("issues", []),
        "next_command": report.get("next_command", ""),
    }
    if unsafe_hooks:
        output["hooks"] = unsafe_hooks
    return output


def render_hooks_all(report: dict[str, object]) -> str:
    lines = ["# Workflow Hook Checks", ""]
    lines.append(f"- Status: {report.get('status')}")
    lines.append(f"- Checked workflows: {report.get('checked_count', 0)}")
    lines.append(f"- Hooks: {report.get('hook_count', 0)}")
    lines.append(f"- Required: {report.get('required_count', 0)}")
    lines.append(f"- Unsafe: {report.get('unsafe_count', 0)}")
    workflows = report.get("workflows") if isinstance(report.get("workflows"), list) else []
    if workflows:
        lines.extend(["", "## Workflows", ""])
        for row in workflows:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- `{row.get('workflow')}`: {row.get('hook_count', 0)} hooks, "
                f"{row.get('unsafe_count', 0)} unsafe"
            )
            issues = row.get("issues") if isinstance(row.get("issues"), list) else []
            for issue in issues:
                lines.append(f"  - {issue}")
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"


def review_all_workflows(
    root: Path,
    *,
    include_plan: bool = False,
    summary: bool = False,
    compact: bool = False,
    include_completed: bool = False,
) -> dict[str, object]:
    rows = [
        review_workflow(
            root,
            name,
            include_plan=include_plan and not summary,
            include_completed=include_completed,
        )
        for name in accepted_workflow_names(root)
    ]
    issues = []
    warnings = []
    for row in rows:
        validation = row.get("validation") if isinstance(row.get("validation"), dict) else {}
        issues.extend(str(item) for item in validation.get("errors", []) if str(item))
        warnings.extend(str(item) for item in validation.get("warnings", []) if str(item))
        budget = row.get("context_budget") if isinstance(row.get("context_budget"), dict) else {}
        warnings.extend(
            f"{row.get('workflow')}: {item.get('path')} {item.get('words')}>{item.get('warning_threshold')} ({item.get('tier')})"
            for item in budget.get("warning_details", [])
            if isinstance(item, dict)
        )
    primary_rows: list[dict[str, object]] = []
    context_rows: list[dict[str, object]] = []
    for row in rows:
        validation = row.get("validation") if isinstance(row.get("validation"), dict) else {}
        budget = row.get("context_budget") if isinstance(row.get("context_budget"), dict) else {}
        run_summary = row.get("run_summary") if isinstance(row.get("run_summary"), dict) else {}
        context_packet = run_summary.get("context_packet") if isinstance(run_summary.get("context_packet"), dict) else {}
        out_of_scope = row.get("out_of_scope") if isinstance(row.get("out_of_scope"), dict) else {}
        risk = "ok"
        if validation.get("errors"):
            risk = "validation"
        elif budget.get("status") == "warning":
            risk = "budget"
        elif out_of_scope.get("required") is True and out_of_scope.get("status") != "ok":
            risk = "out-of-scope"
        elif run_summary.get("status") == "unindexed":
            risk = str(run_summary.get("status"))
        elif (
            run_summary.get("status") != "no-retained-runs"
            and run_summary.get("blocking") is True
        ):
            risk = "context-packet"
        primary_rows.append(
            {
                "workflow": row.get("workflow", ""),
                "risk": risk,
                "context_budget_status": budget.get("status", ""),
                "run_id": run_summary.get("run_id", ""),
                "run_status": run_summary.get("status", ""),
                "run_retention_policy": run_summary.get("run_retention_policy", ""),
                "run_advisory": run_summary.get("advisory") is True,
                "run_advisory_issue": (
                    run_summary.get("advisory") is True
                    and context_packet.get("required") is True
                    and context_packet.get("status") != "ok"
                ),
                "out_of_scope_status": out_of_scope.get("status", ""),
                "out_of_scope_missing": out_of_scope.get("missing", []),
                "context_packet_status": context_packet.get("status", ""),
                "context_packet_fresh": context_packet.get("fresh"),
                "next_command": (
                    context_packet.get("next_command")
                    if risk == "context-packet" or run_summary.get("advisory") is True
                    else f"python -B .agents/manage.py workflow doctor --name {row.get('workflow')}"
                ),
            }
        )
        run_rows = run_summary.get("runs") if isinstance(run_summary.get("runs"), list) else []
        for run_row in run_rows:
            if not isinstance(run_row, dict):
                continue
            if run_row.get("blocking") is not True and run_row.get("advisory") is not True:
                continue
            context_rows.append(
                {
                    "workflow": row.get("workflow", ""),
                    "risk": "context-packet" if run_row.get("blocking") is True else "ok",
                    "run_id": run_row.get("run_id", ""),
                    "run_status": run_row.get("run_status", "unknown"),
                    "completed": run_row.get("completed") is True,
                    "blocking": run_row.get("blocking") is True,
                    "advisory": run_row.get("advisory") is True,
                    "run_advisory": run_row.get("advisory") is True,
                    "run_advisory_issue": run_row.get("advisory") is True,
                    "context_packet_status": run_row.get("context_packet_status", "unknown"),
                    "context_packet_fresh": run_row.get("context_packet_fresh"),
                    "next_command": run_row.get("next_command", ""),
                }
            )
    blocking_context_rows = [row for row in context_rows if row.get("blocking") is True]
    advisory_rows = [row for row in context_rows if row.get("advisory") is True]
    static_risks = [
        row
        for row in primary_rows
        if row.get("risk") not in {"ok", "context-packet"}
    ]
    risk_rows = [*static_risks, *blocking_context_rows]
    workflows = primary_rows if summary else rows
    if summary and compact:
        workflows = [*risk_rows, *advisory_rows]
    ok = not issues and not blocking_context_rows
    output = {
        "schema_version": 1,
        "tool": "workflow-manager.doctor-all",
        "ok": ok,
        "status": "failed" if not ok else (
            "warning"
            if risk_rows or advisory_rows
            else "ok"
        ),
        "workflow_count": len(rows),
        "summary": {
            "workflow_count": len(rows),
            "risk_count": len(risk_rows),
            "issue_count": len(issues),
            "warning_count": len(set(warnings)),
            "retained_run_count": sum(
                int((row.get("run_summary") or {}).get("run_count", 0))
                for row in rows
                if isinstance(row.get("run_summary"), dict)
            ),
            "context_row_count": len(context_rows),
            "blocking_context_count": len(blocking_context_rows),
            "advisory_count": len(advisory_rows),
            "completed_count": sum(
                int((row.get("run_summary") or {}).get("completed_count", 0))
                for row in rows
                if isinstance(row.get("run_summary"), dict)
            ),
        },
        "issues": issues,
        "warnings": sorted(set(warnings)),
        "risks": risk_rows,
        "advisories": advisory_rows,
        "context_rows": context_rows,
        "workflows": workflows,
        "include_completed": include_completed,
        "next_command": (
            str(blocking_context_rows[0].get("next_command", ""))
            if blocking_context_rows
            else "python -B .agents/manage.py validate-automations --strict-phase-quality"
        ),
    }
    if summary and compact:
        if not output.get("issues"):
            output.pop("issues", None)
        if not output.get("warnings"):
            output.pop("warnings", None)
        if not output.get("risks"):
            output.pop("risks", None)
        if not output.get("advisories"):
            output.pop("advisories", None)
        if not output.get("context_rows"):
            output.pop("context_rows", None)
        if not output.get("workflows"):
            output.pop("workflows", None)
        if output.get("status") == "ok":
            output.pop("next_command", None)
    return output


def render_review_all(report: dict[str, object]) -> str:
    lines = ["# Workflow Doctor Summary", ""]
    lines.append(f"- Status: {report.get('status')}")
    lines.append(f"- Workflows: {report.get('workflow_count')}")
    risks = report.get("risks", []) if isinstance(report.get("risks"), list) else []
    lines.append(f"- Risks: {len(risks)}")
    if risks:
        lines.extend(["", "## Risks", ""])
        for item in risks[:30]:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('workflow')}`: {item.get('risk')} - `{item.get('next_command')}`")
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        for warning in report.get("warnings", [])[:40]:
            lines.append(f"- {warning}")
    if report.get("issues"):
        lines.extend(["", "## Issues", ""])
        for issue in report.get("issues", []):
            lines.append(f"- {issue}")
    lines.extend(["", f"Next command: `{report.get('next_command')}`", ""])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    common.require_supported_python()
    args = build_parser().parse_args(argv)
    root = root_from_args(args)

    if hasattr(args, "workflow_name") and hasattr(args, "run_id"):
        args.run_id = canonical_workflow_run_id(
            str(getattr(args, "workflow_name", "") or ""),
            getattr(args, "run_id", None),
            require_ticket_identifier=args.command == "start-run",
        )

    if args.command == "validate-automations":
        errors, warnings, modules = validate_automations.validate_automations(
            root,
            workflow_name=args.workflow_name,
            strict_phase_quality=args.strict_phase_quality,
        )
        if args.output_format == "json":
            print(
                render_json_report(
                    root,
                    errors,
                    warnings,
                    modules,
                    summary=bool(getattr(args, "summary", False)),
                    compact=bool(getattr(args, "compact", False)),
                ),
                end="",
            )
        else:
            print(
                render_markdown_report(
                    root,
                    errors,
                    warnings,
                    modules,
                    summary=bool(getattr(args, "summary", False)),
                    compact=bool(getattr(args, "compact", False)),
                ),
                end="",
            )
        return 1 if errors else 0

    if args.command == "eval-workflow":
        report = eval_workflow.run_eval(
            eval_workflow.Args(
                root=root,
                workflow_name=args.workflow_name,
                suite=Path(args.suite).expanduser().resolve(),
                output_format=args.output_format,
                summary=bool(getattr(args, "summary", False)),
                compact=bool(getattr(args, "compact", False)),
            )
        )
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(eval_workflow.render_markdown(report))
        return 1 if report["summary"]["failed"] else 0

    if args.command == "eval-workflows":
        report = eval_all_workflows(
            root,
            summary=bool(getattr(args, "summary", False) or getattr(args, "compact", False)),
        )
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_eval_all(report), end="")
        return 0 if report["ok"] else 1

    if args.command == "smoke-workflows":
        report = smoke_workflows(
            root,
            workflow_names=args.workflow_names,
            include_domain_checks=not args.lifecycle_only,
            dry_run=bool(getattr(args, "dry_run", False)),
            summary=bool(getattr(args, "summary", False)),
            compact=bool(getattr(args, "compact", False)),
        )
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_smoke_markdown(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "scorecard-workflows":
        report = scorecards(
            root,
            workflow_names=args.workflow_names,
            run_lifecycle=not bool(getattr(args, "no_lifecycle", False)),
        )
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = compact_scorecards(report)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_scorecards(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "analytics-workflows":
        report = workflow_analytics(root, workflow_names=args.workflow_names)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = compact_analytics(report)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_analytics(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "workflow-workers":
        if getattr(args, "profiles", False):
            if getattr(args, "run_id", None):
                raise SystemExit("workflow workers --run-id cannot be combined with --profiles")
            report = profile_catalog_report(compact=bool(getattr(args, "compact", False)))
        else:
            runtime_observation = None
            runtime_observation_verification_issues: list[str] = []
            observation_run_id = ""
            if getattr(args, "run_id", None):
                if args.all or not isinstance(args.workflow_names, list) or len(args.workflow_names) != 1:
                    raise SystemExit("workflow workers --run-id requires exactly one --name")
                if not args.phase:
                    raise SystemExit("workflow workers --run-id requires --phase")
                workflow_name = args.workflow_names[0]
                run_dir = latest_or_selected_run_dir(root, workflow_name, args.run_id)
                run_packet = normalized_run_state(
                    root,
                    workflow_name,
                    run_dir,
                    read_json_object(run_dir / "run.json"),
                )
                current_phase = str(run_packet.get("current_phase") or "").strip()
                requested_phase = str(args.phase or "").strip()
                if requested_phase != current_phase:
                    raise SystemExit(
                        "workflow workers --run-id phase must match run.json current_phase: "
                        f"requested {requested_phase!r}, current {current_phase!r}"
                    )
                persisted = (
                    run_packet.get("runtime_observation")
                    if isinstance(run_packet.get("runtime_observation"), dict)
                    else None
                )
                runtime_observation, runtime_observation_verification_issues = (
                    verify_persisted_runtime_observation(
                        root,
                        workflow_name,
                        run_dir.name,
                        current_phase,
                        persisted,
                    )
                )
                observation_run_id = run_dir.name
            report = workflow_workers_report(
                root,
                workflow_names=None if args.all else args.workflow_names,
                phase=args.phase,
                summary=bool(getattr(args, "summary", False)),
                compact=bool(getattr(args, "compact", False)),
                delegation_requested=bool(getattr(args, "delegation_requested", False)),
                task_class=str(getattr(args, "task_class", "independent-read-heavy")),
                runtime_observation=runtime_observation,
                runtime_observation_verification_issues=runtime_observation_verification_issues,
                observation_run_id=observation_run_id,
            )
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_workers_markdown(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "workflow-route-model":
        report = resolve_orchestration(
            root,
            task=getattr(args, "task", None),
            task_set=getattr(args, "task_set", None),
            host=str(args.host),
            available_models=getattr(args, "available_models", None),
            failed_models=getattr(args, "failed_models", None),
            validate_only=bool(getattr(args, "validate", False)),
        )
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_orchestration_markdown(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "index-workflow-runs":
        return index_workflow_runs.run(
            index_workflow_runs.Args(
                root=root,
                workflow_name=args.workflow_name,
                write=args.write,
                check=args.check,
                output_format=args.output_format,
            ),
            emit=True,
        )

    if args.command == "sync-automation-routing":
        return sync_automation_routing.sync_automation_routing(root, check=args.check)

    if args.command == "create-workflow":
        args.root = str(root)
        written = create_workflow.create_workflow(args)
        for path in written:
            print(path.relative_to(root).as_posix())
        print(f"Next: python -B .agents/manage.py validate-automations --name {args.workflow_name}")
        return 0

    if args.command == "propose-workflow":
        compact_output = bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False))
        report = proposal_report(
            root,
            args.from_request,
            workflow_name=args.workflow_name,
            recipe_id=args.recipe,
            profile=args.profile,
            force_new=args.force_new,
            compact=compact_output,
        )
        if args.output_format == "json":
            _print_json_report(report, compact=compact_output)
        else:
            print(render_builder_report(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "workflow-recipes":
        compact_output = bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False))
        report = recipes_report(compact=compact_output)
        if args.output_format == "json":
            _print_json_report(report, compact=compact_output)
        else:
            print(render_builder_report(report), end="")
        return 0

    if args.command == "create-workflow-from-request":
        compact_output = bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False))
        report = create_from_request(
            root,
            args.from_request,
            workflow_name=args.workflow_name,
            recipe_id=args.recipe,
            profile=args.profile,
            uses_skill=args.uses_skill,
            uses_script=args.uses_script,
            write=args.write,
            force=args.force,
            force_new=args.force_new,
            compact=compact_output,
        )
        if args.output_format == "json":
            _print_json_report(report, compact=compact_output)
        else:
            print(render_builder_report(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "adjust-workflow":
        compact_output = bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False))
        report = adjust_plan_report(
            root,
            args.workflow_name,
            args.from_request,
            recipe_id=args.recipe,
            profile=args.profile,
            compact=compact_output,
        )
        if args.output_format == "json":
            _print_json_report(report, compact=compact_output)
        else:
            print(render_builder_report(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "review-workflow":
        if getattr(args, "all", False):
            report = review_all_workflows(
                root,
                include_plan=args.plan,
                summary=bool(args.summary),
                compact=bool(getattr(args, "compact", False)),
                include_completed=bool(getattr(args, "include_completed", False)),
            )
            if args.output_format == "json":
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print(render_review_all(report), end="")
            return 0 if report.get("ok") else 1
        if bool(getattr(args, "include_completed", False)):
            raise SystemExit("workflow review --include-completed requires --all")
        report = review_workflow(root, args.workflow_name, include_plan=args.plan)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_review(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "start-run":
        compact_output = bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False))
        report = start_workflow_run(
            root,
            args.workflow_name,
            run_id=args.run_id,
            from_ticket=args.from_ticket,
            from_request=getattr(args, "from_request", None),
            profile=args.profile,
        )
        if compact_output:
            report = compact_start_run_report(report)
        if args.output_format == "json":
            _print_json_report(report, compact=compact_output)
        else:
            print(render_start_run(report), end="")
        return 0

    if args.command == "resume-run":
        report = resume_workflow_run(root, args.workflow_name, run_id=args.run_id)
        compact_output = bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False))
        if compact_output:
            report = compact_resume_run_report(report)
        if args.output_format == "json":
            _print_json_report(report, compact=compact_output)
        else:
            print(render_resume_run(report), end="")
        return 0

    if args.command == "recover-run":
        report = recover_workflow_run(root, args.workflow_name, run_id=args.run_id, write=args.write)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_recover_markdown(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "context-run":
        if args.all:
            if args.workflow_name or args.run_id or args.write or args.runtime_observation_file:
                raise SystemExit(
                    "workflow context --all only supports --check, --include-completed, "
                    "--summary, --compact, and --format"
                )
            if not args.check:
                raise SystemExit("workflow context --all requires --check")
            report = context_all_workflow_runs(
                root,
                include_completed=bool(getattr(args, "include_completed", False)),
            )
            if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
                report = compact_context_all_report(report)
            if args.output_format == "json":
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print(render_context_all(report), end="")
            return 0 if report.get("ok") else 1
        if not args.workflow_name:
            raise SystemExit("workflow context requires --name or --all")
        if bool(getattr(args, "include_completed", False)):
            raise SystemExit("workflow context --include-completed requires --all")
        runtime_observation = None
        if args.runtime_observation_file:
            if not args.write:
                raise SystemExit("workflow context --runtime-observation-file requires --write")
            runtime_observation = load_runtime_observation_packet(
                root,
                args.workflow_name,
                args.run_id,
                args.runtime_observation_file,
            )
        report = context_workflow_run(
            root,
            args.workflow_name,
            run_id=args.run_id,
            write=args.write,
            check=args.check,
            runtime_observation=runtime_observation,
        )
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = compact_context_run_report(report)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_context_packet_markdown(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "context-audit-run":
        report = context_audit_workflow_run(root, args.workflow_name, run_id=args.run_id)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = compact_context_audit_report(report)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_context_audit(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "checkpoint-run":
        report = checkpoint_workflow_run(
            root,
            args.workflow_name,
            run_id=args.run_id,
            write=args.write,
            check=args.check,
        )
        if bool(getattr(args, "compact", False)):
            report = compact_checkpoint_run_report(report)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_checkpoint_markdown(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "plan-check-run":
        report = workflow_plan_check.check_plan(
            root,
            args.workflow_name,
            run_id=args.run_id,
            template=args.template,
            plan_path=Path(args.plan) if args.plan else None,
        )
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(workflow_plan_check.render_plan_check(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "template-run":
        if args.template_command == "resolve":
            report = resolve_template(root, args.workflow_name, args.template, profile=args.profile)
            title = "Workflow Template Resolve"
        elif args.template_command == "lint":
            report = lint_templates(root, args.workflow_name)
            title = "Workflow Template Lint"
        elif args.template_command == "gate-check":
            report = template_gate_check(root, None if args.all else args.workflow_name)
            title = "Workflow Template Gate Check"
        else:
            raise SystemExit(f"unknown template command: {args.template_command}")
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_simple_report(report, title), end="")
        return 0 if report.get("ok") else 1

    if args.command == "integration-check-run":
        report = integration_check(root)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_simple_report(report, "Integration Descriptor Check"), end="")
        return 0 if report.get("ok") else 1

    if args.command == "metadata-run":
        if args.metadata_command != "inspect":
            raise SystemExit(f"unknown metadata command: {args.metadata_command}")
        report = metadata_inspect(root, args.workflow_name)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_simple_report(report, "Workflow Metadata Inspect"), end="")
        return 0 if report.get("ok") else 1

    if args.command == "managed-section-diff-run":
        report = managed_section_diff(
            root,
            Path(args.target),
            Path(args.replacement),
            start_marker=args.start_marker,
            end_marker=args.end_marker,
        )
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_simple_report(report, "Managed Section Diff"), end="")
        return 0 if report.get("ok") else 1

    if args.command == "branch-policy-run":
        report = branch_policy_check(root, pattern=args.pattern, branch=args.branch)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_simple_report(report, "Branch Policy"), end="")
        return 0 if report.get("ok") else 1

    if args.command == "context-evidence-run":
        run_dir = latest_or_selected_run_dir(root, args.workflow_name, args.run_id)
        run_packet = normalized_run_state(root, args.workflow_name, run_dir, read_json_object(run_dir / "run.json"))
        if args.check:
            issues = workflow_context_evidence.validate_context_evidence_packet(
                root,
                args.workflow_name,
                run_dir,
                event=args.event,
            )
            report = {
                "schema_version": 1,
                "tool": "workflow-manager.context-evidence-check",
                "ok": not issues,
                "status": "ok" if not issues else "failed",
                "workflow": args.workflow_name,
                "run_id": run_dir.name,
                "event": args.event,
                "issues": issues,
                "next_command": (
                    f"python -B .agents/manage.py workflow context-evidence --name {args.workflow_name} "
                    f"--run-id {run_dir.name} --event {args.event} --write"
                ),
            }
        else:
            report = workflow_context_evidence.write_context_evidence_packet(
                root,
                args.workflow_name,
                run_dir,
                run_packet,
                event=args.event,
                write=args.write,
                write_run=args.write,
            )
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(workflow_context_evidence.render_packet(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "validation-packet-run":
        report = validate_packet(
            root,
            args.workflow_name,
            args.run_id,
            kind=args.kind,
            require_llm_analysis=bool(getattr(args, "require_llm_analysis", False)),
        )
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_validation_packet(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "hooks-run":
        if args.all:
            if args.workflow_name or args.run_id:
                raise SystemExit("workflow hooks --all only supports --check, --event, and --format")
            if not args.check:
                raise SystemExit("workflow hooks --all requires --check")
            report = hooks_all_workflow_runs(root, event=args.event, check=True)
            if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
                report = compact_hooks_all_report(report)
            if args.output_format == "json":
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print(render_hooks_all(report), end="")
            return 0 if report.get("ok") else 1
        if not args.workflow_name:
            raise SystemExit("workflow hooks requires --name or --all")
        report = hooks_workflow_run(root, args.workflow_name, run_id=args.run_id, event=args.event)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = compact_hooks_run_report(report)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_hooks_run(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "hook-audit-run":
        run_dir = Path(args.run_dir)
        if args.run_id and run_dir.name != args.run_id:
            raise SystemExit("workflow hook-audit --run-id must match --run-dir name")
        report = write_hook_audit_packet(
            root,
            args.workflow_name,
            run_dir,
            event=args.event,
            hook_id=args.hook_id,
            output_path=Path(args.output) if args.output else None,
        )
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_hook_audit_packet(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "finish-run":
        report = finish_workflow_run(root, args.workflow_name, run_id=args.run_id)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = compact_finish_run_report(report)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_finish_run(report), end="")
        return 0 if report.get("ok") else 1

    if args.command == "handoff-run":
        report = handoff_workflow_run(
            root,
            args.workflow_name,
            run_id=args.run_id,
            write=args.write,
        )
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_handoff_markdown(report), end="")
        return 0 if report.get("ok") else 1

    raise SystemExit(f"unknown workflow command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
