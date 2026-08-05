"""Summary helpers for changed-scope reports."""

from __future__ import annotations

from typing import Any

from repo_support import repo_changed
from repo_support import repo_command_metrics
from repo_support import repo_cost_policy
from repo_support import repo_fingerprint
from repo_support import repo_review_progress
from repo_support.repo_navigation_status import navigation_context_trace

FINISH_COMMAND = "python -B .agents/manage.py finish --summary --compact --format json"


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _validation_progress_matches_current_input(
    validation_progress: dict[str, Any],
    input_fingerprint: dict[str, Any],
    required_check_ids: list[str],
    *,
    profile: str = "changed",
) -> bool:
    return repo_command_metrics.validation_progress_covers_input(
        validation_progress,
        input_fingerprint,
        required_check_ids=required_check_ids,
        profile=profile,
    )


def summarize_check_changed_payload(payload: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    failed_checks = [
        {
            "name": item.get("name", ""),
            "elapsed_ms": item.get("elapsed_ms", 0),
            "output_summary": item.get("output_summary", {}),
        }
        for item in checks
        if isinstance(item, dict) and not item.get("ok")
    ]
    changed_files = payload.get("changed_files") if isinstance(payload.get("changed_files"), list) else []
    validation_plan = payload.get("validation_plan") if isinstance(payload.get("validation_plan"), list) else []
    required = [item for item in validation_plan if isinstance(item, dict) and item.get("required") is not False]
    required_check_ids = [
        str(item.get("check_id") or "") for item in required if str(item.get("check_id") or "")
    ]
    proof_hygiene = payload.get("proof_hygiene") if isinstance(payload.get("proof_hygiene"), dict) else {}
    proof_summary = proof_hygiene.get("summary") if isinstance(proof_hygiene.get("summary"), dict) else {}
    portability = payload.get("portable_constraints") if isinstance(payload.get("portable_constraints"), dict) else {}
    portability_summary = portability.get("summary") if isinstance(portability.get("summary"), dict) else {}
    context_guardrails = payload.get("context_guardrails") if isinstance(payload.get("context_guardrails"), dict) else {}
    addition_acceptance = payload.get("addition_acceptance") if isinstance(payload.get("addition_acceptance"), dict) else {}
    addition_summary = addition_acceptance.get("summary") if isinstance(addition_acceptance.get("summary"), dict) else {}
    input_fingerprint = payload.get("input_fingerprint") if isinstance(payload.get("input_fingerprint"), dict) else {}
    review_packet = payload.get("review_packet") if isinstance(payload.get("review_packet"), dict) else {}
    review_plan = repo_review_progress.build_review_plan(review_packet) if review_packet else {}
    review_progress = payload.get("review_progress") if isinstance(payload.get("review_progress"), dict) else {}
    validation_progress = payload.get("validation_progress") if isinstance(payload.get("validation_progress"), dict) else {}
    review_plan_summary = repo_review_progress.summarize_review_plan(review_plan) if review_plan else {}
    if review_progress:
        completed_units = _int_value(review_progress.get("completed_unit_count", 0))
        review_unit_count = _int_value(review_plan_summary.get("review_unit_count", 0))
        pending_units = _int_value(
            review_progress.get(
                "pending_unit_count",
                review_plan_summary.get("pending_unit_count", 0),
            )
        )
        pending_review_units = max(0, review_unit_count - min(completed_units, review_unit_count))
        review_plan_summary.update(
            {
                "status": review_progress.get("status", review_plan_summary.get("status", "unknown")),
                "review_state": review_progress.get(
                    "review_state",
                    review_plan_summary.get("review_state", "unknown"),
                ),
                "completed_unit_count": completed_units,
                "pending_unit_count": pending_units,
                "pending_review_unit_count": pending_review_units,
                "pending_validation_unit_count": max(0, pending_units - pending_review_units),
                "next_pending_command": review_progress.get(
                    "next_pending_command",
                    review_plan_summary.get("next_pending_command", ""),
                ),
                "stale": bool(review_progress.get("stale", False)),
            }
        )
    validation_current = (
        not failed_checks
        and payload.get("status") == "passed"
        and _validation_progress_matches_current_input(
            validation_progress,
            input_fingerprint,
            required_check_ids,
            profile=str(payload.get("profile") or "changed"),
        )
    )
    review_units_complete = (
        not review_plan
        or _int_value(review_plan_summary.get("review_unit_count", 0)) == 0
        or _int_value(review_plan_summary.get("pending_review_unit_count", 0)) == 0
    )
    evidence_complete = validation_current and review_units_complete
    if evidence_complete and review_plan_summary:
        review_plan_summary.update(
            {
                "status": "complete",
                "review_state": "complete",
                "pending_unit_count": 0,
                "pending_review_unit_count": 0,
                "pending_validation_unit_count": 0,
                "validation_status": "satisfied-by-validation-progress",
            }
        )
        review_plan_summary.pop("next_pending_command", None)
        review_plan_summary.pop("resume_rule", None)
    review_packet_status = "complete" if evidence_complete else review_packet.get("status", "unknown")
    review_packet_tokens_over_budget = 0 if evidence_complete else review_packet.get("tokens_over_review_budget", 0)
    review_progress_summary = {
        "status": "complete" if evidence_complete else review_progress.get("status", "unknown"),
        "review_state": "complete" if evidence_complete else review_progress.get("review_state", "unknown"),
        "stale": False if evidence_complete else bool(review_progress.get("stale", False)),
        "completed_unit_count": review_progress.get("completed_unit_count", 0),
        "pending_unit_count": 0 if evidence_complete else review_progress.get("pending_unit_count", 0),
        "current_unit": {} if evidence_complete else review_progress.get("current_unit", {}),
    } if review_progress else {}
    review_cost_report = repo_review_progress.build_review_cost_report(review_packet) if review_packet else {}
    cost_ledger = review_plan.get("cost_ledger") if isinstance(review_plan.get("cost_ledger"), dict) else {}
    if not cost_ledger:
        cost_ledger = repo_cost_policy.compact_review_cost_ledger(review_packet.get("cost_ledger", {}))
    skipped = payload.get("skipped", []) if isinstance(payload.get("skipped"), list) else []
    deep_skipped = any("--deep" in str(item) for item in skipped)
    output: dict[str, Any] = {
        "status": payload.get("status", "unknown"),
        "changed_file_count": len(changed_files),
        "changed_groups": repo_changed.compact_path_groups(changed_files) if changed_files else "",
        "check_count": len(checks),
        "failed_check_count": len(failed_checks),
        "failed_checks": failed_checks,
        "skipped": skipped,
        "docs_count": len(payload.get("docs", []) if isinstance(payload.get("docs"), list) else []),
        "unclassified_count": len(
            payload.get("unclassified", []) if isinstance(payload.get("unclassified"), list) else []
        ),
        "navigation": payload.get("navigation", {}),
        "context_trace": navigation_context_trace(
            payload.get("navigation", {}) if isinstance(payload.get("navigation"), dict) else {}
        ),
        "navigation_auto_refresh": payload.get("navigation_auto_refresh", {}),
        "review_packet": {
            "status": review_packet_status,
            "review_budget_tokens": review_packet.get("review_budget_tokens", 0),
            "changed_diff_estimated_tokens": review_packet.get("changed_diff_estimated_tokens", 0),
            "tokens_over_review_budget": review_packet_tokens_over_budget,
            "owner_counts": review_packet.get("owner_counts", {}),
            "owner_review_packet_count": review_packet.get("owner_review_packet_count", 0),
            "owner_review_packets": repo_changed.summarize_owner_review_packets(
                review_packet.get("owner_review_packets", [])
                if isinstance(review_packet.get("owner_review_packets"), list)
                else []
            )[:8],
            "affected_owner_context": repo_changed.affected_owner_context(review_packet),
            "owner_review_commands": review_packet.get("owner_review_commands", [])[:8],
            "risk_counts": review_packet.get("risk_counts", {}),
            "read_first": review_packet.get("read_first", []),
            "validation_first": repo_changed.summarize_validation_commands(review_packet.get("validation_first", [])),
            "navigation_next_command": review_packet.get("navigation_next_command", ""),
            "cost_ledger": repo_cost_policy.compact_review_cost_ledger(cost_ledger),
            "review_plan_summary": review_plan_summary,
            "review_cost_report": repo_review_progress.summarize_review_cost_report(review_cost_report),
        } if review_packet else {},
        "review_progress": review_progress_summary,
        "proof_hygiene": {
            "status": proof_hygiene.get("status", "unknown"),
            "finding_count": proof_summary.get("finding_count", 0),
            "skipped_count": proof_summary.get("skipped_count", 0),
        } if proof_hygiene else {},
        "portable_constraints": {
            "status": portability.get("status", "unknown"),
            "finding_count": portability_summary.get("finding_count", 0),
            "error_count": portability_summary.get("error_count", 0),
            "warning_count": portability_summary.get("warning_count", 0),
            "findings": portability.get("findings", []),
        } if portability else {},
        "context_guardrails": {
            "status": context_guardrails.get("status", "unknown"),
            "finding_count": context_guardrails.get("finding_count", 0),
            "findings": context_guardrails.get("findings", []),
        } if context_guardrails else {},
        "addition_acceptance": {
            "status": addition_acceptance.get("status", "unknown"),
            "issue_count": addition_summary.get("issue_count", 0),
        } if addition_acceptance else {},
        "input_fingerprint": repo_fingerprint.summarize_input_fingerprint(input_fingerprint)
        if input_fingerprint
        else {},
        "validation_plan_summary": payload.get("validation_plan_summary", {}),
        "validation_progress": validation_progress,
        "timing_summary": payload.get("timing_summary", {}),
        "latency_budget": payload.get("latency_budget", {}),
        "next_command": (
            "fix failed changed-scope checks, then rerun python -B .agents/manage.py check-changed"
            if failed_checks
            else FINISH_COMMAND if evidence_complete else str(required[0].get("command")) if required else FINISH_COMMAND
        ),
        "next_command_reason": (
            "One or more changed-scope checks failed."
            if failed_checks
            else (
                "Changed-scope validation passed and matches current input; finish is the authoritative completion gate."
                if evidence_complete
                else (
                    "Run the first required validation command for the changed files."
                    if required
                    else "Changed-scope checks passed; run finish."
                )
            )
        ),
    }
    if payload.get("next_command_reason") and not evidence_complete:
        output["next_command_reason"] = payload.get("next_command_reason")
    if deep_skipped:
        output["deep_next_command"] = "python -B .agents/manage.py check-changed --deep --summary --compact --format json"
    if compact:
        if evidence_complete:
            output.pop("review_packet", None)
            output.pop("review_progress", None)
        if not failed_checks:
            output.pop("failed_checks", None)
        if not output.get("skipped"):
            output.pop("skipped", None)
        if not output.get("changed_groups"):
            output.pop("changed_groups", None)
        if not output.get("proof_hygiene"):
            output.pop("proof_hygiene", None)
        if not output.get("portable_constraints"):
            output.pop("portable_constraints", None)
        elif output["portable_constraints"].get("status") == "passed":
            output["portable_constraints"].pop("findings", None)
        if not output.get("context_guardrails"):
            output.pop("context_guardrails", None)
        elif output["context_guardrails"].get("status") == "passed":
            output["context_guardrails"].pop("findings", None)
        if not output.get("navigation_auto_refresh") or output["navigation_auto_refresh"].get("status") == "skipped":
            output.pop("navigation_auto_refresh", None)
        if not output.get("addition_acceptance"):
            output.pop("addition_acceptance", None)
        if not output.get("input_fingerprint"):
            output.pop("input_fingerprint", None)
        if not output.get("review_packet") or output["review_packet"].get("status") not in {"over-budget", "complete"}:
            output.pop("review_packet", None)
        elif isinstance(output.get("review_packet"), dict):
            for key in (
                "owner_review_packets",
                "owner_review_commands",
                "read_first",
                "validation_first",
                "owner_counts",
                "risk_counts",
                "navigation_next_command",
            ):
                output["review_packet"].pop(key, None)
        if not output.get("review_progress"):
            output.pop("review_progress", None)
    else:
        output["changed_files"] = changed_files
        output["validation_plan"] = validation_plan
    return repo_command_metrics.attach_output_budget(output, "check-changed")
