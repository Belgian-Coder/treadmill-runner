"""Renderers for repo daily/status command reports."""

from __future__ import annotations

import json
from typing import Any


def render_review_progress(report: dict[str, Any]) -> str:
    lines = [
        "# Review Progress",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Review state: {report.get('review_state', 'unknown')}",
        f"- Completed units: {report.get('completed_unit_count', 0)}",
        f"- Pending units: {report.get('pending_unit_count', 0)}",
        f"- State path: `{report.get('state_path', '')}`",
        f"- Next command: `{report.get('next_pending_command', '')}`",
    ]
    if report.get("stale"):
        lines.append("- Stale: true")
    if report.get("issue"):
        lines.append(f"- Issue: {report.get('issue')}")
    current = report.get("current_unit") if isinstance(report.get("current_unit"), dict) else {}
    if current:
        lines.extend(["", "## Current Unit", ""])
        lines.append(f"- `{current.get('id')}`")
        if current.get("path"):
            lines.append(f"- Path: `{current.get('path')}`")
        if current.get("hunk"):
            lines.append(f"- Hunk: `{current.get('hunk')}`")
        lines.append(f"- Estimated tokens: {current.get('estimated_changed_tokens', 0)}")
    lines.extend(["", f"Rule: {report.get('resume_rule', '')}", ""])
    return "\n".join(lines)


def render_next_action(report: dict[str, Any]) -> str:
    lines = [
        "# Next Action",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Next command: `{report.get('next_command', '')}`",
        f"- Why: {report.get('why', '')}",
        "",
        "## Required Context",
        "",
    ]
    for path in report.get("required_context", []) if isinstance(report.get("required_context"), list) else []:
        lines.append(f"- `{path}`")
    if not report.get("required_context"):
        lines.append("- None beyond command output.")
    lines.extend(["", f"- Validation after: `{report.get('validation_after', '')}`", f"- Stop condition: {report.get('stop_condition', '')}", ""])
    return "\n".join(lines)


def render_review_loop(report: dict[str, Any]) -> str:
    lines = [
        "# Review Loop",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Executed units: {report.get('executed_unit_count', 0)} / {report.get('max_units', 0)}",
        f"- Planned units: {report.get('planned_unit_count', 0)}",
        f"- Next command: `{report.get('next_command', '')}`",
        "",
        "## Iterations",
        "",
    ]
    for item in report.get("iterations", []) if isinstance(report.get("iterations"), list) else []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('index')}: `{item.get('status')}` `{item.get('next_command', '')}`")
    lines.extend(["", f"Boundary: {report.get('boundary', '')}", ""])
    return "\n".join(lines)


def render_review_next(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Review Next",
            "",
            f"- Status: {report.get('status', 'unknown')}",
            f"- Executed: {'yes' if report.get('executed') else 'no'}",
            f"- Review command: `{report.get('review_command', '')}`",
            f"- Next command: `{report.get('next_command', '')}`",
            "",
            f"Boundary: {report.get('boundary', '')}",
            "",
        ]
    )


def render_change_ledger(report: dict[str, Any]) -> str:
    lines = [
        "# Change Ledger",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Changed files: {report.get('changed_file_count', 0)}",
        f"- Dominant reason: {report.get('dominant_reason', '')}",
        "",
        "## Owners",
        "",
    ]
    acceptance = report.get("acceptance") if isinstance(report.get("acceptance"), dict) else {}
    if acceptance:
        lines.insert(5, f"- Acceptance: {acceptance.get('status', 'unknown')}")
    for item in report.get("owner_groups", []) if isinstance(report.get("owner_groups"), list) else []:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- `{item.get('owner', '')}`: {item.get('changed_file_count', 0)} file(s), "
            f"{item.get('reason', '')}; {item.get('acceptance_status', 'unknown')}"
        )
    lines.extend(["", f"Boundary: {report.get('boundary', '')}", ""])
    return "\n".join(lines)


def render_latest_evidence(report: dict[str, Any]) -> str:
    lines = ["# Latest Evidence", ""]
    if report.get("latest_validation"):
        lines.append(f"- Last failed validation: `{report['latest_validation']}`")
    else:
        lines.append("- Last failed validation: none")
    lines.extend(["", "## Workflow Runs", ""])
    for item in report.get("workflow_runs", []):
        lines.append(f"- `{item.get('workflow')}` `{item.get('run_id')}`: {item.get('status')} - `{item.get('path')}`")
    if not report.get("workflow_runs"):
        lines.append("- None.")
    lines.extend(["", "## Benchmarks", ""])
    for item in report.get("benchmarks", []):
        lines.append(f"- `{item.get('run')}`: {item.get('status') or item.get('ok')} - `{item.get('path')}`")
    if not report.get("benchmarks"):
        lines.append("- None.")
    lines.extend(["", "## Document Evidence", ""])
    for item in report.get("document_evidence", []):
        lines.append(f"- `{item.get('path')}`")
    if not report.get("document_evidence"):
        lines.append("- None.")
    highlighted = report.get("open_latest") if isinstance(report.get("open_latest"), dict) else {}
    if highlighted:
        lines.extend(["", "## Highlighted Latest", ""])
        for label in ("workflow_run", "benchmark", "document_evidence", "local_ai_report"):
            item = highlighted.get(label)
            if isinstance(item, dict) and item:
                lines.append(f"- {label.replace('_', ' ')}: `{item.get('path') or item.get('run_id') or item.get('run')}`")
    lines.extend(["", f"Next command: `{report.get('next_command')}`", ""])
    return "\n".join(lines)


def render_dashboard(report: dict[str, Any]) -> str:
    dirty = report.get("dirty_state", {}) if isinstance(report.get("dirty_state"), dict) else {}
    lines = ["# Daily Dashboard", ""]
    if report.get("plain_status"):
        lines.append(f"- Status: {report.get('plain_status')}")
    if report.get("watch_once"):
        lines.append("- Refreshed once after command completion.")
    lines.append(f"- Mode: {report.get('mode', 'fast')}")
    lines.append(f"- Elapsed: {report.get('total_elapsed_ms', 0)} ms")
    lines.append(f"- Branch: `{report.get('branch') or 'unknown'}`")
    lines.append(f"- Git state: {dirty.get('status', 'unknown')}")
    lines.append(f"- Changed files: {report.get('changed_file_count', 0)}")
    if report.get("changed_groups"):
        lines.append(f"- Changed groups: {report.get('changed_groups')}")
    github_validation = report.get("github_validation", {}) if isinstance(report.get("github_validation"), dict) else {}
    if github_validation:
        automatic = ", ".join(str(item) for item in github_validation.get("automatic_triggers", []) or [])
        trigger_note = f"; automatic: {automatic}" if automatic else ""
        lines.append(f"- GitHub validation: {github_validation.get('status')}{trigger_note}")
    context = report.get("context_budget", {}) if isinstance(report.get("context_budget"), dict) else {}
    if context:
        lines.append(f"- Context estimate: {context.get('estimated_low_context_tokens', 0)} tokens across low-context entry docs")
    navigation = report.get("navigation", {}) if isinstance(report.get("navigation"), dict) else {}
    if navigation:
        lines.append(f"- Navigation maps: {navigation.get('status', 'unknown')} ({navigation.get('summary', '')})")
        if navigation.get("read_first"):
            lines.append(f"- Source orientation: `{navigation.get('read_first')}`")
    router = report.get("validation_router", {}) if isinstance(report.get("validation_router"), dict) else {}
    if router and router.get("status") != "no-changes":
        summary = router.get("summary") if isinstance(router.get("summary"), dict) else {}
        owners = summary.get("owners") if isinstance(summary.get("owners"), dict) else {}
        owner_text = ", ".join(f"{owner}={count}" for owner, count in sorted(owners.items())) or "none"
        lines.append(
            f"- Validation router: {summary.get('required_count', 0)} required, "
            f"{summary.get('optional_count', 0)} optional ({owner_text})"
        )
        if router.get("next_command"):
            lines.append(f"- First validation command: `{router.get('next_command')}`")
    lines.extend(["", "## Checks", ""])
    for item in report.get("checks", []):
        suffix = " advisory" if item.get("advisory") else ""
        lines.append(f"- {item.get('name')}: {'ok' if item.get('ok') else 'check'}{suffix}")
    generated = [item for item in report.get("generated_checks", []) if isinstance(item, dict) and not item.get("ok")]
    if generated:
        lines.extend(["", "## Generated Artifacts", ""])
        for item in generated:
            lines.append(f"- {item.get('name')}: {item.get('message') or 'stale'}")
        lines.append("- Fix: `python -B .agents/manage.py sync`")
    trust = report.get("command_trust", {}) if isinstance(report.get("command_trust"), dict) else {}
    if trust.get("status") == "demote-advisory":
        lines.extend(["", "## Advisory Trust", ""])
        lines.append("- One or more advisory sections were slow or failed; use deterministic checks first.")
    evidence = report.get("evidence", {}) if isinstance(report.get("evidence"), dict) else {}
    workflows = evidence.get("workflow_runs", []) if isinstance(evidence.get("workflow_runs"), list) else []
    if workflows:
        lines.extend(["", "## Latest Workflow Runs", ""])
        for item in workflows[:3]:
            lines.append(f"- `{item.get('workflow')}` `{item.get('run_id')}`: {item.get('status')}")
    if report.get("why_this_took_long"):
        lines.extend(["", "## Slow Sections", ""])
        for item in report.get("why_this_took_long", []):
            lines.append(f"- {item}")
    if report.get("fix_suggestions"):
        lines.extend(["", "## Fix Suggestions", ""])
        for item in report.get("fix_suggestions", []):
            lines.append(f"- `{item}`")
    if router and router.get("commands"):
        lines.extend(["", "## Validation Router", ""])
        for item in router.get("commands", [])[:5]:
            if isinstance(item, dict):
                required = "required" if item.get("required", True) else "optional"
                lines.append(f"- `{item.get('owner')}` {required}: `{item.get('command')}`")
    audit = report.get("capability_audit") if isinstance(report.get("capability_audit"), dict) else {}
    if audit:
        summary = audit.get("summary") if isinstance(audit.get("summary"), dict) else {}
        lines.extend(["", "## Capability Audit", ""])
        lines.append(f"- Completion supported: {'yes' if audit.get('completion_supported') else 'no'}")
        lines.append(
            f"- Requirements: {summary.get('proved', 0)} proved, "
            f"{summary.get('partial', 0)} partial, {summary.get('missing', 0)} missing"
        )
        for item in audit.get("requirements", []):
            if isinstance(item, dict) and item.get("status") != "proved":
                lines.append(f"- `{item.get('id')}`: {item.get('status')}")
    lines.extend(["", f"Next command: `{report.get('next_command')}`", ""])
    return "\n".join(lines)


def render_what_now(report: dict[str, Any]) -> str:
    lines = ["# What Now", ""]
    if report.get("source"):
        lines.append(f"- Source: `{report.get('source')}`")
    command_result = report.get("command_result") if isinstance(report.get("command_result"), dict) else {}
    if command_result:
        lines.append(f"- Command: `{command_result.get('command')}`")
        lines.append(f"- Exit code: {command_result.get('status')}")
        if command_result.get("raw_output_path"):
            lines.append(f"- Raw output: `{command_result.get('raw_output_path')}`")
    lines.append(f"- First failing fact: {report.get('first_failing_fact')}")
    lines.append(f"- Failure type: `{report.get('failure_type')}`")
    lines.append(f"- Likely owner: `{report.get('likely_owner')}`")
    if report.get("owner_rationale"):
        lines.append(f"- Owner rationale: {report.get('owner_rationale')}")
    lines.append(f"- Next command: `{report.get('next_command')}`")
    lines.append(f"- Deterministic fallback: {report.get('deterministic_fallback')}")
    if report.get("optional_local_ai_command"):
        lines.append(f"- Optional local AI: `{report.get('optional_local_ai_command')}`")
    warnings = report.get("warnings") if isinstance(report.get("warnings"), list) else []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines) + "\n"


def render_resume_work(report: dict[str, Any]) -> str:
    dirty = report.get("dirty_state", {}) if isinstance(report.get("dirty_state"), dict) else {}
    lines = ["# Resume Work", ""]
    lines.append(f"- Branch: `{report.get('branch') or 'unknown'}`")
    lines.append(f"- Git state: {dirty.get('status', 'unknown')}")
    lines.append(f"- Changed files: {len(report.get('changed_files', []))}")
    if report.get("changed_groups"):
        lines.append(f"- Changed groups: {report.get('changed_groups')}")
    evidence = report.get("evidence", {}) if isinstance(report.get("evidence"), dict) else {}
    if evidence.get("latest_validation"):
        lines.append(f"- Last failed validation: `{evidence.get('latest_validation')}`")
    runs = evidence.get("workflow_runs", []) if isinstance(evidence.get("workflow_runs"), list) else []
    if runs:
        latest = runs[0]
        lines.append(f"- Latest workflow run: `{latest.get('workflow')}` `{latest.get('run_id')}` ({latest.get('status')})")
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"


def render_finish_work(report: dict[str, Any]) -> str:
    lines = ["# Finish", "", f"- Status: {report.get('status')}", "", "## Checks", ""]
    if "completion_supported" in report:
        lines.insert(3, f"- Completion supported: {bool(report.get('completion_supported'))}")
        claim = report.get("claim_receipt") if isinstance(report.get("claim_receipt"), dict) else {}
        lines.insert(4, f"- Claim receipt: {claim.get('status', 'missing')}")
        missing = report.get("missing_evidence") if isinstance(report.get("missing_evidence"), list) else []
        if missing:
            lines.insert(5, f"- Missing evidence: {', '.join(str(item) for item in missing)}")
    finish_readiness = report.get("finish_readiness", {}) if isinstance(report.get("finish_readiness"), dict) else {}
    if finish_readiness:
        lines.insert(3, f"- Finish readiness: {finish_readiness.get('status', 'unknown')}")
        if finish_readiness.get("next_command"):
            lines.insert(4, f"- Readiness next command: `{finish_readiness.get('next_command')}`")
        for reason in finish_readiness.get("reasons", []) if isinstance(finish_readiness.get("reasons"), list) else []:
            lines.insert(5, f"- Readiness reason: {reason}")
    for item in report.get("checks", []):
        lines.append(f"- `{item.get('command')}`: {'ok' if item.get('ok') else 'failed'}")
        if not item.get("ok") and item.get("output_tail"):
            lines.append("  " + str(item.get("output_tail")).strip().replace("\n", "\n  ")[-1200:])
        if not item.get("ok") and item.get("raw_output_path"):
            lines.append(f"  Raw output: `{item.get('raw_output_path')}`")
    run_indexes = report.get("workflow_run_indexes", {}) if isinstance(report.get("workflow_run_indexes"), dict) else {}
    if run_indexes:
        workflows = ", ".join(str(item) for item in run_indexes.get("workflows", []) or [])
        workflow_note = f" ({workflows})" if workflows else ""
        lines.append(f"- Workflow run indexes: {run_indexes.get('checked_count', 0)} checked{workflow_note}")
    workflow_eval = report.get("workflow_eval", {}) if isinstance(report.get("workflow_eval"), dict) else {}
    if workflow_eval and workflow_eval.get("status") != "skipped":
        lines.append(
            f"- Workflow eval suites: {workflow_eval.get('suites', 0)} checked, "
            f"{workflow_eval.get('passed', 0)}/{workflow_eval.get('cases', 0)} cases"
        )
    evidence_refs = report.get("workflow_evidence_references", {})
    if isinstance(evidence_refs, dict) and evidence_refs.get("status") != "skipped":
        lines.append(
            f"- Workflow evidence references: {evidence_refs.get('checked_count', 0)} checked, "
            f"{evidence_refs.get('missing_count', 0)} missing"
        )
    out_of_scope = report.get("story_bug_out_of_scope_templates", {})
    if isinstance(out_of_scope, dict) and out_of_scope.get("status") not in {"skipped", "not-applicable"}:
        lines.append(
            f"- Story/bug out-of-scope templates: {out_of_scope.get('checked_count', 0)} checked, "
            f"{out_of_scope.get('missing_count', 0)} missing"
        )
    navigation = report.get("navigation", {}) if isinstance(report.get("navigation"), dict) else {}
    if navigation:
        lines.append(
            f"- Navigation: {navigation.get('status', 'unknown')} "
            f"(stale outputs={navigation.get('stale_output_count', 0)})"
        )
        if navigation.get("read_first"):
            lines.append(f"  Read first: `{navigation.get('read_first')}`")
        if navigation.get("read_only_next_step"):
            lines.append(f"  Read-only: {navigation.get('read_only_next_step')}")
        if navigation.get("next_command") and navigation.get("status") in {"stale", "missing", "blocked"}:
            lines.append(f"  Next: `{navigation.get('next_command')}`")
    navigation_refresh = report.get("navigation_auto_refresh", {}) if isinstance(report.get("navigation_auto_refresh"), dict) else {}
    if navigation_refresh and navigation_refresh.get("status") not in {"skipped", "skipped-fresh"}:
        lines.append(
            f"- Navigation auto-refresh: {navigation_refresh.get('status', 'unknown')} "
            f"({navigation_refresh.get('summary', '')})"
        )
    review_packet = report.get("review_packet", {}) if isinstance(report.get("review_packet"), dict) else {}
    if review_packet and review_packet.get("status") == "over-budget":
        lines.append(
            f"- Review packet: over-budget "
            f"({review_packet.get('changed_diff_estimated_tokens', 0)} > "
            f"{review_packet.get('review_budget_tokens', 0)} tokens; "
            f"{review_packet.get('owner_review_packet_count', 0)} owner slices; "
            f"{review_packet.get('owner_review_subpacket_count', 0)} path subpackets; "
            f"{review_packet.get('owner_review_hunk_count', 0)} hunk packets)"
        )
        cost_ledger = review_packet.get("cost_ledger") if isinstance(review_packet.get("cost_ledger"), dict) else {}
        if cost_ledger:
            lines.append(
                f"  Cost ledger: largest owner packet "
                f"{cost_ledger.get('largest_owner_packet_estimated_tokens', 0)} tokens; "
                f"largest path subpacket "
                f"{cost_ledger.get('largest_owner_subpacket_estimated_tokens', 0)} tokens; "
                f"largest hunk packet "
                f"{cost_ledger.get('largest_owner_hunk_estimated_tokens', 0)} tokens; "
                f"single-agent saved estimate "
                f"{cost_ledger.get('single_agent_saved_tokens_vs_raw_estimated', 0)} tokens "
                f"({cost_ledger.get('single_agent_saved_percent_vs_raw_estimated', 0.0)}%)."
            )
        commands = review_packet.get("owner_review_commands") if isinstance(review_packet.get("owner_review_commands"), list) else []
        for command in commands[:3]:
            lines.append(f"  - `{command}`")
    budget_hotspots = report.get("budget_hotspots", {})
    if isinstance(budget_hotspots, dict) and budget_hotspots:
        delta = budget_hotspots.get("delta", {}) if isinstance(budget_hotspots.get("delta"), dict) else {}
        summary = delta.get("summary", {}) if isinstance(delta.get("summary"), dict) else {}
        lines.append(
            f"- Budget hotspots: {budget_hotspots.get('status', 'unknown')}; "
            f"total {int(summary.get('total_text_words', 0) or 0):+}, "
            f"tool {int(summary.get('tool_load_words', 0) or 0):+}"
        )
        for item in budget_hotspots.get("top", []) if isinstance(budget_hotspots.get("top"), list) else []:
            if isinstance(item, dict):
                lines.append(
                    f"  - `{item.get('name')}`: {item.get('total_text_words', 0)} words "
                    f"(largest `{item.get('largest_file', '')}`)"
                )
    budget_gate = report.get("budget_gate", {})
    if isinstance(budget_gate, dict) and budget_gate.get("status") != "skipped":
        delta = budget_gate.get("delta", {}) if isinstance(budget_gate.get("delta"), dict) else {}
        summary = delta.get("summary", {}) if isinstance(delta.get("summary"), dict) else {}
        lines.append(
            f"- Budget gate: {budget_gate.get('status')} ({budget_gate.get('intent', '')}); "
            f"total {int(summary.get('total_text_words', 0) or 0):+}, "
            f"tool {int(summary.get('tool_load_words', 0) or 0):+}"
        )
        for issue in budget_gate.get("issues", []) if isinstance(budget_gate.get("issues"), list) else []:
            lines.append(f"  - {issue}")
    metrics = report.get("check_metrics", {}) if isinstance(report.get("check_metrics"), dict) else {}
    if metrics:
        lines.append(
            f"- Finish check output saved: {metrics.get('output_tail_bytes_saved', 0)} bytes; "
            f"checks: {metrics.get('check_count', 0)}"
        )
    github_validation = report.get("github_validation", {}) if isinstance(report.get("github_validation"), dict) else {}
    if github_validation:
        automatic = ", ".join(str(item) for item in github_validation.get("automatic_triggers", []) or [])
        trigger_note = f"; automatic: {automatic}" if automatic else ""
        lines.extend(["", "## GitHub Validation", ""])
        lines.append(f"- Status: {github_validation.get('status')}{trigger_note}")
    if report.get("advisories"):
        lines.extend(["", "## Advisories", ""])
        lines.extend(f"- {item}" for item in report.get("advisories", []))
    fingerprint = report.get("input_fingerprint") if isinstance(report.get("input_fingerprint"), dict) else {}
    if fingerprint:
        lines.extend(["", "## Input Fingerprint", ""])
        lines.append(f"- Digest: `{fingerprint.get('digest', '')}`")
        lines.append(f"- Changed files: `{fingerprint.get('changed_file_count', 0)}`")
        lines.append(f"- Hashed paths: `{fingerprint.get('hashed_path_count', 0)}`")
        lines.append(f"- Commands: `{fingerprint.get('command_count', 0)}`")
        skipped = fingerprint.get("skipped_fingerprint_paths") if isinstance(fingerprint.get("skipped_fingerprint_paths"), list) else []
        if skipped:
            lines.append(f"- Skipped fingerprint paths: `{len(skipped)}`")
        stale_if = fingerprint.get("stale_if") if isinstance(fingerprint.get("stale_if"), list) else []
        if stale_if:
            lines.append("- Stale if: " + "; ".join(str(item) for item in stale_if))
    if report.get("artifacts"):
        lines.extend(["", "## Commit Packet", ""])
        lines.extend(f"- `{item}`" for item in report.get("artifacts", []))
    lines.extend(["", f"Next command: `{report.get('next_command')}`", ""])
    return "\n".join(lines)


def print_report(report: dict[str, Any], output_format: str, renderer) -> int:
    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(renderer(report), end="")
    return 0 if report.get("ok", True) else 1
