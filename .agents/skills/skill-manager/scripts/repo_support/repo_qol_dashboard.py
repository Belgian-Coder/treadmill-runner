"""Dashboard and low-context status helpers for repo_qol."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from repo_support import repo_changed
from repo_support import repo_command_metrics
from repo_support import repo_common as repo
from repo_support import repo_cost_policy
from repo_support import repo_policy
from repo_support import repo_doctor
from repo_support import repo_health
from repo_support import repo_optimizations
from repo_support import repo_review_progress
from repo_support.repo_fingerprint import input_fingerprint_report
from repo_support.repo_navigation_status import navigation_context_trace
from repo_support.repo_navigation_status import navigation_status
from repo_support.repo_qol_capture import run_json_local_ai
from repo_support.repo_qol_evidence import latest_evidence_report
from repo_support.repo_qol_github import github_validation_trigger_state

MANAGE = "python -B .agents/manage.py"
SYNC_COMMAND = f"{MANAGE} sync"
CHECK_CHANGED_DEEP_COMMAND = f"{MANAGE} check-changed --deep"
WHAT_NOW_COMMAND = f"{MANAGE} what-now"
FINISH_COMMAND = f"{MANAGE} finish"

def branch_name(root: Path) -> str:
    status, lines = repo.git_output(root, "branch", "--show-current")
    return lines[0] if status == 0 and lines else ""


def generated_sync_details(root: Path) -> list[dict[str, Any]]:
    health = repo_health.build_repo_health_report(root)
    rows: list[dict[str, Any]] = []
    for item in health.get("generated_checks", []):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "name": item.get("name", ""),
                "ok": bool(item.get("ok")),
                "message": str(item.get("message") or "").strip(),
                "fix_command": SYNC_COMMAND,
            }
        )
    return rows


def dashboard_health_report(root: Path, *, fast: bool) -> dict[str, Any]:
    try:
        return repo_health.build_repo_health_report(root, fast=fast)
    except TypeError:
        return repo_health.build_repo_health_report(root)


def dashboard_navigation_status(root: Path, *, fast: bool) -> dict[str, Any]:
    try:
        return navigation_status(root, fast=fast)
    except TypeError:
        return navigation_status(root)


def timed_section(name: str, callback) -> tuple[Any, dict[str, Any]]:
    return repo_command_metrics.timed_section(name, callback)


def skipped_advisory(name: str, reason: str) -> dict[str, Any]:
    return {"name": name, "ok": True, "elapsed_ms": 0.0, "skipped": True, "reason": reason}


def estimate_tokens_from_bytes(byte_count: int) -> int:
    return max(1, (max(byte_count, 0) + 3) // 4) if byte_count else 0


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def context_budget_report(
    root: Path,
    changed: list[str],
    *,
    review_packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a small context-cost estimate without opening large content."""
    low_context_files = [
        "AGENTS.md",
        "README.md",
        "docs/start-here.md",
        "docs/operations/daily-use.md",
        ".agents/routing.md",
        "automations/routing.md",
    ]
    files: list[dict[str, Any]] = []
    total_tokens = 0
    for rel in low_context_files:
        path = root / rel
        if not path.exists() or not path.is_file():
            continue
        size = path.stat().st_size
        tokens = estimate_tokens_from_bytes(size)
        total_tokens += tokens
        files.append({"path": rel, "size_bytes": size, "estimated_tokens": tokens})
    status = "ok"
    warnings: list[str] = []
    if total_tokens > 12_000:
        status = "warning"
        warnings.append("low-context entry files are large; use routing docs before opening full folders")
    policy, _policy_error = repo_cost_policy.load_cost_policy(root)
    guidance_savings = repo_cost_policy.guidance_savings_report(root, policy)
    routes = repo_cost_policy.task_routes(policy)
    review_budget = int(routes.get("review", {}).get("max_context_tokens", 5000) or 5000)
    diff_estimate: dict[str, Any] = {}
    if review_packet:
        changed_tokens = int(review_packet.get("changed_diff_estimated_tokens", 0) or 0)
        tracked_tokens = int(review_packet.get("tracked_diff_estimated_tokens", 0) or 0)
        untracked_tokens = int(review_packet.get("untracked_file_estimated_tokens", 0) or 0)
        tracked_file_count = int(review_packet.get("tracked_changed_file_count", 0) or 0)
        untracked_file_count = int(review_packet.get("untracked_changed_file_count", 0) or 0)
    elif changed:
        diff_estimate = repo_cost_policy.changed_diff_estimate(root)
        changed_tokens = int(diff_estimate.get("estimated_tokens", 0) or 0)
        tracked_tokens = int(diff_estimate.get("tracked_estimated_tokens", 0) or 0)
        untracked_tokens = int(diff_estimate.get("untracked_estimated_tokens", 0) or 0)
        tracked_file_count = int(diff_estimate.get("tracked_files", 0) or 0)
        untracked_file_count = int(diff_estimate.get("untracked_files", 0) or 0)
    else:
        changed_tokens = 0
        tracked_tokens = 0
        untracked_tokens = 0
        tracked_file_count = 0
        untracked_file_count = 0
    over_review_budget = max(0, changed_tokens - review_budget)
    if over_review_budget:
        status = "warning"
        warnings.append(
            "changed diff estimate exceeds review route budget; use changed-evidence or focused navigation before broad review"
        )
    return {
        "schema_version": 1,
        "status": status,
        "low_context_files": files,
        "estimated_low_context_tokens": total_tokens,
        "changed_file_count": len(changed),
        "changed_diff_estimated_tokens": changed_tokens,
        "tracked_diff_estimated_tokens": tracked_tokens,
        "untracked_file_estimated_tokens": untracked_tokens,
        "tracked_changed_file_count": tracked_file_count,
        "untracked_changed_file_count": untracked_file_count,
        "review_budget_tokens": review_budget,
        "changed_diff_tokens_over_review_budget": over_review_budget,
        "guidance_savings": guidance_savings,
        "guidance": "Open routing and compact docs first; use exact search before broad folder reads.",
        "warnings": warnings,
    }


def changed_validation_router_report(root: Path, changed: list[str], *, deep: bool = False) -> dict[str, Any]:
    if not changed:
        return {
            "status": "no-changes",
            "changed_file_count": 0,
            "summary": {"command_count": 0, "required_count": 0, "optional_count": 0, "owners": {}},
            "commands": [],
            "next_command": "none, no changed files",
        }
    scope = repo_changed.changed_scope(changed)
    plan = repo_optimizations.changed_validation_plan(root, changed, scope, deep=deep)
    summary = repo_optimizations.validation_plan_summary(plan)
    required = [item for item in plan if isinstance(item, dict) and item.get("required") is not False]
    return {
        "status": "planned" if plan else "no-matches",
        "changed_file_count": len(changed),
        "changed_groups": repo_changed.compact_path_groups(changed),
        "summary": summary,
        "commands": plan,
        "next_command": str(required[0].get("command")) if required else CHECK_CHANGED_DEEP_COMMAND,
    }


def dashboard_review_progress(
    root: Path,
    *,
    changed: list[str],
    validation_plan: list[dict[str, Any]],
    review_packet: dict[str, Any],
    input_fingerprint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not changed or not review_packet:
        return {}
    review_plan = repo_review_progress.build_review_plan(review_packet)
    fingerprint = input_fingerprint or input_fingerprint_report(root, changed, validation_plan)
    return repo_review_progress.review_progress_report(
        root,
        review_plan,
        input_fingerprint=fingerprint,
    )


def _validation_progress_matches_current_input(
    validation_progress: dict[str, Any],
    input_fingerprint: dict[str, Any],
    required_check_ids: list[str],
) -> bool:
    return repo_command_metrics.validation_progress_covers_input(
        validation_progress,
        input_fingerprint,
        required_check_ids=required_check_ids,
        profile="changed",
    )


def _validation_progress_summary(
    validation_progress: dict[str, Any],
    input_fingerprint: dict[str, Any],
    validation_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    if not validation_progress:
        return {}
    return {
        "status": validation_progress.get("status", "unknown"),
        "phase": validation_progress.get("phase", "unknown"),
        "input_fingerprint_match": _validation_progress_matches_current_input(
            validation_progress,
            input_fingerprint,
            [
                str(item.get("check_id") or "")
                for item in validation_plan
                if isinstance(item, dict)
                and item.get("required") is not False
                and str(item.get("check_id") or "")
            ],
        ),
    }


def _review_coverage_is_complete(review_progress: dict[str, Any]) -> bool:
    if not review_progress or bool(review_progress.get("stale", False)):
        return False
    coverage = review_progress.get("coverage") if isinstance(review_progress.get("coverage"), dict) else {}
    return (
        str(coverage.get("status") or "") in {"complete", "no-review-units"}
        and _int_value(coverage.get("pending_review_unit_count", 0)) == 0
    )


def _over_budget_next_action(
    review_progress: dict[str, Any],
    validation_router: dict[str, Any],
    *,
    validation_progress: dict[str, Any] | None = None,
    input_fingerprint: dict[str, Any] | None = None,
) -> tuple[str, str]:
    progress_command = str(review_progress.get("next_pending_command") or "")
    progress_current = review_progress.get("current_unit") if isinstance(review_progress.get("current_unit"), dict) else {}
    coverage = review_progress.get("coverage") if isinstance(review_progress.get("coverage"), dict) else {}
    pending_review_units = _int_value(coverage.get("pending_review_unit_count", 0)) if coverage else -1
    progress_is_fresh = bool(review_progress) and not bool(review_progress.get("stale", False))
    validation_next = str(validation_router.get("next_command") or "")
    if (
        progress_is_fresh
        and pending_review_units == 0
        and _validation_progress_matches_current_input(
            validation_progress or {},
            input_fingerprint or {},
            [
                str(item.get("check_id") or "")
                for item in validation_router.get("commands", [])
                if isinstance(item, dict)
                and item.get("required") is not False
                and str(item.get("check_id") or "")
            ],
        )
    ):
        return (
            FINISH_COMMAND,
            "Review coverage is complete and validation progress matches current input; run finish.",
        )
    if (
        progress_is_fresh
        and progress_command
        and pending_review_units == 0
        and str(progress_current.get("scope") or "") == "validation"
    ):
        return (
            progress_command,
            "Review coverage is complete; run the next validation command.",
        )
    if progress_is_fresh and pending_review_units == 0 and validation_next:
        return (
            validation_next,
            "Review coverage is complete; run validation evidence next.",
        )
    if progress_is_fresh and pending_review_units == 0 and not progress_command:
        return (
            FINISH_COMMAND,
            "Review coverage is complete; finish changed-file evidence.",
        )
    return (
        repo_review_progress.default_review_loop_command(),
        "Changed diff exceeds the review budget; run the bounded review loop before broad raw diff review.",
    )


def _mark_validation_router_satisfied(
    validation_router: dict[str, Any],
    validation_progress_summary: dict[str, Any],
) -> dict[str, Any]:
    if not validation_progress_summary.get("input_fingerprint_match"):
        return validation_router
    router = dict(validation_router)
    planned_next = str(router.get("next_command") or "")
    if planned_next:
        router["planned_next_command"] = planned_next
    router["status"] = "satisfied-by-validation-progress"
    router["next_command"] = "none, validation progress is current"
    return router


def _mark_review_packet_validation_satisfied(
    review_packet: dict[str, Any],
    *,
    planned_next_command: str = "",
) -> dict[str, Any]:
    packet = dict(review_packet)
    planned_next = str(
        planned_next_command
        or packet.get("next_review_command")
        or packet.get("progress_next_review_command")
        or ""
    )
    if planned_next and not planned_next.startswith("none"):
        packet["planned_next_review_command"] = planned_next
    packet["next_review_command"] = "none, validation progress is current"
    return packet


def _compact_review_batching(batch: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "status",
        "source_review_unit_count",
        "batched_review_unit_count",
        "saved_review_unit_count",
        "hunk_batch_count",
        "max_hunks_per_batch",
        "max_hunks_per_batch_limit",
        "max_batch_estimated_tokens",
    )
    return {key: batch[key] for key in keep if key in batch}


def _compact_dashboard_review_plan(plan_summary: dict[str, Any]) -> dict[str, Any]:
    if not plan_summary:
        return {}
    output = {
        "status": plan_summary.get("status", "unknown"),
        "review_state": plan_summary.get("review_state", "unknown"),
        "owner_group_count": plan_summary.get("owner_group_count", 0),
        "review_unit_count": plan_summary.get("review_unit_count", 0),
        "validation_unit_count": plan_summary.get("validation_unit_count", 0),
    }
    for key in ("completed_unit_count", "pending_unit_count"):
        if key in plan_summary:
            output[key] = plan_summary.get(key, 0)
    for key in ("pending_review_unit_count", "pending_validation_unit_count", "validation_status"):
        if key in plan_summary:
            output[key] = plan_summary.get(key, 0 if key.startswith("pending_") else "")
    if plan_summary.get("stale"):
        output["stale"] = True
    batching = plan_summary.get("review_batching")
    if isinstance(batching, dict) and batching:
        output["review_batching"] = _compact_review_batching(batching)
    return output


def _compact_dashboard_review_cost(report: dict[str, Any]) -> dict[str, Any]:
    if not report:
        return {}
    estimate = report.get("money_saving_estimate") if isinstance(report.get("money_saving_estimate"), dict) else {}
    return {
        "status": report.get("status", "unknown"),
        "billing_scope": report.get("billing_scope", "input-context-estimate-only"),
        "money_saving_status": report.get("money_saving_status", "unknown"),
        "raw_changed_diff_estimated_tokens": report.get("raw_changed_diff_estimated_tokens", 0),
        "next_review_unit_estimated_tokens": report.get("next_review_unit_estimated_tokens", 0),
        "next_review_unit_saved_tokens_vs_raw_estimated": report.get(
            "next_review_unit_saved_tokens_vs_raw_estimated",
            0,
        ),
        "money_saving_estimate": {
            "status": estimate.get("status", "unknown"),
            "billing_scope": estimate.get("billing_scope", ""),
            "input_tokens_saved": estimate.get("input_tokens_saved", 0),
            "assumed_extra_output_tokens": estimate.get("assumed_extra_output_tokens", 0),
            "default_output_price_multiplier": estimate.get("default_output_price_multiplier", 0),
            "default_net_input_token_equivalent_savings": estimate.get(
                "default_net_input_token_equivalent_savings",
                0,
            ),
        } if estimate else {},
    }


def _compact_dashboard_review_progress(progress_summary: dict[str, Any]) -> dict[str, Any]:
    if not progress_summary:
        return {}
    output = {
        "status": progress_summary.get("status", "unknown"),
        "review_state": progress_summary.get("review_state", "unknown"),
        "stale": bool(progress_summary.get("stale", False)),
        "completed_unit_count": progress_summary.get("completed_unit_count", 0),
        "pending_unit_count": progress_summary.get("pending_unit_count", 0),
    }
    current = progress_summary.get("current_unit") if isinstance(progress_summary.get("current_unit"), dict) else {}
    if current:
        current_unit = {
            "scope": current.get("scope", ""),
            "owner": current.get("owner", ""),
            "hunk": current.get("hunk", ""),
            "estimated_changed_tokens": current.get("estimated_changed_tokens", 0),
        }
        path = str(current.get("path", "") or "")
        paths = [item.strip() for item in path.split(",") if item.strip()]
        path_chars = repo_policy.int_value(
            repo_policy.project_root(), "limits.dashboard.path_chars"
        )
        if len(paths) > 1 or len(path) > path_chars:
            current_unit.update(
                {
                    "first_path": paths[0] if paths else path[:path_chars],
                    "path_count": len(paths) if paths else 1,
                    "omitted_path_count": max((len(paths) if paths else 1) - 1, 0),
                }
            )
        else:
            current_unit["path"] = path
        output["current_unit"] = current_unit
    coverage = progress_summary.get("coverage") if isinstance(progress_summary.get("coverage"), dict) else {}
    if coverage:
        output["coverage"] = {
            "status": coverage.get("status", "unknown"),
            "owner_total": coverage.get("owner_total", 0),
            "owners_complete": coverage.get("owners_complete", 0),
            "review_unit_count": coverage.get("review_unit_count", 0),
            "pending_review_unit_count": coverage.get("pending_review_unit_count", 0),
            "validation_unit_count": coverage.get("validation_unit_count", 0),
            "largest_unreviewed_owner": coverage.get("largest_unreviewed_owner", ""),
            "cross_cutting_sample_required": bool(coverage.get("cross_cutting_sample_required", False)),
        }
    return output


def summarize_dashboard_report(
    report: dict[str, Any],
    *,
    compact: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    omit_detail = compact
    context = report.get("context_budget") if isinstance(report.get("context_budget"), dict) else {}
    generated = report.get("generated_checks") if isinstance(report.get("generated_checks"), list) else []
    generated_failures = [item for item in generated if isinstance(item, dict) and not item.get("ok")]
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    failed_checks = [
        str(item.get("name"))
        for item in checks
        if isinstance(item, dict) and not item.get("ok") and not item.get("advisory")
    ]
    advisory_failed_checks = [
        str(item.get("name"))
        for item in checks
        if isinstance(item, dict) and not item.get("ok") and item.get("advisory")
    ]
    dirty = report.get("dirty_state") if isinstance(report.get("dirty_state"), dict) else {}
    github = report.get("github_validation") if isinstance(report.get("github_validation"), dict) else {}
    audit = report.get("capability_audit") if isinstance(report.get("capability_audit"), dict) else {}
    validation_router = report.get("validation_router") if isinstance(report.get("validation_router"), dict) else {}
    validation_progress = report.get("validation_progress") if isinstance(report.get("validation_progress"), dict) else {}
    review_packet = report.get("review_packet") if isinstance(report.get("review_packet"), dict) else {}
    review_progress = report.get("review_progress") if isinstance(report.get("review_progress"), dict) else {}
    review_progress_summary = repo_review_progress.summarize_review_progress(review_progress) if review_progress else {}
    review_plan = repo_review_progress.build_review_plan(review_packet) if review_packet else {}
    review_plan_summary = repo_review_progress.summarize_review_plan(review_plan) if review_plan else {}
    if review_progress_summary:
        coverage = (
            review_progress_summary.get("coverage")
            if isinstance(review_progress_summary.get("coverage"), dict)
            else {}
        )
        pending_review_units = _int_value(coverage.get("pending_review_unit_count", 0)) if coverage else 0
        pending_units = _int_value(review_progress_summary.get("pending_unit_count", 0))
        review_plan_summary.update(
            {
                "review_state": review_progress_summary.get("review_state", review_plan_summary.get("review_state", "")),
                "next_pending_command": review_progress_summary.get(
                    "next_pending_command",
                    review_plan_summary.get("next_pending_command", ""),
                ),
                "completed_unit_count": review_progress_summary.get("completed_unit_count", 0),
                "pending_unit_count": pending_units,
                "pending_review_unit_count": pending_review_units,
                "pending_validation_unit_count": max(0, pending_units - pending_review_units),
                "stale": bool(review_progress_summary.get("stale", False)),
            }
        )
        if (
            bool(validation_progress.get("input_fingerprint_match", False))
            and str(coverage.get("status") or "") in {"complete", "no-review-units"}
            and pending_review_units == 0
        ):
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
    cost_ledger = review_plan.get("cost_ledger") if isinstance(review_plan.get("cost_ledger"), dict) else {}
    if not cost_ledger:
        cost_ledger = review_packet.get("cost_ledger") if isinstance(review_packet.get("cost_ledger"), dict) else {}
    review_evidence_complete = (
        bool(validation_progress.get("input_fingerprint_match", False))
        and str(review_plan_summary.get("status") or "") == "complete"
        and str(review_plan_summary.get("validation_status") or "") == "satisfied-by-validation-progress"
    )
    review_packet_status = "complete" if review_evidence_complete else review_packet.get("status", "unknown")
    review_packet_tokens_over_budget = (
        0 if review_evidence_complete else review_packet.get("tokens_over_review_budget", 0)
    )
    if review_evidence_complete and review_progress_summary:
        review_progress_summary = dict(review_progress_summary)
        review_progress_summary.update(
            {
                "status": "complete",
                "review_state": "complete",
                "pending_unit_count": 0,
            }
        )
        review_progress_summary.pop("next_pending_command", None)
        review_progress_summary.pop("current_unit", None)
    navigation = report.get("navigation") if isinstance(report.get("navigation"), dict) else {}
    guidance_savings = context.get("guidance_savings") if isinstance(context.get("guidance_savings"), dict) else {}
    compact_guidance_savings = dict(guidance_savings)
    if compact_guidance_savings:
        default_context = dict(compact_guidance_savings.get("default_context", {}))
        broad_baseline = dict(compact_guidance_savings.get("broad_baseline", {}))
        if compact:
            default_context.pop("files", None)
            broad_baseline.pop("files", None)
        compact_guidance_savings["default_context"] = default_context
        compact_guidance_savings["broad_baseline"] = broad_baseline
    router_commands = validation_router.get("commands") if isinstance(validation_router.get("commands"), list) else []
    compact_validation_router = {
        "status": validation_router.get("status", "unknown"),
        "summary": validation_router.get("summary", {}),
        "next_command": validation_router.get("next_command", ""),
    }
    if validation_router.get("planned_next_command"):
        compact_validation_router["planned_next_command"] = validation_router.get("planned_next_command", "")
    if not compact:
        compact_validation_router["commands"] = router_commands
    navigation_summary = {
        "status": navigation.get("status", ""),
        "read_first": navigation.get("read_first", ""),
        "next_command": navigation.get("next_command", ""),
        "read_only_next_step": navigation.get("read_only_next_step", ""),
        "stale_output_count": navigation.get("stale_output_count", 0),
        "summary": navigation.get("summary", ""),
    }
    if navigation.get("reason"):
        navigation_summary["reason"] = navigation.get("reason", "")
    output: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "repo-dashboard"),
        "ok": bool(report.get("ok", True)),
        "status": report.get("status", ""),
        "plain_status": report.get("plain_status", ""),
        "mode": report.get("mode", "fast"),
        "total_elapsed_ms": report.get("total_elapsed_ms", 0),
        "branch": report.get("branch", ""),
        "dirty_state": {
            "ok": bool(dirty.get("ok", True)),
            "status": dirty.get("status", ""),
            "dirty": bool(dirty.get("dirty", False)),
        },
        "changed_file_count": report.get("changed_file_count", 0),
        "changed_groups": report.get("changed_groups", ""),
        "summary": {
            "check_count": len(checks),
            "failed_check_count": len(failed_checks),
            "advisory_failed_check_count": len(advisory_failed_checks),
            "generated_failed_count": len(generated_failures),
            "skipped_count": len(report.get("skipped", []) if isinstance(report.get("skipped"), list) else []),
            "low_context_tokens": context.get("estimated_low_context_tokens", 0),
            "changed_diff_estimated_tokens": context.get("changed_diff_estimated_tokens", 0),
            "changed_diff_tokens_over_review_budget": context.get("changed_diff_tokens_over_review_budget", 0),
            "untracked_file_estimated_tokens": context.get("untracked_file_estimated_tokens", 0),
            "review_packet_status": review_packet_status,
            "review_packet_tokens_over_budget": review_packet_tokens_over_budget,
            "owner_review_packet_count": review_packet.get("owner_review_packet_count", 0),
            "owner_review_subpacket_count": review_packet.get("owner_review_subpacket_count", 0),
            "largest_owner_subpacket_estimated_tokens": review_packet.get("largest_owner_subpacket_estimated_tokens", 0),
            "owner_review_hunk_count": review_packet.get("owner_review_hunk_count", 0),
            "largest_owner_hunk_estimated_tokens": review_packet.get("largest_owner_hunk_estimated_tokens", 0),
            "review_single_agent_saved_tokens_estimated": cost_ledger.get("single_agent_saved_tokens_vs_raw_estimated", 0),
            "review_single_agent_saved_percent_estimated": cost_ledger.get("single_agent_saved_percent_vs_raw_estimated", 0.0),
            "review_next_unit_saved_tokens_estimated": cost_ledger.get("next_review_unit_saved_tokens_vs_raw_estimated", 0),
            "review_next_unit_saved_percent_estimated": cost_ledger.get("next_review_unit_saved_percent_vs_raw_estimated", 0.0),
            "guidance_saved_tokens_estimated": (
                context.get("guidance_savings", {}).get("saved_tokens_estimated", 0)
                if isinstance(context.get("guidance_savings"), dict)
                else 0
            ),
            "guidance_saved_percent_estimated": (
                context.get("guidance_savings", {}).get("saved_percent_estimated", 0.0)
                if isinstance(context.get("guidance_savings"), dict)
                else 0.0
            ),
        },
        "checks": [
            {
                "name": item.get("name"),
                "ok": bool(item.get("ok")),
                **({"advisory": True} if item.get("advisory") else {}),
            }
            for item in checks
            if isinstance(item, dict) and (not omit_detail or item.get("ok") is not True)
        ],
        "generated_checks": generated_failures,
        "context_budget": {
            "status": context.get("status", ""),
            "estimated_low_context_tokens": context.get("estimated_low_context_tokens", 0),
            "changed_file_count": context.get("changed_file_count", 0),
            "changed_diff_estimated_tokens": context.get("changed_diff_estimated_tokens", 0),
            "tracked_diff_estimated_tokens": context.get("tracked_diff_estimated_tokens", 0),
            "untracked_file_estimated_tokens": context.get("untracked_file_estimated_tokens", 0),
            "tracked_changed_file_count": context.get("tracked_changed_file_count", 0),
            "untracked_changed_file_count": context.get("untracked_changed_file_count", 0),
            "review_budget_tokens": context.get("review_budget_tokens", 0),
            "changed_diff_tokens_over_review_budget": context.get("changed_diff_tokens_over_review_budget", 0),
            "guidance_savings": compact_guidance_savings,
            "warnings": context.get("warnings", []),
        },
        "navigation": navigation_summary,
        "context_trace": navigation_context_trace(navigation_summary),
        "validation_router": compact_validation_router,
        "validation_progress": {
            "status": validation_progress.get("status", "unknown"),
            "phase": validation_progress.get("phase", "unknown"),
            "input_fingerprint_match": bool(validation_progress.get("input_fingerprint_match", False)),
        } if validation_progress else {},
        "review_packet": {
            "status": review_packet_status,
            "review_budget_tokens": review_packet.get("review_budget_tokens", 0),
            "changed_diff_estimated_tokens": review_packet.get("changed_diff_estimated_tokens", 0),
            "tokens_over_review_budget": review_packet_tokens_over_budget,
            "owner_counts": review_packet.get("owner_counts", {}),
            "owner_review_packet_count": review_packet.get("owner_review_packet_count", 0),
            "owner_review_subpacket_count": review_packet.get("owner_review_subpacket_count", 0),
            "largest_owner_subpacket_estimated_tokens": review_packet.get("largest_owner_subpacket_estimated_tokens", 0),
            "owner_review_hunk_count": review_packet.get("owner_review_hunk_count", 0),
            "largest_owner_hunk_estimated_tokens": review_packet.get("largest_owner_hunk_estimated_tokens", 0),
            "affected_owner_context": repo_changed.affected_owner_context(review_packet),
            "owner_review_commands": review_packet.get("owner_review_commands", [])[:8],
            "owner_summary_commands": review_packet.get("owner_summary_commands", [])[:8],
            "risk_counts": review_packet.get("risk_counts", {}),
            "read_first": review_packet.get("read_first", []),
            "validation_first": repo_changed.summarize_validation_commands(review_packet.get("validation_first", [])),
            "navigation_next_command": review_packet.get("navigation_next_command", ""),
            "next_review_command": review_packet.get("next_review_command", ""),
            "planned_next_review_command": review_packet.get("planned_next_review_command", ""),
            "cost_ledger": repo_cost_policy.compact_review_cost_ledger(cost_ledger),
            "review_plan_summary": review_plan_summary,
            "review_cost_report": repo_review_progress.summarize_review_cost_report(
                repo_review_progress.build_review_cost_report(review_packet)
            ),
        } if review_packet else {},
        "review_progress": review_progress_summary,
        "local_ai": {
            "ok": bool(report.get("local_ai", {}).get("ok", True))
            if isinstance(report.get("local_ai"), dict)
            else True,
            "status": report.get("local_ai", {}).get("status", "")
            if isinstance(report.get("local_ai"), dict)
            else "",
        },
        "benchmark": {
            "ok": bool(report.get("benchmark", {}).get("ok", True))
            if isinstance(report.get("benchmark"), dict)
            else True,
            "status": report.get("benchmark", {}).get("status", "")
            if isinstance(report.get("benchmark"), dict)
            else "",
        },
        "github_validation": {
            "status": github.get("status", ""),
            "automatic_triggers_enabled": bool(github.get("automatic_triggers_enabled", False)),
            "automatic_triggers": github.get("automatic_triggers", []),
        },
        "skipped": report.get("skipped", []),
        "next_command": (
            repo_changed.compact_next_command(report.get("next_command", ""), root=root)
            if compact
            else report.get("next_command", "")
        ),
        "next_command_reason": report.get("next_command_reason", ""),
        "latency_budget": report.get("latency_budget", {}),
    }
    if audit:
        output["capability_audit"] = {
            "completion_supported": bool(audit.get("completion_supported")),
            "summary": audit.get("summary", {}),
        }
    if compact:
        context_warnings = context.get("warnings", []) if isinstance(context.get("warnings"), list) else []
        output["summary"]["context_status"] = context.get("status", "")
        output["summary"]["context_warning_count"] = len(context_warnings)
        output["dirty_status"] = dirty.get("status", "")
        output.pop("context_budget", None)
        router = output.get("validation_router")
        if isinstance(router, dict):
            router.pop("commands", None)
        packet = output.get("review_packet")
        if navigation_summary.get("status") in {"stale", "missing", "blocked"}:
            output.pop("review_packet", None)
        elif not isinstance(packet, dict) or packet.get("status") not in {"over-budget", "complete"}:
            output.pop("review_packet", None)
        elif isinstance(packet, dict):
            for key in (
                "read_first",
                "owner_review_commands",
                "owner_summary_commands",
                "owner_counts",
                "risk_counts",
                "validation_first",
                "navigation_next_command",
            ):
                packet.pop(key, None)
            cost = packet.get("cost_ledger") if isinstance(packet.get("cost_ledger"), dict) else {}
            if cost:
                keep_cost_keys = (
                    "status",
                    "billing_scope",
                    "raw_changed_diff_estimated_tokens",
                    "next_review_unit_estimated_tokens",
                    "largest_review_unit_estimated_tokens",
                    "review_unit_count",
                    "source_review_unit_count",
                    "batched_review_unit_count",
                    "saved_batched_review_unit_count",
                    "max_hunks_per_batch_limit",
                )
                packet["cost_ledger"] = {key: cost[key] for key in keep_cost_keys if key in cost}
            plan_summary = packet.get("review_plan_summary")
            if isinstance(plan_summary, dict):
                packet["review_plan_summary"] = _compact_dashboard_review_plan(plan_summary)
            cost_report = packet.get("review_cost_report")
            if isinstance(cost_report, dict):
                packet["review_cost_report"] = _compact_dashboard_review_cost(cost_report)
        progress = output.get("review_progress")
        if isinstance(progress, dict) and progress:
            output["review_progress"] = _compact_dashboard_review_progress(progress)
            packet = output.get("review_packet")
            if isinstance(packet, dict):
                packet.pop("affected_owner_context", None)
            if _review_coverage_is_complete(progress):
                output.pop("review_packet", None)
        if not output.get("review_progress"):
            output.pop("review_progress", None)
        if not output.get("checks"):
            output.pop("checks", None)
        if not output.get("generated_checks"):
            output.pop("generated_checks", None)
        output.pop("skipped", None)
        if not output.get("changed_groups"):
            output.pop("changed_groups", None)
        if dirty.get("dirty") is not True:
            output.pop("dirty_state", None)
        github_summary = output.get("github_validation") if isinstance(output.get("github_validation"), dict) else {}
        if isinstance(github_summary, dict) and not github_summary.get("automatic_triggers"):
            github_summary.pop("automatic_triggers", None)
    command_id = "status-full" if output.get("mode") == "full" else "status-fast"
    return repo_command_metrics.attach_output_budget(output, command_id)


def loaded_context_ledger(
    *,
    full: bool,
    skip_local_ai: bool,
    skip_github: bool,
    timings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = [
        {"source": "repo_health", "kind": "deterministic-check", "loaded": True, "reason": "dashboard health"},
        {"source": "git_status", "kind": "deterministic-check", "loaded": True, "reason": "branch and dirty state"},
        {"source": "changed_files", "kind": "deterministic-check", "loaded": True, "reason": "changed evidence routing"},
        {
            "source": "local_ai_readiness",
            "kind": "advisory-readiness",
            "loaded": bool(full and not skip_local_ai),
            "reason": "full dashboard only" if not full and not skip_local_ai else "local AI readiness",
        },
        {
            "source": "github",
            "kind": "external-state",
            "loaded": False,
            "reason": "--no-github" if skip_github else "dashboard stays local-only; use release-evidence for GitHub hygiene",
        },
    ]
    elapsed = {str(item.get("name")): item.get("elapsed_ms") for item in timings}
    for row in rows:
        row["elapsed_ms"] = elapsed.get(row["source"], 0)
    return rows


def git_cache_key(root: Path, dirty: dict[str, Any]) -> str:
    status, lines = repo.git_output(root, "rev-parse", "--short", "HEAD")
    ref = lines[0] if status == 0 and lines else "no-git-ref"
    return f"{ref}:{dirty.get('status', 'unknown')}"


def trace_summary(
    command: str,
    *,
    total_elapsed_ms: float,
    timings: list[dict[str, Any]],
    changed_count: int,
    mutates_repo: bool,
) -> dict[str, Any]:
    failed = [str(item.get("name")) for item in timings if not item.get("ok")]
    skipped = [str(item.get("name")) for item in timings if item.get("skipped")]
    return {
        "command": command,
        "duration_ms": total_elapsed_ms,
        "section_count": len(timings),
        "failed_sections": failed,
        "skipped_sections": skipped,
        "changed_file_count": changed_count,
        "mutates_repo": mutates_repo,
        "trace_to_test_suggestion": (
            "Add a self-test fixture for repeated failed sections."
            if failed
            else "No repeated failure signal in this run."
        ),
    }


def advisory_trust_report(timings: list[dict[str, Any]]) -> dict[str, Any]:
    failed_or_slow = [
        {
            "section": str(item.get("name")),
            "reason": str(item.get("issue") or ("slow" if float(item.get("elapsed_ms", 0) or 0) > 10000 else "")),
            "elapsed_ms": item.get("elapsed_ms", 0),
        }
        for item in timings
        if (not item.get("ok")) or float(item.get("elapsed_ms", 0) or 0) > 10000
    ]
    return {
        "status": "demote-advisory" if failed_or_slow else "normal",
        "failed_or_slow_sections": failed_or_slow,
        "fallback": "Use deterministic launcher checks and skip advisory sections when they are slow or unavailable.",
    }


def dashboard_report(
    root: Path,
    *,
    watch_once: bool = False,
    full: bool = False,
    skip_local_ai: bool = False,
    skip_github: bool = False,
    include_fix_suggestions: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    if watch_once:
        time.sleep(1)
    timings: list[dict[str, Any]] = []
    health, timing = timed_section("repo_health", lambda: dashboard_health_report(root, fast=not full))
    timings.append(timing)
    dirty, timing = timed_section("git_status", lambda: repo_doctor.git_dirty_state(root))
    timings.append(timing)
    changed, timing = timed_section("changed_files", lambda: repo_changed.changed_files(root))
    timings.append(timing)
    navigation, timing = timed_section("navigation_status", lambda: dashboard_navigation_status(root, fast=not full))
    timings.append(timing)
    if skip_local_ai:
        local_ai = {"ok": True, "status": "skipped", "skipped": ["local AI skipped by --no-local-ai"]}
        local_ai_check_name = "local_ai_fast_status" if not full else "local_ai_readiness"
        timings.append(skipped_advisory(local_ai_check_name, "--no-local-ai"))
    elif not full:
        local_ai = {"ok": True, "status": "skipped", "skipped": ["use status --full for local AI readiness details"]}
        local_ai_check_name = "local_ai_fast_status"
        timings.append(skipped_advisory(local_ai_check_name, "fast dashboard mode"))
    else:
        local_ai_check_name = "local_ai_readiness"
        local_ai, timing = timed_section("local_ai_readiness", lambda: repo_doctor.setup_local_ai_readiness(root))
        timings.append(timing)
    if full:
        benchmark, timing = timed_section("benchmark_doctor", lambda: repo_doctor.benchmark_doctor_report(root))
        timings.append(timing)
        evidence, timing = timed_section("latest_evidence", lambda: latest_evidence_report(root))
        timings.append(timing)
    else:
        benchmark = {"ok": True, "status": "skipped", "skipped": ["use status --full for benchmark doctor"]}
        evidence = {"workflow_runs": [], "benchmarks": [], "document_evidence": [], "local_ai_reports": []}
        timings.append(skipped_advisory("benchmark_doctor", "fast dashboard mode"))
        timings.append(skipped_advisory("latest_evidence", "fast dashboard mode"))
    if skip_github:
        timings.append(skipped_advisory("github", "--no-github"))
    github_validation = github_validation_trigger_state(root)
    checks = [
        {"name": "repo_health", "ok": bool(health.get("ok"))},
        {"name": "generated_sync", "ok": all(bool(item.get("ok")) for item in health.get("generated_checks", []))},
        {"name": "navigation_freshness", "ok": navigation.get("status") == "fresh", "advisory": True},
        {"name": local_ai_check_name, "ok": bool(local_ai.get("ok")), "advisory": True},
        {"name": "benchmark_doctor", "ok": bool(benchmark.get("ok"))},
    ]
    generated_ok = all(bool(item.get("ok")) for item in health.get("generated_checks", []))
    next_command = FINISH_COMMAND if changed else "none, repo is healthy"
    next_command_reason = (
        "Changed files need finish evidence."
        if changed
        else "No changed files and required generated checks are synchronized."
    )
    if not generated_ok:
        next_command = SYNC_COMMAND
        next_command_reason = "Generated routing or instruction artifacts are not synchronized."
    elif not bool(health.get("ok")) and not changed:
        next_command = WHAT_NOW_COMMAND
        next_command_reason = "Repository health failed without changed-file evidence to route."
    fix_suggestions = []
    if include_fix_suggestions:
        if not all(bool(item.get("ok")) for item in health.get("generated_checks", [])):
            fix_suggestions.append(SYNC_COMMAND)
        if changed:
            fix_suggestions.append(CHECK_CHANGED_DEEP_COMMAND)
        if not fix_suggestions:
            fix_suggestions.append("python -B .agents/manage.py finish")
    total_elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    slow_sections = [item for item in timings if float(item.get("elapsed_ms", 0) or 0) > 10000]
    validation_router = changed_validation_router_report(root, changed)
    validation_plan = validation_router.get("commands", []) if isinstance(validation_router.get("commands"), list) else []
    review_packet = (
        repo_changed.large_diff_review_packet(
            root,
            changed,
            validation_plan,
            navigation,
        )
        if changed
        else {"status": "no-changes", "ok": True}
    )
    context_budget = context_budget_report(root, changed, review_packet=review_packet if changed else None)
    input_fingerprint = (
        input_fingerprint_report(root, changed, validation_plan)
        if changed and review_packet.get("status") == "over-budget"
        else {}
    )
    review_progress = (
        dashboard_review_progress(
            root,
            changed=changed,
            validation_plan=validation_plan,
            review_packet=review_packet,
            input_fingerprint=input_fingerprint,
        )
        if changed and review_packet.get("status") == "over-budget"
        else {}
    )
    validation_progress = (
        repo_command_metrics.read_validation_progress(root)
        if changed and review_packet.get("status") == "over-budget"
        else {}
    )
    validation_progress_summary = _validation_progress_summary(
        validation_progress,
        input_fingerprint,
        validation_plan,
    )
    validation_current = (
        _review_coverage_is_complete(review_progress)
        and bool(validation_progress_summary.get("input_fingerprint_match", False))
    )
    progress_command = str(review_progress.get("next_pending_command") or "")
    if validation_current:
        planned_review_command = progress_command or str(validation_router.get("next_command") or "")
        validation_router = _mark_validation_router_satisfied(validation_router, validation_progress_summary)
        review_packet = _mark_review_packet_validation_satisfied(
            review_packet,
            planned_next_command=planned_review_command,
        )
    elif progress_command and not bool(review_progress.get("stale", False)):
        review_packet = dict(review_packet)
        review_packet["next_review_command"] = progress_command
        review_packet["progress_next_review_command"] = progress_command
    if changed and review_packet.get("status") == "over-budget" and generated_ok and bool(health.get("ok")):
        next_command, next_command_reason = _over_budget_next_action(
            review_progress,
            validation_router,
            validation_progress=validation_progress,
            input_fingerprint=input_fingerprint,
        )
    failed_checks = [str(item.get("name")) for item in checks if not item.get("ok") and not item.get("advisory")]
    advisory_failed_checks = [str(item.get("name")) for item in checks if not item.get("ok") and item.get("advisory")]
    plain_status = "Ready for daily work."
    if failed_checks:
        plain_status = f"Attention needed: {failed_checks[0]} is not ok."
    elif changed:
        plain_status = f"{len(changed)} changed file(s) need evidence."
    elif navigation.get("status") != "fresh":
        plain_status = str(navigation.get("summary") or "Navigation maps need attention.")
    elif (
        advisory_failed_checks
        or context_budget.get("warnings")
    ):
        plain_status = "Repo is healthy; advisory context notes are available."
    return {
        "schema_version": 1,
        "tool": "repo-dashboard",
        "ok": True,
        "status": "attention" if failed_checks else "ok",
        "watch_once": watch_once,
        "mode": "full" if full else "fast",
        "timings_ms": {str(item["name"]): item.get("elapsed_ms", 0.0) for item in timings},
        "timing_sections": timings,
        "total_elapsed_ms": total_elapsed_ms,
        "latency_budget": repo_command_metrics.timing_budget_report(
            "status-full" if full else "status-fast",
            total_elapsed_ms,
            timings=timings,
        ),
        "slow_sections": slow_sections,
        "why_this_took_long": [
            f"{item.get('name')} took {item.get('elapsed_ms')}ms"
            for item in slow_sections
        ],
        "branch": branch_name(root),
        "dirty_state": dirty,
        "changed_paths": changed,
        "changed_file_count": len(changed),
        "changed_groups": repo_changed.compact_path_groups(changed) if changed else "",
        "plain_status": plain_status,
        "context_budget": context_budget,
        "review_packet": review_packet,
        "review_progress": review_progress,
        "validation_progress": validation_progress_summary,
        "navigation": navigation,
        "validation_router": validation_router,
        "loaded_context_ledger": loaded_context_ledger(
            full=full,
            skip_local_ai=skip_local_ai,
            skip_github=skip_github,
            timings=timings,
        ),
        "command_cache": {
            "status": "transparent-read-only-cache",
            "cache_hit": False,
            "cache_key": git_cache_key(root, dirty),
            "policy": "Pure read-only sections may be deduplicated inside one command run; mutating commands are never cached.",
            "force_refresh": "rerun the command with --full or rerun after a new git change",
        },
        "command_trust": advisory_trust_report(timings),
        "external_blockers": [
            {
                "name": "github",
                "status": "not-checked",
                "reason": "dashboard is local-only; release-evidence classifies GitHub billing or hygiene blockers",
            }
        ],
        "trace_summary": trace_summary(
            "dashboard",
            total_elapsed_ms=total_elapsed_ms,
            timings=timings,
            changed_count=len(changed),
            mutates_repo=False,
        ),
        "checks": checks,
        "generated_checks": [
            {
                "name": item.get("name", ""),
                "ok": bool(item.get("ok")),
                "message": str(item.get("message") or "").strip(),
                "fix_command": SYNC_COMMAND,
            }
            for item in health.get("generated_checks", [])
            if isinstance(item, dict)
        ],
        "local_ai": local_ai,
        "benchmark": benchmark,
        "github_validation": github_validation,
        "evidence": evidence,
        "fix_suggestions": fix_suggestions,
        "skipped": [
            str(item.get("reason"))
            for item in timings
            if item.get("skipped") and str(item.get("reason", "")).strip()
        ],
        "next_command": next_command,
        "next_command_reason": next_command_reason,
    }
