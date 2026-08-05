"""Markdown renderers for workflow run command reports."""

from __future__ import annotations

from workflow_support.run_story_bug import PROGRESS_DOCUMENT_WORKFLOWS


def render_handoff_markdown(packet: dict[str, object]) -> str:
    lines = [
        f"# Context Handoff: {packet.get('workflow')} {packet.get('run_id')}",
        "",
        "Use this file first when continuing the run in a new chat. Load only the listed files unless the current step needs more detail.",
        "",
        "## Resume State",
        "",
        f"- Current phase: {packet.get('current_phase') or 'unknown'}",
        f"- Phase status: {packet.get('phase_status') or 'unknown'}",
        f"- Last completed step: {packet.get('last_completed_step') or 'none'}",
        f"- Next action: {packet.get('next_action') or 'not recorded'}",
        f"- Last command: `{packet.get('last_command') or 'none'}`",
        f"- External validation: {packet.get('external_validation_status')}",
        f"- Evidence entries: {packet.get('evidence_count')}",
        f"- Unsupported claims: {packet.get('unsupported_claim_count')}",
        "",
        "## Required Next Context",
        "",
    ]
    for item in packet.get("required_next_context", []):
        lines.append(f"- `{item}`")
    loaded = packet.get("loaded_context", []) if isinstance(packet.get("loaded_context"), list) else []
    if loaded:
        lines.extend(["", "## Already Loaded Context", ""])
        lines.extend(f"- `{item}`" for item in loaded)
    blockers = packet.get("blockers", []) if isinstance(packet.get("blockers"), list) else []
    if blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in blockers)
    lifecycle = packet.get("phase_lifecycle") if isinstance(packet.get("phase_lifecycle"), dict) else {}
    if lifecycle:
        lines.extend(["", "## Phase Lifecycle", ""])
        lines.append(f"- Started: {lifecycle.get('phase_started_at') or 'not recorded'}")
        lines.append(f"- Completed: {lifecycle.get('phase_completed_at') or 'not recorded'}")
        for label, key in (
            ("Entry checks", "phase_entry_checks"),
            ("Exit checks", "phase_exit_checks"),
            ("Evidence", "phase_evidence"),
        ):
            values = lifecycle.get(key, []) if isinstance(lifecycle.get(key), list) else []
            if values:
                lines.append(f"- {label}: {', '.join(str(item) for item in values)}")
    execution = packet.get("execution_profile") if isinstance(packet.get("execution_profile"), dict) else {}
    if execution:
        lines.extend(["", "## Execution Profile", ""])
        lines.append(f"- Profile: `{execution.get('profile_id', '')}`")
        lines.append(f"- Purpose: {execution.get('profile_purpose', '')}")
        lines.append(f"- Adapter: {execution.get('prompt_adapter', '')}")
        declared_target = " ".join(
            str(execution.get(field, "")).strip()
            for field in ("declared_model_provider", "declared_model")
            if str(execution.get(field, "")).strip()
        ) or str(execution.get("model_target", "active model"))
        declared_tier = str(execution.get("declared_deliberation_tier") or "unspecified")
        lines.append(
            "- Declared target: "
            f"{declared_target} "
            f"(deliberation={declared_tier}, surface={execution.get('declared_host_surface', 'unattested')})"
        )
        lines.append(f"- Endpoint status: {execution.get('endpoint_status', 'unattested-active')}")
        lines.append(f"- Capability status: {execution.get('capability_status', 'unavailable')}")
        lines.append(f"- Effective execution: {execution.get('effective_execution_mode', 'serial-active-model')}")
        observed_host = str(execution.get("observed_host_surface", "")).strip()
        observed_model = str(execution.get("observed_model", "")).strip()
        if observed_host or observed_model:
            parts = [observed_host] if observed_host else []
            if observed_model:
                parts.append(
                    f"{execution.get('observed_model_provider', '')} {observed_model} "
                    f"(deliberation={execution.get('observed_deliberation') or 'unspecified'})"
                )
            lines.append("- Observed runtime: " + " / ".join(parts))
            evidence_path = str(execution.get("observation_evidence_path", "")).strip()
            if evidence_path:
                lines.append(f"- Runtime observation evidence: `{evidence_path}`")
        fallback_reason = str(execution.get("fallback_reason", "")).strip()
        if fallback_reason:
            lines.append(f"- Fallback: {fallback_reason}")
        lines.append(f"- Context budget: {execution.get('context_budget', '')}")
        lines.append(f"- Tool policy: {execution.get('tool_policy', '')}")
        lines.append(f"- Validation gate: {execution.get('validation_gate', '')}")
        header = execution.get("instruction_header") if isinstance(execution.get("instruction_header"), list) else []
        if header:
            lines.append("- Semantic instruction header:")
            lines.extend(f"  - {item}" for item in header)
        overlay = execution.get("prompt_overlay") if isinstance(execution.get("prompt_overlay"), dict) else {}
        if overlay:
            lines.append(f"- Prompt delivery overlay: `{overlay.get('id', 'generic-v1')}`")
        surface_adapter = execution.get("surface_adapter") if isinstance(execution.get("surface_adapter"), dict) else {}
        if surface_adapter:
            lines.append(
                f"- Surface adapter: `{surface_adapter.get('id', 'generic-v1')}`; "
                f"orchestration={surface_adapter.get('orchestration_mode', 'direct-tools')}; "
                f"continuation={surface_adapter.get('continuation_mode', 'durable-workflow-checkpoint')}"
            )
    decisions = packet.get("decisions", []) if isinstance(packet.get("decisions"), list) else []
    if decisions:
        lines.extend(["", "## Decisions", ""])
        lines.extend(f"- {item}" if not isinstance(item, dict) else f"- {item.get('decision', item)}" for item in decisions)
    issues = packet.get("things_that_went_wrong", []) if isinstance(packet.get("things_that_went_wrong"), list) else []
    if issues:
        lines.extend(["", "## Things That Did Not Go Well", ""])
        lines.extend(f"- {item}" if not isinstance(item, dict) else f"- {item.get('issue', item)}" for item in issues)
    lines.extend(
        [
            "",
            "## Copyable Prompt",
            "",
            f"> {packet.get('new_chat_prompt')}",
            "",
            f"Next command: `{packet.get('next_command')}`",
            "",
        ]
    )
    return "\n".join(lines)


def render_start_run(report: dict[str, object]) -> str:
    lines = ["# Workflow Run Started", ""]
    lines.append(f"- Workflow: `{report.get('workflow')}`")
    lines.append(f"- Run: `{report.get('run_id')}`")
    lines.append(f"- Path: `{report.get('run_path')}`")
    lines.append("- What happened: created the run packet, report, required progress files, lifecycle hook evidence, and context evidence when declared.")
    if report.get("operator_next_action"):
        lines.append(f"- Next move: {report.get('operator_next_action')}")
    elif report.get("workflow") in PROGRESS_DOCUMENT_WORKFLOWS:
        lines.append("- Next move: fill `plan.md`, run `workflow plan-check`, then stop before implementation until approval is recorded.")
    else:
        lines.append("- Next move: follow the required checklist, record evidence in `run.json`, and resume with the command below after any interruption.")
    if report.get("from_request"):
        lines.append(f"- Routed request: {report.get('from_request')}")
    if report.get("context_packet_path"):
        lines.append(f"- Context packet: `{report.get('context_packet_path')}`")
    preflight = report.get("workflow_preflight") if isinstance(report.get("workflow_preflight"), dict) else {}
    if preflight:
        lines.append(f"- Preflight: {preflight.get('confidence', 'unknown')} -> `{preflight.get('next_command', '')}`")
    lines.append("- Created files:")
    for path in report.get("created_files", []):
        lines.append(f"  - `{path}`")
    checklist = report.get("start_checklist") if isinstance(report.get("start_checklist"), dict) else {}
    required = checklist.get("required_before_work") if isinstance(checklist.get("required_before_work"), list) else []
    if required:
        lines.extend(["", "## Required Start Checklist", ""])
        lines.extend(f"- [ ] {item}" for item in required)
    first_commands = checklist.get("first_commands") if isinstance(checklist.get("first_commands"), list) else []
    if first_commands:
        lines.extend(["", "## First Commands", ""])
        lines.extend(f"- `{item}`" for item in first_commands)
    if preflight:
        read_first = preflight.get("read_first") if isinstance(preflight.get("read_first"), list) else []
        if read_first:
            lines.extend(["", "## Preflight Read First", ""])
            lines.extend(f"- `{item}`" for item in read_first[:6])
        tool_only = preflight.get("tool_only_inputs") if isinstance(preflight.get("tool_only_inputs"), list) else []
        if tool_only:
            lines.extend(["", "## Tool-Only Inputs", ""])
            lines.extend(f"- `{item}`" for item in tool_only)
    context_evidence_packet = report.get("context_evidence") if isinstance(report.get("context_evidence"), dict) else {}
    if context_evidence_packet:
        lines.extend(["", "## Context Evidence", ""])
        lines.append(f"- Status: {context_evidence_packet.get('status')}")
        written = context_evidence_packet.get("written") if isinstance(context_evidence_packet.get("written"), list) else []
        for path in written:
            lines.append(f"- `{path}`")
    checkpoint = report.get("checkpoint") if isinstance(report.get("checkpoint"), dict) else {}
    if checkpoint:
        lines.extend(["", "## Checkpoint", ""])
        lines.append(f"- Status: {checkpoint.get('status')}")
        lines.append(f"- Path: `{checkpoint.get('checkpoint_path')}`")
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"


def render_hooks_run(report: dict[str, object]) -> str:
    lines = ["# Workflow Hooks", ""]
    lines.append(f"- Workflow: `{report.get('workflow')}`")
    lines.append(f"- Run: `{report.get('run_id')}` ({'exists' if report.get('run_exists') else 'dry-run path'})")
    lines.append(f"- Status: {'passed' if report.get('ok') else 'failed'}")
    lines.append(f"- Hooks: {report.get('hook_count', 0)}")
    lines.append(f"- Required: {report.get('required_count', 0)}")
    lines.append(f"- Unsafe: {report.get('unsafe_count', 0)}")
    hooks = report.get("hooks", []) if isinstance(report.get("hooks"), list) else []
    if hooks:
        lines.extend(["", "## Hooks", ""])
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            safe = "safe" if hook.get("safe") else "unsafe"
            lines.append(
                f"- `{hook.get('event')}:{hook.get('id')}` "
                f"({hook.get('scope')}, {safe}) -> `{hook.get('evidence_path')}`"
            )
            lines.append(f"  - Command: `{hook.get('command')}`")
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"


def render_resume_run(report: dict[str, object]) -> str:
    lines = ["# Workflow Resume", ""]
    lines.append(f"- Workflow: `{report.get('workflow')}`")
    lines.append(f"- Run: `{report.get('run_id')}`")
    lines.append(f"- Status: {report.get('status')}")
    lines.append(f"- Current phase: {report.get('current_phase') or 'unknown'}")
    lines.append(f"- Phase status: {report.get('phase_status') or 'unknown'}")
    lines.append(f"- Last completed step: {report.get('last_completed_step') or 'none'}")
    lines.append(f"- Next action: {report.get('next_action') or 'not recorded'}")
    lines.append(f"- Last command: `{report.get('last_command') or 'none'}`")
    lines.append(f"- External validation: {report.get('external_validation_status')}")
    execution = report.get("execution_profile") if isinstance(report.get("execution_profile"), dict) else {}
    if execution:
        lines.append(
            "- Execution profile: "
            f"`{execution.get('profile_id', '')}` / {execution.get('prompt_adapter', '')} / "
            f"{execution.get('model_target', 'active model')} "
            f"(deliberation={execution.get('deliberation_tier') or 'unspecified'})"
        )
    lines.append("- What happened: refreshed resume context evidence and rebuilt the context handoff when the workflow declares one.")
    lines.append("- Resume rule: load the context handoff first, then only the listed required context before continuing.")
    plan_gate = report.get("plan_gate") if isinstance(report.get("plan_gate"), dict) else {}
    if plan_gate:
        lines.append(
            "- Plan gate: "
            f"{plan_gate.get('status', 'not checked')} "
            f"(ready for approval: {str(plan_gate.get('ready_for_approval')).lower()}, "
            f"implementation allowed: {str(plan_gate.get('implementation_allowed')).lower()})."
        )
        if plan_gate.get("operator_next_action"):
            lines.append(f"- Operator next action: {plan_gate.get('operator_next_action')}")
    if report.get("context_auto_refreshed"):
        lines.append(f"- Context auto-refreshed: `{report.get('context_handoff_path')}`")
    if report.get("checkpoint_auto_refreshed"):
        lines.append(f"- Checkpoint auto-refreshed: `{report.get('checkpoint_path')}`")
    context_evidence_packet = report.get("context_evidence") if isinstance(report.get("context_evidence"), dict) else {}
    if context_evidence_packet:
        lines.append(f"- Context evidence: {context_evidence_packet.get('status')} - `{(context_evidence_packet.get('written') or [''])[0] if isinstance(context_evidence_packet.get('written'), list) and context_evidence_packet.get('written') else ''}`")
    blockers = report.get("blockers", []) if isinstance(report.get("blockers"), list) else []
    if blockers:
        lines.append("- Blockers:")
        lines.extend(f"  - {item}" for item in blockers)
    lines.append(f"- Evidence entries: {report.get('evidence_count')}")
    lines.append(f"- Unsupported claims: {report.get('unsupported_claim_count')}")
    lines.append(f"- Context handoff: `{report.get('context_handoff_path')}`")
    required = report.get("required_next_context", []) if isinstance(report.get("required_next_context"), list) else []
    if required:
        lines.extend(["", "## Load This Context First", ""])
        lines.extend(f"- `{item}`" for item in required)
    fix_items = plan_gate.get("fix_queue") if isinstance(plan_gate.get("fix_queue"), list) else []
    if fix_items:
        lines.extend(["", "## Fix Queue", ""])
        for index, item in enumerate(fix_items, start=1):
            if not isinstance(item, dict):
                continue
            detail = []
            if item.get("row") is not None:
                detail.append(f"row {item.get('row')}")
            if item.get("field"):
                detail.append(str(item.get("field")))
            suffix = f" ({', '.join(detail)})" if detail else ""
            lines.append(f"{index}. {item.get('section', 'Plan')}{suffix}: {item.get('action')}")
    work_item = report.get("current_work_item") if isinstance(report.get("current_work_item"), dict) else {}
    if work_item:
        lines.extend(["", "## Current Work Item", ""])
        lines.append(f"- {work_item.get('section')} row {work_item.get('row')}: {work_item.get('step')}")
        if work_item.get("verification"):
            lines.append(f"- Verification: {work_item.get('verification')}")
    proof_summary = report.get("proof_gap_summary") if isinstance(report.get("proof_gap_summary"), dict) else {}
    if proof_summary:
        lines.extend(["", "## Proof Gap Summary", ""])
        lines.append(f"- Missing proof: {proof_summary.get('missing_count', 0)}")
        by_section = proof_summary.get("by_section") if isinstance(proof_summary.get("by_section"), dict) else {}
        for section, count in by_section.items():
            lines.append(f"- {section}: {count}")
    completeness = report.get("evidence_completeness") if isinstance(report.get("evidence_completeness"), dict) else {}
    if completeness:
        lines.extend(["", "## Evidence Completeness", ""])
        lines.append(f"- Status: {completeness.get('status')}")
        lines.append(f"- Missing: {completeness.get('missing_count', 0)}")
    if report.get("handoff_prompt"):
        lines.extend(["", "## New Chat Prompt", "", f"> {report.get('handoff_prompt')}"])
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"


def render_finish_run(report: dict[str, object]) -> str:
    lines = ["# Workflow Finish Check", ""]
    lines.append(f"- Workflow: `{report.get('workflow')}`")
    lines.append(f"- Run: `{report.get('run_id')}`")
    lines.append(f"- Status: {'passed' if report.get('ok') else 'failed'}")
    lines.append(f"- External validation: {report.get('external_validation_status')}")
    if report.get("issues"):
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in report.get("issues", []))
    proof_matrix = report.get("proof_matrix") if isinstance(report.get("proof_matrix"), dict) else {}
    if proof_matrix and proof_matrix.get("status") != "skipped":
        lines.append(f"- Proof matrix: {proof_matrix.get('status')} ({proof_matrix.get('missing_count', 0)} missing)")
    lessons = report.get("lesson_candidates") if isinstance(report.get("lesson_candidates"), list) else []
    if lessons:
        lines.extend(["", "## Lesson Candidates", ""])
        lines.extend(f"- {item}" for item in lessons)
    context_evidence_packet = report.get("context_evidence") if isinstance(report.get("context_evidence"), dict) else {}
    if context_evidence_packet:
        lines.append(f"- Context evidence: {context_evidence_packet.get('status')}")
    if report.get("context_packet_path"):
        lines.append(f"- Context packet: `{report.get('context_packet_path')}`")
    checkpoint = report.get("checkpoint") if isinstance(report.get("checkpoint"), dict) else {}
    if checkpoint:
        lines.append(f"- Checkpoint: `{checkpoint.get('checkpoint_path')}`")
    run_index = report.get("run_index") if isinstance(report.get("run_index"), dict) else {}
    if run_index:
        paths = run_index.get("paths") if isinstance(run_index.get("paths"), list) else []
        suffix = f" ({', '.join(f'`{path}`' for path in paths)})" if paths else ""
        lines.append(f"- Run index: {run_index.get('status')}{suffix}")
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"


def render_context_audit(report: dict[str, object]) -> str:
    lines = ["# Workflow Context Audit", ""]
    lines.append(f"- Workflow: `{report.get('workflow')}`")
    lines.append(f"- Run: `{report.get('run_id')}`")
    lines.append(f"- Status: {report.get('status')}")
    lines.append(f"- Context packet: {report.get('context_packet_status')} fresh={str(report.get('context_packet_fresh')).lower()}")
    lines.append(f"- Context packet path: `{report.get('context_packet_path')}`")
    lines.append(f"- Required context: {report.get('required_next_context_count', 0)}")
    missing_required = report.get("missing_required_context") if isinstance(report.get("missing_required_context"), list) else []
    missing_evidence = report.get("missing_evidence_paths") if isinstance(report.get("missing_evidence_paths"), list) else []
    lines.append(f"- Missing required context: {len(missing_required)}")
    lines.append(f"- Missing evidence paths: {len(missing_evidence)}")
    lines.append(f"- Quality gate: {report.get('quality_gate_status')} ({report.get('quality_gate_failed_count', 0)} failed)")
    resume_contract = report.get("resume_contract") if isinstance(report.get("resume_contract"), dict) else {}
    if resume_contract:
        lines.append(
            f"- Resume contract: {resume_contract.get('status', 'unknown')} "
            f"(can resume: {str(resume_contract.get('can_resume', False)).lower()})"
        )
        read_first = resume_contract.get("read_first") if isinstance(resume_contract.get("read_first"), list) else []
        if read_first:
            lines.extend(["", "## Resume Read First", ""])
            lines.extend(f"- `{item}`" for item in read_first)
        blocking = resume_contract.get("blocking_reasons") if isinstance(resume_contract.get("blocking_reasons"), list) else []
        if blocking:
            lines.extend(["", "## Resume Blockers", ""])
            lines.extend(f"- {item}" for item in blocking)
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in issues)
    lines.extend(["", "## Next Command", "", f"- `{report.get('next_command')}`"])
    return "\n".join(lines) + "\n"
