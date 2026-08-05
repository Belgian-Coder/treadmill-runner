#!/usr/bin/env python3
"""Compact generated workflow checkpoint helpers."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import workflow_manager_common as common
import workflow_plan_check
from workflow_context_packet import context_packet_paths
from workflow_support import run_common as common_run

CHECKPOINT_DIR = "artifacts/checkpoint"
CHECKPOINT_JSON = "checkpoint.json"
CHECKPOINT_MARKDOWN = "checkpoint.md"


def unique_list(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def checkpoint_paths(run_dir: Path) -> tuple[Path, Path]:
    checkpoint_dir = run_dir / CHECKPOINT_DIR
    return checkpoint_dir / CHECKPOINT_JSON, checkpoint_dir / CHECKPOINT_MARKDOWN


def compact_snapshot_value(value: object, *, limit_chars: int = 700) -> object:
    if isinstance(value, str):
        text = " ".join(value.split())
        return text if len(text) <= limit_chars else text[: max(0, limit_chars - 20)].rstrip() + " ... [truncated]"
    if isinstance(value, list):
        return [compact_snapshot_value(item, limit_chars=max(160, limit_chars // 2)) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key): compact_snapshot_value(item, limit_chars=max(160, limit_chars // 2))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key).lower() not in {"stdout", "stderr", "output", "content", "body", "text"}
        }
    return value


def checkpoint_file_row(root: Path, path: Path) -> dict[str, object]:
    row: dict[str, object] = {
        "path": common.relative(root, path),
        "exists": path.exists() and path.is_file(),
    }
    if not path.exists() or not path.is_file():
        return row
    data = path.read_bytes()
    text = data.decode("utf-8-sig", errors="replace")
    row.update(
        {
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "tokens_estimated": approx_tokens(text),
        }
    )
    return row


def checkpoint_public_file_row(row: dict[str, object]) -> dict[str, object]:
    output = {
        "path": row.get("path", ""),
        "exists": row.get("exists", False),
    }
    if row.get("sha256"):
        output["sha256"] = str(row.get("sha256", ""))[:16]
    if row.get("tokens_estimated"):
        output["tokens_estimated"] = row.get("tokens_estimated", 0)
    return output


def checkpoint_source_paths(root: Path, workflow_name: str, run_dir: Path) -> list[Path]:
    module_dir = root / "automations" / workflow_name
    paths = [
        root / "automations" / "routing.md",
        common.workflow_start_path(module_dir),
        module_dir / "module.json",
    ]
    instructions = module_dir / "instructions.md"
    if instructions.exists():
        paths.append(instructions)
    paths.extend([run_dir / "run.json", run_dir / "REPORT.md", run_dir / "plan.md"])
    return paths


def checkpoint_normalize_handle(root: Path, run_dir: Path, value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    if path.exists():
        return common.relative(root, path)
    if raw in {"run.json", "REPORT.md"} or raw.startswith(("validation/", "artifacts/")):
        return common.relative(root, run_dir / raw)
    return raw.replace("\\", "/")


def checkpoint_evidence_handles(root: Path, run_dir: Path, run_packet: dict[str, object]) -> list[str]:
    values: list[str] = []
    for key in ("evidence_paths", "files_changed", "changed_files"):
        items = run_packet.get(key)
        if isinstance(items, list):
            values.extend(checkpoint_normalize_handle(root, run_dir, item) for item in items)
    evidence = run_packet.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict):
                for key in ("path", "source", "evidence_path"):
                    if item.get(key):
                        values.append(checkpoint_normalize_handle(root, run_dir, item.get(key)))
    commands = run_packet.get("commands")
    if isinstance(commands, list):
        for item in commands:
            if isinstance(item, dict) and item.get("evidence_path"):
                values.append(checkpoint_normalize_handle(root, run_dir, item.get("evidence_path")))
    validation_dir = run_dir / "validation"
    if validation_dir.exists():
        values.extend(common.relative(root, path) for path in sorted(validation_dir.rglob("*")) if path.is_file())
    return unique_list([value for value in values if value])


def checkpoint_validation_summary(run_packet: dict[str, object]) -> dict[str, object]:
    checks = run_packet.get("checks") if isinstance(run_packet.get("checks"), dict) else {}
    skipped = run_packet.get("skipped", checks.get("skipped", []))
    blocked = run_packet.get("blocked", checks.get("blocked", []))
    failed = run_packet.get("failed", checks.get("failed", []))
    commands = run_packet.get("commands") if isinstance(run_packet.get("commands"), list) else []
    command_rows: list[dict[str, object]] = []
    included_indexes: set[int] = set()
    latest_start = max(0, len(commands) - 6)
    for index, item in enumerate(commands):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", ""))
        ok = item.get("ok", status in {"ok", "complete", "available", "deferred", "skipped"})
        if ok is not True or index >= latest_start:
            included_indexes.add(index)
    for index, item in enumerate(commands):
        if index not in included_indexes or not isinstance(item, dict):
            continue
        if len(command_rows) >= 10:
            break
        status = str(item.get("status", ""))
        ok = item.get("ok", status in {"ok", "complete", "available", "deferred", "skipped"})
        command = compact_snapshot_value(item.get("command", ""), limit_chars=160)
        evidence_path = compact_snapshot_value(item.get("evidence_path", ""), limit_chars=180)
        if isinstance(item, dict):
            command_rows.append(
                {
                    "command": command,
                    "status": status,
                    "ok": ok,
                    "returncode": item.get("returncode", ""),
                    "evidence_path": evidence_path,
                }
            )
    return {
        "external_validation_status": run_packet.get("external_validation_status", "not-recorded"),
        "command_count": len(commands),
        "commands": command_rows,
        "omitted_command_count": max(0, len(commands) - len(command_rows)),
        "skipped": compact_snapshot_value(skipped),
        "blocked": compact_snapshot_value(blocked),
        "failed": compact_snapshot_value(failed),
        "skipped_count": len(skipped) if isinstance(skipped, list) else 0,
        "blocked_count": len(blocked) if isinstance(blocked, list) else 0,
        "failed_count": len(failed) if isinstance(failed, list) else 0,
    }


def comparable_checkpoint(packet: dict[str, object]) -> dict[str, object]:
    comparable = dict(packet)
    for key in (
        "written",
        "check",
        "existing_checkpoint_path",
        "existing_markdown_path",
        "checkpoint_path",
        "checkpoint_markdown_path",
    ):
        comparable.pop(key, None)
    return comparable


def build_workflow_checkpoint(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    run_packet: dict[str, object],
) -> dict[str, object]:
    module_dir = root / "automations" / workflow_name
    manifest, _error = common.read_json_file(module_dir / "module.json")
    if not isinstance(manifest, dict):
        manifest = {}
    phase = run_packet.get("phase") if isinstance(run_packet.get("phase"), dict) else {}
    handoff = run_packet.get("handoff") if isinstance(run_packet.get("handoff"), dict) else {}
    source_files = [checkpoint_file_row(root, path) for path in checkpoint_source_paths(root, workflow_name, run_dir)]
    evidence_handles = checkpoint_evidence_handles(root, run_dir, run_packet)
    all_evidence_files = [
        checkpoint_file_row(root, root / handle)
        for handle in evidence_handles
        if handle and not Path(handle).is_absolute()
    ]
    evidence_files = all_evidence_files[:24]
    raw_tokens = sum(int(row.get("tokens_estimated", 0) or 0) for row in [*source_files, *all_evidence_files])
    validation = checkpoint_validation_summary(run_packet)
    context_json, _context_markdown = context_packet_paths(run_dir)
    checkpoint_json, checkpoint_markdown = checkpoint_paths(run_dir)
    required_next_context = unique_list(
        [
            common.relative(root, checkpoint_json),
            common.relative(root, context_json) if context_json.exists() else "",
            common.relative(root, run_dir / "run.json"),
            common.relative(root, run_dir / "REPORT.md"),
        ]
    )
    plan_path = run_dir / "plan.md"
    implementation_allowed = True
    if plan_path.exists():
        plan_sections = workflow_plan_check.parse_sections(common.read_text(plan_path, limit=120_000))
        implementation_allowed = workflow_plan_check.approval_state(
            workflow_plan_check.approval_status(plan_sections)
        ) == "approved"
    execution_queue = (
        common_run.generic_execution_queue(root, workflow_name, run_dir, run_packet, manifest)
        if implementation_allowed
        else []
    )
    next_unblocked = execution_queue[0] if execution_queue else {}
    plan_file = next((row for row in source_files if str(row.get("path", "")).endswith("/plan.md")), {})
    snapshot = {
        "workflow": {
            "name": workflow_name,
            "path": common.relative(root, module_dir),
            "start_file": common.relative(root, common.workflow_start_path(module_dir)),
            "version": manifest.get("version", ""),
        },
        "run": {
            "run_id": run_packet.get("run_id") or run_dir.name,
            "path": common.relative(root, run_dir),
            "status": run_packet.get("status", "unknown"),
            "current_phase": run_packet.get("current_phase", ""),
            "phase_status": phase.get("status", run_packet.get("status", "unknown")),
            "next_action": compact_snapshot_value(run_packet.get("next_action", ""), limit_chars=360),
        },
        "handoff": {
            "last_completed_step": compact_snapshot_value(handoff.get("last_completed_step", ""), limit_chars=260),
            "last_command": compact_snapshot_value(handoff.get("last_command", ""), limit_chars=260),
            "blockers": compact_snapshot_value(handoff.get("blockers", [])),
        },
        "validation": validation,
        "decisions": compact_snapshot_value(run_packet.get("decisions", []) if isinstance(run_packet.get("decisions"), list) else []),
        "plan": {
            "sha256": plan_file.get("sha256", ""),
            "next_unblocked_package": compact_snapshot_value(next_unblocked),
        },
    }
    fingerprint_payload = {
        "snapshot": snapshot,
        "source_files": [{key: row.get(key) for key in ("path", "exists", "sha256")} for row in source_files],
        "evidence_files": [{key: row.get(key) for key in ("path", "exists", "sha256")} for row in evidence_files],
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True).encode("utf-8")).hexdigest()
    packet: dict[str, object] = {
        "schema_version": 1,
        "tool": "workflow-manager.checkpoint",
        "ok": True,
        "status": "ok",
        "checkpoint_kind": "compact-generated",
        "workflow": workflow_name,
        "run_id": run_packet.get("run_id") or run_dir.name,
        "run_path": common.relative(root, run_dir),
        "snapshot": snapshot,
        "source_files": [checkpoint_public_file_row(row) for row in source_files],
        "evidence_files": [checkpoint_public_file_row(row) for row in evidence_files],
        "evidence_file_count": len(all_evidence_files),
        "omitted_evidence_file_count": max(0, len(all_evidence_files) - len(evidence_files)),
        "required_next_context": required_next_context,
        "fingerprint": fingerprint,
        "context_budget": {
            "method": "rough chars/4 estimate for context budgeting, not billing",
            "raw_tokens_estimated": raw_tokens,
            "checkpoint_tokens_estimated": 0,
            "estimated_tokens_saved": 0,
        },
        "issues": [],
        "next_command": f"python -B .agents/manage.py workflow resume --name {workflow_name} --run-id {run_dir.name}",
    }
    checkpoint_tokens = approx_tokens(json.dumps(packet, sort_keys=True))
    budget = packet["context_budget"]
    if isinstance(budget, dict):
        budget["checkpoint_tokens_estimated"] = checkpoint_tokens
        budget["estimated_tokens_saved"] = max(raw_tokens - checkpoint_tokens, 0)
    packet["checkpoint_path"] = common.relative(root, checkpoint_json)
    packet["checkpoint_markdown_path"] = common.relative(root, checkpoint_markdown)
    return packet


def render_checkpoint_markdown(packet: dict[str, object]) -> str:
    snapshot = packet.get("snapshot") if isinstance(packet.get("snapshot"), dict) else {}
    run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
    validation = snapshot.get("validation") if isinstance(snapshot.get("validation"), dict) else {}
    plan = snapshot.get("plan") if isinstance(snapshot.get("plan"), dict) else {}
    next_package = plan.get("next_unblocked_package") if isinstance(plan.get("next_unblocked_package"), dict) else {}
    lines = [
        f"# Workflow Checkpoint: {packet.get('workflow')} {packet.get('run_id')}",
        "",
        f"- Status: {packet.get('status')}",
        f"- Fingerprint: `{packet.get('fingerprint')}`",
        f"- Current phase: {run.get('current_phase') or 'unknown'}",
        f"- Phase status: {run.get('phase_status') or 'unknown'}",
        f"- Next action: {run.get('next_action') or 'not recorded'}",
        f"- Plan SHA-256: `{plan.get('sha256') or 'not-recorded'}`",
        f"- Next unblocked package: {next_package.get('id') or 'none'} - {next_package.get('step') or 'not recorded'}",
        f"- External validation: {validation.get('external_validation_status', 'not-recorded')}",
        f"- Commands: {validation.get('command_count', 0)}",
        f"- Failed checks: {validation.get('failed_count', 0)}",
        "",
        "## Source Files",
        "",
    ]
    for item in packet.get("source_files", []):
        if isinstance(item, dict):
            lines.append(
                f"- `{item.get('path')}` exists={item.get('exists')} sha256=`{str(item.get('sha256', ''))[:16]}`"
            )
    evidence = packet.get("evidence_files", []) if isinstance(packet.get("evidence_files"), list) else []
    if evidence:
        lines.extend(["", "## Evidence Files", ""])
        for item in evidence[:20]:
            if isinstance(item, dict):
                lines.append(
                    f"- `{item.get('path')}` exists={item.get('exists')} sha256=`{str(item.get('sha256', ''))[:16]}`"
                )
    issues = packet.get("issues", []) if isinstance(packet.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in issues)
    required = packet.get("required_next_context", []) if isinstance(packet.get("required_next_context"), list) else []
    lines.extend(["", "## Required Next Context", ""])
    lines.extend(f"- `{item}`" for item in required)
    lines.extend(["", f"Next command: `{packet.get('next_command')}`", ""])
    return "\n".join(lines)


def write_checkpoint_packet(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    run_packet: dict[str, object],
    *,
    write: bool = False,
) -> dict[str, object]:
    packet = build_workflow_checkpoint(root, workflow_name, run_dir, run_packet)
    if write:
        json_path, markdown_path = checkpoint_paths(run_dir)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        markdown_path.write_text(render_checkpoint_markdown(packet), encoding="utf-8", newline="\n")
        packet["written"] = [common.relative(root, json_path), common.relative(root, markdown_path)]
    else:
        packet["written"] = []
    return packet
