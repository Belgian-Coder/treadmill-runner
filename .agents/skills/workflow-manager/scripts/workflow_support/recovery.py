"""Workflow run recovery helpers."""

from __future__ import annotations

import json
import time
from pathlib import Path

import workflow_manager_common as common


def read_json_with_error(path: Path) -> tuple[dict[str, object], str]:
    data, error = common.read_json_file(path)
    return (data, "") if isinstance(data, dict) and not error else ({}, error or "not a JSON object")


def evidence_paths(root: Path, run_dir: Path) -> list[str]:
    candidates = [
        run_dir / "REPORT.md",
        run_dir / "execution-log.md",
        run_dir / "artifacts" / "context" / "context-packet.json",
        run_dir / "artifacts" / "context" / "context-packet.md",
        run_dir / "artifacts" / "checkpoint" / "checkpoint.json",
        run_dir / "artifacts" / "checkpoint" / "checkpoint.md",
    ]
    validation_dir = run_dir / "validation"
    if validation_dir.exists():
        candidates.extend(sorted(path for path in validation_dir.rglob("*") if path.is_file())[:20])
    return [common.relative(root, path) for path in candidates if path.exists()]


def required_context(root: Path, workflow_name: str, run_dir: Path) -> list[str]:
    context_json = run_dir / "artifacts" / "context" / "context-packet.json"
    values = []
    if context_json.exists():
        values.append(common.relative(root, context_json))
    execution_log = run_dir / "execution-log.md"
    if execution_log.exists():
        values.append(common.relative(root, execution_log))
    project_context = root / "docs" / "project" / "project-context.md"
    if project_context.exists():
        values.append(common.relative(root, project_context))
    values.extend(
        [
            f"automations/{workflow_name}/WORKFLOW.md",
            f"automations/{workflow_name}/module.json",
            common.relative(root, run_dir / "run.json"),
            common.relative(root, run_dir / "REPORT.md"),
        ]
    )
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def recovery_packet(root: Path, workflow_name: str, run_dir: Path, existing: dict[str, object]) -> dict[str, object]:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    context_packet, _context_error = read_json_with_error(run_dir / "artifacts" / "context" / "context-packet.json")
    context_scope = context_packet.get("scope") if isinstance(context_packet.get("scope"), dict) else {}
    current_phase = str(existing.get("current_phase") or context_packet.get("current_phase") or "recovery")
    status = str(existing.get("status") or context_scope.get("run_status") or "partial")
    next_action = str(
        existing.get("next_action")
        or context_packet.get("next_action")
        or f"Run workflow resume --name {workflow_name} --run-id {run_dir.name} and continue from recovered state."
    )
    evidence = evidence_paths(root, run_dir)
    return {
        "schema_version": 2,
        "tool": "workflow-manager.run",
        "workflow": workflow_name,
        "run_id": run_dir.name,
        "status": status,
        "created_at": str(existing.get("created_at") or now),
        "updated_at": now,
        "current_phase": current_phase,
        "phase": {
            "current": current_phase,
            "status": status,
            "started_at": str(existing.get("created_at") or now),
            "completed_at": "",
            "entry_checks": ["recovered run packet"],
            "exit_checks": [],
        },
        "next_action": next_action,
        "checks": {"skipped": [], "blocked": [], "failed": []},
        "skipped": [],
        "blocked": [],
        "failed": [],
        "commands": [],
        "decisions": [
            {
                "decision": "recover-run-packet",
                "reason": "run.json was missing or invalid; recovered from surviving workflow artifacts.",
                "evidence": evidence,
            }
        ],
        "evidence": [{"kind": "recovery", "paths": evidence}],
        "evidence_paths": evidence,
        "handoff": {
            "loaded_context": [],
            "required_next_context": required_context(root, workflow_name, run_dir),
            "skipped_context": [],
            "blockers": [],
            "last_completed_step": "recovered run packet",
            "last_command": "",
        },
        "reasoning_notes": ["Recovered by workflow recover; verify context and validation before implementation."],
        "unsupported_claims": [],
        "external_validation_status": str(existing.get("external_validation_status") or "not-recorded"),
    }


def recover_run_packet(root: Path, workflow_name: str, run_dir: Path, *, write: bool = False) -> dict[str, object]:
    run_path = run_dir / "run.json"
    existing, error = read_json_with_error(run_path)
    needs_recovery = bool(error) or existing.get("schema_version") != 2 or existing.get("workflow") != workflow_name
    packet = existing if not needs_recovery else recovery_packet(root, workflow_name, run_dir, existing)
    written: list[str] = []
    backup_path = ""
    if write and needs_recovery:
        if run_path.exists():
            backup = run_dir / f"run.invalid.{time.strftime('%Y%m%d%H%M%S', time.gmtime())}.txt"
            backup.write_text(run_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8", newline="\n")
            backup_path = common.relative(root, backup)
        run_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        written.append(common.relative(root, run_path))
    return {
        "schema_version": 1,
        "tool": "workflow-manager.recover-run",
        "ok": not needs_recovery or write,
        "workflow": workflow_name,
        "run_id": run_dir.name,
        "run_path": common.relative(root, run_dir),
        "needs_recovery": needs_recovery,
        "run_json_error": error,
        "recovered_packet": packet,
        "backup_path": backup_path,
        "written": written,
        "next_command": f"python -B .agents/manage.py workflow resume --name {workflow_name} --run-id {run_dir.name}",
    }


def render_recover_markdown(report: dict[str, object]) -> str:
    lines = ["# Workflow Recover", ""]
    lines.append(f"- Workflow: `{report.get('workflow')}`")
    lines.append(f"- Run: `{report.get('run_id')}`")
    lines.append(f"- Needs recovery: {report.get('needs_recovery')}")
    lines.append(f"- Status: {'passed' if report.get('ok') else 'needs-write'}")
    if report.get("run_json_error"):
        lines.append(f"- Run JSON issue: {report.get('run_json_error')}")
    if report.get("backup_path"):
        lines.append(f"- Backup: `{report.get('backup_path')}`")
    written = report.get("written") if isinstance(report.get("written"), list) else []
    if written:
        lines.append("- Written:")
        lines.extend(f"  - `{item}`" for item in written)
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"
