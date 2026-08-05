#!/usr/bin/env python3
"""Workflow run scaffold, resume, finish, and handoff helpers."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True

import workflow_manager_common as common
import workflow_plan_check
import workflow_context_evidence
from workflow_support.hooks import (
    WORKFLOW_HOOK_EVENTS,
    current_phase_id,
    dry_run_hook_details,
    execute_workflow_hooks,
    hook_results_ok,
    hooks_for_event,
    render_hook_audit_packet,
    workflow_declares_context_packet,
    write_hook_audit_packet,
)
from workflow_context_packet import (
    build_context_packet,
    context_packet_quality_gate,
    context_packet_paths,
    render_context_packet_markdown,
    write_context_packet,
)
from workflow_checkpoint import (
    checkpoint_paths,
    comparable_checkpoint,
    render_checkpoint_markdown,
    write_checkpoint_packet,
)
from workflow_support.recovery import recover_run_packet, render_recover_markdown
from workflow_support.run_render import (
    render_context_audit,
    render_finish_run,
    render_handoff_markdown,
    render_hooks_run,
    render_resume_run,
    render_start_run,
)
from workflow_support.run_common import (
    evidence_completeness,
    finish_proof_report,
    format_proof_issue as format_generic_proof_issue,
    generic_execution_queue,
    lesson_candidates,
    plan_gate as generic_plan_gate,
    scaffold_generic_run_files,
)
from workflow_support.run_lifecycle import (
    canonical_workflow_run_id,
    comparable_context_packet,
    default_workflow_context,
    is_completion_evidence_entry,
    is_completion_evidence_path,
    latest_or_selected_run_dir,
    normalized_ledger,
    normalized_run_state,
    phase_has_blockers,
    read_json_object,
    refresh_run_index,
    resolve_repo_path,
    safe_run_id,
    ticket_intake_context,
    workflow_handoff_packet,
)
from workflow_support.run_story_bug import (
    PROGRESS_DOCUMENT_WORKFLOWS,
    PROGRESS_TEMPLATE_FILE,
    initial_progress_log,
    scaffold_story_bug_run_files,
    story_bug_execution_queue,
    story_bug_plan_gate,
)
from workflow_support.start_checklist import build_start_checklist, build_start_preflight_packet, unique_list
from workflow_support.template_layers import resolve_template, template_layers_config
from workflow_support.workers import RUNTIME_OBSERVATION_MAX_BYTES, runtime_observation_issues
from workflow_support.story_bug_quality import (
    closeout_evidence_issues,
    format_proof_issue,
    out_of_scope_template_issues,
    pr_handoff_issues,
    progress_log_issues,
    story_bug_finish_proof_report,
)

def load_runtime_observation_packet(
    root: Path,
    workflow_name: str,
    run_id: str | None,
    value: str,
) -> dict[str, object]:
    """Load a strict, durable host-surface and model-provider observation."""

    run_dir = latest_or_selected_run_dir(root, workflow_name, run_id)
    run_packet = normalized_run_state(
        root,
        workflow_name,
        run_dir,
        read_json_object(run_dir / "run.json"),
    )
    current_phase = str(run_packet.get("current_phase") or "").strip()
    path = resolve_repo_path(root, value)
    validation_dir = (run_dir / "validation").resolve(strict=False)
    try:
        path.relative_to(validation_dir)
    except ValueError as exc:
        raise SystemExit(
            "runtime observation file must stay under the selected run's validation directory"
        ) from exc
    if not path.exists() or not path.is_file():
        raise SystemExit(
            f"runtime observation file not found: {common.relative(root, path)}"
        )
    size = path.stat().st_size
    if size > RUNTIME_OBSERVATION_MAX_BYTES:
        raise SystemExit(
            f"runtime observation file exceeds {RUNTIME_OBSERVATION_MAX_BYTES} bytes"
        )
    packet = read_json_object(path)
    packet["evidence_path"] = common.relative(root, path)
    issues = runtime_observation_issues(
        packet,
        expected_workflow=workflow_name,
        expected_run_id=run_dir.name,
        expected_phase=current_phase,
    )
    if issues:
        raise SystemExit("invalid runtime observation packet: " + "; ".join(issues))
    normalized: dict[str, object] = {
        "schema_version": packet["schema_version"],
        "tool": packet["tool"],
        "workflow": str(packet["workflow"]).strip(),
        "run_id": str(packet["run_id"]).strip(),
        "phase": str(packet["phase"]).strip(),
        "evidence_path": str(packet["evidence_path"]),
    }
    host = packet.get("host")
    if isinstance(host, dict):
        normalized["host"] = {
            "attested": True,
            "source": str(host["source"]).strip(),
            "surface": str(host["surface"]).strip(),
            "capabilities": sorted({str(item).strip() for item in host.get("capabilities", [])}),
        }
    model = packet.get("model")
    if isinstance(model, dict):
        normalized["model"] = {
            "attested": True,
            "source": str(model["source"]).strip(),
            "provider": str(model["provider"]).strip(),
            "model": str(model["model"]).strip(),
            "observed_deliberation": str(model.get("observed_deliberation", "")).strip(),
        }
    return normalized


def compact_feedback_arg(value: object, *, limit: int = 1200) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def record_workflow_feedback(root: Path, report: dict[str, object]) -> None:
    if report.get("ok") is True:
        return
    manager = root / ".agents" / "manage.py"
    if not manager.exists():
        return
    workflow = compact_feedback_arg(report.get("workflow"), limit=160) or "unknown"
    run_id = compact_feedback_arg(report.get("run_id"), limit=120)
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    missing = report.get("missing_proof") if isinstance(report.get("missing_proof"), list) else []
    issue_text = "; ".join(compact_feedback_arg(item, limit=240) for item in [*issues[:8], *missing[:8]])
    args = [
        sys.executable,
        "-B",
        str(manager),
        "feedback",
        "record",
        "--target-kind",
        "workflow",
        "--target",
        workflow,
        "--summary",
        f"Workflow finish failed: {workflow}",
        "--bad",
        issue_text or "workflow finish reported unresolved issues",
        "--good",
        "workflow finish produced structured issue and missing-proof evidence",
        "--trigger-command",
        f"python -B .agents/manage.py workflow finish --name {workflow} --run-id {run_id}".strip(),
        "--failure-type",
        "missing-proof" if missing else "failed-check",
        "--first-failing-fact",
        compact_feedback_arg(issues[0] if issues else (missing[0] if missing else "")),
        "--suggested-next-command",
        compact_feedback_arg(report.get("next_command"), limit=400),
        "--source-tool",
        compact_feedback_arg(report.get("tool") or "workflow-manager.finish-run", limit=160),
        "--format",
        "json",
    ]
    for context in (
        report.get("state_path", ""),
        report.get("final_report_path", ""),
        report.get("context_packet_path", ""),
    ):
        text = compact_feedback_arg(context, limit=400)
        if text:
            args.extend(["--context", text])
    try:
        subprocess.run(
            args,
            cwd=root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return


FEEDBACK_ACTION_COLUMNS = (
    "Target",
    "Failure Type",
    "Count",
    "First Failing Fact",
    "Owner",
    "Follow-up Vehicle",
    "Evidence References",
    "Risk",
    "Baseline Command",
    "Expected Failing Fact Before Change",
    "Expected Behavior After Change",
    "Acceptance Commands",
    "Evidence To Capture",
    "Regression Guard",
    "Regression Owner",
    "Regression Rationale",
)


def feedback_action_value(record: dict[str, str], column: str) -> str:
    return str(record.get(workflow_plan_check.normalize_heading(column), "")).strip()


def feedback_value_missing(value: str) -> bool:
    normalized = value.strip().casefold()
    return not normalized or normalized in {"-", "tbd", "todo", "n/a"} or normalized.startswith("replace with")


def feedback_improvement_finish_proof_report(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    run_packet: dict[str, object],
) -> dict[str, object]:
    if workflow_name != "feedback-improvement-workflow":
        return {"ok": True, "status": "skipped", "proof_matrix": [], "missing_proof": [], "lesson_candidates": []}
    plan_path = run_dir / "action-plan.md"
    required_files = [
        (plan_path, "Action Plan", "action-plan.md"),
        (run_dir / "artifacts" / "feedback" / "feedback-candidates.json", "Feedback Candidates", "feedback-candidates.json"),
        (run_dir / "validation" / "clear-feedback.json", "Clear Evidence", "clear-feedback.json"),
    ]
    missing: list[dict[str, object]] = []
    for path, section, label in required_files:
        if not path.exists():
            missing.append(
                {
                    "section": section,
                    "field": "Output",
                    "path": common.relative(root, path),
                    "message": f"{label} is required",
                }
            )
    if not plan_path.exists():
        return {
            "ok": False,
            "status": "failed",
            "proof_matrix": [],
            "missing_proof": missing,
            "missing_count": len(missing),
            "proof_gap_summary": {"missing_count": len(missing), "by_section": {"Action Plan": len(missing)}},
            "lesson_candidates": [],
        }
    text = common.read_text(plan_path, limit=120_000)
    sections = workflow_plan_check.parse_sections(text)
    action_body = sections.get(workflow_plan_check.normalize_heading("Candidate Action Items"), "")
    not_actionable = sections.get(workflow_plan_check.normalize_heading("Not Actionable Now"), "").strip()
    records = workflow_plan_check.markdown_table_records(action_body)
    matrix: list[dict[str, object]] = []
    target_path = common.relative(root, plan_path)

    if not records and not not_actionable:
        missing.append(
            {
                "section": "Not Actionable Now",
                "field": "Reason",
                "path": target_path,
                "message": "action plan must contain candidate rows or a not-actionable reason",
            }
        )
    for row_index, record in records:
        for column in FEEDBACK_ACTION_COLUMNS:
            value = feedback_action_value(record, column)
            present = not feedback_value_missing(value)
            matrix.append(
                {
                    "section": "Candidate Action Items",
                    "row": row_index,
                    "field": column,
                    "status": "present" if present else "missing",
                    "path": target_path,
                }
            )
            if not present:
                missing.append(
                    {
                        "section": "Candidate Action Items",
                        "row": row_index,
                        "field": column,
                        "path": target_path,
                        "message": "action item field is required",
                    }
                )
    return {
        "ok": not missing,
        "status": "ok" if not missing else "failed",
        "proof_matrix": matrix,
        "missing_proof": missing,
        "missing_count": len(missing),
        "proof_gap_summary": {
            "missing_count": len(missing),
            "by_section": {"Candidate Action Items": len(missing)} if missing else {},
        },
        "lesson_candidates": [],
    }


def hooks_workflow_run(
    root: Path,
    workflow_name: str,
    *,
    run_id: str | None = None,
    event: str | None = None,
) -> dict[str, object]:
    module_dir = root / "automations" / workflow_name
    if not module_dir.exists():
        raise SystemExit(f"workflow not found: automations/{workflow_name}")
    if event and event not in WORKFLOW_HOOK_EVENTS:
        raise SystemExit(f"unknown workflow hook event: {event}")
    selected_run_id = safe_run_id(run_id) if run_id else "dry-run"
    run_dir = module_dir / "runs" / selected_run_id
    run_exists = run_dir.exists()
    run_packet = (
        normalized_run_state(root, workflow_name, run_dir, read_json_object(run_dir / "run.json"))
        if run_exists
        else {"current_phase": "unknown", "phase": {"current": "unknown"}}
    )
    selected_phase = current_phase_id(run_packet)
    selected_events = [event] if event else sorted(WORKFLOW_HOOK_EVENTS)
    hooks: list[dict[str, object]] = []
    for selected_event in selected_events:
        for hook in hooks_for_event(root, workflow_name, selected_event):
            hooks.append(dry_run_hook_details(root, workflow_name, run_dir, hook, selected_event, selected_phase))
    unsafe = [hook for hook in hooks if hook.get("safe") is not True]
    return {
        "schema_version": 1,
        "tool": "workflow-manager.hooks-run",
        "ok": not unsafe,
        "workflow": workflow_name,
        "run_id": selected_run_id,
        "run_path": common.relative(root, run_dir),
        "run_exists": run_exists,
        "events": selected_events,
        "hook_count": len(hooks),
        "required_count": sum(1 for hook in hooks if hook.get("required") is True),
        "unsafe_count": len(unsafe),
        "hooks": hooks,
        "next_command": f"python -B .agents/manage.py workflow start --name {workflow_name}",
    }


def start_workflow_run(
    root: Path,
    workflow_name: str,
    *,
    run_id: str | None = None,
    from_ticket: str | None = None,
    from_request: str | None = None,
    profile: str = "default",
) -> dict[str, object]:
    module_dir = root / "automations" / workflow_name
    if not module_dir.exists():
        raise SystemExit(f"workflow not found: automations/{workflow_name}")
    start = common.workflow_start_path(module_dir)
    if not start.exists():
        raise SystemExit("workflow start file not found: WORKFLOW.md")
    selected_run_id = safe_run_id(run_id)
    run_dir = module_dir / "runs" / selected_run_id
    layers = template_layers_config(root, workflow_name)
    profiles = layers.get("profiles") if isinstance(layers.get("profiles"), dict) else {}
    if profiles or profile != "default":
        template_preflight = resolve_template(root, workflow_name, profile=profile)
        if template_preflight.get("ok") is not True:
            issues = template_preflight.get("issues") if isinstance(template_preflight.get("issues"), list) else []
            detail = "; ".join(str(issue) for issue in issues) or str(
                template_preflight.get("status", "template resolution failed")
            )
            raise SystemExit(f"template profile preflight failed: {detail}")
    if run_dir.exists():
        raise SystemExit(f"workflow run already exists: {common.relative(root, run_dir)}")
    run_dir.mkdir(parents=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ticket_context = ticket_intake_context(root, from_ticket)
    request_text = str(from_request or "").strip()
    loaded_context = default_workflow_context(root, workflow_name)
    if ticket_context:
        loaded_context.extend(str(item) for item in ticket_context.get("evidence_files", []))
    if request_text:
        loaded_context.append("workflow start --from-request")
    required_next_context = default_workflow_context(root, workflow_name, run_dir)
    phase_entry_checks = ["WORKFLOW.md exists", "module.json exists"]
    phase_evidence = ["run.json", "REPORT.md"]
    manifest = read_json_object(module_dir / "module.json")
    progress_log_required = workflow_name in PROGRESS_DOCUMENT_WORKFLOWS and (module_dir / PROGRESS_TEMPLATE_FILE).exists()
    if progress_log_required:
        phase_evidence.append("execution-log.md")
    scaffolded_files = scaffold_story_bug_run_files(
        root,
        workflow_name,
        module_dir,
        run_dir,
        ticket_context,
        profile=profile,
    )
    start_scaffold = {
        "created": [],
        "plan_path": "",
        "execution_log_path": "",
    }
    if workflow_name not in PROGRESS_DOCUMENT_WORKFLOWS:
        scaffolded_files.extend(
            scaffold_generic_run_files(
                root,
                workflow_name,
                module_dir,
                run_dir,
                now=now,
                profile=profile,
            )
        )
    if scaffolded_files:
        phase_evidence = unique_list([*phase_evidence, *scaffolded_files])
        required_next_context = unique_list([*required_next_context, *scaffolded_files])
        start_scaffold["created"] = scaffolded_files
        if (run_dir / "plan.md").exists():
            start_scaffold["plan_path"] = common.relative(root, run_dir / "plan.md")
        if (run_dir / "execution-log.md").exists():
            start_scaffold["execution_log_path"] = common.relative(root, run_dir / "execution-log.md")
    next_action = f"Read automations/{workflow_name}/WORKFLOW.md and module.json."
    if workflow_name in PROGRESS_DOCUMENT_WORKFLOWS:
        next_action = (
            "Fill ticket-info.md and plan.md, run workflow plan-check, "
            "then stop for approval before implementation."
        )
    elif (run_dir / "plan.md").exists():
        next_action = "Fill plan.md, run workflow plan-check, then resume from the approved or next executable phase."
    elif (run_dir / "execution-log.md").exists():
        next_action = "Update execution-log.md with the current objective, then resume the next workflow phase."
    start_checklist = build_start_checklist(
        root,
        workflow_name,
        run_dir,
        manifest=manifest,
        progress_document="execution-log.md" if (progress_log_required or (run_dir / "execution-log.md").exists()) else "",
    )
    context_packet_path = ""
    if workflow_declares_context_packet(root, workflow_name):
        context_json_path, _context_markdown_path = context_packet_paths(run_dir)
        context_packet_path = common.relative(root, context_json_path)
    checkpoint_json_path, checkpoint_markdown_path = checkpoint_paths(run_dir)
    planned_checkpoint_paths = [
        common.relative(root, checkpoint_json_path),
        common.relative(root, checkpoint_markdown_path),
    ]
    next_command = f"python -B .agents/manage.py workflow resume --name {workflow_name} --run-id {selected_run_id}"
    start_preflight = build_start_preflight_packet(
        root,
        workflow_name,
        run_dir,
        from_request=request_text,
        start_checklist=start_checklist,
        context_packet_path=context_packet_path,
        checkpoint_paths=planned_checkpoint_paths,
        next_command=next_command,
    )
    run_packet = {
        "schema_version": 2,
        "tool": "workflow-manager.run",
        "workflow": workflow_name,
        "run_id": selected_run_id,
        "template_profile": profile,
        "status": "partial",
        "created_at": now,
        "updated_at": now,
        "current_phase": "orientation",
        "phase": {
            "current": "orientation",
            "status": "not-started",
            "started_at": now,
            "completed_at": "",
            "entry_checks": phase_entry_checks,
            "exit_checks": [],
        },
        "next_action": next_action,
        "start_checklist": start_checklist,
        "checks": {"skipped": [], "blocked": [], "failed": []},
        "skipped": [],
        "blocked": [],
        "failed": [],
        "commands": [],
        "request": {
            "source": "workflow start --from-request" if request_text else "",
            "text": request_text,
        },
        "decisions": [
            {
                "id": "initial-user-request",
                "decision": "start workflow from natural-language request",
                "rationale": request_text,
            }
        ] if request_text else [],
        "evidence": [
            {
                "kind": "ticket-intake",
                "path": ticket_context.get("path", ""),
                "attachment_count": ticket_context.get("attachment_count", 0),
            }
        ] if ticket_context else [],
        "evidence_paths": phase_evidence,
        "handoff": {
            "loaded_context": loaded_context,
            "required_next_context": required_next_context,
            "skipped_context": [],
            "blockers": [],
            "last_completed_step": "",
            "last_command": "",
        },
        "reasoning_notes": [
            "Run scaffold created; no workflow decision has been made yet."
        ],
        "workflow_preflight": start_preflight,
        "unsupported_claims": [],
        "external_validation_status": "not-recorded",
    }
    report = (
        f"# {workflow_name} Run {selected_run_id}\n\n"
        f"- Created: {now}\n"
        "- Current phase: orientation\n"
        f"- Next action: {next_action}\n"
        "\n## Phase Handoff\n\n"
        "Completed: run scaffold created.\n"
        "Skipped: none.\n"
        "Blocked: none.\n"
        "Failed: none.\n"
        "Validation: not run yet.\n"
        "Evidence: run.json, REPORT.md.\n"
        "Decisions: none yet.\n"
        "Things that did not go well: none recorded yet.\n"
        f"Next action: {next_action}\n"
    )
    (run_dir / "run.json").write_text(json.dumps(run_packet, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (run_dir / "REPORT.md").write_text(report, encoding="utf-8", newline="\n")
    created_files = [
        common.relative(root, run_dir / "run.json"),
        common.relative(root, run_dir / "REPORT.md"),
        *scaffolded_files,
    ]
    if progress_log_required:
        (run_dir / "execution-log.md").write_text(
            initial_progress_log(workflow_name, selected_run_id, now),
            encoding="utf-8",
            newline="\n",
        )
        created_files.append(common.relative(root, run_dir / "execution-log.md"))
    if ticket_context:
        artifacts_dir = run_dir / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)
        (artifacts_dir / "ticket-intake-context.json").write_text(
            json.dumps(ticket_context, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    context_evidence_packet: dict[str, object] = {}
    if workflow_context_evidence.workflow_requires_context_evidence(root, workflow_name):
        context_evidence_packet = workflow_context_evidence.write_context_evidence_packet(
            root,
            workflow_name,
            run_dir,
            run_packet,
            event="start",
            write=True,
            write_run=False,
        )
        created_files.extend(str(item) for item in context_evidence_packet.get("written", []) if isinstance(item, str))
    hook_results = [
        *execute_workflow_hooks(
            root,
            workflow_name,
            run_dir,
            run_packet,
            "workflow-pre",
        ),
        *execute_workflow_hooks(root, workflow_name, run_dir, run_packet, "run-started"),
        *execute_workflow_hooks(root, workflow_name, run_dir, run_packet, "phase-pre"),
        *execute_workflow_hooks(root, workflow_name, run_dir, run_packet, "phase-started"),
    ]
    if hook_results or context_evidence_packet:
        (run_dir / "run.json").write_text(
            json.dumps(run_packet, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        hook_lines = ["", "## Workflow Hooks", ""]
        for result in hook_results:
            hook_lines.append(
                f"- `{result.get('event')}:{result.get('id')}`: "
                f"{result.get('status')} - `{result.get('evidence_path')}`"
            )
        with (run_dir / "REPORT.md").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(hook_lines) + "\n")
    hooks_ok = hook_results_ok(hook_results)
    context_evidence_ok = (
        context_evidence_packet.get("ok", True) is True if context_evidence_packet else True
    )
    context_packet: dict[str, object] = {}
    context_packet_refreshed = False
    if workflow_declares_context_packet(root, workflow_name):
        context_packet = context_workflow_run(root, workflow_name, run_id=selected_run_id, write=True)
        context_written = [str(item) for item in context_packet.get("written", []) if isinstance(item, str)]
        context_packet_refreshed = bool(context_written)
        context_packet_path = next((path for path in context_written if path.endswith(".json")), "")
        created_files = unique_list([*created_files, *context_written])
        run_packet = normalized_run_state(root, workflow_name, run_dir, read_json_object(run_dir / "run.json"))
    checkpoint_packet = write_checkpoint_packet(root, workflow_name, run_dir, run_packet, write=True)
    checkpoint_written = [str(item) for item in checkpoint_packet.get("written", []) if isinstance(item, str)]
    created_files.extend(checkpoint_written)
    return {
        "schema_version": 2,
        "tool": "workflow-manager.start-run",
        "ok": hooks_ok and context_evidence_ok,
        "workflow": workflow_name,
        "run_id": selected_run_id,
        "run_path": common.relative(root, run_dir),
        "created_files": created_files
        + ([common.relative(root, run_dir / "artifacts" / "ticket-intake-context.json")] if ticket_context else []),
        "hook_results": hook_results,
        "context_evidence": context_evidence_packet,
        "context_packet": context_packet,
        "context_packet_refreshed": context_packet_refreshed,
        "context_packet_path": context_packet_path,
        "checkpoint": checkpoint_packet,
        "checkpoint_written": checkpoint_written,
        "ticket_intake": ticket_context or {},
        "from_request": request_text,
        "start_scaffold": start_scaffold,
        "template_profile": profile,
        "start_checklist": start_checklist,
        "workflow_preflight": start_preflight,
        "operator_next_action": next_action,
        "next_action": next_action,
        "next_command": next_command,
    }


def checkpoint_workflow_run(
    root: Path,
    workflow_name: str,
    *,
    run_id: str | None = None,
    write: bool = False,
    check: bool = False,
) -> dict[str, object]:
    if write and check:
        raise SystemExit("workflow checkpoint accepts either --write or --check, not both")
    run_dir = latest_or_selected_run_dir(root, workflow_name, run_id)
    run_packet = normalized_run_state(root, workflow_name, run_dir, read_json_object(run_dir / "run.json"))
    packet = write_checkpoint_packet(root, workflow_name, run_dir, run_packet, write=write)
    if check:
        checkpoint_json, checkpoint_markdown = checkpoint_paths(run_dir)
        existing = read_json_object(checkpoint_json) if checkpoint_json.exists() else {}
        fresh = bool(existing) and comparable_checkpoint(existing) == comparable_checkpoint(packet)
        issues = packet.get("issues") if isinstance(packet.get("issues"), list) else []
        check_issues = [*issues]
        if not checkpoint_json.exists():
            check_issues.append("checkpoint is missing")
        elif not fresh:
            check_issues.append("checkpoint is stale")
        missing = not checkpoint_json.exists()
        stale = checkpoint_json.exists() and not fresh
        packet["ok"] = packet.get("ok") is True and fresh
        if packet["ok"]:
            packet["status"] = "ok"
        elif missing:
            packet["status"] = "missing"
        elif stale:
            packet["status"] = "stale"
        else:
            packet["status"] = packet.get("status", "needs-attention")
        packet["issues"] = check_issues
        packet["check"] = {
            "existing": checkpoint_json.exists(),
            "fresh": fresh,
            "markdown_exists": checkpoint_markdown.exists(),
        }
        packet["existing_checkpoint_path"] = common.relative(root, checkpoint_json)
        packet["existing_markdown_path"] = common.relative(root, checkpoint_markdown)
    return packet


def context_workflow_run(
    root: Path,
    workflow_name: str,
    *,
    run_id: str | None = None,
    write: bool = False,
    check: bool = False,
    runtime_observation: dict[str, object] | None = None,
) -> dict[str, object]:
    if write and check:
        raise SystemExit("workflow context accepts either --write or --check, not both")
    if runtime_observation is not None and not write:
        raise SystemExit("runtime observation ingestion requires workflow context --write")
    run_dir = latest_or_selected_run_dir(root, workflow_name, run_id)
    run_packet = normalized_run_state(root, workflow_name, run_dir, read_json_object(run_dir / "run.json"))
    if runtime_observation is not None:
        observation_issues = runtime_observation_issues(
            runtime_observation,
            expected_workflow=workflow_name,
            expected_run_id=run_dir.name,
            expected_phase=str(run_packet.get("current_phase") or "").strip(),
        )
        if observation_issues:
            raise SystemExit(
                "invalid runtime observation packet: " + "; ".join(observation_issues)
            )
        run_packet["runtime_observation"] = dict(runtime_observation)
    elif write:
        clear_phase_mismatched_runtime_observation(run_packet)
    packet = write_context_packet(root, workflow_name, run_dir, run_packet, write=write)
    if check:
        context_json, context_markdown = context_packet_paths(run_dir)
        existing = read_json_object(context_json) if context_json.exists() else {}
        fresh = bool(existing) and comparable_context_packet(existing) == comparable_context_packet(packet)
        issues = packet.get("issues") if isinstance(packet.get("issues"), list) else []
        check_issues = [*issues]
        if not context_json.exists():
            check_issues.append("context packet is missing")
        elif not fresh:
            check_issues.append("context packet is stale")
        missing = not context_json.exists()
        stale = context_json.exists() and not fresh
        packet["ok"] = packet.get("ok") is True and fresh
        if packet["ok"]:
            packet["status"] = "ok"
        elif missing:
            packet["status"] = "missing"
        elif stale:
            packet["status"] = "stale"
        else:
            packet["status"] = packet.get("status", "needs-attention")
        packet["issues"] = check_issues
        packet["check"] = {
            "existing": context_json.exists(),
            "fresh": fresh,
            "markdown_exists": context_markdown.exists(),
        }
        packet["existing_packet_path"] = common.relative(root, context_json)
        packet["existing_markdown_path"] = common.relative(root, context_markdown)
        quality_gate = context_packet_quality_gate(
            root,
            run_dir,
            packet,
            existing=context_json.exists(),
            fresh=fresh,
            markdown_exists=context_markdown.exists(),
        )
        packet["quality_gate"] = quality_gate
        if quality_gate.get("ok") is not True:
            packet["ok"] = False
            if packet["status"] == "ok":
                packet["status"] = "quality-failed"
            failed_checks = quality_gate.get("failed_checks") if isinstance(quality_gate.get("failed_checks"), list) else []
            failed_names = [
                str(item.get("name"))
                for item in failed_checks
                if isinstance(item, dict) and item.get("name")
            ]
            if packet["status"] not in {"missing", "stale"} and failed_names:
                packet["issues"] = [
                    *check_issues,
                    "context packet quality gate failed: " + ", ".join(failed_names),
                ]
    if write:
        handoff = run_packet.get("handoff") if isinstance(run_packet.get("handoff"), dict) else {}
        handoff["required_next_context"] = packet.get("required_next_context", [])
        run_packet["handoff"] = handoff
        (run_dir / "run.json").write_text(
            json.dumps(run_packet, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        packet = write_context_packet(root, workflow_name, run_dir, run_packet, write=True)
        written = packet.get("written") if isinstance(packet.get("written"), list) else []
        packet["written"] = unique_list([*written, common.relative(root, run_dir / "run.json")])
        context_json, context_markdown = context_packet_paths(run_dir)
        packet["quality_gate"] = context_packet_quality_gate(
            root,
            run_dir,
            packet,
            existing=context_json.exists(),
            fresh=True,
            markdown_exists=context_markdown.exists(),
        )
        if packet["quality_gate"].get("ok") is not True:
            packet["ok"] = False
            if packet["status"] == "ok":
                packet["status"] = "quality-failed"
            failed_checks = packet["quality_gate"].get("failed_checks")
            failed_names = (
                [
                    str(item.get("name"))
                    for item in failed_checks
                    if isinstance(item, dict) and item.get("name")
                ]
                if isinstance(failed_checks, list)
                else []
            )
            if failed_names:
                issues = packet.get("issues") if isinstance(packet.get("issues"), list) else []
                packet["issues"] = [
                    *issues,
                    "context packet quality gate failed: " + ", ".join(failed_names),
                ]
    return packet


def clear_phase_mismatched_runtime_observation(run_packet: dict[str, object]) -> bool:
    """Drop stale active model evidence before any lifecycle context write."""

    persisted_observation = run_packet.get("runtime_observation")
    if not isinstance(persisted_observation, dict):
        return False
    observed_phase = str(persisted_observation.get("phase") or "").strip()
    current_phase = str(run_packet.get("current_phase") or "").strip()
    if not observed_phase or not current_phase or observed_phase == current_phase:
        return False
    run_packet.pop("runtime_observation", None)
    return True


def compact_context_budget_summary(packet: dict[str, object]) -> dict[str, object]:
    estimates = packet.get("token_estimates") if isinstance(packet.get("token_estimates"), dict) else {}
    budget = packet.get("context_budget") if isinstance(packet.get("context_budget"), dict) else {}
    raw_tokens = int(estimates.get("raw_context_tokens_estimated", 0) or 0)
    effective_tokens = int(estimates.get("effective_load_tokens_estimated", 0) or 0)
    return {
        "status": budget.get("status", ""),
        "compact_packet_tokens_estimated": estimates.get("compact_packet_tokens_estimated", 0),
        "effective_load_tokens_estimated": effective_tokens,
        "raw_context_tokens_estimated": raw_tokens,
        "raw_reference_inventory_tokens_estimated": raw_tokens,
        "raw_reference_inventory_is_loaded": False,
        "effective_load_reduction_percent": round((1 - (effective_tokens / raw_tokens)) * 100, 1) if raw_tokens else 0.0,
        "estimated_tokens_saved": estimates.get("estimated_tokens_saved", 0),
    }


def context_reference_exists(root: Path, run_dir: Path, reference: str) -> bool:
    if not reference:
        return True
    path = Path(reference)
    module_dir = run_dir.parents[1] if len(run_dir.parents) > 1 else run_dir
    candidates = [path] if path.is_absolute() else [root / path, run_dir / path, module_dir / path]
    return any(candidate.exists() for candidate in candidates)


def run_evidence_references(run_packet: dict[str, object]) -> list[str]:
    references: list[str] = []
    for item in run_packet.get("evidence_paths", []) if isinstance(run_packet.get("evidence_paths"), list) else []:
        references.append(str(item))
    for item in run_packet.get("commands", []) if isinstance(run_packet.get("commands"), list) else []:
        if isinstance(item, dict) and item.get("evidence_path"):
            references.append(str(item.get("evidence_path")))
    for item in run_packet.get("evidence", []) if isinstance(run_packet.get("evidence"), list) else []:
        if isinstance(item, dict) and item.get("source"):
            references.append(str(item.get("source")))
    return unique_list(references)


def context_audit_resume_contract(
    *,
    ok: bool,
    context_report: dict[str, object],
    context_packet_path: str,
    required_next_context: list[str],
    missing_required: list[str],
    missing_evidence: list[str],
    quality_failed: list[object],
    issues: list[str],
    next_command: str,
) -> dict[str, object]:
    blocking_reasons: list[str] = []
    if not context_packet_path:
        blocking_reasons.append("missing-context-packet-path")
    if context_report.get("ok") is not True:
        blocking_reasons.append("context-packet-check-failed")
    if missing_required:
        blocking_reasons.append("missing-required-context")
    if missing_evidence:
        blocking_reasons.append("missing-evidence-paths")
    if quality_failed:
        blocking_reasons.append("quality-gate-failed")
    if issues and not blocking_reasons:
        blocking_reasons.append("context-audit-issues")
    read_first = unique_list(
        [
            context_packet_path,
            "automations/navigation/artifacts/maps/HANDOFF.md",
            *required_next_context[:4],
        ]
    )
    return {
        "schema_version": 1,
        "status": "ready" if ok else "blocked",
        "can_resume": ok,
        "read_first": read_first,
        "blocking_reasons": blocking_reasons,
        "reason_counts": {
            "missing_required_context": len(missing_required),
            "missing_evidence_paths": len(missing_evidence),
            "quality_gate_failed": len(quality_failed),
            "issue_count": len(issues),
        },
        "next_command": next_command,
        "next_command_mode": "resume" if ok else "refresh-context",
        "boundary": (
            "Read-only resume contract derived from context-audit evidence. "
            "It does not refresh context, infer task safety, or replace workflow resume."
        ),
    }


def context_audit_workflow_run(root: Path, workflow_name: str, *, run_id: str | None = None) -> dict[str, object]:
    run_dir = latest_or_selected_run_dir(root, workflow_name, run_id)
    run_packet = normalized_run_state(root, workflow_name, run_dir, read_json_object(run_dir / "run.json"))
    context_report = context_workflow_run(root, workflow_name, run_id=run_dir.name, check=True)
    handoff = run_packet.get("handoff") if isinstance(run_packet.get("handoff"), dict) else {}
    handoff_required = handoff.get("required_next_context") if isinstance(handoff.get("required_next_context"), list) else []
    packet_required = context_report.get("required_next_context") if isinstance(context_report.get("required_next_context"), list) else []
    required_next_context = unique_list([str(item) for item in [*packet_required, *handoff_required] if str(item).strip()])
    missing_required = [path for path in required_next_context if not context_reference_exists(root, run_dir, path)]
    evidence_paths = run_evidence_references(run_packet)
    missing_evidence = [path for path in evidence_paths if not context_reference_exists(root, run_dir, path)]
    check = context_report.get("check") if isinstance(context_report.get("check"), dict) else {}
    quality_gate = context_report.get("quality_gate") if isinstance(context_report.get("quality_gate"), dict) else {}
    quality_failed = quality_gate.get("failed_checks") if isinstance(quality_gate.get("failed_checks"), list) else []
    issues = (
        [str(item) for item in context_report.get("issues", []) if str(item).strip()]
        if isinstance(context_report.get("issues"), list)
        else []
    )
    issues.extend(f"required context missing: {path}" for path in missing_required)
    issues.extend(f"evidence path missing: {path}" for path in missing_evidence)
    context_paths = context_report.get("context_packet_paths") if isinstance(context_report.get("context_packet_paths"), dict) else {}
    context_packet_path = str(context_report.get("existing_packet_path") or "") or str(context_paths.get("json") or "")
    ok = context_report.get("ok") is True and not missing_required and not missing_evidence
    next_command = (
        f"python -B .agents/manage.py workflow context --name {workflow_name} --run-id {run_dir.name} --write"
        if not ok
        else f"python -B .agents/manage.py workflow resume --name {workflow_name} --run-id {run_dir.name}"
    )
    resume_contract = context_audit_resume_contract(
        ok=ok,
        context_report=context_report,
        context_packet_path=context_packet_path,
        required_next_context=required_next_context,
        missing_required=missing_required,
        missing_evidence=missing_evidence,
        quality_failed=quality_failed,
        issues=issues,
        next_command=next_command,
    )
    return {
        "schema_version": 1,
        "tool": "workflow-manager.context-audit",
        "ok": ok,
        "status": "ok" if ok else "failed",
        "workflow": workflow_name,
        "run_id": run_packet.get("run_id") or run_dir.name,
        "run_path": common.relative(root, run_dir),
        "context_packet_status": context_report.get("status", "unknown"),
        "context_packet_fresh": bool(check.get("fresh", False)),
        "context_packet_path": context_packet_path,
        "context_markdown_path": str(context_report.get("existing_markdown_path") or context_paths.get("markdown") or ""),
        "required_next_context_count": len(required_next_context),
        "required_next_context": required_next_context,
        "missing_required_context": missing_required,
        "evidence_path_count": len(evidence_paths),
        "missing_evidence_paths": missing_evidence,
        "quality_gate_status": quality_gate.get("status", "unknown"),
        "quality_gate_failed_count": len(quality_failed),
        "failed_quality_checks": quality_failed,
        "issues": issues,
        "issue_count": len(issues),
        "resume_contract": resume_contract,
        "next_command": next_command,
    }


def resume_workflow_run(root: Path, workflow_name: str, *, run_id: str | None = None) -> dict[str, object]:
    run_dir = latest_or_selected_run_dir(root, workflow_name, run_id)
    run_packet = normalized_run_state(root, workflow_name, run_dir, read_json_object(run_dir / "run.json"))
    context_evidence_packet: dict[str, object] = {}
    if workflow_context_evidence.workflow_requires_context_evidence(root, workflow_name):
        context_evidence_packet = workflow_context_evidence.write_context_evidence_packet(
            root,
            workflow_name,
            run_dir,
            run_packet,
            event="resume",
            write=True,
            write_run=True,
        )
        run_packet = normalized_run_state(root, workflow_name, run_dir, read_json_object(run_dir / "run.json"))
    context_auto_refreshed = False
    context_packet: dict[str, object] = {}
    context_written: list[str] = []
    if workflow_declares_context_packet(root, workflow_name):
        context_packet = context_workflow_run(root, workflow_name, run_id=run_dir.name, write=True)
        context_auto_refreshed = context_packet.get("ok") is True
        written = context_packet.get("written") if isinstance(context_packet.get("written"), list) else []
        context_written = [str(item) for item in written]
        run_packet = normalized_run_state(root, workflow_name, run_dir, read_json_object(run_dir / "run.json"))
    checkpoint_packet = write_checkpoint_packet(root, workflow_name, run_dir, run_packet, write=True)
    checkpoint_written = [str(item) for item in checkpoint_packet.get("written", []) if isinstance(item, str)]
    handoff = workflow_handoff_packet(root, workflow_name, run_dir, run_packet)
    phase = run_packet.get("phase") if isinstance(run_packet.get("phase"), dict) else {}
    packet_handoff = run_packet.get("handoff") if isinstance(run_packet.get("handoff"), dict) else {}
    context_json, context_markdown = context_packet_paths(run_dir)
    context_path = context_json if context_json.exists() else run_dir / "run.json"
    plan_gate = (
        story_bug_plan_gate(root, workflow_name, run_dir)
        if workflow_name in PROGRESS_DOCUMENT_WORKFLOWS
        else generic_plan_gate(root, workflow_name, run_dir)
    )
    execution_queue: list[dict[str, object]] = []
    current_work_item: dict[str, object] = {}
    proof_report: dict[str, object] = {}
    proof_gap_summary: dict[str, object] = {}
    if plan_gate.get("implementation_allowed") is True:
        execution_queue = (
            story_bug_execution_queue(root, workflow_name, run_dir, run_packet)
            if workflow_name in PROGRESS_DOCUMENT_WORKFLOWS
            else generic_execution_queue(
                root,
                workflow_name,
                run_dir,
                run_packet,
                read_json_object(root / "automations" / workflow_name / "module.json"),
            )
        )
        current_work_item = execution_queue[0] if execution_queue else {}
        domain_report = (
            story_bug_finish_proof_report(root, workflow_name, run_dir, run_packet)
            if workflow_name in PROGRESS_DOCUMENT_WORKFLOWS
            else None
        )
        proof_report = finish_proof_report(root, workflow_name, run_dir, run_packet, domain_report=domain_report)
        proof_gap_summary = proof_report.get("proof_gap_summary", {}) if isinstance(proof_report.get("proof_gap_summary"), dict) else {}
    elif not plan_gate:
        execution_queue = generic_execution_queue(
            root,
            workflow_name,
            run_dir,
            run_packet,
            read_json_object(root / "automations" / workflow_name / "module.json"),
        )
        current_work_item = execution_queue[0] if execution_queue else {}
        proof_report = finish_proof_report(root, workflow_name, run_dir, run_packet)
        proof_gap_summary = proof_report.get("proof_gap_summary", {}) if isinstance(proof_report.get("proof_gap_summary"), dict) else {}
    operator_next_action = str(plan_gate.get("operator_next_action") or run_packet.get("next_action", ""))
    if current_work_item:
        operator_next_action = str(current_work_item.get("action") or operator_next_action)
    elif plan_gate.get("implementation_allowed") is True:
        missing = proof_report.get("missing_proof") if isinstance(proof_report.get("missing_proof"), list) else []
        if missing and isinstance(missing[0], dict):
            formatter = format_proof_issue if workflow_name in PROGRESS_DOCUMENT_WORKFLOWS else format_generic_proof_issue
            operator_next_action = f"Close proof gap: {formatter(missing[0])}"
        else:
            operator_next_action = "All planned checklist rows have terminal status; continue validation or finish."
    elif not plan_gate and not operator_next_action:
        operator_next_action = "Continue the current workflow phase and record evidence before finish."
    next_command = f"python -B .agents/manage.py workflow finish --name {workflow_name} --run-id {run_dir.name}"
    if plan_gate and plan_gate.get("implementation_allowed") is not True:
        next_command = f"python -B .agents/manage.py workflow plan-check --name {workflow_name} --run-id {run_dir.name}"
    completeness = evidence_completeness(root, workflow_name, run_dir, run_packet)
    return {
        "schema_version": 2,
        "tool": "workflow-manager.resume-run",
        "ok": (
            context_evidence_packet.get("ok", True) is True
            if context_evidence_packet
            else True
        ),
        "workflow": workflow_name,
        "run_id": run_packet.get("run_id") or run_dir.name,
        "run_path": common.relative(root, run_dir),
        "status": run_packet.get("status", "unknown"),
        "current_phase": run_packet.get("current_phase", ""),
        "phase_status": phase.get("status", run_packet.get("status", "unknown")),
        "phase_lifecycle": handoff.get("phase_lifecycle", {}),
        "last_completed_step": packet_handoff.get("last_completed_step", ""),
        "next_action": operator_next_action,
        "operator_next_action": operator_next_action,
        "last_command": packet_handoff.get("last_command", ""),
        "blockers": run_packet.get("blocked", []),
        "loaded_context": packet_handoff.get("loaded_context", []),
        "required_next_context": handoff.get("required_next_context", []),
        "execution_profile": handoff.get("execution_profile", {}),
        "context_handoff_path": common.relative(root, context_path),
        "context_handoff_json_path": common.relative(root, context_json if context_json.exists() else run_dir / "run.json"),
        "context_handoff_markdown_path": common.relative(root, context_markdown) if context_markdown.exists() else "",
        "context_budget": compact_context_budget_summary(context_packet),
        "context_auto_refreshed": context_auto_refreshed,
        "context_written": context_written,
        "checkpoint_auto_refreshed": checkpoint_packet.get("ok") is True,
        "checkpoint_path": checkpoint_packet.get("checkpoint_path", ""),
        "checkpoint_written": checkpoint_written,
        "context_evidence": context_evidence_packet,
        "external_validation_status": run_packet.get("external_validation_status", "not-recorded"),
        "plan_gate": plan_gate,
        "execution_queue": execution_queue,
        "current_work_item": current_work_item,
        "proof_gap_summary": proof_gap_summary,
        "evidence_completeness": completeness,
        "lesson_candidates": lesson_candidates(root, run_dir, run_packet),
        "evidence_count": len(run_packet.get("evidence", [])) if isinstance(run_packet.get("evidence"), list) else 0,
        "unsupported_claim_count": len(run_packet.get("unsupported_claims", []))
        if isinstance(run_packet.get("unsupported_claims"), list)
        else 0,
        "handoff_prompt": handoff.get("new_chat_prompt", ""),
        "next_command": next_command,
    }


def recover_workflow_run(root: Path, workflow_name: str, *, run_id: str | None = None, write: bool = False) -> dict[str, object]:
    run_dir = latest_or_selected_run_dir(root, workflow_name, run_id)
    report = recover_run_packet(root, workflow_name, run_dir, write=write)
    if write and report.get("ok") and workflow_declares_context_packet(root, workflow_name):
        context_report = context_workflow_run(root, workflow_name, run_id=run_dir.name, write=True)
        report["context_auto_refreshed"] = context_report.get("ok") is True
        report["context_packet_path"] = (
            context_report.get("context_packet_paths", {}).get("json", "")
            if isinstance(context_report.get("context_packet_paths"), dict)
            else ""
        )
        written = report.get("written") if isinstance(report.get("written"), list) else []
        context_written = context_report.get("written") if isinstance(context_report.get("written"), list) else []
        report["written"] = unique_list([*[str(item) for item in written], *[str(item) for item in context_written]])
    return report


def handoff_workflow_run(
    root: Path,
    workflow_name: str,
    *,
    run_id: str | None = None,
    write: bool = False,
) -> dict[str, object]:
    run_dir = latest_or_selected_run_dir(root, workflow_name, run_id)
    run_packet = read_json_object(run_dir / "run.json")
    hook_results: list[dict[str, object]] = []
    if write:
        run_packet = normalized_run_state(root, workflow_name, run_dir, run_packet)
        clear_phase_mismatched_runtime_observation(run_packet)
        hook_results = [
            *execute_workflow_hooks(root, workflow_name, run_dir, run_packet, "phase-between"),
            *execute_workflow_hooks(root, workflow_name, run_dir, run_packet, "phase-handoff"),
        ]
        context_packet = write_context_packet(root, workflow_name, run_dir, run_packet, write=True)
        context_json, _context_markdown = context_packet_paths(run_dir)
        handoff = run_packet.get("handoff") if isinstance(run_packet.get("handoff"), dict) else {}
        required_next_context = handoff.get("required_next_context")
        if not isinstance(required_next_context, list):
            required_next_context = default_workflow_context(root, workflow_name, run_dir)
        handoff["required_next_context"] = unique_list([common.relative(root, context_json), *[str(item) for item in required_next_context]])
        run_packet["handoff"] = handoff
        (run_dir / "run.json").write_text(
            json.dumps(run_packet, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        context_packet = write_context_packet(root, workflow_name, run_dir, run_packet, write=True)
        packet = workflow_handoff_packet(root, workflow_name, run_dir, run_packet)
    else:
        packet = workflow_handoff_packet(root, workflow_name, run_dir, run_packet)
    packet["ok"] = hook_results_ok(hook_results)
    packet["hook_results"] = hook_results
    if write:
        packet["context_packet"] = context_packet
        packet["written"] = [
            common.relative(root, run_dir / "run.json"),
            *context_packet.get("written", []),
        ]
    else:
        packet["written"] = []
    return packet


def finish_workflow_run(root: Path, workflow_name: str, *, run_id: str | None = None) -> dict[str, object]:
    run_dir = latest_or_selected_run_dir(root, workflow_name, run_id)
    issues: list[str] = []
    advisories: list[str] = []
    run_path = run_dir / "run.json"
    report_path = run_dir / "REPORT.md"
    run_packet = normalized_run_state(root, workflow_name, run_dir, read_json_object(run_path))
    context_evidence_packet: dict[str, object] = {}
    if workflow_context_evidence.workflow_requires_context_evidence(root, workflow_name):
        context_evidence_packet = workflow_context_evidence.write_context_evidence_packet(
            root,
            workflow_name,
            run_dir,
            run_packet,
            event="finish",
            write=True,
            write_run=True,
        )
        run_packet = normalized_run_state(root, workflow_name, run_dir, read_json_object(run_path))
    phase_event = "phase-blocked" if phase_has_blockers(run_packet) else "phase-completed"
    hook_results = [
        *execute_workflow_hooks(root, workflow_name, run_dir, run_packet, phase_event),
        *execute_workflow_hooks(root, workflow_name, run_dir, run_packet, "phase-post"),
        *execute_workflow_hooks(root, workflow_name, run_dir, run_packet, "run-finished"),
        *execute_workflow_hooks(root, workflow_name, run_dir, run_packet, "workflow-post"),
    ]
    if hook_results:
        run_path.write_text(
            json.dumps(run_packet, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    context_packet_refreshed = False
    context_packet_path = ""
    if (hook_results or context_evidence_packet) and workflow_declares_context_packet(root, workflow_name):
        refreshed_context = context_workflow_run(root, workflow_name, run_id=run_dir.name, write=True)
        context_packet_refreshed = True
        written = refreshed_context.get("written") if isinstance(refreshed_context.get("written"), list) else []
        context_packet_path = next((str(path) for path in written if str(path).endswith(".json")), "")
        run_packet = normalized_run_state(root, workflow_name, run_dir, read_json_object(run_path))
    required_fields = (
        "schema_version",
        "tool",
        "workflow",
        "run_id",
        "current_phase",
        "status",
        "decisions",
        "checks",
        "commands",
        "evidence",
        "skipped",
        "blocked",
        "failed",
        "handoff",
        "next_action",
    )
    for key in required_fields:
        if key not in run_packet:
            issues.append(f"run.json missing {key}")
    if not run_path.exists():
        issues.append("run.json is missing")
    if isinstance(run_packet.get("unsupported_claims"), list) and run_packet.get("unsupported_claims"):
        advisories.append("run packet records unsupported claims for explicit disclosure")
    completion_candidate = not phase_has_blockers(run_packet)
    external_validation_status = str(run_packet.get("external_validation_status") or "not-recorded")
    evidence_entries = run_packet.get("evidence")
    evidence_paths = run_packet.get("evidence_paths")
    has_evidence_entries = isinstance(evidence_entries, list) and any(
        is_completion_evidence_entry(item) for item in evidence_entries
    )
    has_evidence_paths = isinstance(evidence_paths, list) and any(
        is_completion_evidence_path(item) for item in evidence_paths
    )
    if completion_candidate:
        if not has_evidence_entries and not has_evidence_paths:
            issues.append("completed run has no evidence entries")
        if external_validation_status in {"", "not-recorded"}:
            issues.append("completed run has no external validation status")
        elif external_validation_status in {"failed", "blocked"}:
            issues.append(f"completed run external validation status is {external_validation_status}")
    for result in hook_results:
        if result.get("required") is True and result.get("ok") is not True:
            issues.append(hook_failure_label(result))
    if workflow_context_evidence.workflow_requires_context_evidence(root, workflow_name):
        issues.extend(workflow_context_evidence.validate_context_evidence_packet(root, workflow_name, run_dir, event="start"))
        issues.extend(workflow_context_evidence.validate_context_evidence_packet(root, workflow_name, run_dir, event="finish"))
    start = root / "automations" / workflow_name / "WORKFLOW.md"
    start_text = common.read_text(start, limit=80_000) if start.exists() else ""
    for required in ("## Example Prompts", "Start", "Resume", "Handoff", "Finish"):
        if required not in start_text:
            issues.append(f"{common.relative(root, start)} missing workflow prompt example marker: {required}")
    declared_outputs = []
    contract = root / "automations" / workflow_name / "module.json"
    if contract.exists():
        declared_outputs.append(common.relative(root, contract))
    if not report_path.exists():
        issues.append("final report evidence is missing; expected REPORT.md")
    if workflow_name in {"user-story-workflow", "bug-ticket-workflow"}:
        issues.extend(out_of_scope_template_issues(root, workflow_name))
        issues.extend(progress_log_issues(root, workflow_name, run_dir, run_packet))
        issues.extend(pr_handoff_issues(root, run_dir))
        context_json, _context_markdown = context_packet_paths(run_dir)
        context_packet = build_context_packet(root, workflow_name, run_dir, run_packet)
        if not context_json.exists():
            issues.append("context packet is missing; run workflow context --write")
        else:
            existing_context_packet = read_json_object(context_json)
            if comparable_context_packet(existing_context_packet) != comparable_context_packet(context_packet):
                issues.append("context packet is stale; run workflow context --write")
        if context_packet.get("issues"):
            issues.extend(f"context packet: {item}" for item in context_packet.get("issues", []))
    if workflow_name in {"user-story-workflow", "bug-ticket-workflow", "disciplined-change-workflow"}:
        issues.extend(closeout_evidence_issues(root, workflow_name, run_dir))
    domain_proof_report = None
    if workflow_name in {"user-story-workflow", "bug-ticket-workflow"}:
        domain_proof_report = story_bug_finish_proof_report(root, workflow_name, run_dir, run_packet)
    elif workflow_name == "feedback-improvement-workflow":
        domain_proof_report = feedback_improvement_finish_proof_report(root, workflow_name, run_dir, run_packet)
    proof_report = finish_proof_report(root, workflow_name, run_dir, run_packet, domain_report=domain_proof_report)
    missing_proof = proof_report.get("missing_proof") if isinstance(proof_report.get("missing_proof"), list) else []
    if missing_proof:
        for item in missing_proof:
            if not isinstance(item, dict):
                continue
            formatter = format_proof_issue if workflow_name in PROGRESS_DOCUMENT_WORKFLOWS and item.get("section") != "Declared Outputs" else format_generic_proof_issue
            issues.append(formatter(item))
    if not issues and completion_candidate:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        run_packet["status"] = "completed"
        if "workflow_status" in run_packet:
            run_packet["workflow_status"] = "completed"
        phase = run_packet.get("phase") if isinstance(run_packet.get("phase"), dict) else {}
        phase["status"] = "completed"
        phase.setdefault("completed_at", now)
        run_packet["phase"] = phase
        run_packet["next_action"] = ""
        run_packet["updated_at"] = now
        run_path.write_text(
            json.dumps(run_packet, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if workflow_declares_context_packet(root, workflow_name):
            final_context = context_workflow_run(root, workflow_name, run_id=run_dir.name, write=True)
            context_packet_refreshed = True
            written = final_context.get("written") if isinstance(final_context.get("written"), list) else []
            context_packet_path = next((str(path) for path in written if str(path).endswith(".json")), context_packet_path)
            run_packet = normalized_run_state(root, workflow_name, run_dir, read_json_object(run_path))
    checkpoint_packet = write_checkpoint_packet(root, workflow_name, run_dir, run_packet, write=True)
    checkpoint_written = [str(item) for item in checkpoint_packet.get("written", []) if isinstance(item, str)]
    completeness = evidence_completeness(root, workflow_name, run_dir, run_packet)
    run_index = (
        refresh_run_index(root, workflow_name)
        if not issues
        else {
            "ok": False,
            "status": "skipped",
            "reason": "finish reported issues; resume and resolve before refreshing the run index",
            "paths": [],
        }
    )
    report = {
        "schema_version": 2,
        "tool": "workflow-manager.finish-run",
        "ok": not issues,
        "workflow": workflow_name,
        "run_id": run_packet.get("run_id") or run_dir.name,
        "status": run_packet.get("status", "unknown"),
        "run_path": common.relative(root, run_dir),
        "state_path": common.relative(root, run_path),
        "evidence_ledger_path": common.relative(root, run_path),
        "final_report_path": common.relative(root, report_path) if report_path.exists() else "",
        "declared_output_sources": declared_outputs,
        "external_validation_status": run_packet.get("external_validation_status", "not-recorded"),
        "issues": issues,
        "advisories": advisories,
        "hook_results": hook_results,
        "context_evidence": context_evidence_packet,
        "context_packet_refreshed": context_packet_refreshed,
        "context_packet_path": context_packet_path,
        "proof_matrix": proof_report,
        "missing_proof": missing_proof,
        "lesson_candidates": proof_report.get("lesson_candidates", []),
        "evidence_completeness": completeness,
        "checkpoint": checkpoint_packet,
        "checkpoint_written": checkpoint_written,
        "run_index": run_index,
        "next_command": (
            f"python -B .agents/manage.py index-workflow-runs --name {workflow_name} --check"
            if not issues
            else f"python -B .agents/manage.py workflow resume --name {workflow_name} --run-id {run_dir.name}"
        ),
    }
    record_workflow_feedback(root, report)
    return report
