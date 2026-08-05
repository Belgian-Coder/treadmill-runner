"""Markdown rendering for workflow context packets."""

from __future__ import annotations


def render_context_packet_markdown(packet: dict[str, object]) -> str:
    lines = [
        f"# Context Packet: {packet.get('workflow')} {packet.get('run_id')}",
        "",
        f"- Status: {packet.get('status')}",
        f"- Current phase: {packet.get('current_phase') or 'unknown'}",
        f"- Phase status: {packet.get('phase_status') or 'unknown'}",
        f"- Next action: {packet.get('next_action') or 'not recorded'}",
        "",
        "## Scope",
        "",
    ]
    scope = packet.get("scope") if isinstance(packet.get("scope"), dict) else {}
    for label, key in (("In scope", "in_scope"), ("Out of scope", "out_of_scope"), ("Assumptions", "assumptions")):
        values = scope.get(key, []) if isinstance(scope.get(key), list) else []
        lines.append(f"- {label}:")
        lines.extend(f"  - {item}" for item in values) if values else lines.append("  - not recorded")
    execution = packet.get("execution_profile") if isinstance(packet.get("execution_profile"), dict) else {}
    if execution:
        lines.extend(["", "## Execution Profile", ""])
        lines.append(f"- Status: {execution.get('status', 'unknown')}")
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
        lines.append(
            f"- Numeric phase budget: {execution.get('budget_tokens', 0)} tokens "
            f"(`{execution.get('context_budget_ref', '')}`)"
        )
        lines.append(f"- Effective context: {execution.get('effective_context_tokens', 'not measured')} tokens")
        lines.append(f"- Remaining margin: {execution.get('remaining_margin_tokens', 'not measured')} tokens")
        lines.append(f"- Within phase budget: {execution.get('within_budget', 'not measured')}")
        lines.append(f"- Tool policy: {execution.get('tool_policy', '')}")
        lines.append(f"- Expected output: {execution.get('expected_output', '')}")
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
    instruction_context = packet.get("instruction_context") if isinstance(packet.get("instruction_context"), dict) else {}
    if instruction_context:
        lines.extend(["", "## Instruction Context", ""])
        lines.append(f"- Status: {instruction_context.get('status')}")
        lines.append(f"- Current phase: {instruction_context.get('current_phase') or 'unknown'}")
        lines.append(f"- Requires full instructions: {instruction_context.get('requires_full_instructions')}")
        for label, key in (
            ("Always load", "always_load"),
            ("Stop rules", "stop_rules"),
            ("Completion contract", "completion_contract"),
            ("Current phase instructions", "current_phase_instructions"),
        ):
            value = str(instruction_context.get(key, "")).strip()
            if value:
                lines.extend([f"- {label}:", value])
    work_item = packet.get("work_item_summary") if isinstance(packet.get("work_item_summary"), dict) else {}
    if work_item:
        lines.extend(["", "## Work Item Summary", ""])
        for key, value in work_item.items():
            if key == "type" or not value:
                continue
            rendered = value if isinstance(value, str) else ", ".join(str(item) for item in value)
            lines.append(f"- {key.replace('_', ' ').title()}: {rendered}")
    validation = packet.get("validation_summary") if isinstance(packet.get("validation_summary"), dict) else {}
    commands = validation.get("commands", []) if isinstance(validation.get("commands"), list) else []
    lines.extend(["", "## Validation", "", f"- External validation: {validation.get('external_validation_status', 'not-recorded')}"])
    for item in commands[:10]:
        if isinstance(item, dict):
            lines.append(f"- `{item.get('command')}`: {item.get('status') or item.get('returncode')}")
    documentation = packet.get("documentation_delta") if isinstance(packet.get("documentation_delta"), dict) else {}
    if documentation:
        lines.extend(["", "## Documentation Delta", ""])
        lines.append(f"- Status: {documentation.get('status')}")
        changed_docs = documentation.get("changed_docs") if isinstance(documentation.get("changed_docs"), list) else []
        lines.append(f"- Changed docs: {', '.join(str(item) for item in changed_docs) if changed_docs else 'none recorded'}")
        reason = str(documentation.get("no_doc_impact_reason", "")).strip()
        if reason:
            lines.append(f"- No documentation impact reason: {reason}")
    estimates = packet.get("token_estimates") if isinstance(packet.get("token_estimates"), dict) else {}
    lines.extend(
        [
            "",
            "## Context Savings",
            "",
            f"- Raw context tokens estimated: {estimates.get('raw_context_tokens_estimated', 0)}",
            f"- Packet tokens estimated: {estimates.get('packet_tokens_estimated', 0)}",
            f"- Compact decision tokens estimated: {estimates.get('compact_packet_tokens_estimated', 0)}",
            f"- Estimated tokens saved: {estimates.get('estimated_tokens_saved', 0)}",
        ]
    )
    budget = packet.get("context_budget") if isinstance(packet.get("context_budget"), dict) else {}
    if budget:
        lines.extend(
            [
                f"- Packet budget status: {budget.get('status', 'unknown')}",
                f"- Savings ratio: {budget.get('savings_ratio', 0)}",
            ]
        )
    guidance = packet.get("guidance_savings") if isinstance(packet.get("guidance_savings"), dict) else {}
    if guidance:
        lines.extend(
            [
                f"- Default guidance status: {guidance.get('status', 'unknown')}",
                f"- Default guidance tokens estimated: {guidance.get('default_guidance_tokens', 0)}",
                f"- Broad guidance baseline tokens estimated: {guidance.get('broad_baseline_tokens', 0)}",
                f"- Default guidance saved tokens estimated: {guidance.get('saved_tokens_estimated', 0)}",
                f"- Default guidance saved percent estimated: {guidance.get('saved_percent_estimated', 0)}",
            ]
        )
    coordinates = packet.get("coordinate_closet") if isinstance(packet.get("coordinate_closet"), dict) else {}
    if coordinates:
        lines.extend(["", "## Exact Identifier Closet", ""])
        lines.append(f"- Status: {coordinates.get('status', 'unknown')}")
        for label, key in (
            ("Paths", "paths"),
            ("Hashes", "hashes"),
            ("IDs", "ids"),
            ("Ports", "ports"),
            ("Environment names", "env"),
        ):
            values = coordinates.get(key) if isinstance(coordinates.get(key), list) else []
            if values:
                rendered = ", ".join(f"`{item}`" for item in values[:20])
                lines.append(f"- {label}: {rendered}")
    evidence = packet.get("evidence_handles", []) if isinstance(packet.get("evidence_handles"), list) else []
    if evidence:
        lines.extend(["", "## Evidence Handles", ""])
        lines.extend(f"- `{item}`" for item in evidence[:30])
    issues = packet.get("issues", []) if isinstance(packet.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in issues)
    required = packet.get("required_next_context", []) if isinstance(packet.get("required_next_context"), list) else []
    lines.extend(["", "## Required Next Context", ""])
    lines.extend(f"- `{item}`" for item in required)
    lines.extend(["", f"Next command: `{packet.get('next_command')}`", ""])
    return "\n".join(lines)
