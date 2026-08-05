#!/usr/bin/env python3
"""Low-context review routing, review-loop, and context-cost helpers."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from repo_support import repo_changed
from repo_support import repo_command_metrics
from repo_support import repo_cost_policy
from repo_support import repo_policy
from repo_support import repo_optimizations
from repo_support import repo_qol_costs
from repo_support import repo_qol_review_loop
from repo_support import repo_review_progress
from repo_support import repo_common as repo
from repo_support.repo_fingerprint import summarize_input_fingerprint
from repo_support.repo_navigation_status import navigation_context_trace, navigation_status
from repo_support.repo_qol_daily import (
    input_fingerprint_report,
    startup_context_report,
    summarize_startup_context_report,
)
from repo_support.repo_qol_render import (
    render_change_ledger,
    render_next_action,
    render_review_loop,
    render_review_next,
    render_review_progress,
)

def fast_navigation_status(root: Path) -> dict[str, Any]:
    try:
        return navigation_status(root, fast=True)
    except TypeError:
        return navigation_status(root)


def current_review_plan_packet(
    root: Path,
    *,
    changed: list[str] | None = None,
    scope: dict[str, Any] | None = None,
    validation_plan: list[dict[str, Any]] | None = None,
    navigation: dict[str, Any] | None = None,
    review_packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    changed = list(changed) if changed is not None else repo_changed.changed_files(root)
    scope = scope if isinstance(scope, dict) else (repo_changed.changed_scope(changed) if changed else {})
    validation_plan = (
        validation_plan
        if isinstance(validation_plan, list)
        else (repo_optimizations.changed_validation_plan(root, changed, scope, deep=False) if changed else [])
    )
    navigation = navigation if isinstance(navigation, dict) else fast_navigation_status(root)
    review_packet = (
        review_packet
        if isinstance(review_packet, dict)
        else (
            repo_changed.large_diff_review_packet(root, changed, validation_plan, navigation)
            if changed
            else {"status": "no-changes", "ok": True, "validation_first": []}
        )
    )
    policy, _policy_error = repo_cost_policy.load_cost_policy(root)
    review_loop = repo_cost_policy.review_loop_policy(policy)
    if isinstance(review_packet, dict):
        review_packet = dict(review_packet)
        review_packet.setdefault("review_batch_max_hunks", review_loop["max_hunks_per_batch"])
    fingerprint = input_fingerprint_report(root, changed, validation_plan) if changed else {}
    plan = repo_review_progress.build_review_plan(review_packet)
    return {
        "changed": changed,
        "scope": scope,
        "validation_plan": validation_plan,
        "navigation": navigation,
        "review_packet": review_packet,
        "input_fingerprint": fingerprint,
        "review_plan": plan,
    }


def current_review_progress_report(
    root: Path,
    *,
    mark_unit_id: str = "",
    mark_command: str = "",
    note: str = "",
    reset: bool = False,
    state_path: str | None = None,
    plan_factory: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    context = (plan_factory or current_review_plan_packet)(root)
    progress = repo_review_progress.review_progress_report(
        root,
        context["review_plan"],
        input_fingerprint=context["input_fingerprint"],
        state_path=state_path,
        mark_unit_id=mark_unit_id,
        mark_command=mark_command,
        note=note,
        reset=reset,
    )
    progress["changed_file_count"] = len(context["changed"])
    progress["review_packet_status"] = context["review_packet"].get("status", "unknown")
    progress["navigation"] = {
        "status": context["navigation"].get("status", "unknown"),
        "read_first": context["navigation"].get("read_first", ""),
        "next_command": context["navigation"].get("next_command", ""),
    }
    progress["input_fingerprint"] = summarize_input_fingerprint(context["input_fingerprint"]) if context["input_fingerprint"] else {}
    return progress


def next_action_report(
    root: Path,
    *,
    fast: bool = True,
    plan_factory: Callable[..., dict[str, Any]] | None = None,
    dashboard_factory: Callable[..., dict[str, Any]] | None = None,
    dashboard_summarizer: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    dashboard: dict[str, Any] = {}
    build_plan = plan_factory or current_review_plan_packet
    if fast:
        context = build_plan(root)
    else:
        if dashboard_factory is None:
            dashboard = {"status": "unavailable", "plain_status": ""}
        else:
            dashboard = dashboard_factory(root, full=True, skip_local_ai=True, skip_github=True)
        validation_router = dashboard.get("validation_router") if isinstance(dashboard.get("validation_router"), dict) else {}
        context = build_plan(
            root,
            changed=dashboard.get("changed_paths") if isinstance(dashboard.get("changed_paths"), list) else None,
            validation_plan=validation_router.get("commands") if isinstance(validation_router.get("commands"), list) else None,
            navigation=dashboard.get("navigation") if isinstance(dashboard.get("navigation"), dict) else None,
            review_packet=dashboard.get("review_packet") if isinstance(dashboard.get("review_packet"), dict) else None,
        )
    progress = repo_review_progress.review_progress_report(
        root,
        context["review_plan"],
        input_fingerprint=context["input_fingerprint"],
    )
    policy, _policy_error = repo_cost_policy.load_cost_policy(root)
    review_loop = repo_cost_policy.review_loop_policy(policy)
    navigation = context["navigation"]
    review_packet = context["review_packet"]
    required_validation = [
        item for item in context["validation_plan"] if isinstance(item, dict) and item.get("required") is not False
    ]
    next_command = str(dashboard.get("next_command") or "")
    why = str(dashboard.get("plain_status") or "")
    if not next_command:
        if not context["changed"] and navigation.get("status") != "fresh":
            next_command = str(navigation.get("next_command") or "python -B .agents/manage.py setup --check")
            why = str(navigation.get("summary") or "Navigation maps need attention before broad source reads.")
        elif context["changed"] and required_validation:
            next_command = str(required_validation[0].get("command") or "python -B .agents/manage.py check-changed --summary --compact --format json")
            why = f"{len(context['changed'])} changed file(s) need evidence."
        elif context["changed"]:
            next_command = "python -B .agents/manage.py check-changed --summary --compact --format json"
            why = f"{len(context['changed'])} changed file(s) need changed-scope validation."
        else:
            next_command = "none, repo is healthy"
            why = "No changed files detected by the fast next-action route."
    required_context: list[str] = []
    if navigation.get("read_first"):
        required_context.append(str(navigation["read_first"]))
    review_loop_command = repo_review_progress.default_review_loop_command(
        max_units=review_loop["max_units"],
        max_estimated_tokens=review_loop["max_estimated_tokens"],
        max_elapsed_ms=review_loop["max_elapsed_ms"],
    )
    review_forecast = repo_review_progress.build_review_loop_forecast(
        context["review_plan"],
        completed_unit_ids=progress.get("completed_units", []),
        max_units=review_loop["max_units"],
        max_estimated_tokens=review_loop["max_estimated_tokens"],
    )
    review_owner_forecast = repo_review_progress.build_review_owner_forecast(context["review_plan"], review_forecast)
    if review_packet.get("status") == "over-budget" and progress.get("next_pending_command"):
        next_command = review_loop_command
        why = "Changed diff exceeds the review budget; run the bounded review loop before broad raw diff review."
    validation_after = "python -B .agents/manage.py check-changed --summary --compact --format json"
    if next_command and "review-loop" in next_command:
        validation_after = "python -B .agents/manage.py review-progress --summary --compact --format json"
    elif next_command and "review-packet" in next_command:
        validation_after = (
            'python -B .agents/manage.py review-progress --mark-command "'
            + next_command.replace('"', '\\"')
            + '" --summary --compact --format json'
        )
    if dashboard and dashboard_summarizer:
        dashboard_summary = dashboard_summarizer(dashboard, compact=True)
    elif dashboard:
        dashboard_summary = {"status": dashboard.get("status", "unknown")}
    else:
        dashboard_summary = {"status": "skipped-fast"}
    total_elapsed_ms = repo_command_metrics.elapsed_ms_since(started)
    return {
        "schema_version": 1,
        "tool": "skill-manager.next-action",
        "ok": True,
        "status": "ready",
        "total_elapsed_ms": total_elapsed_ms,
        "latency_budget": repo_command_metrics.timing_budget_report("next-action", total_elapsed_ms),
        "source": "fast changed-file route plus review-progress" if fast else "status --full plus review-progress",
        "next_command": next_command,
        "why": why,
        "required_context": list(dict.fromkeys(required_context)),
        "validation_after": validation_after,
        "stop_condition": "Stop and run what-now when the next command fails or required context is missing/stale.",
        "navigation": {
            "status": navigation.get("status", "unknown"),
            "read_first": navigation.get("read_first", ""),
            "next_command": navigation.get("next_command", ""),
        },
        "context_trace": navigation_context_trace(navigation),
        "review_progress": repo_review_progress.summarize_review_progress(progress),
        "review_autopilot": {
            "status": "default" if review_packet.get("status") == "over-budget" else "not-needed",
            "command": review_loop_command if review_packet.get("status") == "over-budget" else "",
            "max_units": review_loop["max_units"],
            "max_estimated_tokens": review_loop["max_estimated_tokens"],
            "max_elapsed_ms": review_loop["max_elapsed_ms"],
            "max_hunks_per_batch": review_loop["max_hunks_per_batch"],
            "forecast": review_forecast if review_packet.get("status") == "over-budget" else {},
        },
        "review_owner_forecast": review_owner_forecast if review_packet.get("status") == "over-budget" else {},
        "review_packet": {
            "status": review_packet.get("status", "unknown"),
            "changed_diff_estimated_tokens": review_packet.get("changed_diff_estimated_tokens", 0),
            "review_budget_tokens": review_packet.get("review_budget_tokens", 0),
            "next_review_command": review_packet.get("next_review_command", ""),
        },
        "local_ai_route": {
            "status": "advisory-only",
            "allowed_use_cases": ["validation-triage", "changed-files-summary", "handoff-draft"],
            "latency_budget_seconds": 45,
            "fallback": "deterministic launcher commands and compact evidence packets",
        },
        "dashboard_summary": dashboard_summary,
    }


def summarize_next_action_report(
    report: dict[str, Any],
    *,
    compact: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    review_autopilot = dict(report.get("review_autopilot") if isinstance(report.get("review_autopilot"), dict) else {})
    if isinstance(review_autopilot.get("forecast"), dict):
        review_autopilot["forecast"] = repo_review_progress.summarize_review_loop_forecast(review_autopilot["forecast"])
    review_progress = report.get("review_progress", {}) if isinstance(report.get("review_progress"), dict) else {}
    if compact and review_progress:
        review_progress = {
            key: review_progress[key]
            for key in (
                "status",
                "review_state",
                "completed_unit_count",
                "pending_unit_count",
                "stale",
                "current_unit",
                "next_pending_command",
            )
            if key in review_progress
        }
        if review_progress.get("next_pending_command"):
            review_progress["next_pending_command"] = repo_changed.compact_next_command(
                review_progress["next_pending_command"],
                root=root,
            )
    context_trace = report.get(
        "context_trace",
        navigation_context_trace(report.get("navigation", {}) if isinstance(report.get("navigation"), dict) else {}),
    )
    if compact and isinstance(context_trace, dict):
        context_trace = {
            key: context_trace[key]
            for key in ("status", "read_first", "read_now", "skip_raw_json", "next_command")
            if key in context_trace
        }
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.next-action"),
        "ok": bool(report.get("ok", True)),
        "status": report.get("status", "unknown"),
        "next_command": (
            repo_changed.compact_next_command(report.get("next_command", ""), root=root)
            if compact
            else report.get("next_command", "")
        ),
        "why": report.get("why", ""),
        "required_context": report.get("required_context", []),
        "validation_after": report.get("validation_after", ""),
        "stop_condition": report.get("stop_condition", ""),
        "navigation": report.get("navigation", {}),
        "context_trace": context_trace,
        "latency_budget": report.get("latency_budget", {}),
        "review_progress": review_progress,
        "review_autopilot": review_autopilot,
        "review_owner_forecast": report.get("review_owner_forecast", {}),
        "local_ai_route": report.get("local_ai_route", {}),
    }
    if not compact:
        output["review_packet"] = report.get("review_packet", {})
        output["dashboard_summary"] = report.get("dashboard_summary", {})
    else:
        output.pop("local_ai_route", None)
        output.pop("review_owner_forecast", None)
    return repo_command_metrics.attach_output_budget(output, "next-action")


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
    next_action_factory: Callable[..., dict[str, Any]] | None = None,
    progress_factory: Callable[..., dict[str, Any]] | None = None,
    runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return repo_qol_review_loop.review_loop_report(
        root,
        max_units=max_units,
        timeout_seconds=timeout_seconds,
        max_estimated_tokens=max_estimated_tokens,
        max_elapsed_ms=max_elapsed_ms,
        include_validation=include_validation,
        dry_run=dry_run,
        reset_stale=reset_stale,
        next_action_factory=next_action_factory or next_action_report,
        progress_factory=progress_factory or current_review_progress_report,
        runner=runner,
        plan_factory=current_review_plan_packet,
    )


def summarize_review_loop_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    return repo_qol_review_loop.summarize_review_loop_report(report, compact=compact)


def review_next_report(
    root: Path,
    *,
    timeout_seconds: int = 120,
    include_validation: bool = False,
    dry_run: bool = False,
    next_action_factory: Callable[..., dict[str, Any]] | None = None,
    progress_factory: Callable[..., dict[str, Any]] | None = None,
    runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return repo_qol_review_loop.review_next_report(
        root,
        timeout_seconds=timeout_seconds,
        include_validation=include_validation,
        dry_run=dry_run,
        next_action_factory=next_action_factory or next_action_report,
        progress_factory=progress_factory or current_review_progress_report,
        runner=runner,
        plan_factory=current_review_plan_packet,
    )


def summarize_review_next_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    return repo_qol_review_loop.summarize_review_next_report(report, compact=compact)


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
    completion_factory: Callable[..., dict[str, Any]],
    loop_factory: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return repo_qol_review_loop.review_autopilot_report(
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
        completion_factory=completion_factory,
        loop_factory=loop_factory or review_loop_report,
    )


def summarize_review_autopilot_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    return repo_qol_review_loop.summarize_review_autopilot_report(report, compact=compact)


def _context_file_rows(root: Path, paths: list[str]) -> tuple[list[dict[str, Any]], int]:
    return repo_qol_costs.context_file_rows(root, paths)


def _saved_percent(raw_tokens: int, route_tokens: int) -> float:
    return repo_qol_costs.saved_percent(raw_tokens, route_tokens)


def context_cost_benchmark_report(
    root: Path,
    *,
    min_saved_percent: float = 25.0,
    record: bool = False,
    history_path: str | None = None,
    startup_factory: Callable[..., dict[str, Any]] | None = None,
    next_action_factory: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return repo_qol_costs.context_cost_benchmark_report(
        root,
        min_saved_percent=min_saved_percent,
        record=record,
        history_path=history_path,
        startup_factory=startup_factory,
        next_action_factory=next_action_factory or next_action_report,
        next_action_summarizer=summarize_next_action_report,
    )


def summarize_context_cost_benchmark_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    return repo_qol_costs.summarize_context_cost_benchmark_report(report, compact=compact)


def render_context_cost_benchmark(report: dict[str, Any]) -> str:
    return repo_qol_costs.render_context_cost_benchmark(report)


def _path_reason(path: str) -> str:
    value = path.replace("\\", "/")
    if value == "AGENTS.md" or "repository-instructions" in value or "copilot-instructions" in value:
        return "agent instruction and adapter routing"
    if "repo_qol" in value or "repo_review" in value or "repo_commands" in value:
        return "low-context command routing and review automation"
    if "repo_context_guardrails" in value or "repo_portability" in value:
        return "misuse prevention and portability gates"
    if "local-ai-benchmark-workflow" in value:
        return "local AI benchmark workflow evidence and runtime support"
    if "agent-benchmarking" in value:
        return "agent benchmark shared measurement support"
    if "navigation/artifacts/maps" in value:
        return "generated navigation map refresh"
    if value.startswith("docs/"):
        return "documentation update"
    return "changed implementation or support file"


def _dominant_reason(paths: list[str]) -> str:
    counts: dict[str, int] = {}
    for path in paths:
        reason = _path_reason(path)
        counts[reason] = counts.get(reason, 0) + 1
    if not counts:
        return "no changed files"
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _owner_validation(owner_packet: dict[str, Any], validation_plan: list[dict[str, Any]]) -> str:
    validation = owner_packet.get("validation_first") if isinstance(owner_packet.get("validation_first"), list) else []
    for item in validation:
        value = str(item).strip()
        if value:
            return value
    return _owner_validation_command(str(owner_packet.get("owner", "")), validation_plan)


def _read_first_paths(rows: list[Any], *, limit: int = 4) -> list[str]:
    paths: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            value = str(row.get("path") or "").strip()
        else:
            value = str(row).strip()
        if value and value not in paths:
            paths.append(value)
        if len(paths) >= limit:
            break
    return paths


def _tool_only_navigation_json_path(path: str) -> bool:
    value = path.replace("\\", "/")
    return value.startswith("automations/navigation/") and value.endswith(".json")


def _split_human_and_tool_only_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    human: list[str] = []
    tool_only: list[str] = []
    for path in paths:
        target = tool_only if _tool_only_navigation_json_path(path) else human
        if path and path not in target:
            target.append(path)
    return human, tool_only


def _tool_only_path_sort_key(path: str) -> tuple[int, str]:
    value = path.replace("\\", "/")
    priority = {
        "automations/navigation/artifacts/maps/handoff.json": 0,
        "automations/navigation/artifacts/maps/staleness.json": 1,
    }.get(value, 9)
    return priority, value


def _compact_validation_command(command: Any) -> str:
    value = str(command or "").strip()
    limit = repo_policy.int_value(
        repo_policy.project_root(), "limits.context.validation_command_chars"
    )
    if len(value) <= limit:
        return value
    return "python -B .agents/manage.py check-additions --summary --compact --format json"


def _review_budget_tokens(root: Path) -> tuple[int, str]:
    policy, policy_error = repo_cost_policy.load_cost_policy(root)
    routes = repo_cost_policy.task_routes(policy)
    return repo_cost_policy.int_field(routes.get("review", {}).get("max_context_tokens"), 5000), policy_error or ""


def _changed_files_and_statuses(root: Path) -> tuple[list[str], dict[str, set[str]]]:
    statuses = repo_changed.changed_file_statuses(root)
    if statuses:
        return sorted(statuses), statuses
    changed = repo_changed.changed_files(root)
    return changed, {path: {"M"} for path in changed}


def _changed_context_owner_groups(
    root: Path,
    changed: list[str],
    validation_plan: list[dict[str, Any]],
    *,
    review_budget: int,
    statuses: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    statuses = statuses if statuses is not None else repo_changed.changed_file_statuses(root)
    try:
        estimates = repo_changed.changed_path_token_estimates(root, changed, statuses=statuses)
    except TypeError:
        estimates = repo_changed.changed_path_token_estimates(root, changed)
    grouped: dict[str, dict[str, Any]] = {}
    risk_order = {"high": 0, "medium": 1, "low": 2}
    for path in changed:
        owner = repo_changed.review_owner(path)
        risk = repo_changed.review_risk(path)
        estimate = int(estimates.get(path, {}).get("estimated_tokens", 0) or 0)
        marker = "".join(sorted(statuses.get(path, {"M"})))
        group = grouped.setdefault(
            owner,
            {
                "owner": owner,
                "changed_file_count": 0,
                "estimated_changed_tokens": 0,
                "risk_counts": {},
                "rows": [],
                "paths": [],
            },
        )
        group["changed_file_count"] += 1
        group["estimated_changed_tokens"] += estimate
        group["risk_counts"][risk] = group["risk_counts"].get(risk, 0) + 1
        group["paths"].append(path)
        group["rows"].append(
            {
                "path": path,
                "risk": risk,
                "status": marker,
                "estimated_tokens": estimate,
            }
        )
    owner_groups: list[dict[str, Any]] = []
    for group in grouped.values():
        rows = sorted(
            group["rows"],
            key=lambda row: (
                risk_order.get(str(row.get("risk")), 9),
                -int(row.get("estimated_tokens", 0) or 0),
                str(row.get("path", "")),
            ),
        )
        read_first_candidates = _read_first_paths(rows, limit=6)
        read_first, _tool_only_from_candidates = _split_human_and_tool_only_paths(read_first_candidates)
        _human_all, tool_only_inputs = _split_human_and_tool_only_paths(sorted(group.get("paths", [])))
        tool_only_inputs = sorted(tool_only_inputs, key=_tool_only_path_sort_key)
        estimated_tokens = int(group.get("estimated_changed_tokens", 0) or 0)
        status = "over-budget" if estimated_tokens > review_budget else "within-budget"
        owner = str(group.get("owner", ""))
        owner_groups.append(
            {
                "owner": owner,
                "status": status,
                "changed_file_count": group.get("changed_file_count", 0),
                "estimated_changed_tokens": estimated_tokens,
                "risk_counts": dict(sorted(group.get("risk_counts", {}).items())),
                "risk_tags": sorted(
                    str(key)
                    for key, value in group.get("risk_counts", {}).items()
                    if int(value or 0) > 0
                ),
                "read_first": read_first,
                "tool_only_inputs": tool_only_inputs,
                "review_command": repo_changed.owner_review_command(owner, read_first[:1] or None),
                "validation_command": _owner_validation_command(owner, validation_plan),
                "paths": sorted(group.get("paths", [])),
            }
        )
    owner_groups.sort(
        key=lambda item: (
            0 if item.get("status") == "over-budget" else 1,
            -int(item.get("estimated_changed_tokens", 0) or 0),
            str(item.get("owner", "")),
        )
    )
    return owner_groups


def changed_context_report(root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    changed, statuses = _changed_files_and_statuses(root)
    navigation = fast_navigation_status(root)
    if not changed:
        total_elapsed_ms = repo_command_metrics.elapsed_ms_since(started)
        report = {
            "schema_version": 1,
            "tool": "skill-manager.changed-context",
            "ok": True,
            "status": "clean",
            "total_elapsed_ms": total_elapsed_ms,
            "latency_budget": repo_command_metrics.timing_budget_report("changed-context", total_elapsed_ms),
            "changed_file_count": 0,
            "navigation": {
                "status": navigation.get("status", "unknown"),
                "read_first": navigation.get("read_first", ""),
                "next_command": navigation.get("next_command", ""),
            },
            "owner_groups": [],
            "comparison": {
                "raw_diff_input_tokens": 0,
                "selected_route_input_tokens": 0,
                "saved_input_tokens_vs_raw": 0,
                "saved_input_percent_vs_raw": 0.0,
            },
            "next_command": "none, no changed files",
            "boundary": "No changed files detected; no changed-context packet is needed.",
        }
        return report
    scope = repo_changed.changed_scope(changed)
    validation_plan = repo_optimizations.changed_validation_plan(root, changed, scope, deep=False)
    review_budget, policy_error = _review_budget_tokens(root)
    owner_groups = _changed_context_owner_groups(
        root,
        changed,
        validation_plan,
        review_budget=review_budget,
        statuses=statuses,
    )
    raw_tokens = sum(int(group.get("estimated_changed_tokens", 0) or 0) for group in owner_groups)
    selected_paths = [
        "AGENTS.md",
        str(navigation.get("read_first") or ""),
        *(
            owner_groups[0].get("read_first", [])
            if owner_groups and isinstance(owner_groups[0].get("read_first"), list)
            else []
        ),
    ]
    route_rows, route_tokens = _context_file_rows(root, selected_paths)
    comparison = {
        "raw_diff_input_tokens": raw_tokens,
        "selected_route_input_tokens": route_tokens,
        "saved_input_tokens_vs_raw": max(0, raw_tokens - route_tokens),
        "saved_input_percent_vs_raw": _saved_percent(raw_tokens, route_tokens),
        "token_counter": "raw diff estimate vs selected route file bytes/4",
        "route_paths": route_rows,
    }
    next_command = str((owner_groups[0].get("review_command", "") if owner_groups else "") or "python -B .agents/manage.py check-changed --summary --compact --format json")
    total_elapsed_ms = repo_command_metrics.elapsed_ms_since(started)
    return {
        "schema_version": 1,
        "tool": "skill-manager.changed-context",
        "ok": True,
        "status": "ready",
        "total_elapsed_ms": total_elapsed_ms,
        "latency_budget": repo_command_metrics.timing_budget_report("changed-context", total_elapsed_ms),
        "changed_file_count": len(changed),
        "changed_groups": repo_changed.compact_path_groups(changed),
        "navigation": {
            "status": navigation.get("status", "unknown"),
            "read_first": navigation.get("read_first", ""),
            "next_command": navigation.get("next_command", ""),
        },
        "owner_groups": owner_groups,
        "review_packet": {
            "status": "over-budget" if raw_tokens > review_budget else "within-budget",
            "review_budget_tokens": review_budget,
            "changed_diff_estimated_tokens": raw_tokens,
            "tokens_over_review_budget": max(0, raw_tokens - review_budget),
            "next_review_command": next_command,
            "policy_error": policy_error,
        },
        "comparison": comparison,
        "validation_plan_summary": repo_optimizations.validation_plan_summary(validation_plan),
        "next_command": next_command,
        "boundary": (
            "Compact changed-context packet uses owner-level routing only; run review-packet for source slices, "
            "hunks, or full validation output."
        ),
    }


def summarize_changed_context_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    groups = []
    source_groups = report.get("owner_groups", []) if isinstance(report.get("owner_groups"), list) else []
    group_limit = 5 if compact else len(source_groups)
    for item in source_groups[:group_limit]:
        if not isinstance(item, dict):
            continue
        read_first = item.get("read_first", [])
        tool_only_inputs = item.get("tool_only_inputs", [])
        if compact:
            read_first = read_first[:3] if isinstance(read_first, list) else []
            tool_only_inputs = tool_only_inputs[:3] if isinstance(tool_only_inputs, list) else []
        row = {
            "owner": item.get("owner", ""),
            "status": item.get("status", "unknown"),
            "changed_file_count": item.get("changed_file_count", 0),
            "estimated_changed_tokens": item.get("estimated_changed_tokens", 0),
            "risk_counts": item.get("risk_counts", {}),
            "read_first": read_first,
            "tool_only_inputs": tool_only_inputs,
            "review_command": item.get("review_command", ""),
            "validation_command": _compact_validation_command(item.get("validation_command", "")) if compact else item.get("validation_command", ""),
        }
        if not compact:
            row["paths"] = item.get("paths", [])
        groups.append(row)
    comparison = dict(report.get("comparison") if isinstance(report.get("comparison"), dict) else {})
    if compact:
        comparison.pop("route_paths", None)
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.changed-context"),
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", "unknown"),
        "changed_file_count": report.get("changed_file_count", 0),
        "changed_groups": report.get("changed_groups", ""),
        "navigation": report.get("navigation", {}),
        "latency_budget": report.get("latency_budget", {}),
        "owner_group_count": len(source_groups),
        "owner_groups_returned": len(groups),
        "omitted_owner_group_count": max(0, len(source_groups) - len(groups)),
        "owner_groups": groups,
        "comparison": comparison,
        "review_packet": report.get("review_packet", {}),
        "validation_plan_summary": report.get("validation_plan_summary", {}),
        "next_command": report.get("next_command", ""),
        "boundary": report.get("boundary", ""),
    }
    if compact:
        output.pop("changed_groups", None)
    elif not output.get("changed_groups"):
        output.pop("changed_groups", None)
    return repo_command_metrics.attach_output_budget(output, "changed-context")


def render_changed_context(report: dict[str, Any]) -> str:
    lines = [
        "# Changed Context",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Changed files: {report.get('changed_file_count', 0)}",
        f"- Next command: `{report.get('next_command', '')}`",
        "",
        "## Owners",
        "",
    ]
    for item in report.get("owner_groups", []) if isinstance(report.get("owner_groups"), list) else []:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- `{item.get('owner', '')}`: {item.get('changed_file_count', 0)} file(s), "
            f"{item.get('estimated_changed_tokens', 0)} tokens, `{item.get('review_command', '')}`"
        )
    lines.extend(["", f"Boundary: {report.get('boundary', '')}", ""])
    return "\n".join(lines)


def _owner_key(owner: str) -> str:
    if owner.startswith("skill:") or owner.startswith("workflow:"):
        return owner.split(":", 1)[1]
    return owner


def _owner_validation_command(owner: str, validation_plan: list[dict[str, Any]]) -> str:
    key = _owner_key(owner)
    for item in validation_plan:
        if not isinstance(item, dict):
            continue
        command_owner = str(item.get("owner", ""))
        command = str(item.get("command", ""))
        if not command:
            continue
        if command_owner == owner or command_owner == key or key in command:
            return command
    for item in validation_plan:
        if isinstance(item, dict) and item.get("required") is not False and str(item.get("command", "")).strip():
            return str(item["command"])
    return ""


def change_ledger_report(root: Path) -> dict[str, Any]:
    changed = repo_changed.changed_files(root)
    statuses = repo_changed.changed_file_statuses(root)
    scope = repo_changed.changed_scope(changed) if changed else {}
    validation_plan = repo_optimizations.changed_validation_plan(root, changed, scope, deep=False) if changed else []
    navigation = fast_navigation_status(root)
    groups: dict[str, dict[str, Any]] = {}
    for path in changed:
        owner = repo_changed.review_owner(path)
        risk = repo_changed.review_risk(path)
        row = groups.setdefault(
            owner,
            {
                "owner": owner,
                "changed_file_count": 0,
                "risk_counts": {},
                "status_counts": {},
                "paths": [],
            },
        )
        row["changed_file_count"] += 1
        row["risk_counts"][risk] = row["risk_counts"].get(risk, 0) + 1
        marker = "".join(sorted(statuses.get(path, {"M"})))
        row["status_counts"][marker] = row["status_counts"].get(marker, 0) + 1
        row["paths"].append({"path": path, "status": marker, "risk": risk, "reason": _path_reason(path)})
    owner_groups = []
    for group in groups.values():
        paths = [str(item.get("path", "")) for item in group.get("paths", []) if isinstance(item, dict)]
        group["reason"] = _dominant_reason(paths)
        group["paths"] = sorted(group["paths"], key=lambda item: str(item.get("path", "")))
        group["review_required"] = bool(paths)
        group["acceptance_status"] = "needs-review" if paths else "accepted"
        group["review_command"] = repo_changed.owner_review_command(str(group.get("owner", "")), paths) if paths else ""
        group["validation_command"] = _owner_validation_command(str(group.get("owner", "")), validation_plan)
        group["acceptance_evidence"] = (
            "Run the review command for source understanding, then run the validation command before commit."
            if paths
            else "No changed files for this owner."
        )
        owner_groups.append(group)
    owner_groups.sort(key=lambda item: (-int(item.get("changed_file_count", 0) or 0), str(item.get("owner", ""))))
    required_commands = [
        str(item.get("command", ""))
        for item in validation_plan
        if isinstance(item, dict) and item.get("required") is not False and str(item.get("command", "")).strip()
    ]
    return {
        "schema_version": 1,
        "tool": "skill-manager.change-ledger",
        "ok": True,
        "status": "ready" if changed else "clean",
        "changed_file_count": len(changed),
        "changed_groups": repo_changed.compact_path_groups(changed) if changed else "",
        "dominant_reason": _dominant_reason(changed),
        "navigation": {
            "status": navigation.get("status", "unknown"),
            "read_first": navigation.get("read_first", ""),
            "next_command": navigation.get("next_command", ""),
        },
        "owner_groups": owner_groups,
        "acceptance": {
            "status": "needs-review" if changed else "accepted",
            "review_required_owner_count": sum(1 for group in owner_groups if group.get("review_required")),
            "accepted_owner_count": sum(1 for group in owner_groups if not group.get("review_required")),
            "next_command": "python -B .agents/manage.py review-loop --max-units 3 --summary --compact --format json"
            if changed
            else "none, no changed files",
        },
        "validation_commands": required_commands[:12],
        "next_command": "python -B .agents/manage.py next-action --summary --compact --format json" if changed else "none, no changed files",
        "boundary": "Heuristic why-changed ledger from paths, owners, risks, and validation plan; verify exact intent against source before final claims.",
    }


def summarize_change_ledger_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    groups = []
    for item in report.get("owner_groups", []) if isinstance(report.get("owner_groups"), list) else []:
        if not isinstance(item, dict):
            continue
        group = {
            "owner": item.get("owner", ""),
            "changed_file_count": item.get("changed_file_count", 0),
            "reason": item.get("reason", ""),
            "risk_counts": item.get("risk_counts", {}),
            "status_counts": item.get("status_counts", {}),
            "acceptance_status": item.get("acceptance_status", "unknown"),
            "review_required": bool(item.get("review_required", False)),
            "review_command": item.get("review_command", ""),
            "validation_command": item.get("validation_command", ""),
        }
        if not compact:
            group["paths"] = item.get("paths", [])
        groups.append(group)
    return {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.change-ledger"),
        "ok": bool(report.get("ok", True)),
        "status": report.get("status", "unknown"),
        "changed_file_count": report.get("changed_file_count", 0),
        "changed_groups": report.get("changed_groups", ""),
        "dominant_reason": report.get("dominant_reason", ""),
        "navigation": report.get("navigation", {}),
        "owner_groups": groups,
        "acceptance": report.get("acceptance", {}),
        "validation_commands": report.get("validation_commands", []),
        "next_command": report.get("next_command", ""),
        "boundary": report.get("boundary", ""),
    }
