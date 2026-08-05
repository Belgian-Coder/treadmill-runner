"""Workflow quality scorecards for repeatable harness checks."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import eval_workflow
import validate_automations
import workflow_manager_common as common
import workflow_plan_check
from workflow_support.review import workflow_declares_context_packet
from workflow_support.smoke import workflow_lifecycle_smoke
from workflow_support.template_layers import template_gate_check


IMPLEMENTATION_PLAN_WORKFLOWS = {
    "bug-ticket-workflow",
    "dotnet-framework-migration",
    "dotnet-upgrade",
    "user-story-workflow",
}


def accepted_workflow_names(root: Path) -> list[str]:
    automations = root / "automations"
    if not automations.exists():
        return []
    names: list[str] = []
    for child in sorted(automations.iterdir(), key=lambda item: item.name.lower()):
        if child.is_dir() and (child / "WORKFLOW.md").exists() and (child / "module.json").exists():
            names.append(child.name)
    return names


def workflow_text(root: Path, workflow_name: str) -> str:
    workflow_dir = root / "automations" / workflow_name
    return common.read_text(workflow_dir / "WORKFLOW.md", limit=100_000)


def has_example_prompts(text: str) -> bool:
    return all(f"- {label}:" in text for label in ("Start", "Resume", "Handoff", "Finish"))


def mermaid_syntax_status(workflow_dir: Path) -> dict[str, Any]:
    diagrams_dir = workflow_dir / "diagrams"
    mmd_paths = sorted(diagrams_dir.glob("*.mmd")) if diagrams_dir.exists() else []
    if not mmd_paths:
        return {"ok": False, "status": "missing", "errors": [], "warnings": []}
    script_dir = Path(__file__).resolve().parents[4] / "skills" / "mermaid-diagrams-azure-devops" / "scripts"
    if not script_dir.exists():
        return {
            "ok": False,
            "status": "validator-missing",
            "errors": [f"Mermaid validator not found at {script_dir}"],
            "warnings": [],
        }
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    try:
        from mermaid_support import validation_impl
    except Exception as exc:  # pragma: no cover - defensive import evidence
        return {"ok": False, "status": "validator-error", "errors": [str(exc)], "warnings": []}
    report = validation_impl.validate_paths(mmd_paths, allow_markdown_blocks=True)
    return {
        "ok": report.get("valid") is True,
        "status": "passed" if report.get("valid") is True else "failed",
        "files": [str(path.relative_to(workflow_dir)) for path in mmd_paths],
        "errors": report.get("errors", []),
        "warnings": report.get("warnings", []),
    }


def diagram_status(text: str, workflow_dir: Path | None = None) -> dict[str, Any]:
    lower = text.lower()
    fenced_count = text.count("::: mermaid")
    linked_mmd_count = len(re.findall(r"\]\([^)]*\.mmd\)", text, flags=re.IGNORECASE))
    linked_svg_count = len(re.findall(r"\]\([^)]*\.svg\)", text, flags=re.IGNORECASE))
    folder_mmd_count = 0
    folder_mmd_names: list[str] = []
    if workflow_dir is not None:
        diagrams_dir = workflow_dir / "diagrams"
        if diagrams_dir.exists():
            folder_mmd_names = [path.name.lower() for path in diagrams_dir.glob("*.mmd")]
            folder_mmd_count = len(folder_mmd_names)
    mermaid_count = max(fenced_count + linked_mmd_count, folder_mmd_count)
    has_mermaid_reference = mermaid_count >= 1 or linked_svg_count >= 1
    has_process = has_mermaid_reference and ("process" in lower or any("process" in name for name in folder_mmd_names))
    has_connection = has_mermaid_reference and ("connection" in lower or any("connection" in name for name in folder_mmd_names))
    syntax = mermaid_syntax_status(workflow_dir) if workflow_dir is not None else {"ok": True, "status": "not-checked"}
    return {
        "ok": mermaid_count >= 2 and has_process and has_connection and syntax.get("ok") is True,
        "mermaid_count": mermaid_count,
        "fenced_count": fenced_count,
        "linked_mmd_count": linked_mmd_count,
        "linked_svg_count": linked_svg_count,
        "folder_mmd_count": folder_mmd_count,
        "folder_mmd_names": folder_mmd_names,
        "has_process": has_process,
        "has_connection": has_connection,
        "syntax": syntax,
    }


def eval_suite_status(root: Path, workflow_name: str) -> dict[str, Any]:
    suite = root / "automations" / workflow_name / "suites" / "workflow-evals.json"
    if not suite.exists():
        return {"ok": False, "status": "missing", "summary": {"passed": 0, "failed": 1, "total": 1}}
    try:
        report = eval_workflow.run_eval(
            eval_workflow.Args(root=root, workflow_name=workflow_name, suite=suite, output_format="json")
        )
    except SystemExit as exc:
        return {"ok": False, "status": "failed", "issue": str(exc)}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return {"ok": int(summary.get("failed", 0) or 0) == 0, "status": "ok", "summary": summary}


def plan_gate_status(root: Path, workflow_name: str) -> dict[str, Any]:
    metadata_report = template_gate_check(root, workflow_name)
    workflow_rows = metadata_report.get("workflows") if isinstance(metadata_report.get("workflows"), list) else []
    workflow_row = workflow_rows[0] if workflow_rows and isinstance(workflow_rows[0], dict) else {}
    metadata_required = workflow_row.get("status") != "skipped"
    if metadata_required and metadata_report.get("ok") is not True:
        return {
            "ok": False,
            "status": "failed",
            "issues": metadata_report.get("issues", []),
            "profiles": workflow_row.get("profiles", []),
        }
    if workflow_name not in IMPLEMENTATION_PLAN_WORKFLOWS:
        if metadata_required:
            return {
                "ok": True,
                "status": "passed",
                "issues": [],
                "profiles": workflow_row.get("profiles", []),
            }
        return {"ok": True, "status": "not-required"}
    template = root / "automations" / workflow_name / "templates" / "plan.md"
    if not template.exists():
        return {"ok": False, "status": "missing", "issues": ["templates/plan.md is required"]}
    report = workflow_plan_check.check_plan(root, workflow_name, template=True)
    return {
        "ok": report.get("ok") is True,
        "status": report.get("status", "unknown"),
        "issues": report.get("issues", []),
        "metadata_gate": workflow_row,
    }


def context_declaration_status(root: Path, workflow_name: str) -> dict[str, Any]:
    required = workflow_declares_context_packet(root / "automations" / workflow_name)
    return {"ok": True, "status": "declared" if required else "not-required", "required": required}


def score_item(name: str, points: int, ok: bool, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "points": points if ok else 0,
        "max_points": points,
        "ok": ok,
        "details": details or {},
    }


def workflow_scorecard(root: Path, workflow_name: str, *, run_lifecycle: bool = True) -> dict[str, Any]:
    text = workflow_text(root, workflow_name)
    workflow_dir = root / "automations" / workflow_name
    errors, warnings, _modules = validate_automations.validate_automations(root, workflow_name=workflow_name)
    validation_ok = not errors
    diagrams = diagram_status(text, workflow_dir)
    eval_status = eval_suite_status(root, workflow_name)
    plan_status = plan_gate_status(root, workflow_name)
    context_status = context_declaration_status(root, workflow_name)
    lifecycle_status: dict[str, Any] = {"ok": True, "status": "skipped"}
    if run_lifecycle:
        lifecycle = workflow_lifecycle_smoke(root, workflow_name)
        cleanup = lifecycle.get("cleanup", {}) if isinstance(lifecycle.get("cleanup"), dict) else {}
        lifecycle_status = {
            "ok": lifecycle.get("ok") is True and cleanup.get("removed") is True,
            "run_id": lifecycle.get("run_id", ""),
            "cleanup": cleanup,
            "failed_checks": [
                item for item in lifecycle.get("checks", []) if isinstance(item, dict) and item.get("ok") is not True
            ],
        }
    checks = [
        score_item("module-validation", 20, validation_ok, {"errors": errors, "warnings": warnings}),
        score_item("example-prompts", 10, has_example_prompts(text)),
        score_item("mermaid-diagrams", 15, bool(diagrams.get("ok")), diagrams),
        score_item("eval-suite", 15, bool(eval_status.get("ok")), eval_status),
        score_item("lifecycle-smoke", 20, bool(lifecycle_status.get("ok")), lifecycle_status),
        score_item("plan-gate", 10, bool(plan_status.get("ok")), plan_status),
        score_item("context-declaration", 10, bool(context_status.get("ok")), context_status),
    ]
    score = sum(int(item["points"]) for item in checks)
    max_score = sum(int(item["max_points"]) for item in checks)
    return {
        "workflow": workflow_name,
        "score": score,
        "max_score": max_score,
        "percent": round((score / max_score) * 100, 2) if max_score else 0,
        "ok": score >= 90 and all(item.get("ok") for item in checks if item["name"] in {"module-validation", "plan-gate"}),
        "checks": checks,
    }


def scorecard_next_command(workflow_names: list[str] | None = None, *, run_lifecycle: bool = True) -> str:
    parts = ["python", "-B", ".agents/manage.py", "workflow", "scorecard"]
    if workflow_names:
        for workflow_name in workflow_names:
            parts.extend(["--name", workflow_name])
    else:
        parts.append("--all")
    if not run_lifecycle:
        parts.append("--no-lifecycle")
    parts.extend(["--summary", "--compact", "--format", "json"])
    return " ".join(parts)


def scorecards(root: Path, workflow_names: list[str] | None = None, *, run_lifecycle: bool = True) -> dict[str, Any]:
    names = workflow_names or accepted_workflow_names(root)
    rows = [workflow_scorecard(root, name, run_lifecycle=run_lifecycle) for name in names]
    failing = [row for row in rows if row.get("ok") is not True]
    return {
        "schema_version": 1,
        "tool": "workflow-manager.scorecard",
        "ok": not failing,
        "status": "passed" if not failing else "failed",
        "summary": {
            "workflow_count": len(rows),
            "passing": len(rows) - len(failing),
            "failing": len(failing),
            "minimum_percent": min([float(row.get("percent", 0)) for row in rows] or [0]),
        },
        "workflows": rows,
        "next_command": scorecard_next_command(workflow_names=workflow_names, run_lifecycle=run_lifecycle),
    }


def compact_scorecards(report: dict[str, Any]) -> dict[str, Any]:
    workflows = report.get("workflows") if isinstance(report.get("workflows"), list) else []
    failing = [row for row in workflows if isinstance(row, dict) and row.get("ok") is not True]
    return {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "workflow-manager.scorecard"),
        "ok": report.get("ok", False),
        "status": report.get("status", ""),
        "summary": report.get("summary", {}),
        "workflows": failing,
        "next_command": report.get("next_command", ""),
    }


def render_scorecards(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = ["# Workflow Scorecard", ""]
    lines.append(f"- Status: {report.get('status')}")
    lines.append(f"- Workflows: {summary.get('passing', 0)}/{summary.get('workflow_count', 0)} passing")
    lines.append(f"- Minimum score: {summary.get('minimum_percent', 0)}")
    workflows = report.get("workflows") if isinstance(report.get("workflows"), list) else []
    if workflows:
        lines.extend(["", "## Workflows", ""])
        for row in workflows:
            if not isinstance(row, dict):
                continue
            lines.append(f"- `{row.get('workflow')}`: {row.get('percent')} ({row.get('score')}/{row.get('max_score')})")
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"
