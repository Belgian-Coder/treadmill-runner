"""Compact finish-packet shaping helpers."""

from __future__ import annotations

from typing import Any

from repo_support import repo_command_metrics
from repo_support import repo_cost_policy
from repo_support import repo_qol_readiness
from repo_support import repo_review_progress
from repo_support.repo_fingerprint import summarize_input_fingerprint


def summarize_budget_trend_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    latest = report.get("latest") if isinstance(report.get("latest"), dict) else {}
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.budget-trend"),
        "ok": bool(report.get("ok", True)),
        "status": report.get("status", "unknown"),
        "entry_count": report.get("entry_count", 0),
        "path": report.get("path", ""),
        "latest": {
            "recorded_at": latest.get("recorded_at", ""),
            "source": latest.get("source", ""),
            "changed_diff_estimated_tokens": latest.get("changed_diff_estimated_tokens", 0),
            "next_review_unit_estimated_tokens": latest.get("next_review_unit_estimated_tokens", 0),
            "largest_budget_hotspot_words": latest.get("largest_budget_hotspot_words", 0),
            "finish_elapsed_seconds": latest.get("finish_elapsed_seconds", 0),
        } if latest else {},
        "delta_from_previous": report.get("delta_from_previous", {}),
        "boundary": report.get("boundary", ""),
    }
    if compact and not output["latest"]:
        output.pop("latest", None)
    return output


def compact_finish_cost_ledger(value: Any) -> dict[str, Any]:
    ledger = value if isinstance(value, dict) else {}
    return {
        "status": ledger.get("status", "unknown"),
        "billing_scope": ledger.get("billing_scope", ""),
        "raw_changed_diff_estimated_tokens": ledger.get("raw_changed_diff_estimated_tokens", 0),
        "review_budget_tokens": ledger.get("review_budget_tokens", 0),
        "review_unit_count": ledger.get("review_unit_count", 0),
        "owner_packet_count": ledger.get("owner_packet_count", 0),
        "next_review_unit_estimated_tokens": ledger.get("next_review_unit_estimated_tokens", 0),
        "single_agent_saved_tokens_vs_raw_estimated": ledger.get("single_agent_saved_tokens_vs_raw_estimated", 0),
        "release_gate": ledger.get("release_gate", ""),
    }


def compact_finish_readiness(value: dict[str, Any]) -> dict[str, Any]:
    coverage = value.get("review_coverage") if isinstance(value.get("review_coverage"), dict) else {}
    return {
        "schema_version": value.get("schema_version", 1),
        "tool": value.get("tool", "skill-manager.finish-readiness"),
        "ok": bool(value.get("ok")),
        "status": value.get("status", "unknown"),
        "changed_file_count": value.get("changed_file_count", 0),
        "required_validation_count": value.get("required_validation_count", 0),
        "failed_check_count": value.get("failed_check_count", 0),
        "review_packet_status": value.get("review_packet_status", "unknown"),
        "review_budget_tokens": value.get("review_budget_tokens", 0),
        "changed_diff_estimated_tokens": value.get("changed_diff_estimated_tokens", 0),
        "tokens_over_review_budget": value.get("tokens_over_review_budget", 0),
        "owner_review_packet_count": value.get("owner_review_packet_count", 0),
        "owner_review_subpacket_count": value.get("owner_review_subpacket_count", 0),
        "owner_review_hunk_count": value.get("owner_review_hunk_count", 0),
        "largest_owner_subpacket_estimated_tokens": value.get("largest_owner_subpacket_estimated_tokens", 0),
        "largest_owner_hunk_estimated_tokens": value.get("largest_owner_hunk_estimated_tokens", 0),
        "review_coverage": {
            "status": coverage.get("status", "unknown"),
            "review_unit_count": coverage.get("review_unit_count", 0),
            "pending_review_unit_count": coverage.get("pending_review_unit_count", 0),
            "validation_unit_count": coverage.get("validation_unit_count", 0),
            "largest_unreviewed_owner": coverage.get("largest_unreviewed_owner", ""),
        },
        "cost_ledger_summary": compact_finish_cost_ledger(value.get("cost_ledger")),
        "reasons": value.get("reasons", [])[:3] if isinstance(value.get("reasons"), list) else [],
        "next_command": value.get("next_command", ""),
    }


def compact_finish_review_packet(value: dict[str, Any]) -> dict[str, Any]:
    review_plan = value.get("review_plan_summary") if isinstance(value.get("review_plan_summary"), dict) else {}
    review_batching = review_plan.get("review_batching") if isinstance(review_plan.get("review_batching"), dict) else {}
    review_cost = value.get("review_cost_report") if isinstance(value.get("review_cost_report"), dict) else {}
    return {
        "status": value.get("status", "unknown"),
        "review_budget_tokens": value.get("review_budget_tokens", 0),
        "changed_diff_estimated_tokens": value.get("changed_diff_estimated_tokens", 0),
        "tokens_over_review_budget": value.get("tokens_over_review_budget", 0),
        "owner_review_packet_count": value.get("owner_review_packet_count", 0),
        "owner_review_subpacket_count": value.get("owner_review_subpacket_count", 0),
        "owner_review_hunk_count": value.get("owner_review_hunk_count", 0),
        "largest_owner_subpacket_estimated_tokens": value.get("largest_owner_subpacket_estimated_tokens", 0),
        "largest_owner_hunk_estimated_tokens": value.get("largest_owner_hunk_estimated_tokens", 0),
        "next_review_command": value.get("next_review_command", ""),
        "cost_ledger": compact_finish_cost_ledger(value.get("cost_ledger")),
        "review_plan_summary": {
            "status": review_plan.get("status", "unknown"),
            "review_unit_count": review_plan.get("review_unit_count", 0),
            "validation_unit_count": review_plan.get("validation_unit_count", 0),
            "owner_group_count": review_plan.get("owner_group_count", 0),
            "next_pending_command": review_plan.get("next_pending_command", ""),
            "review_batching": review_batching,
        },
        "review_cost_report": {
            "status": review_cost.get("status", "unknown"),
            "billing_scope": review_cost.get("billing_scope", ""),
            "raw_changed_diff_estimated_tokens": review_cost.get("raw_changed_diff_estimated_tokens", 0),
            "next_review_unit_estimated_tokens": review_cost.get("next_review_unit_estimated_tokens", 0),
            "next_review_unit_saved_tokens_vs_raw_estimated": review_cost.get(
                "next_review_unit_saved_tokens_vs_raw_estimated",
                0,
            ),
            "money_saving_status": review_cost.get("money_saving_status", ""),
        },
    }


def summarize_finish_work_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    failed_checks = [
        {
            "command": item.get("command"),
            "phase": item.get("phase", ""),
            "status": item.get("status"),
            "returncode": item.get("returncode"),
            "issue": item.get("issue", ""),
            "timeout_seconds": item.get("timeout_seconds", 0),
            "elapsed_seconds": item.get("elapsed_seconds", 0.0),
            "distilled_output": item.get("distilled_output") or item.get("output_tail", ""),
            "raw_output_path": item.get("raw_output_path", ""),
            "output_summary": item.get("output_summary", {}),
        }
        for item in checks
        if isinstance(item, dict) and not item.get("ok")
    ]
    indexes = report.get("workflow_run_indexes") if isinstance(report.get("workflow_run_indexes"), dict) else {}
    github = report.get("github_validation") if isinstance(report.get("github_validation"), dict) else {}
    budget_gate = report.get("budget_gate") if isinstance(report.get("budget_gate"), dict) else {}
    budget_hotspots = report.get("budget_hotspots") if isinstance(report.get("budget_hotspots"), dict) else {}
    fingerprint = report.get("input_fingerprint") if isinstance(report.get("input_fingerprint"), dict) else {}
    navigation = report.get("navigation") if isinstance(report.get("navigation"), dict) else {}
    navigation_auto_refresh = report.get("navigation_auto_refresh") if isinstance(report.get("navigation_auto_refresh"), dict) else {}
    review_packet = report.get("review_packet") if isinstance(report.get("review_packet"), dict) else {}
    finish_readiness = report.get("finish_readiness") if isinstance(report.get("finish_readiness"), dict) else {}
    review_progress = report.get("review_progress") if isinstance(report.get("review_progress"), dict) else {}
    budget_trend = report.get("budget_trend") if isinstance(report.get("budget_trend"), dict) else {}
    latency_budget = report.get("latency_budget") if isinstance(report.get("latency_budget"), dict) else {}
    validation_reuse = (
        report.get("validation_reuse")
        if isinstance(report.get("validation_reuse"), dict)
        else {}
    )
    finish_input_stability = (
        report.get("finish_input_stability")
        if isinstance(report.get("finish_input_stability"), dict)
        else {}
    )
    hotspot_delta = budget_hotspots.get("delta") if isinstance(budget_hotspots.get("delta"), dict) else {}
    hotspot_delta_summary = hotspot_delta.get("summary") if isinstance(hotspot_delta.get("summary"), dict) else {}
    output: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "repo-finish"),
        "ok": bool(report.get("ok")),
        "status": report.get("status", ""),
        "profile": report.get("profile", "changed"),
        "completion_supported": bool(report.get("completion_supported", False)),
        "missing_evidence": report.get("missing_evidence", []),
        "claim_receipt": repo_qol_readiness.summarize_claim_receipt(
            report.get("claim_receipt", {}) if isinstance(report.get("claim_receipt"), dict) else {},
            compact=compact,
        ),
        "summary": {
            "check_count": len(checks),
            "failed_check_count": len(failed_checks),
            "workflow_index_count": indexes.get("checked_count", 0),
            "budget_gate_status": budget_gate.get("status", "skipped"),
            "budget_hotspot_status": budget_hotspots.get("status", "skipped"),
            "budget_hotspot_count": len(budget_hotspots.get("top", []) if isinstance(budget_hotspots.get("top"), list) else []),
            "budget_delta_total_text_words": int(hotspot_delta_summary.get("total_text_words", 0) or 0),
            "budget_delta_tool_load_words": int(hotspot_delta_summary.get("tool_load_words", 0) or 0),
            "navigation_status": navigation.get("status", "unknown"),
            "navigation_auto_refresh_status": navigation_auto_refresh.get("status", "skipped"),
            "finish_readiness_status": finish_readiness.get("status", "unknown"),
            "finish_input_stability_status": finish_input_stability.get("status", "unknown"),
            "owner_review_packet_count": finish_readiness.get("owner_review_packet_count", 0),
            "owner_review_subpacket_count": finish_readiness.get("owner_review_subpacket_count", 0),
            "largest_owner_subpacket_estimated_tokens": finish_readiness.get("largest_owner_subpacket_estimated_tokens", 0),
            "owner_review_hunk_count": finish_readiness.get("owner_review_hunk_count", 0),
            "largest_owner_hunk_estimated_tokens": finish_readiness.get("largest_owner_hunk_estimated_tokens", 0),
            "advisory_count": len(report.get("advisories", [])) if isinstance(report.get("advisories"), list) else 0,
        },
        "failed_checks": failed_checks,
        "workflow_run_indexes": {
            "checked_count": indexes.get("checked_count", 0),
            "workflows": indexes.get("workflows", []),
        },
        "workflow_eval": report.get("workflow_eval", {}),
        "workflow_evidence_references": report.get("workflow_evidence_references", {}),
        "story_bug_out_of_scope_templates": report.get("story_bug_out_of_scope_templates", {}),
        "budget_hotspots": budget_hotspots,
        "budget_gate": budget_gate,
        "validation_reuse": {
            "status": validation_reuse.get("status", "not-reused"),
            "eligible": bool(validation_reuse.get("eligible", False)),
            "profile": validation_reuse.get("profile", ""),
            "reason": validation_reuse.get("reason", ""),
            "receipt_path": validation_reuse.get("receipt_path", ""),
            "recorded_at": validation_reuse.get("recorded_at", ""),
            "age_seconds": validation_reuse.get("age_seconds"),
            "max_age_seconds": validation_reuse.get("max_age_seconds", 0),
            "input_stable": bool(validation_reuse.get("input_stable", False)),
            "source_elapsed_ms": validation_reuse.get("source_elapsed_ms", 0),
        } if validation_reuse else {},
        "finish_input_stability": {
            "status": finish_input_stability.get("status", "unknown"),
            "ok": bool(finish_input_stability.get("ok")),
            "profile": finish_input_stability.get("profile", ""),
            "phase_selection_stable": bool(
                finish_input_stability.get("phase_selection_stable", False)
            ),
            "post_phase_stable": bool(
                finish_input_stability.get("post_phase_stable", False)
            ),
            "validation_proof_matches_final_input": bool(
                finish_input_stability.get("validation_proof_matches_final_input", False)
            ),
            "changed_scope_execution_mode": finish_input_stability.get(
                "changed_scope_execution_mode",
                "",
            ),
            "reasons": finish_input_stability.get("reasons", []),
        } if finish_input_stability else {},
        "navigation": {
            "status": navigation.get("status", "unknown"),
            "read_first": navigation.get("read_first", ""),
            "next_command": navigation.get("next_command", ""),
            "stale_output_count": navigation.get("stale_output_count", 0),
            "summary": navigation.get("summary", ""),
        } if navigation else {},
        "navigation_auto_refresh": {
            "status": navigation_auto_refresh.get("status", "unknown"),
            "ok": bool(navigation_auto_refresh.get("ok")),
            "written": navigation_auto_refresh.get("written", []),
            "written_count": len(
                navigation_auto_refresh.get("written", [])
                if isinstance(navigation_auto_refresh.get("written"), list)
                else []
            ),
            "summary": navigation_auto_refresh.get("summary", ""),
        } if navigation_auto_refresh else {},
        "review_packet": {
            "status": review_packet.get("status", "unknown"),
            "review_budget_tokens": review_packet.get("review_budget_tokens", 0),
            "changed_diff_estimated_tokens": review_packet.get("changed_diff_estimated_tokens", 0),
            "tokens_over_review_budget": review_packet.get("tokens_over_review_budget", 0),
            "owner_review_packet_count": review_packet.get("owner_review_packet_count", 0),
            "owner_review_subpacket_count": review_packet.get("owner_review_subpacket_count", 0),
            "largest_owner_subpacket_estimated_tokens": review_packet.get("largest_owner_subpacket_estimated_tokens", 0),
            "owner_review_hunk_count": review_packet.get("owner_review_hunk_count", 0),
            "largest_owner_hunk_estimated_tokens": review_packet.get("largest_owner_hunk_estimated_tokens", 0),
            "owner_review_commands": review_packet.get("owner_review_commands", [])[:8],
            "owner_summary_commands": review_packet.get("owner_summary_commands", [])[:8],
            "next_review_command": review_packet.get("next_review_command", ""),
            "cost_ledger": repo_cost_policy.compact_review_cost_ledger(review_packet.get("cost_ledger", {})),
            "review_plan_summary": repo_review_progress.summarize_review_plan(
                repo_review_progress.build_review_plan(review_packet)
            ),
            "review_cost_report": repo_review_progress.summarize_review_cost_report(
                repo_review_progress.build_review_cost_report(review_packet)
            ),
        } if review_packet else {},
        "finish_readiness": finish_readiness,
        "review_progress": repo_review_progress.summarize_review_progress(review_progress) if review_progress else {},
        "budget_trend": summarize_budget_trend_report(budget_trend, compact=True) if budget_trend else {},
        "latency_budget": latency_budget,
        "check_metrics": report.get("check_metrics", {}),
        "progress_events": report.get("progress_events", []),
        "github_validation": {
            "status": github.get("status", ""),
            "automatic_triggers_enabled": bool(github.get("automatic_triggers_enabled", False)),
            "automatic_triggers": github.get("automatic_triggers", []),
        },
        "input_fingerprint": summarize_input_fingerprint(fingerprint) if fingerprint else {},
        "advisories": report.get("advisories", []),
        "next_command": report.get("next_command", ""),
    }
    if report.get("fast_path"):
        output["fast_path"] = report.get("fast_path", {})
    if compact:
        progress_coverage = (
            review_progress.get("coverage")
            if isinstance(review_progress.get("coverage"), dict)
            else {}
        )
        review_coverage_complete = (
            bool(review_progress)
            and not bool(review_progress.get("stale", False))
            and str(progress_coverage.get("status") or "") in {"complete", "no-review-units"}
            and int(progress_coverage.get("pending_review_unit_count", 0) or 0) == 0
        )
        completion_supported = bool(output.get("ok")) and bool(output.get("completion_supported"))
        if output.get("finish_readiness"):
            output["finish_readiness"] = compact_finish_readiness(finish_readiness)
        if output.get("review_packet"):
            output["review_packet"] = compact_finish_review_packet(output["review_packet"])
        if output.get("navigation_auto_refresh"):
            auto_refresh = output["navigation_auto_refresh"]
            output["navigation_auto_refresh"] = {
                "status": auto_refresh.get("status", "unknown"),
                "ok": bool(auto_refresh.get("ok")),
                "written_count": auto_refresh.get("written_count", 0),
                "summary": auto_refresh.get("summary", ""),
            }
        if output.get("input_fingerprint"):
            fingerprint_summary = output["input_fingerprint"]
            output["input_fingerprint"] = {
                "digest": fingerprint_summary.get("digest", ""),
                "changed_file_count": fingerprint_summary.get("changed_file_count", 0),
                "command_count": fingerprint_summary.get("command_count", 0),
            }
        if output.get("fast_path"):
            for skipped_key in (
                "budget_gate",
                "budget_hotspots",
                "budget_trend",
                "check_metrics",
                "github_validation",
                "progress_events",
                "review_progress",
                "story_bug_out_of_scope_templates",
                "workflow_eval",
                "workflow_evidence_references",
                "workflow_run_indexes",
            ):
                output.pop(skipped_key, None)
        if not output.get("failed_checks"):
            output.pop("failed_checks", None)
        if review_coverage_complete:
            output.pop("review_packet", None)
        if completion_supported:
            output.pop("budget_hotspots", None)
            output.pop("progress_events", None)
            if review_coverage_complete:
                output.pop("review_progress", None)
        if isinstance(output.get("budget_gate"), dict) and output["budget_gate"].get("status") == "skipped":
            output.pop("budget_gate", None)
        hotspots_summary = output.get("budget_hotspots")
        if isinstance(hotspots_summary, dict):
            hotspots_summary.pop("summary", None)
            baseline = hotspots_summary.get("baseline")
            if isinstance(baseline, dict) and baseline.get("ok"):
                baseline.pop("issue_count", None)
        if not output.get("navigation"):
            output.pop("navigation", None)
        if not output.get("navigation_auto_refresh") or output["navigation_auto_refresh"].get("status") in {"skipped", "skipped-fresh"}:
            output.pop("navigation_auto_refresh", None)
        if not output.get("review_packet") or output["review_packet"].get("status") != "over-budget":
            output.pop("review_packet", None)
        if not output.get("finish_readiness"):
            output.pop("finish_readiness", None)
        if not output.get("review_progress"):
            output.pop("review_progress", None)
        if not output.get("budget_trend"):
            output.pop("budget_trend", None)
        if not output.get("latency_budget"):
            output.pop("latency_budget", None)
        indexes_summary = output.get("workflow_run_indexes")
        if isinstance(indexes_summary, dict):
            indexes_summary.pop("workflows", None)
        evidence_summary = output.get("workflow_evidence_references")
        if isinstance(evidence_summary, dict) and not evidence_summary.get("missing"):
            evidence_summary.pop("missing", None)
        story_summary = output.get("story_bug_out_of_scope_templates")
        if isinstance(story_summary, dict) and not story_summary.get("missing"):
            story_summary.pop("missing", None)
        github_summary = output.get("github_validation")
        if isinstance(github_summary, dict) and not github_summary.get("automatic_triggers"):
            github_summary.pop("automatic_triggers", None)
        if not output.get("input_fingerprint"):
            output.pop("input_fingerprint", None)
        if not output.get("advisories"):
            output.pop("advisories", None)
    return repo_command_metrics.attach_output_budget(output, "finish")
