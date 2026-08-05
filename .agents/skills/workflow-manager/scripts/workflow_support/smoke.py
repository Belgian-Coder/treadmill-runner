#!/usr/bin/env python3
"""Offline workflow smoke checks for accepted workflow modules."""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import workflow_manager_common as common

from workflow_support.smoke_common import (
    cleanup_smoke_run as _cleanup_smoke_run,
    is_tracked_file,
    named_command_check,
    read_json,
    restore_run_index_state,
    skipped_check,
    smoke_run_id,
    snapshot_run_index_state,
    workflow_manifest,
    write_json,
)
from workflow_support.smoke_domain import (
    domain_smoke_checks,
    dotnet_framework_migration_fixture_model_checks,
    dotnet_upgrade_fixture_model_checks,
    fill_smoke_domain_outputs,
)
from workflow_run_support import (
    checkpoint_workflow_run,
    context_workflow_run,
    finish_workflow_run,
    handoff_workflow_run,
    start_workflow_run,
)


SMOKE_PREFIX = "smoke-local"
SMOKE_COMMAND_ID = "smoke-workflows"


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def elapsed_ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def estimated_json_output_tokens(payload: dict[str, Any]) -> int:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return max(1, (len(text) + 3) // 4)


def smoke_latency_budget(elapsed_ms: float) -> dict[str, Any]:
    latency_budget_ms = common.project_policy_int("commands.latency_ms.smoke-workflows")
    over = max(0, int(round(elapsed_ms)) - latency_budget_ms)
    return {
        "command": SMOKE_COMMAND_ID,
        "status": "over-budget" if over else "within-budget",
        "elapsed_ms": round(float(elapsed_ms or 0), 2),
        "budget_ms": latency_budget_ms,
        "over_budget_ms": over,
        "summary": f"{SMOKE_COMMAND_ID} elapsed {round(float(elapsed_ms or 0), 2)}ms against {latency_budget_ms}ms budget",
    }


def attach_smoke_budgets(payload: dict[str, Any], started: float) -> dict[str, Any]:
    payload["latency_budget"] = smoke_latency_budget(elapsed_ms_since(started))
    estimate = estimated_json_output_tokens(payload)
    output_budget_tokens = common.project_policy_int("commands.output_tokens.smoke-workflows")
    status = "within-budget" if estimate <= output_budget_tokens else "over-budget"
    payload["output_budget"] = {
        "command": SMOKE_COMMAND_ID,
        "status": status,
        "estimated_output_tokens": estimate,
        "budget_tokens": output_budget_tokens,
        "tokens_over_budget": max(0, estimate - output_budget_tokens),
        "counter": "compact_json_bytes_div_4",
        "scope": "summary-compact-json-estimate",
        "summary": f"{estimate}/{output_budget_tokens} estimated output tokens",
    }
    return payload


def accepted_workflow_names(root: Path) -> list[str]:
    automations = root / "automations"
    if not automations.exists():
        return []
    names: list[str] = []
    for module_dir in sorted(automations.iterdir(), key=lambda item: item.name):
        manifest = module_dir / "module.json"
        if not module_dir.is_dir() or not (module_dir / "WORKFLOW.md").exists() or not manifest.exists():
            continue
        try:
            data = read_json(manifest)
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("kind") == "workflow":
            names.append(module_dir.name)
    return names

def declares_context_packet(root: Path, workflow_name: str) -> bool:
    manifest = workflow_manifest(root, workflow_name)
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), list) else []
    return any("context-packet.json" in str(item) for item in outputs)


def cleanup_smoke_run(root: Path, workflow_name: str, run_id: str) -> dict[str, Any]:
    return _cleanup_smoke_run(root, workflow_name, run_id, is_tracked=is_tracked_file)


def report_check(name: str, report: dict[str, Any], *, status: str = "passed") -> dict[str, Any]:
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    failed_hooks = [
        item
        for item in report.get("hook_results", [])
        if isinstance(item, dict) and item.get("required") is True and item.get("ok") is not True
    ]
    ok = report.get("ok") is not False and not issues and not failed_hooks
    row: dict[str, Any] = {
        "name": name,
        "kind": "workflow-lifecycle",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "tool": report.get("tool", ""),
    }
    if status == "skipped":
        row.update({"ok": True, "status": "skipped"})
    if issues:
        row["issues"] = issues
    if failed_hooks:
        row["failed_hooks"] = failed_hooks
    for key in ("run_path", "state_path", "context_packet_path", "checkpoint_path", "next_command"):
        if report.get(key):
            row[key] = report[key]
    return row


def workflow_lifecycle_smoke(root: Path, workflow_name: str) -> dict[str, Any]:
    run_id = smoke_run_id(workflow_name, "lifecycle")
    checks: list[dict[str, Any]] = []
    cleanup: dict[str, Any] | None = None
    index_snapshot = snapshot_run_index_state(root, workflow_name)
    if index_snapshot.get("ok") is not True:
        issue = str(index_snapshot.get("issue") or "run-index snapshot is unsafe")
        return {
            "workflow": workflow_name,
            "run_id": run_id,
            "ok": False,
            "checks": [
                {
                    "name": "run-index-snapshot",
                    "kind": "workflow-lifecycle",
                    "ok": False,
                    "status": "failed",
                    "issue": issue,
                }
            ],
            "cleanup": {"removed": False, "reason": issue},
        }
    try:
        start = start_workflow_run(root, workflow_name, run_id=run_id)
        checks.append(report_check("start-run", start))
        launcher = root / ".agents" / "manage.py"
        if launcher.exists():
            checks.append(
                named_command_check(
                    "resume-after-abort",
                    root,
                    [
                        sys.executable,
                        "-B",
                        str(launcher),
                        "workflow",
                        "resume",
                        "--name",
                        workflow_name,
                        "--run-id",
                        run_id,
                        "--format",
                        "json",
                    ],
                    timeout_seconds=60,
                )
            )
        else:
            checks.append(skipped_check("resume-after-abort", ".agents/manage.py is not present in this minimal fixture repo."))
        checkpoint = checkpoint_workflow_run(root, workflow_name, run_id=run_id, write=True, check=False)
        checks.append(report_check("checkpoint-write", checkpoint))
        handoff = handoff_workflow_run(root, workflow_name, run_id=run_id, write=True)
        checks.append(report_check("handoff-write", handoff))
        if declares_context_packet(root, workflow_name):
            context_fixture = (
                root
                / "automations"
                / workflow_name
                / "runs"
                / run_id
                / "validation"
                / "context-budget-smoke-evidence.md"
            )
            context_fixture.parent.mkdir(parents=True, exist_ok=True)
            context_fixture.write_text(
                "# Context Budget Smoke Evidence\n\n"
                + "Deterministic verbose validation evidence for effective-load accounting.\n" * 1_024,
                encoding="utf-8",
                newline="\n",
            )
            context = context_workflow_run(root, workflow_name, run_id=run_id, write=True, check=False)
            checks.append(report_check("context-write", context))
        fill_smoke_domain_outputs(root, workflow_name, run_id)
        run_path = root / "automations" / workflow_name / "runs" / run_id / "run.json"
        run_packet = read_json(run_path)
        run_packet["external_validation_status"] = "not-required"
        write_json(run_path, run_packet)
        finish = finish_workflow_run(root, workflow_name, run_id=run_id)
        checks.append(report_check("finish-run", finish))
    except SystemExit as exc:
        checks.append({"name": "lifecycle-exception", "kind": "workflow-lifecycle", "ok": False, "status": "failed", "issue": str(exc)})
    finally:
        try:
            cleanup = cleanup_smoke_run(root, workflow_name, run_id)
        except Exception as exc:
            cleanup = {
                "removed": False,
                "path": common.relative(
                    root,
                    root / "automations" / workflow_name / "runs" / run_id,
                ),
                "reason": "cleanup-exception",
                "issues": [f"{type(exc).__name__}: {exc}"],
            }
        try:
            restoration = restore_run_index_state(index_snapshot)
        except Exception as exc:
            restoration = {
                "ok": False,
                "status": "failed",
                "issues": [f"{type(exc).__name__}: {exc}"],
            }
        cleanup["index_restoration"] = restoration
    cleanup_ok = (
        isinstance(cleanup, dict)
        and cleanup.get("removed") is True
        and isinstance(cleanup.get("index_restoration"), dict)
        and cleanup["index_restoration"].get("ok") is True
    )
    return {
        "workflow": workflow_name,
        "run_id": run_id,
        "ok": all(row.get("ok") is True for row in checks) and cleanup_ok,
        "checks": checks,
        "cleanup": cleanup,
    }


def compact_workflow_row(row: dict[str, Any]) -> dict[str, Any]:
    checks = row.get("checks") if isinstance(row.get("checks"), list) else []
    failed = [item for item in checks if isinstance(item, dict) and item.get("ok") is False]
    skipped = [item for item in checks if isinstance(item, dict) and item.get("status") == "skipped"]
    planned = [item for item in checks if isinstance(item, dict) and item.get("status") == "planned"]
    names = [str(item.get("name", "")) for item in checks if isinstance(item, dict) and str(item.get("name", ""))]
    output: dict[str, Any] = {
        "workflow": row.get("workflow", ""),
        "ok": row.get("ok") is True,
        "status": row.get("status", "passed" if row.get("ok") is True else "failed"),
        "check_count": len(checks),
        "check_names": names,
        "failed_count": len(failed),
        "skipped_count": len(skipped),
        "planned_count": len(planned),
        "cleanup_ok": row.get("cleanup_ok", False),
    }
    if row.get("dry_run"):
        output["dry_run"] = True
    if failed:
        output["failed_checks"] = failed
    if skipped:
        output["skipped_checks"] = skipped
    if planned:
        output["planned_checks"] = [str(item.get("name", "")) for item in planned if str(item.get("name", ""))]
    return output


def smoke_check_names(workflows: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for row in workflows:
        for item in row.get("checks", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))
            if name and name not in names:
                names.append(name)
    return names


def smoke_check_counts(workflows: list[dict[str, Any]]) -> dict[str, int]:
    checks = [
        check
        for row in workflows
        for check in row.get("checks", [])
        if isinstance(check, dict)
    ]
    return {
        "checks": len(checks),
        "passed_checks": len(
            [check for check in checks if check.get("ok") is True and check.get("status") not in {"planned", "skipped"}]
        ),
        "failed_checks": len([check for check in checks if check.get("ok") is False]),
        "skipped_checks": len([check for check in checks if check.get("status") == "skipped"]),
        "planned_checks": len([check for check in checks if check.get("status") == "planned"]),
    }


def planned_smoke_checks(*, include_domain_checks: bool = True) -> list[dict[str, Any]]:
    names = [
        "start",
        "checkpoint",
        "handoff",
        "context",
        "finish",
        "cleanup",
    ]
    if include_domain_checks:
        names.append("domain-fixture")
    return [
        {
            "name": name,
            "kind": "workflow-smoke-plan",
            "ok": True,
            "status": "planned",
            "reason": "dry-run only; no workflow run files are written",
        }
        for name in names
    ]


def smoke_next_command(*, workflow_names: list[str] | None = None, dry_run: bool = False) -> str:
    parts = ["python", "-B", ".agents/manage.py", "workflow", "smoke"]
    if workflow_names:
        for workflow_name in workflow_names:
            parts.extend(["--name", workflow_name])
    else:
        parts.append("--all")
    if dry_run:
        parts.append("--dry-run")
    parts.extend(["--summary", "--compact", "--format", "json"])
    return " ".join(parts)


def smoke_workflows(
    root: Path,
    *,
    workflow_names: list[str] | None = None,
    include_domain_checks: bool = True,
    dry_run: bool = False,
    summary: bool = False,
    compact: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    root = root.expanduser().resolve()
    names = workflow_names or accepted_workflow_names(root)
    if not names:
        return attach_smoke_budgets({
            "schema_version": 1,
            "tool": "workflow-manager.smoke",
            "ok": False,
            "status": "failed",
            "summary": {
                "workflows": 0,
                "passed": 0,
                "failed": 1,
                "workflows_passed": 0,
                "workflows_failed": 1,
                "workflows_planned": 0,
                "checks": 0,
                "passed_checks": 0,
                "failed_checks": 0,
                "skipped_checks": 0,
                "planned_checks": 0,
            },
            "issues": ["no workflows selected"],
        }, started)
    missing = [name for name in names if name not in accepted_workflow_names(root)]
    if missing:
        return attach_smoke_budgets({
            "schema_version": 1,
            "tool": "workflow-manager.smoke",
            "ok": False,
            "status": "failed",
            "summary": {
                "workflows": len(names),
                "passed": 0,
                "failed": len(missing),
                "workflows_passed": 0,
                "workflows_failed": len(missing),
                "workflows_planned": 0,
                "checks": 0,
                "passed_checks": 0,
                "failed_checks": 0,
                "skipped_checks": 0,
                "planned_checks": 0,
            },
            "issues": [f"unknown workflow: {name}" for name in missing],
        }, started)
    if dry_run:
        workflows = [
            {
                "workflow": workflow_name,
                "ok": True,
                "status": "planned",
                "dry_run": True,
                "run_id": "",
                "cleanup": {"removed": False, "status": "planned", "reason": "dry-run only"},
                "cleanup_ok": True,
                "checks": planned_smoke_checks(include_domain_checks=include_domain_checks),
            }
            for workflow_name in names
        ]
        output_workflows = [compact_workflow_row(row) for row in workflows] if summary else workflows
        check_counts = smoke_check_counts(workflows)
        return attach_smoke_budgets({
            "schema_version": 1,
            "tool": "workflow-manager.smoke",
            "ok": True,
            "status": "planned",
            "offline": True,
            "dry_run": True,
            "generated_at": utc_now(),
            "summary": {
                "workflows": len(workflows),
                "planned": len(workflows),
                "passed": 0,
                "failed": 0,
                "workflows_passed": 0,
                "workflows_failed": 0,
                "workflows_planned": len(workflows),
                "checks": check_counts["checks"],
                "passed_checks": check_counts["passed_checks"],
                "failed_checks": check_counts["failed_checks"],
                "check_names": smoke_check_names(workflows),
                "skipped_checks": check_counts["skipped_checks"],
                "planned_checks": check_counts["planned_checks"],
                "domain_checks": include_domain_checks,
            },
            "workflows": output_workflows,
            "next_command": smoke_next_command(workflow_names=workflow_names, dry_run=True),
        }, started)
    workflows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="skills-workflow-smoke-") as raw_tmp:
        temp_root = Path(raw_tmp)
        for workflow_name in names:
            lifecycle = workflow_lifecycle_smoke(root, workflow_name)
            checks = list(lifecycle["checks"])
            if include_domain_checks:
                checks.extend(domain_smoke_checks(root, workflow_name, temp_root / workflow_name))
            cleanup = lifecycle.get("cleanup") if isinstance(lifecycle.get("cleanup"), dict) else {}
            cleanup_ok = cleanup.get("removed") is True
            workflow_ok = lifecycle.get("ok") is True and all(item.get("ok") is True for item in checks) and cleanup_ok
            workflows.append(
                {
                    "workflow": workflow_name,
                    "ok": workflow_ok,
                    "status": "passed" if workflow_ok else "failed",
                    "run_id": lifecycle.get("run_id", ""),
                    "cleanup": cleanup,
                    "cleanup_ok": cleanup_ok,
                    "checks": checks,
                }
            )
    failed_workflows = [row for row in workflows if row.get("ok") is not True]
    check_counts = smoke_check_counts(workflows)
    output_workflows: list[dict[str, Any]]
    if summary:
        output_workflows = [compact_workflow_row(row) for row in workflows]
        if compact:
            output_workflows = [row for row in output_workflows if row.get("failed_count") or row.get("skipped_count")]
    else:
        output_workflows = workflows
    return attach_smoke_budgets({
        "schema_version": 1,
        "tool": "workflow-manager.smoke",
        "ok": not failed_workflows,
        "status": "passed" if not failed_workflows else "failed",
        "offline": True,
        "generated_at": utc_now(),
        "summary": {
            "workflows": len(workflows),
            "passed": len(workflows) - len(failed_workflows),
            "failed": len(failed_workflows),
            "workflows_passed": len(workflows) - len(failed_workflows),
            "workflows_failed": len(failed_workflows),
            "workflows_planned": 0,
            "checks": check_counts["checks"],
            "passed_checks": check_counts["passed_checks"],
            "failed_checks": check_counts["failed_checks"],
            "check_names": smoke_check_names(workflows),
            "skipped_checks": check_counts["skipped_checks"],
            "planned_checks": check_counts["planned_checks"],
            "domain_checks": include_domain_checks,
        },
        "workflows": output_workflows,
        "next_command": smoke_next_command(),
    }, started)


def render_smoke_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = ["# Workflow Smoke", ""]
    lines.append(f"- Status: {report.get('status')}")
    lines.append(f"- Offline: {str(report.get('offline', False)).lower()}")
    workflows_passed = summary.get("workflows_passed", summary.get("passed", 0))
    lines.append(f"- Workflows: {workflows_passed}/{summary.get('workflows', 0)} passed")
    lines.append(f"- Checks: {summary.get('passed_checks', summary.get('checks', 0))}/{summary.get('checks', 0)} passed")
    lines.append(f"- Skipped external checks: {summary.get('skipped_checks', 0)}")
    workflows = report.get("workflows") if isinstance(report.get("workflows"), list) else []
    if workflows:
        lines.extend(["", "## Workflows", ""])
        for row in workflows:
            if not isinstance(row, dict):
                continue
            status = "pass" if row.get("ok") else "fail"
            check_count = row.get("check_count", len(row.get("checks", [])) if isinstance(row.get("checks"), list) else 0)
            lines.append(f"- `{row.get('workflow')}`: {status} ({check_count} checks)")
            failed = row.get("failed_checks") if isinstance(row.get("failed_checks"), list) else []
            for item in failed:
                if isinstance(item, dict):
                    lines.append(f"  - failed `{item.get('name')}`: {item.get('issue') or item.get('stderr_tail') or item.get('issues', '')}")
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"
