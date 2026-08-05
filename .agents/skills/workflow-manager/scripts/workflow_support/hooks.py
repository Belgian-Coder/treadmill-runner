#!/usr/bin/env python3
"""Workflow hook lifecycle helpers."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import workflow_manager_common as common

WORKFLOW_HOOK_EVENTS = {
    "workflow-pre",
    "workflow-post",
    "phase-pre",
    "phase-started",
    "phase-between",
    "phase-completed",
    "phase-blocked",
    "phase-post",
    "phase-handoff",
    "run-started",
    "run-finished",
}


def read_workflow_manifest(root: Path, workflow_name: str) -> dict[str, object]:
    path = root / "automations" / workflow_name / "module.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def workflow_hooks(root: Path, workflow_name: str, event: str) -> list[dict[str, object]]:
    manifest = read_workflow_manifest(root, workflow_name)
    hooks = manifest.get("hooks")
    if event not in WORKFLOW_HOOK_EVENTS or not isinstance(hooks, list):
        return []
    return [
        {**hook, "_hook_scope": "workflow"}
        for hook in hooks
        if isinstance(hook, dict) and str(hook.get("event", "")).strip() == event
    ]


def global_workflow_hooks(root: Path, event: str) -> list[dict[str, object]]:
    path = root / "automations" / "hooks.json"
    if event not in WORKFLOW_HOOK_EVENTS or not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    hooks = data.get("hooks") if isinstance(data, dict) else []
    if not isinstance(hooks, list):
        return []
    return [
        {**hook, "_hook_scope": "global"}
        for hook in hooks
        if isinstance(hook, dict) and str(hook.get("event", "")).strip() == event
    ]


def hooks_for_event(root: Path, workflow_name: str, event: str) -> list[dict[str, object]]:
    global_hooks = global_workflow_hooks(root, event)
    local_hooks = workflow_hooks(root, workflow_name, event)
    if event in {"workflow-post", "run-finished", "phase-post", "phase-completed", "phase-blocked"}:
        return [*local_hooks, *global_hooks]
    return [*global_hooks, *local_hooks]


def hook_command_is_safe(root: Path, workflow_name: str, command: str) -> bool:
    _ = root
    workflow_script_prefix = f"python -B automations/{workflow_name}/scripts/"
    workflow_script_placeholder_prefix = "python -B automations/{workflow}/scripts/"
    return (
        command.startswith("python -B .agents/manage.py ")
        or command.startswith("python -B .agents/skills/")
        or command.startswith(workflow_script_prefix)
        or command.startswith(workflow_script_placeholder_prefix)
    )


def current_phase_id(run_packet: dict[str, object]) -> str:
    phase = run_packet.get("phase") if isinstance(run_packet.get("phase"), dict) else {}
    return str(run_packet.get("current_phase") or phase.get("current") or "").strip() or "unknown"


def format_hook_command(
    command: str,
    *,
    workflow_name: str,
    run_dir: Path,
    event: str,
    hook_id: str,
    phase_id: str,
) -> str:
    replacements = {
        "{workflow}": workflow_name,
        "{run_id}": run_dir.name,
        "{run_dir}": run_dir.as_posix(),
        "{run_dir_abs}": str(run_dir.resolve()),
        "{event}": event,
        "{hook_id}": hook_id,
        "{phase}": phase_id,
        "{phase_id}": phase_id,
    }
    formatted = command
    for placeholder, value in replacements.items():
        formatted = formatted.replace(placeholder, value)
    return formatted


def split_hook_command(command: str) -> list[str]:
    parts = shlex.split(command, posix=os.name != "nt")
    if parts and parts[0].lower() == "python":
        parts[0] = sys.executable
    return parts


def hook_command_emits_json(command: str) -> bool:
    return "workflow hook-audit" in command and "--format json" in command


def hook_evidence_path(run_dir: Path, hook: dict[str, object], event: str, hook_id: str, phase_id: str) -> Path:
    configured = str(hook.get("evidence_path", "")).strip()
    if configured:
        candidate = run_dir / configured.format(
            event=event,
            hook_id=hook_id,
            run_id=run_dir.name,
            phase=phase_id,
            phase_id=phase_id,
        )
        try:
            candidate.resolve(strict=False).relative_to(run_dir.resolve())
            return candidate
        except ValueError:
            pass
    suffix = ".json" if hook_command_emits_json(str(hook.get("command", ""))) else ".txt"
    return run_dir / "validation" / "hooks" / f"{event}-{hook_id}{suffix}"


def hook_source_path(root: Path, workflow_name: str, hook: dict[str, object]) -> str:
    if str(hook.get("_hook_scope") or "workflow") == "global":
        return "automations/hooks.json"
    return f"automations/{workflow_name}/module.json"


def dry_run_hook_details(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    hook: dict[str, object],
    event: str,
    phase_id: str,
) -> dict[str, object]:
    hook_id = str(hook.get("id", "")).strip()
    raw_command = str(hook.get("command", "")).strip()
    command = format_hook_command(
        raw_command,
        workflow_name=workflow_name,
        run_dir=run_dir,
        event=event,
        hook_id=hook_id,
        phase_id=phase_id,
    )
    safe = bool(hook_id and raw_command and hook_command_is_safe(root, workflow_name, command))
    evidence_path = hook_evidence_path(run_dir, hook, event, hook_id or "unknown", phase_id)
    return {
        "id": hook_id,
        "scope": str(hook.get("_hook_scope") or "workflow"),
        "source": hook_source_path(root, workflow_name, hook),
        "event": event,
        "phase": phase_id,
        "required": bool(hook.get("required", True)),
        "timeout_seconds": int(hook.get("timeout_seconds", 60) or 60),
        "raw_command": raw_command,
        "command": command,
        "safe": safe,
        "would_execute": safe,
        "evidence_path": common.relative(root, evidence_path),
    }


def execute_workflow_hooks(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    run_packet: dict[str, object],
    event: str,
    *,
    phase_id: str | None = None,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    selected_phase = phase_id or current_phase_id(run_packet)
    for hook in hooks_for_event(root, workflow_name, event):
        hook_id = str(hook.get("id", "")).strip()
        hook_scope = str(hook.get("_hook_scope") or "workflow")
        required = bool(hook.get("required", True))
        timeout_seconds = int(hook.get("timeout_seconds", 60) or 60)
        raw_command = str(hook.get("command", "")).strip()
        command = format_hook_command(
            raw_command,
            workflow_name=workflow_name,
            run_dir=run_dir,
            event=event,
            hook_id=hook_id,
            phase_id=selected_phase,
        )
        evidence_path = hook_evidence_path(run_dir, hook, event, hook_id, selected_phase)
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        returncode = 1
        ok = False
        status = "failed"
        output = ""
        if not hook_id or not raw_command or not hook_command_is_safe(root, workflow_name, command):
            output = "Workflow hook was not executed because its module.json declaration is invalid.\n"
        else:
            try:
                completed = subprocess.run(
                    split_hook_command(command),
                    cwd=root,
                    env=common.child_env(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
                returncode = completed.returncode
                output = completed.stdout
                if completed.stderr:
                    output = output + ("\n" if output else "") + completed.stderr
                ok = completed.returncode == 0
                status = "ok" if ok else "failed"
            except subprocess.TimeoutExpired as exc:
                returncode = 124
                output = f"Workflow hook timed out after {timeout_seconds}s.\n"
                if exc.stdout:
                    output += str(exc.stdout)
                if exc.stderr:
                    output += ("\n" if output else "") + str(exc.stderr)
                status = "timeout"
        evidence_path.write_text(output, encoding="utf-8", newline="\n")
        results.append(
            {
                "id": hook_id,
                "scope": hook_scope,
                "event": event,
                "ok": ok,
                "status": status,
                "required": required,
                "phase": selected_phase,
                "command": command,
                "returncode": returncode,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "timeout_seconds": timeout_seconds,
                "evidence_path": common.relative(root, evidence_path),
            }
        )
    apply_hook_results(root, run_packet, results)
    return results


def apply_hook_results(root: Path, run_packet: dict[str, object], results: list[dict[str, object]]) -> None:
    if not results:
        return
    existing = run_packet.get("hook_results")
    if not isinstance(existing, list):
        existing = []
    keys = {
        (str(item.get("scope", "workflow")), str(item.get("event")), str(item.get("id")), str(item.get("phase", "")))
        for item in results
    }
    retained = [
        item
        for item in existing
        if not (
            isinstance(item, dict)
            and (
                str(item.get("scope", "workflow")),
                str(item.get("event")),
                str(item.get("id")),
                str(item.get("phase", "")),
            ) in keys
        )
    ]
    run_packet["hook_results"] = [*retained, *results]

    commands = run_packet.get("commands")
    if not isinstance(commands, list):
        commands = []
    commands = [
        item
        for item in commands
        if not (
            isinstance(item, dict)
            and (
                str(item.get("hook_scope", "workflow")),
                str(item.get("hook_event")),
                str(item.get("hook_id")),
                str(item.get("hook_phase", "")),
            ) in keys
        )
    ]
    for result in results:
        commands.append(
            {
                "command": result.get("command", ""),
                "status": result.get("status", ""),
                "ok": result.get("ok", False),
                "returncode": result.get("returncode", 1),
                "elapsed_seconds": result.get("elapsed_seconds", 0),
                "evidence_path": result.get("evidence_path", ""),
                "hook_scope": result.get("scope", "workflow"),
                "hook_id": result.get("id", ""),
                "hook_event": result.get("event", ""),
                "hook_phase": result.get("phase", ""),
            }
        )
    run_packet["commands"] = commands

    evidence = run_packet.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
    evidence = [
        item
        for item in evidence
        if not (
            isinstance(item, dict)
            and item.get("kind") == "workflow-hook"
            and (
                str(item.get("scope", "workflow")),
                str(item.get("event")),
                str(item.get("id")),
                str(item.get("phase", "")),
            ) in keys
        )
    ]
    for result in results:
        evidence.append(
            {
                "kind": "workflow-hook",
                "scope": result.get("scope", "workflow"),
                "id": result.get("id", ""),
                "event": result.get("event", ""),
                "phase": result.get("phase", ""),
                "status": result.get("status", ""),
                "path": result.get("evidence_path", ""),
                "summary": "workflow hook executed" if result.get("ok") else "workflow hook failed",
            }
        )
    run_packet["evidence"] = evidence

    checks = run_packet.get("checks")
    if not isinstance(checks, dict):
        checks = {"skipped": [], "blocked": [], "failed": []}
    failed = checks.get("failed")
    if not isinstance(failed, list):
        failed = []
    current_hook_failures = {hook_failure_label(result) for result in results}
    failed = [item for item in failed if not current_hook_failure_item(item, current_hook_failures)]
    for result in results:
        if result.get("required") is True and result.get("ok") is not True:
            failed.append(hook_failure_label(result))
    checks["failed"] = failed
    checks.setdefault("skipped", [])
    checks.setdefault("blocked", [])
    run_packet["checks"] = checks

    flat_failed = run_packet.get("failed")
    if not isinstance(flat_failed, list):
        flat_failed = []
    flat_failed = [item for item in flat_failed if not current_hook_failure_item(item, current_hook_failures)]
    flat_failed.extend(
        item
        for item in failed
        if isinstance(item, str) and item.startswith("required ") and " workflow hook " in item
    )
    run_packet["failed"] = flat_failed


def current_hook_failure_item(item: object, current_hook_failures: set[str]) -> bool:
    return isinstance(item, str) and item in current_hook_failures


def hook_failure_label(result: dict[str, object]) -> str:
    scope = str(result.get("scope") or "workflow")
    label = f"required {scope} workflow hook {result.get('event')}:{result.get('id')} failed"
    phase = str(result.get("phase") or "")
    if phase and str(result.get("event") or "").startswith("phase-"):
        return f"{label} for phase {phase}"
    return label


def hook_results_ok(results: list[dict[str, object]]) -> bool:
    return all(result.get("ok") is True or result.get("required") is not True for result in results)


def workflow_declares_context_packet(root: Path, workflow_name: str) -> bool:
    manifest = read_workflow_manifest(root, workflow_name)
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list):
        return False
    return any("artifacts/context/context-packet.json" in str(item).replace("\\", "/") for item in outputs)


def resolve_run_relative_path(base: Path, value: Path | str, message: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(base.resolve(strict=False))
    except ValueError as exc:
        raise SystemExit(message) from exc
    return resolved


def default_hook_audit_path(run_dir: Path, event: str, hook_id: str) -> Path:
    return run_dir / "validation" / "hooks" / f"{event}-{hook_id}.json"


def write_hook_audit_packet(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    *,
    event: str,
    hook_id: str,
    output_path: Path | str | None = None,
) -> dict[str, object]:
    if event not in WORKFLOW_HOOK_EVENTS:
        raise SystemExit(f"unknown workflow hook event: {event}")
    if not hook_id or hook_id != hook_id.lower() or not all(
        ch.isascii() and (ch.islower() or ch.isdigit() or ch == "-") for ch in hook_id
    ):
        raise SystemExit("hook id must use lowercase letters, digits, and hyphens")
    run_dir = resolve_run_relative_path(root, run_dir, "hook audit run folder must stay inside the repository")
    output = (
        resolve_run_relative_path(run_dir, output_path, "hook audit output must stay inside the run folder")
        if output_path is not None
        else default_hook_audit_path(run_dir, event, hook_id)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    module_dir = root / "automations" / workflow_name
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    checks = [
        {
            "name": "workflow_exists",
            "ok": module_dir.exists(),
            "path": common.relative(root, module_dir),
        },
        {
            "name": "run_folder_inside_repo",
            "ok": True,
            "path": common.relative(root, run_dir),
        },
        {
            "name": "event_supported",
            "ok": True,
            "event": event,
        },
    ]
    ok = all(check.get("ok") is True for check in checks)
    packet = {
        "schema_version": 1,
        "tool": "workflow-manager.hook-audit",
        "ok": ok,
        "status": "ok" if ok else "failed",
        "summary": f"Recorded deterministic workflow hook audit for {event}:{hook_id}.",
        "workflow": workflow_name,
        "run_id": run_dir.name,
        "run_path": common.relative(root, run_dir),
        "event": event,
        "hook_id": hook_id,
        "timestamp": now,
        "checks": checks,
        "skipped": [],
        "commands": [],
        "evidence_paths": [common.relative(root, output)],
    }
    output.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return packet


def render_hook_audit_packet(packet: dict[str, object]) -> str:
    lines = ["# Workflow Hook Audit", ""]
    lines.append(f"- Workflow: `{packet.get('workflow')}`")
    lines.append(f"- Run: `{packet.get('run_id')}`")
    lines.append(f"- Hook: `{packet.get('event')}:{packet.get('hook_id')}`")
    lines.append(f"- Status: {packet.get('status')}")
    evidence = packet.get("evidence_paths") if isinstance(packet.get("evidence_paths"), list) else []
    if evidence:
        lines.append(f"- Evidence: `{evidence[0]}`")
    return "\n".join(lines) + "\n"
