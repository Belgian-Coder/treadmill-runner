#!/usr/bin/env python3
"""User quality-of-life commands for the repository launcher."""

from __future__ import annotations

import json
import argparse
import time
from pathlib import Path
from typing import Any

from repo_support import repo_changed
from repo_support import repo_capability_audit
from repo_support import repo_command_metrics
from repo_support import repo_common as repo
from repo_support import repo_context_guardrails
from repo_support import repo_cost_policy
from repo_support import repo_doctor
from repo_support import repo_feedback
from repo_support import repo_health
from repo_support import repo_optimizations
from repo_support import repo_qol_finish
from repo_support import repo_qol_finish_packets
from repo_support import repo_qol_readiness
from repo_support import repo_qol_triage
from repo_support import repo_review_progress
from repo_support.repo_qol_capture import run_capture, run_capture_shell, run_json_local_ai
from repo_support.repo_navigation_status import navigation_status
from repo_support.repo_navigation_status import auto_refresh_navigation
from repo_support.repo_qol_evidence import (
    evidence_verify_report,
    latest_evidence_report,
    render_evidence_verify,
    summarize_evidence_report,
    summarize_evidence_verify_report,
)
from repo_support.repo_qol_finish import (
    compact_finish_checks,
    evidence_reference_exists,
    story_bug_out_of_scope_template_report,
    workflow_eval_all_command,
    workflow_run_evidence_reference_report,
    workflow_run_index_check_commands,
    workflows_with_run_folders,
)
from repo_support.repo_qol_github import github_validation_advisories, github_validation_trigger_state
from repo_support.repo_qol_render import (
    print_report,
    render_dashboard,
    render_finish_work,
    render_latest_evidence,
    render_resume_work,
    render_what_now,
)
from repo_support.repo_qol_daily import (
    attachment_route_report,
    changed_evidence_report,
    clean_context_proof_report,
    commit_readiness_report,
    configure_credential_profile,
    credential_doctor_report,
    input_fingerprint_report,
    render_attachment_route,
    render_changed_evidence,
    render_clean_context_proof,
    render_commit_readiness,
    render_configure_credential_profile,
    render_credential_doctor,
    render_startup_context,
    summarize_changed_evidence_report,
    summarize_clean_context_proof_report,
    summarize_credential_doctor_report,
    summarize_startup_context_report,
    changed_file_evidence,
    route_attachment,
    startup_context_report,
)

from repo_support.repo_qol_parsers import add_output_format, add_qol_parsers


def handle_qol_command(args: argparse.Namespace, root: Path) -> int | None:
    if args.command == "dashboard":
        report = dashboard_report(
            root,
            watch_once=bool(args.watch_once),
            full=bool(getattr(args, "full", False)),
            skip_local_ai=bool(getattr(args, "no_local_ai", False)),
            skip_github=bool(getattr(args, "no_github", False)),
            include_fix_suggestions=bool(getattr(args, "fix_suggestions", False)),
        )
        if bool(getattr(args, "capabilities", False)):
            report["capability_audit"] = repo_capability_audit.build_capability_audit(root, report)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_dashboard_report(
                report,
                compact=bool(getattr(args, "compact", False)),
                root=root,
            )
        return print_report(
            report,
            args.output_format,
            render_dashboard,
        )
    if args.command == "startup-context":
        report = startup_context_report(
            root,
            baseline_ref=getattr(args, "baseline_ref", None),
            compact=bool(getattr(args, "compact", False)),
        )
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_startup_context_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_startup_context)
    if args.command == "clean-context-proof":
        report = clean_context_proof_report(root)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_clean_context_proof_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_clean_context_proof)
    if args.command == "context-cost-benchmark":
        report = context_cost_benchmark_report(
            root,
            min_saved_percent=float(getattr(args, "min_saved_percent", 25.0) or 25.0),
            record=bool(getattr(args, "record", False)) and not bool(getattr(args, "no_record", False)),
            history_path=getattr(args, "history", None),
        )
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_context_cost_benchmark_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_context_cost_benchmark)
    if args.command == "next-action":
        report = next_action_report(root, fast=not bool(getattr(args, "full", False)))
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_next_action_report(
                report,
                compact=bool(getattr(args, "compact", False)),
                root=root,
            )
        return print_report(report, args.output_format, render_next_action)
    if args.command == "review-progress":
        report = current_review_progress_report(
            root,
            mark_unit_id=str(getattr(args, "mark_complete", "") or ""),
            mark_command=str(getattr(args, "mark_command", "") or ""),
            note=str(getattr(args, "note", "") or ""),
            reset=bool(getattr(args, "reset", False)),
            state_path=getattr(args, "state", None),
        )
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = repo_review_progress.summarize_review_progress(report)
        return print_report(report, args.output_format, render_review_progress)
    if args.command == "review-loop":
        report = review_loop_report(
            root,
            max_units=int(getattr(args, "max_units", 1) or 1),
            timeout_seconds=int(getattr(args, "timeout_seconds", 120) or 120),
            max_estimated_tokens=int(getattr(args, "max_estimated_tokens", 0) or 0),
            max_elapsed_ms=int(getattr(args, "max_elapsed_ms", 0) or 0),
            include_validation=bool(getattr(args, "include_validation", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            reset_stale=not bool(getattr(args, "no_reset_stale", False)),
        )
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_review_loop_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_review_loop)
    if args.command == "review-next":
        report = review_next_report(
            root,
            timeout_seconds=int(getattr(args, "timeout_seconds", 120) or 120),
            include_validation=bool(getattr(args, "include_validation", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_review_next_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_review_next)
    if args.command == "review-autopilot":
        report = review_autopilot_report(
            root,
            max_cycles=int(getattr(args, "max_cycles", 3) or 3),
            max_units_per_cycle=int(getattr(args, "max_units_per_cycle", 20) or 20),
            max_total_units=int(getattr(args, "max_total_units", 60) or 60),
            timeout_seconds=int(getattr(args, "timeout_seconds", 120) or 120),
            max_estimated_tokens=int(getattr(args, "max_estimated_tokens", 0) or 0),
            max_elapsed_ms=int(getattr(args, "max_elapsed_ms", 0) or 0),
            include_validation=bool(getattr(args, "include_validation", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            reset_stale=not bool(getattr(args, "no_reset_stale", False)),
            deep=bool(getattr(args, "deep", False)),
            release_full=bool(getattr(args, "release_full", False)),
            budget_intent=str(getattr(args, "budget_intent", "off") or "off"),
        )
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_review_autopilot_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_review_autopilot)
    if args.command == "claim-check":
        report = claim_check_report(
            root,
            input_value=getattr(args, "input_value", None),
            text=str(getattr(args, "text", "") or ""),
            evidence_files=getattr(args, "evidence_files", None) or [],
        )
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_claim_check_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_claim_check)
    if args.command == "budget-trend":
        report = repo_review_progress.budget_trend_summary(root, path_value=getattr(args, "state", None))
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_budget_trend_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_budget_trend)
    if args.command == "context-guardrails":
        paths = getattr(args, "paths", None)
        report = repo_context_guardrails.context_guardrail_report(
            root,
            paths=paths,
            include_protected=not bool(getattr(args, "changed_only", False)),
        )
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = repo_context_guardrails.summarize_context_guardrail_report(
                report,
                compact=bool(getattr(args, "compact", False)),
            )
        return print_report(
            report,
            args.output_format,
            lambda payload: repo_context_guardrails.render_context_guardrail_report(
                payload,
                compact=bool(getattr(args, "compact", False)),
            ),
        )
    if args.command == "context-use-check":
        report = repo_context_guardrails.context_use_check_report(root)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = repo_context_guardrails.summarize_context_use_check_report(
                report,
                compact=bool(getattr(args, "compact", False)),
            )
        return print_report(
            report,
            args.output_format,
            repo_context_guardrails.render_context_use_check_report,
        )
    if args.command == "command-budget-check":
        report = repo_command_metrics.command_budget_regression_report(
            root,
            profile=str(getattr(args, "profile", "fast") or "fast"),
            command_ids=getattr(args, "commands", None),
        )
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = repo_command_metrics.summarize_command_budget_regression_report(
                report,
                compact=bool(getattr(args, "compact", False)),
            )
        return print_report(
            report,
            args.output_format,
            repo_command_metrics.render_command_budget_regression_report,
        )
    if args.command == "what-now":
        report = what_now_report(
            root,
            input_value=None if bool(getattr(args, "last", False)) else args.input_value,
            command_label=args.command_label,
            from_command=args.from_command,
            explain_owner=bool(getattr(args, "explain_owner", False)),
        )
        if args.write_dir:
            write_report_files(root, report, args.write_dir, "what-now", render_what_now)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_what_now_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_what_now)
    if args.command == "resume-work":
        report = resume_work_report(root)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_resume_work_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(
            report,
            args.output_format,
            render_resume_work,
        )
    if args.command == "finish":
        release_full = bool(getattr(args, "release_full", False))
        deep = bool(args.deep or release_full)
        skip_benchmark = bool(args.skip_benchmark)
        report = finish_work_report(
            root,
            deep=deep,
            release_full=release_full,
            skip_benchmark=skip_benchmark,
            budget_intent=str(getattr(args, "budget_intent", "off") or "off"),
        )
        if args.commit_packet:
            write_report_files(root, report, args.commit_packet, "finish", render_finish_work)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_finish_work_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_finish_work)
    if args.command == "attachment-route":
        report = attachment_route_report(root, args.file_path)
        if args.write_plan:
            write_report_files(root, report, args.write_plan, "attachment-route", render_attachment_route)
        return print_report(report, args.output_format, render_attachment_route)
    if args.command == "evidence-index":
        report = latest_evidence_report(root, open_latest=bool(args.open_latest))
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_evidence_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(
            report,
            args.output_format,
            render_latest_evidence,
        )
    if args.command == "evidence-verify":
        report = evidence_verify_report(root, files=getattr(args, "files", None))
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_evidence_verify_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_evidence_verify)
    if args.command == "changed-evidence":
        report = changed_evidence_report(root)
        if args.write_dir:
            write_report_files(root, report, args.write_dir, "changed-evidence", render_changed_evidence)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_changed_evidence_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_changed_evidence)
    if args.command == "change-ledger":
        report = change_ledger_report(root)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_change_ledger_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_change_ledger)
    if args.command == "changed-context":
        report = changed_context_report(root)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_changed_context_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_changed_context)
    if args.command == "credential-doctor":
        if bool(getattr(args, "configure", False)):
            return print_report(configure_credential_profile(root, args), args.output_format, render_configure_credential_profile)
        report = credential_doctor_report(root)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = summarize_credential_doctor_report(report, compact=bool(getattr(args, "compact", False)))
        return print_report(report, args.output_format, render_credential_doctor)
    if args.command == "commit-readiness":
        return print_report(commit_readiness_report(root), args.output_format, render_commit_readiness)
    return None


def safe_repo_output_dir(root: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit("output path must stay inside the repository") from exc
    return resolved


def write_report_files(root: Path, report: dict[str, Any], output_dir: str, stem: str, renderer) -> list[str]:
    target = safe_repo_output_dir(root, output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / f"{stem}.json"
    md_path = target / f"{stem}.md"
    artifacts = [repo.relative(root, json_path), repo.relative(root, md_path)]
    report.setdefault("artifacts", [])
    if isinstance(report["artifacts"], list):
        report["artifacts"].extend(artifacts)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    md_path.write_text(renderer(report), encoding="utf-8", newline="\n")
    return artifacts


from repo_support import repo_qol_context


def current_review_plan_packet(
    root: Path,
    *,
    changed: list[str] | None = None,
    scope: dict[str, Any] | None = None,
    validation_plan: list[dict[str, Any]] | None = None,
    navigation: dict[str, Any] | None = None,
    review_packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return repo_qol_context.current_review_plan_packet(
        root,
        changed=changed,
        scope=scope,
        validation_plan=validation_plan,
        navigation=navigation,
        review_packet=review_packet,
    )


def current_review_progress_report(
    root: Path,
    *,
    mark_unit_id: str = "",
    mark_command: str = "",
    note: str = "",
    reset: bool = False,
    state_path: str | None = None,
) -> dict[str, Any]:
    return repo_qol_context.current_review_progress_report(
        root,
        mark_unit_id=mark_unit_id,
        mark_command=mark_command,
        note=note,
        reset=reset,
        state_path=state_path,
        plan_factory=current_review_plan_packet,
    )


def next_action_report(root: Path, *, fast: bool = True) -> dict[str, Any]:
    return repo_qol_context.next_action_report(
        root,
        fast=fast,
        plan_factory=current_review_plan_packet,
        dashboard_factory=dashboard_report,
        dashboard_summarizer=summarize_dashboard_report,
    )


def context_cost_benchmark_report(
    root: Path,
    *,
    min_saved_percent: float = 25.0,
    record: bool = False,
    history_path: str | None = None,
) -> dict[str, Any]:
    return repo_qol_context.context_cost_benchmark_report(
        root,
        min_saved_percent=min_saved_percent,
        record=record,
        history_path=history_path,
        startup_factory=startup_context_report,
        next_action_factory=next_action_report,
    )


def review_loop_report(
    root: Path,
    *,
    max_units: int = 1,
    timeout_seconds: int = 120,
    max_estimated_tokens: int = 0,
    max_elapsed_ms: int = 0,
    include_validation: bool = False,
    dry_run: bool = False,
    reset_stale: bool = False,
) -> dict[str, Any]:
    return repo_qol_context.review_loop_report(
        root,
        max_units=max_units,
        timeout_seconds=timeout_seconds,
        max_estimated_tokens=max_estimated_tokens,
        max_elapsed_ms=max_elapsed_ms,
        include_validation=include_validation,
        dry_run=dry_run,
        reset_stale=reset_stale,
        next_action_factory=next_action_report,
        progress_factory=current_review_progress_report,
        runner=run_capture_shell,
    )


def review_next_report(
    root: Path,
    *,
    timeout_seconds: int = 120,
    include_validation: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    return repo_qol_context.review_next_report(
        root,
        timeout_seconds=timeout_seconds,
        include_validation=include_validation,
        dry_run=dry_run,
        next_action_factory=next_action_report,
        progress_factory=current_review_progress_report,
        runner=run_capture_shell,
    )


def finish_projection_report(
    root: Path,
    *,
    deep: bool = False,
    release_full: bool = False,
    budget_intent: str = "off",
) -> dict[str, Any]:
    """Project review state to the sole completion command without executing validation."""
    changed = repo_changed.changed_files(root)
    navigation = navigation_status(root, fast=True)
    scope = repo_changed.changed_scope(changed) if changed else {}
    validation_plan = repo_optimizations.changed_validation_plan(root, changed, scope, deep=deep or release_full) if changed else []
    fingerprint = input_fingerprint_report(root, changed, validation_plan)
    review_packet = repo_changed.large_diff_review_packet(root, changed, validation_plan, navigation)
    review_plan = repo_review_progress.build_review_plan(review_packet)
    review_progress = repo_review_progress.review_progress_report(
        root,
        review_plan,
        input_fingerprint=fingerprint,
    )
    coverage = review_progress.get("coverage") if isinstance(review_progress.get("coverage"), dict) else {}
    pending_review = int(coverage.get("pending_review_unit_count", 0) or 0)
    needs_review = review_packet.get("status") == "over-budget" and pending_review > 0
    finish_parts = ["python -B .agents/manage.py finish"]
    if release_full:
        finish_parts.append("--release-full")
    elif deep:
        finish_parts.append("--deep")
    if budget_intent != "off":
        finish_parts.append(f"--budget-intent {budget_intent}")
    finish_parts.append("--summary --compact --format json")
    finish_command = " ".join(finish_parts)
    next_command = (
        str(review_progress.get("next_pending_command") or repo_review_progress.default_review_loop_command())
        if needs_review
        else finish_command
    )
    return {
        "schema_version": 1,
        "tool": "skill-manager.finish-projection",
        "ok": not needs_review,
        "status": "needs-review-autopilot" if needs_review else "needs-validation",
        "completion_supported": False,
        "deep": bool(deep or release_full),
        "release_full": bool(release_full),
        "budget_intent": budget_intent,
        "gates": {
            "navigation_status": navigation.get("status", "unknown"),
            "review_coverage_status": coverage.get("status", "unknown"),
            "pending_review_unit_count": pending_review,
            "failed_check_count": 0,
        },
        "missing_evidence": ["review-coverage"] if needs_review else ["finish"],
        "next_command": next_command,
        "boundary": "Review automation advances review only; finish owns all validation and completion evidence.",
    }


def review_autopilot_report(
    root: Path,
    *,
    max_cycles: int = 3,
    max_units_per_cycle: int = 20,
    max_total_units: int = 60,
    timeout_seconds: int = 120,
    max_estimated_tokens: int = 0,
    max_elapsed_ms: int = 0,
    include_validation: bool = False,
    dry_run: bool = False,
    reset_stale: bool = True,
    deep: bool = False,
    release_full: bool = False,
    budget_intent: str = "off",
) -> dict[str, Any]:
    return repo_qol_context.review_autopilot_report(
        root,
        max_cycles=max_cycles,
        max_units_per_cycle=max_units_per_cycle,
        max_total_units=max_total_units,
        timeout_seconds=timeout_seconds,
        max_estimated_tokens=max_estimated_tokens,
        max_elapsed_ms=max_elapsed_ms,
        include_validation=include_validation,
        dry_run=dry_run,
        reset_stale=reset_stale,
        deep=deep,
        release_full=release_full,
        budget_intent=budget_intent,
        completion_factory=finish_projection_report,
        loop_factory=review_loop_report,
    )


render_review_progress = repo_qol_context.render_review_progress
summarize_next_action_report = repo_qol_context.summarize_next_action_report
render_next_action = repo_qol_context.render_next_action
summarize_review_loop_report = repo_qol_context.summarize_review_loop_report
render_review_loop = repo_qol_context.render_review_loop
summarize_review_next_report = repo_qol_context.summarize_review_next_report
render_review_next = repo_qol_context.render_review_next
summarize_review_autopilot_report = repo_qol_context.summarize_review_autopilot_report
summarize_context_cost_benchmark_report = repo_qol_context.summarize_context_cost_benchmark_report
render_context_cost_benchmark = repo_qol_context.render_context_cost_benchmark
change_ledger_report = repo_qol_context.change_ledger_report
summarize_change_ledger_report = repo_qol_context.summarize_change_ledger_report
render_change_ledger = repo_qol_context.render_change_ledger
changed_context_report = repo_qol_context.changed_context_report
summarize_changed_context_report = repo_qol_context.summarize_changed_context_report
render_changed_context = repo_qol_context.render_changed_context


def render_review_autopilot(report: dict[str, Any]) -> str:
    lines = [
        "# Review Autopilot",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Completion supported: {bool(report.get('completion_supported', False))}",
        f"- Cycles: {report.get('cycle_count', 0)}",
        f"- Executed units: {report.get('executed_unit_count', 0)}",
        f"- Estimated review tokens: {report.get('estimated_review_tokens', 0)}",
        f"- Next command: `{report.get('next_command', '')}`",
        "",
        f"Boundary: {report.get('boundary', '')}",
        "",
    ]
    return "\n".join(lines)


def _load_json_evidence(root: Path, files: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    evidence: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    for value in files:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = root / path
        try:
            path = path.resolve()
            path.relative_to(root.resolve())
        except ValueError:
            issues.append({"path": value, "issue": "outside repository"})
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append({"path": value, "issue": str(exc)})
            continue
        if isinstance(data, dict):
            evidence.append(data)
        else:
            issues.append({"path": value, "issue": "JSON evidence was not an object"})
    return evidence, issues


def _evidence_text(evidence: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(item, sort_keys=True).lower() for item in evidence)


def _claim(status: str, claim: str, evidence: str, fix: str) -> dict[str, str]:
    return {"status": status, "claim": claim, "evidence": evidence, "fix": fix}


def claim_check_report(
    root: Path,
    *,
    input_value: str | None = None,
    text: str = "",
    evidence_files: list[str] | None = None,
) -> dict[str, Any]:
    source = "literal"
    claim_text = text
    if not claim_text and input_value:
        source, claim_text = read_input_text(root, input_value)
    evidence, evidence_issues = _load_json_evidence(root, evidence_files or [])
    if not evidence:
        evidence.append(
            summarize_dashboard_report(
                dashboard_report(root, skip_local_ai=True, skip_github=True),
                compact=True,
                root=root,
            )
        )
    lower = claim_text.lower()
    evidence_blob = _evidence_text(evidence)
    claims: list[dict[str, str]] = []

    def add_when(condition: bool, claim: str, proved: bool, evidence_label: str, fix: str) -> None:
        if condition:
            claims.append(_claim("proved" if proved else "unproven", claim, evidence_label if proved else "", fix))

    add_when(
        ("navigation" in lower or "maps" in lower) and "fresh" in lower,
        "navigation maps are fresh",
        '"navigation": {"status": "fresh"' in evidence_blob or '"navigation_status": "fresh"' in evidence_blob,
        "navigation.status=fresh",
        "Run `python -B .agents/manage.py status --fast --summary --compact --format json` or repo_navigation.py check and cite it.",
    )
    add_when(
        "finish" in lower and "passed" in lower,
        "finish passed",
        '"tool": "repo-finish"' in evidence_blob and '"status": "passed"' in evidence_blob,
        "repo-finish status=passed",
        "Run `python -B .agents/manage.py finish --summary --compact --format json` and attach the JSON evidence.",
    )
    add_when(
        "check" in lower and ("passed" in lower or "green" in lower),
        "repo check passed",
        '"phase": "repo-check"' in evidence_blob and '"ok": true' in evidence_blob,
        "finish progress phase repo-check ok=true",
        "Run `python -B .agents/manage.py check` or `finish` and attach the JSON evidence.",
    )
    add_when(
        "user-story" in lower and ("smoke" in lower or "workflow" in lower),
        "user-story workflow smoke passed",
        "workflow smoke --name user-story-workflow" in evidence_blob and (
            '"ok": true' in evidence_blob or '"status": "passed"' in evidence_blob
        ),
        "workflow smoke evidence ok=true",
        "Run `python -B .agents/manage.py workflow smoke --name user-story-workflow --summary --compact --format json` and attach it.",
    )
    add_when(
        "subagent" in lower,
        "fresh subagent validation passed",
        '"subagent_validation"' in evidence_blob and ('"status": "passed"' in evidence_blob or '"ok": true' in evidence_blob),
        "subagent_validation status=passed",
        "Record the fresh subagent review result in JSON evidence before claiming it.",
    )
    add_when(
        any(word in lower for word in ("committed", "pushed", "merged")),
        "git publish action completed",
        '"git_action"' in evidence_blob and ('"status": "passed"' in evidence_blob or '"ok": true' in evidence_blob),
        "git_action status=passed",
        "Only claim commit/push/merge after the corresponding git command succeeds and is recorded.",
    )
    if not claims:
        claims.append(
            _claim(
                "not-detected",
                "no supported completion claim detected",
                "",
                "Use explicit claims such as `finish passed`, `navigation maps are fresh`, or attach evidence files.",
            )
        )
    unproven = [item for item in claims if item.get("status") == "unproven"]
    return {
        "schema_version": 1,
        "tool": "skill-manager.claim-check",
        "ok": not unproven and not evidence_issues,
        "status": "passed" if not unproven and not evidence_issues else "failed",
        "source": source,
        "claim_count": len(claims),
        "unproven_count": len(unproven),
        "claims": claims,
        "evidence_file_count": len(evidence_files or []),
        "evidence_issues": evidence_issues,
        "boundary": "Pattern-based local proof check; absence of a finding is not broad semantic verification.",
    }


def summarize_claim_check_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.claim-check"),
        "ok": bool(report.get("ok", True)),
        "status": report.get("status", "unknown"),
        "claim_count": report.get("claim_count", 0),
        "unproven_count": report.get("unproven_count", 0),
        "claims": report.get("claims", []),
        "evidence_issues": report.get("evidence_issues", []),
    }
    if compact:
        output["claims"] = [
            {"status": item.get("status"), "claim": item.get("claim"), "fix": item.get("fix")}
            for item in report.get("claims", [])
            if isinstance(item, dict) and item.get("status") != "proved"
        ]
        if not output["claims"]:
            output.pop("claims", None)
        if not output["evidence_issues"]:
            output.pop("evidence_issues", None)
    return output


def render_claim_check(report: dict[str, Any]) -> str:
    lines = [
        "# Claim Check",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Claims: {report.get('claim_count', 0)}",
        f"- Unproven: {report.get('unproven_count', 0)}",
        "",
    ]
    for item in report.get("claims", []) if isinstance(report.get("claims"), list) else []:
        if isinstance(item, dict):
            lines.append(f"- `{item.get('status')}` {item.get('claim')}")
            if item.get("fix"):
                lines.append(f"  Fix: {item.get('fix')}")
    return "\n".join(lines)


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


def render_budget_trend(report: dict[str, Any]) -> str:
    latest = report.get("latest") if isinstance(report.get("latest"), dict) else {}
    lines = [
        "# Budget Trend",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Entries: {report.get('entry_count', 0)}",
        f"- Path: `{report.get('path', '')}`",
    ]
    if latest:
        lines.extend(
            [
                f"- Latest changed diff estimate: {latest.get('changed_diff_estimated_tokens', 0)}",
                f"- Latest next review unit estimate: {latest.get('next_review_unit_estimated_tokens', 0)}",
                f"- Latest finish elapsed seconds: {latest.get('finish_elapsed_seconds', 0)}",
            ]
        )
    lines.append(f"- Boundary: {report.get('boundary', '')}")
    return "\n".join(lines)


def finish_readiness_report(
    report: dict[str, Any],
    changed: list[str],
    validation_plan: list[dict[str, object]],
    review_packet: dict[str, Any],
    review_progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    failed_checks = [item for item in checks if isinstance(item, dict) and not item.get("ok")]
    required_validation = [
        item
        for item in validation_plan
        if isinstance(item, dict) and item.get("required") is not False
    ]
    owner_packets = (
        review_packet.get("owner_review_packets")
        if isinstance(review_packet.get("owner_review_packets"), list)
        else []
    )
    advisories = report.get("advisories") if isinstance(report.get("advisories"), list) else []
    navigation = report.get("navigation") if isinstance(report.get("navigation"), dict) else {}
    review_progress = review_progress if isinstance(review_progress, dict) else {}
    coverage = review_progress.get("coverage") if isinstance(review_progress.get("coverage"), dict) else {}
    pending_review_units = int(coverage.get("pending_review_unit_count", 0) or 0)
    review_units = int(coverage.get("review_unit_count", 0) or 0)
    review_coverage_complete = review_units == 0 or pending_review_units == 0
    reasons: list[str] = []
    if failed_checks:
        status = "blocked"
        reasons.append(f"{len(failed_checks)} finish check(s) failed")
        next_command = "python -B .agents/manage.py what-now --from-command \"python -B .agents/manage.py finish\""
    elif not bool(report.get("ok")):
        status = "blocked"
        reasons.append("finish report status is not ok")
        next_command = str(report.get("next_command") or "python -B .agents/manage.py finish --summary --compact --format json")
    elif review_packet.get("status") == "over-budget" and owner_packets and not review_coverage_complete:
        status = "needs-owner-review"
        first_owner = owner_packets[0]
        first_subpacket_tokens = int(first_owner.get("largest_owner_subpacket_estimated_tokens", 0) or 0)
        first_hunk_tokens = int(first_owner.get("largest_owner_hunk_estimated_tokens", 0) or 0)
        reasons.append(
            "changed diff exceeds review budget; run the bounded review loop before raw diff"
        )
        if first_hunk_tokens:
            reasons.append(f"largest hunk packet in first owner is {first_hunk_tokens} tokens")
        if first_subpacket_tokens:
            reasons.append(f"largest subpacket in first owner is {first_subpacket_tokens} tokens")
        next_command = repo_review_progress.default_review_loop_command()
    elif advisories:
        status = "ready-with-advisories"
        reasons.append(f"{len(advisories)} advisory item(s) remain")
        next_command = str(report.get("next_command") or "python -B .agents/manage.py commit-readiness")
    else:
        status = "ready"
        next_command = str(report.get("next_command") or "python -B .agents/manage.py commit-readiness")
    return {
        "schema_version": 1,
        "tool": "skill-manager.finish-readiness",
        "ok": status in {"ready", "ready-with-advisories"},
        "status": status,
        "changed_file_count": len(changed),
        "required_validation_count": len(required_validation),
        "failed_check_count": len(failed_checks),
        "review_packet_status": review_packet.get("status", "unknown"),
        "review_budget_tokens": review_packet.get("review_budget_tokens", 0),
        "changed_diff_estimated_tokens": review_packet.get("changed_diff_estimated_tokens", 0),
        "tokens_over_review_budget": review_packet.get("tokens_over_review_budget", 0),
        "owner_review_packet_count": len(owner_packets),
        "first_owner_review_command": str(owner_packets[0].get("next_command", "")) if owner_packets else "",
        "first_owner_summary_command": str(owner_packets[0].get("owner_summary_command", "")) if owner_packets else "",
        "owner_review_subpacket_count": review_packet.get("owner_review_subpacket_count", 0),
        "largest_owner_subpacket_estimated_tokens": review_packet.get("largest_owner_subpacket_estimated_tokens", 0),
        "owner_review_hunk_count": review_packet.get("owner_review_hunk_count", 0),
        "largest_owner_hunk_estimated_tokens": review_packet.get("largest_owner_hunk_estimated_tokens", 0),
        "next_review_command": review_packet.get("next_review_command", ""),
        "review_progress": repo_review_progress.summarize_review_progress(review_progress) if review_progress else {},
        "review_coverage": coverage,
        "cost_ledger": repo_cost_policy.compact_review_cost_ledger(review_packet.get("cost_ledger", {})),
        "navigation_status": navigation.get("status", "unknown"),
        "advisory_count": len(advisories),
        "reasons": reasons,
        "next_command": next_command,
    }


def finish_work_report(
    root: Path,
    *,
    deep: bool = False,
    release_full: bool = False,
    skip_benchmark: bool = False,
    budget_intent: str = "off",
    refresh_navigation: bool = True,
    record_state: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    previous = repo_qol_finish.run_capture
    repo_qol_finish.run_capture = run_capture
    try:
        navigation_auto_refresh = (
            auto_refresh_navigation(root)
            if refresh_navigation
            else {
                "schema_version": 1,
                "tool": "repo-navigation.auto-refresh",
                "ok": True,
                "status": "skipped-read-only",
                "written": [],
                "summary": "Navigation auto-refresh skipped for read-only readiness.",
            }
        )
        navigation = navigation_status(root)
        changed = repo_changed.changed_files(root)
        scope = repo_changed.changed_scope(changed) if changed else {}
        validation_plan = repo_optimizations.changed_validation_plan(root, changed, scope, deep=deep) if changed else []
        input_fingerprint = input_fingerprint_report(root, changed, validation_plan)
        review_packet = repo_changed.large_diff_review_packet(
            root,
            changed,
            validation_plan,
            navigation,
        )
        review_plan = repo_review_progress.build_review_plan(review_packet)
        review_progress = repo_review_progress.review_progress_report(
            root,
            review_plan,
            input_fingerprint=input_fingerprint,
        )
        if (
            not deep
            and budget_intent == "off"
            and bool(navigation_auto_refresh.get("ok", True))
        ):
            preflight_report = {
                "schema_version": 1,
                "tool": "repo-finish",
                "ok": True,
                "status": "skipped-review-blocked",
                "checks": [],
                "workflow_run_indexes": {"checked_count": 0, "workflows": []},
                "workflow_eval": {"status": "skipped-review-blocked", "ok": True},
                "workflow_evidence_references": {"status": "skipped-review-blocked", "ok": True},
                "story_bug_out_of_scope_templates": {"status": "skipped-review-blocked", "ok": True},
                "budget_hotspots": {"status": "skipped-review-blocked", "ok": True},
                "budget_gate": {"status": "skipped-review-blocked", "ok": True},
                "check_metrics": {},
                "progress_events": ["finish preflight skipped expensive checks"],
                "github_validation": {
                    "status": "skipped-review-blocked",
                    "automatic_triggers_enabled": False,
                    "automatic_triggers": [],
                },
                "advisories": [],
                "next_command": repo_review_progress.default_review_loop_command(),
                "navigation": navigation,
                "navigation_auto_refresh": navigation_auto_refresh,
                "input_fingerprint": input_fingerprint,
                "review_packet": review_packet,
                "review_progress": review_progress,
            }
            preflight_report["finish_readiness"] = finish_readiness_report(
                preflight_report,
                changed,
                validation_plan,
                review_packet,
                review_progress=review_progress,
            )
            if preflight_report["finish_readiness"].get("status") == "needs-owner-review":
                preflight_report["ok"] = False
                preflight_report["next_command"] = str(
                    preflight_report["finish_readiness"].get("next_command")
                    or repo_review_progress.default_review_loop_command()
                )
                preflight_report["fast_path"] = {
                    "status": "used",
                    "reason": "review coverage is the first blocking gate; expensive finish checks are deferred",
                    "fallback_command": "python -B .agents/manage.py finish --deep --summary --compact --format json",
                }
                if record_state:
                    preflight_report["budget_trend"] = repo_review_progress.append_budget_trend(
                        root,
                        preflight_report,
                        source="finish-preflight",
                    )
                    repo_feedback.record_finish_feedback(root, preflight_report)
                else:
                    preflight_report["budget_trend"] = {
                        "schema_version": 1,
                        "tool": "skill-manager.budget-trend",
                        "ok": True,
                        "status": "skipped-read-only",
                        "source": "finish-preflight",
                        "summary": "Budget trend recording skipped for read-only finish preflight.",
                    }
                preflight_report["latency_budget"] = repo_command_metrics.timing_budget_report(
                    "finish",
                    repo_command_metrics.elapsed_ms_since(started),
                )
                claim = repo_qol_readiness.finish_claim_report(
                    preflight_report,
                    deep=deep,
                    release_full=release_full,
                )
                preflight_report.update(
                    {
                        "completion_supported": claim["completion_supported"],
                        "missing_evidence": claim["missing_evidence"],
                        "claim_receipt": claim["claim_receipt"],
                    }
                )
                return preflight_report
        report = repo_qol_finish.finish_work_report(
            root,
            deep=deep,
            release_full=release_full,
            skip_benchmark=skip_benchmark,
            budget_intent=budget_intent,
        )
        report["navigation"] = navigation
        report["navigation_auto_refresh"] = navigation_auto_refresh
        if not bool(navigation_auto_refresh.get("ok", True)):
            report["ok"] = False
            report["status"] = "failed"
            advisories = report.setdefault("advisories", [])
            if isinstance(advisories, list):
                advisories.append(str(navigation_auto_refresh.get("summary") or "Navigation auto-refresh failed."))
            report["next_command"] = str(navigation_auto_refresh.get("next_command") or "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py check --target . --format json")
        report["input_fingerprint"] = input_fingerprint
        report["review_packet"] = review_packet
        report["review_progress"] = review_progress
        report["finish_readiness"] = finish_readiness_report(
            report,
            changed,
            validation_plan,
            report["review_packet"],
            review_progress=report["review_progress"],
        )
        readiness_next_command = str(report["finish_readiness"].get("next_command") or "").strip()
        if report["finish_readiness"].get("status") in {"blocked", "needs-owner-review"} and readiness_next_command:
            report["next_command"] = readiness_next_command
        if record_state:
            report["budget_trend"] = repo_review_progress.append_budget_trend(root, report, source="finish")
            repo_feedback.record_finish_feedback(root, report)
        else:
            report["budget_trend"] = {
                "schema_version": 1,
                "tool": "skill-manager.budget-trend",
                "ok": True,
                "status": "skipped-read-only",
                "source": "finish",
                "summary": "Budget trend recording skipped for read-only readiness.",
            }
        report["latency_budget"] = repo_command_metrics.timing_budget_report(
            "finish",
            repo_command_metrics.elapsed_ms_since(started),
        )
        claim = repo_qol_readiness.finish_claim_report(
            report,
            deep=deep,
            release_full=release_full,
        )
        report.update(
            {
                "completion_supported": claim["completion_supported"],
                "missing_evidence": claim["missing_evidence"],
                "claim_receipt": claim["claim_receipt"],
            }
        )
        report["next_command"] = claim["next_command"]
        if not claim["completion_supported"]:
            report["ok"] = False
            report["status"] = "blocked"
        return report
    finally:
        repo_qol_finish.run_capture = previous


summarize_finish_work_report = repo_qol_finish_packets.summarize_finish_work_report
summarize_what_now_report = repo_qol_triage.summarize_what_now_report


from repo_support.repo_qol_dashboard import (
    advisory_trust_report,
    branch_name,
    changed_validation_router_report,
    context_budget_report,
    dashboard_report,
    generated_sync_details,
    git_cache_key,
    loaded_context_ledger,
    summarize_dashboard_report,
    timed_section,
    trace_summary,
)


read_input_text = repo_qol_triage.read_input_text
first_failing_fact = repo_qol_triage.first_failing_fact
classify_failure_type = repo_qol_triage.classify_failure_type
infer_owner_and_command = repo_qol_triage.infer_owner_and_command


def what_now_report(
    root: Path,
    *,
    input_value: str | None = None,
    command_label: str = "",
    from_command: str | None = None,
    explain_owner: bool = False,
) -> dict[str, Any]:
    return repo_qol_triage.what_now_report(
        root,
        input_value=input_value,
        command_label=command_label,
        from_command=from_command,
        explain_owner=explain_owner,
        command_runner=run_capture_shell,
    )


def resume_work_report(root: Path) -> dict[str, Any]:
    return repo_qol_triage.resume_work_report(
        root,
        evidence_factory=latest_evidence_report,
        branch_factory=branch_name,
    )


owner_rationale = repo_qol_triage.owner_rationale
summarize_resume_work_report = repo_qol_triage.summarize_resume_work_report
