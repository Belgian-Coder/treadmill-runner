#!/usr/bin/env python3
"""Compact workflow context-packet public API."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import workflow_manager_common as common
from validation_support import manifests as contract_manifests
from workflow_support.context_budget import (
    apply_token_estimates,
    cap_file_estimates,
    compact_file_estimate,
    context_budget_status,
    context_packet_quality_gate,
    relative_file_token_estimate,
    serialize_context_packet,
)
from workflow_support.context_coordinates import build_coordinate_closet
from workflow_support.context_contract import context_packet_schema, validate_context_packet
from workflow_support.context_documentation import (
    build_documentation_delta,
    documentation_path,
    render_documentation_delta_markdown,
    run_packet_path_values,
)
from workflow_support.context_evidence import (
    build_work_item_summary,
    cap_evidence_handles,
    collect_evidence_handles,
    collect_validation_files,
    command_status_ok,
    compact_command_text,
    extract_scope,
    failed_checks_are_unresolved,
    summarize_commands,
)
from workflow_support.context_instructions import build_instruction_context
from workflow_support.context_markdown import (
    compact_markdown_snippet,
    current_phase_instruction_section,
    first_markdown_section,
    keyed_bullets,
    list_items,
    markdown_section,
    markdown_sections,
    normalize_heading,
    phase_heading_matches,
)
from workflow_support.context_paths import (
    CONTEXT_PACKET_DIR,
    CONTEXT_PACKET_JSON,
    CONTEXT_PACKET_MARKDOWN,
    DOCUMENTATION_DELTA_DIR,
    DOCUMENTATION_DELTA_JSON,
    DOCUMENTATION_DELTA_MARKDOWN,
    TERMINAL_PHASES,
    approx_tokens,
    context_packet_paths,
    context_packet_relative_paths,
    documentation_delta_paths,
    documentation_delta_relative_paths,
    normalize_path_handle,
    read_optional_text,
    unique_list,
)
from workflow_support.context_render import render_context_packet_markdown
from workflow_support.context_sources import resolve_context_sources, source_file_paths
from workflow_support.workers import verify_persisted_runtime_observation, workflow_execution_profile


def estimate_tokens_from_size(byte_count: int) -> int:
    return max(1, (max(byte_count, 0) + 3) // 4) if byte_count else 0


def load_project_cost_policy(root: Path) -> tuple[dict[str, object], str, bool]:
    """Return the canonical grouped v2 policy used by public configuration."""

    document, issues, exists = common.repo_policy.load_project_policy(root)
    if issues:
        return {}, "; ".join(issues), exists
    policy = document.get("cost_policy")
    return (policy if isinstance(policy, dict) else {}), "", exists


def guidance_token_total(root: Path, paths: list[str]) -> tuple[int, list[str]]:
    total = 0
    missing: list[str] = []
    for rel in paths:
        path = root / rel
        if not path.is_file():
            missing.append(rel)
            continue
        total += estimate_tokens_from_size(path.stat().st_size)
    return total, missing


def compact_guidance_savings(report: dict[str, object]) -> dict[str, object]:
    compact = {
        "use_by_default": report.get("use_by_default", False),
        "status": report.get("status", "unknown"),
        "measurable": report.get("measurable", False),
        "complete": report.get("complete", False),
        "budget_tokens": report.get("budget_tokens", 0),
        "budget_source": report.get("budget_source", "default-missing"),
        "budget_issue": report.get("budget_issue", ""),
        "within_absolute_budget": report.get("within_absolute_budget", False),
        "meets_minimum": report.get("meets_minimum", False),
        "min_saved_percent": report.get("min_saved_percent", 0),
        "token_counter": report.get("token_counter", "estimated_utf8_bytes_div_4"),
        "provenance": report.get("provenance", "heuristic_estimate"),
        "scope": report.get("scope", "artifact"),
        "default_guidance_tokens": report.get("default_guidance_tokens", 0),
        "broad_baseline_tokens": report.get("broad_baseline_tokens", 0),
        "saved_tokens_estimated": report.get("saved_tokens_estimated", 0),
        "saved_percent_estimated": report.get("saved_percent_estimated", 0),
    }
    default_missing = report.get("default_missing", [])
    baseline_missing = report.get("baseline_missing", [])
    compact["default_missing_count"] = len(default_missing) if isinstance(default_missing, list) else 0
    compact["baseline_missing_count"] = len(baseline_missing) if isinstance(baseline_missing, list) else 0
    return compact


def guidance_savings_for_context(root: Path) -> dict[str, object]:
    policy, policy_error, policy_exists = load_project_cost_policy(root)
    if policy_error:
        policy = common.repo_policy.default_policy_document()["cost_policy"]
    guidance = policy["guidance"]
    default_paths = list(guidance["default"]["files"])
    baseline_paths = list(guidance["baseline"]["files"])
    minimum_percent = int(guidance["minimum_saved_percent"])
    budget_tokens = int(guidance["default"]["budget_tokens"])
    budget_source = "fallback-invalid" if policy_error else "configured" if policy_exists else "default-missing"
    default_tokens, default_missing = guidance_token_total(root, default_paths)
    baseline_tokens, baseline_missing = guidance_token_total(root, baseline_paths)
    raw_saved = baseline_tokens - default_tokens
    saved_percent = round((raw_saved / baseline_tokens) * 100, 2) if baseline_tokens else 0.0
    complete = not default_missing and not baseline_missing and bool(default_paths) and bool(baseline_paths)
    within_absolute_budget = default_tokens <= budget_tokens
    measurable = complete and baseline_tokens > 0
    better = measurable and raw_saved > 0
    meets_minimum = better and saved_percent >= minimum_percent
    if not complete:
        status = "incomplete"
    elif not within_absolute_budget:
        status = "over-budget"
    elif not measurable:
        status = "unavailable"
    elif meets_minimum:
        status = "measurably-better"
    elif better:
        status = "better-below-threshold"
    else:
        status = "not-better"
    report = compact_guidance_savings(
        {
            "use_by_default": True,
            "status": status,
            "measurable": measurable,
            "complete": complete,
            "budget_tokens": budget_tokens,
            "budget_source": budget_source,
            "budget_issue": policy_error,
            "within_absolute_budget": within_absolute_budget,
            "meets_minimum": meets_minimum,
            "min_saved_percent": minimum_percent,
            "token_counter": "estimated_utf8_bytes_div_4",
            "provenance": "heuristic_estimate",
            "scope": "artifact",
            "default_guidance_tokens": default_tokens,
            "broad_baseline_tokens": baseline_tokens,
            "saved_tokens_estimated": max(raw_saved, 0),
            "saved_percent_estimated": saved_percent,
            "default_missing": default_missing,
            "baseline_missing": baseline_missing,
        }
    )
    if policy_error:
        report["policy_note"] = policy_error
    return report


def compact_v3_execution_profile(profile: dict[str, object]) -> dict[str, object]:
    """Keep the executable three-axis decision without duplicating catalog metadata."""

    if "declared_model_provider" not in profile:
        return dict(profile)
    profile_id = str(profile.get("profile_id", "")).strip()
    compact: dict[str, object] = {
        key: profile.get(key)
        for key in (
            "profile_id",
            "route_set",
            "prompt_adapter",
            "context_budget",
            "context_budget_ref",
            "budget_tokens",
            "budget_source",
            "effective_context_tokens",
            "remaining_margin_tokens",
            "within_budget",
            "tool_policy",
            "expected_output",
            "validation_gate",
            "endpoint_status",
            "capability_status",
            "effective_execution_mode",
        )
    }
    compact["instruction_header"] = [
        f"Use profile {profile_id}; preserve its contract; attest endpoint or record fallback."
    ]
    reason_limit = common.project_policy_int("limits.workflow.context_reason_chars")
    reason = " ".join(str(profile.get("fallback_reason", "")).split())
    compact["fallback_reason"] = (
        reason[: max(1, reason_limit - 3)].rstrip() + "..." if len(reason) > reason_limit else reason
    )
    overlay = profile.get("prompt_overlay")
    if isinstance(overlay, dict):
        compact["prompt_overlay"] = {
            key: overlay.get(key)
            for key in ("id", "version", "delivery_directive")
            if key in overlay
        }
        directive = " ".join(str(compact["prompt_overlay"].get("delivery_directive", "")).split())
        if directive:
            compact["prompt_overlay"]["delivery_directive"] = (
                directive[: max(1, reason_limit - 3)].rstrip() + "..."
                if len(directive) > reason_limit
                else directive
            )
    surface = profile.get("surface_adapter")
    if isinstance(surface, dict):
        compact_surface = {
            key: surface.get(key)
            for key in (
                "id",
                "host_surface",
                "available_orchestration_mode",
                "orchestration_mode",
                "effective_orchestration_mode",
                "continuation_mode",
                "cache_mode",
            )
        }
        enabled = surface.get("enabled_optimizations")
        if isinstance(enabled, dict) and enabled:
            compact_surface["enabled_optimizations"] = dict(enabled)
        compact["surface_adapter"] = compact_surface
    for field in (
        "observed_host_surface",
        "observed_model_provider",
        "observed_model",
        "observed_deliberation",
        "host_observation_source",
        "model_observation_source",
    ):
        if str(profile.get(field, "")).strip():
            compact[field] = profile[field]
    if profile.get("observed_capabilities"):
        compact["observed_capabilities"] = profile["observed_capabilities"]
    if (
        str(profile.get("observed_host_surface", "")).strip()
        or str(profile.get("observed_model", "")).strip()
    ) and str(profile.get("observation_evidence_path", "")).strip():
        compact["observation_evidence_path"] = profile["observation_evidence_path"]
    return compact


def build_context_packet(root: Path, workflow_name: str, run_dir: Path, run_packet: dict[str, object]) -> dict[str, object]:
    module_dir = root / "automations" / workflow_name
    manifest, _manifest_error = common.read_json_file(module_dir / "module.json")
    if not isinstance(manifest, dict):
        manifest = {}
    context_declared = isinstance(manifest.get("context"), dict)
    context_spec = (
        manifest.get("context") if isinstance(manifest.get("context"), dict) else {}
    )
    context_is_conventional = context_spec == (
        contract_manifests.module_contract_v3.conventional_context(workflow_name)
    )
    context_sources, context_source_issues = resolve_context_sources(
        root,
        workflow_name,
        run_dir,
        context_spec,
    )
    ticket_text = ""
    scope_categories = ("scope-required", "work-item")
    for load_policy in ("must_open", "handle_only"):
        for category in scope_categories:
            for ticket_path in source_file_paths(
                context_sources,
                load_policy=load_policy,
                critical_category=category,
            ):
                ticket_text = read_optional_text(root / ticket_path)
                if ticket_text:
                    break
            if ticket_text:
                break
        if ticket_text:
            break
    declared_sources = (
        context_spec.get("sources")
        if isinstance(context_spec.get("sources"), list)
        else []
    )
    scope_required = any(
        isinstance(source, dict)
        and source.get("critical_category") == "scope-required"
        for source in declared_sources
    )
    scope = extract_scope(
        ticket_text,
        run_packet,
        missing_scope_default=(
            "not applicable to this workflow setup/validation run"
            if scope_required
            else ""
        ),
    )
    checks = run_packet.get("checks") if isinstance(run_packet.get("checks"), dict) else {}
    validation_files = collect_validation_files(root, run_dir)
    evidence_handles = collect_evidence_handles(root, run_dir, run_packet)
    evidence_handles = unique_list(
        [
            *evidence_handles,
            *source_file_paths(context_sources, load_policy="handle_only"),
        ]
    )
    instruction_context = build_instruction_context(root, module_dir, run_packet)
    documentation_delta = build_documentation_delta(root, run_dir, run_packet)
    documentation_json = documentation_delta.get("paths", {}).get("json", "") if isinstance(documentation_delta.get("paths"), dict) else ""
    if documentation_json:
        evidence_handles = unique_list([*evidence_handles, str(documentation_json)])
    evidence_handles = cap_evidence_handles(evidence_handles)
    context_json, context_markdown = context_packet_relative_paths(root, run_dir)
    required_next_context = [
        context_json,
        *source_file_paths(context_sources, load_policy="must_open"),
    ]
    required_next_context = unique_list(required_next_context)
    issues: list[str] = list(context_source_issues)
    if scope_required and not scope["out_of_scope"]:
        issues.append("out-of-scope is not recorded")
    documentation_issues = documentation_delta.get("issues") if isinstance(documentation_delta.get("issues"), list) else []
    issues.extend(f"documentation delta: {item}" for item in documentation_issues)
    if instruction_context.get("requires_full_instructions"):
        instruction_path = str(instruction_context.get("path", ""))
        if instruction_path:
            required_next_context = unique_list([*required_next_context, instruction_path])
    unsupported_claims = run_packet.get("unsupported_claims")
    advisories: list[str] = []
    if isinstance(unsupported_claims, list) and unsupported_claims:
        advisories.append("unsupported claims recorded and preserved for explicit disclosure")
    if failed_checks_are_unresolved(run_packet):
        issues.append("failed checks recorded")
    raw_estimate_by_path = {
        str(item.get("path", "")): dict(item)
        for source in context_sources
        for item in (
            source.get("files") if isinstance(source.get("files"), list) else []
        )
        if isinstance(item, dict) and str(item.get("path", "")).strip()
    }
    for item in validation_files:
        raw_estimate_by_path.setdefault(str(item.get("path", "")), dict(item))
    raw_estimates = [raw_estimate_by_path[path] for path in sorted(raw_estimate_by_path)]
    raw_tokens = sum(int(item["tokens_estimated"]) for item in raw_estimates)
    validation_tokens = sum(int(item["tokens_estimated"]) for item in validation_files)
    cost_policy, _cost_policy_error, _cost_policy_exists = load_project_cost_policy(root)
    persisted_observation = (
        run_packet.get("runtime_observation")
        if isinstance(run_packet.get("runtime_observation"), dict)
        else None
    )
    runtime_observation, runtime_observation_verification_issues = verify_persisted_runtime_observation(
        root,
        workflow_name,
        run_dir.name,
        str(run_packet.get("current_phase") or ""),
        persisted_observation,
    )
    execution_profile = compact_v3_execution_profile(
        workflow_execution_profile(
            manifest,
            str(run_packet.get("current_phase") or ""),
            cost_policy=cost_policy,
            runtime_observation=runtime_observation,
            runtime_observation_verification_issues=runtime_observation_verification_issues,
            workflow=workflow_name,
            run_id=run_dir.name,
        )
    )
    guidance_savings = guidance_savings_for_context(root)
    coordinate_closet = build_coordinate_closet(
        root,
        run_dir,
        run_packet,
        required_next_context=required_next_context,
        evidence_handles=evidence_handles,
        scope=scope,
        preserve_source_paths=source_file_paths(
            context_sources,
            preserve_coordinates=True,
        ),
    )
    packet_preview = {
        "workflow": workflow_name,
        "run_id": run_packet.get("run_id") or run_dir.name,
        "status": run_packet.get("status", "unknown"),
        "current_phase": run_packet.get("current_phase", ""),
        "next_action": run_packet.get("next_action", ""),
        "execution_profile": execution_profile,
        "scope": scope,
        "commands": summarize_commands(run_packet, root=root, run_dir=run_dir),
        "evidence_handles": evidence_handles,
    }
    if coordinate_closet.get("status") == "present":
        packet_preview["coordinate_closet"] = coordinate_closet
    compact_packet_tokens = approx_tokens(json.dumps(packet_preview, sort_keys=True))
    phase = run_packet.get("phase") if isinstance(run_packet.get("phase"), dict) else {}
    packet = {
        "schema_version": 3,
        "tool": "workflow-manager.context-packet",
        "ok": not issues,
        "status": "ok" if not issues else "needs-attention",
        "workflow": workflow_name,
        "run_id": run_packet.get("run_id") or run_dir.name,
        "run_path": common.relative(root, run_dir),
        "current_phase": run_packet.get("current_phase", ""),
        "phase_status": phase.get("status", run_packet.get("status", "unknown")),
        "next_action": run_packet.get("next_action", ""),
        "execution_profile": execution_profile,
        "instruction_context": instruction_context,
        "scope": scope,
        "work_item_summary": build_work_item_summary(root, context_sources),
        "documentation_delta": documentation_delta,
        "validation_summary": {
            "external_validation_status": run_packet.get("external_validation_status", "not-recorded"),
            "commands": summarize_commands(run_packet, root=root, run_dir=run_dir),
            "skipped": run_packet.get("skipped", checks.get("skipped", [])),
            "blocked": run_packet.get("blocked", checks.get("blocked", [])),
            "failed": run_packet.get("failed", checks.get("failed", [])),
            "validation_file_count": len(validation_files),
            "validation_files": cap_file_estimates(
                validation_files,
                limit=common.project_policy_int(
                    "limits.workflow.context_packet_validation_file_limit", start=root
                ),
                omitted_label="validation file",
            ),
        },
        "guidance_savings": guidance_savings,
        "decisions": run_packet.get("decisions", []) if isinstance(run_packet.get("decisions"), list) else [],
        "evidence_handles": evidence_handles,
        "required_next_context": required_next_context,
        "context_packet_paths": {"json": context_json, "markdown": context_markdown},
        "unsupported_claims": unsupported_claims if isinstance(unsupported_claims, list) else [],
        "advisories": advisories,
        "issues": issues,
        "next_command": f"python -B .agents/manage.py workflow resume --name {workflow_name} --run-id {run_dir.name}",
    }
    if coordinate_closet.get("status") == "present":
        packet["coordinate_closet"] = coordinate_closet
    packet_context_sources = [
        source
        for source in context_sources
        if source.get("load_policy") == "must_open"
    ]
    if context_declared and not context_is_conventional and packet_context_sources:
        packet["context_sources"] = packet_context_sources
    apply_token_estimates(
        packet,
        raw_tokens=raw_tokens,
        validation_tokens=validation_tokens,
        compact_packet_tokens=compact_packet_tokens,
        raw_estimates=raw_estimates,
        context_sources=context_sources,
        context_budgets=context_spec.get("budgets", {}),
    )
    return packet


def write_context_packet(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    run_packet: dict[str, object],
    *,
    write: bool = False,
) -> dict[str, object]:
    packet = build_context_packet(root, workflow_name, run_dir, run_packet)
    if write:
        json_path, markdown_path = context_packet_paths(run_dir)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(serialize_context_packet(packet), encoding="utf-8", newline="\n")
        markdown_path.write_text(render_context_packet_markdown(packet), encoding="utf-8", newline="\n")
        documentation = packet.get("documentation_delta") if isinstance(packet.get("documentation_delta"), dict) else {}
        documentation_json, documentation_markdown = documentation_delta_paths(run_dir)
        documentation_json.parent.mkdir(parents=True, exist_ok=True)
        documentation_json.write_text(
            json.dumps(documentation, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        documentation_markdown.write_text(
            render_documentation_delta_markdown(documentation),
            encoding="utf-8",
            newline="\n",
        )
        packet["written"] = [
            common.relative(root, json_path),
            common.relative(root, markdown_path),
            common.relative(root, documentation_json),
            common.relative(root, documentation_markdown),
        ]
    else:
        packet["written"] = []
    return packet
