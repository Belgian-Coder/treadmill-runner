"""Review-loop and review-next helpers for low-context review automation."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from repo_support import repo_command_metrics
from repo_support import repo_review_progress
from repo_support.repo_qol_capture import run_capture_shell

PlanFactory = Callable[..., dict[str, Any]]
NextActionFactory = Callable[..., dict[str, Any]]
ProgressFactory = Callable[..., dict[str, Any]]
Runner = Callable[..., dict[str, Any]]
CompletionFactory = Callable[..., dict[str, Any]]
LoopFactory = Callable[..., dict[str, Any]]


def _compact_command_result(result: dict[str, Any]) -> dict[str, Any]:
    output = {
        "ok": bool(result.get("ok")),
        "status": result.get("status", 0),
        "command": result.get("command", ""),
        "elapsed_seconds": result.get("elapsed_seconds", 0),
        "timeout_seconds": result.get("timeout_seconds", 0),
        "output_summary": result.get("output_summary", {}),
    }
    if result.get("issue"):
        output["issue"] = result.get("issue")
    if result.get("raw_output_path"):
        output["raw_output_path"] = result.get("raw_output_path")
    if not result.get("ok") and result.get("distilled_output"):
        output["distilled_output"] = result.get("distilled_output")
    return output


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _progress_delta(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    executed_count: int,
    stale_reset_count: int,
    raw_output_paths: list[str],
) -> dict[str, Any]:
    before_completed = _int_value(before.get("completed_unit_count"))
    before_pending = _int_value(before.get("pending_unit_count"))
    after_completed = _int_value(after.get("completed_unit_count"))
    after_pending = _int_value(after.get("pending_unit_count"))
    return {
        "status": "changed" if executed_count or stale_reset_count else "unchanged",
        "completed_before": before_completed,
        "completed_after": after_completed,
        "completed_delta": after_completed - before_completed,
        "pending_before": before_pending,
        "pending_after": after_pending,
        "pending_delta": after_pending - before_pending,
        "review_state_before": before.get("review_state", ""),
        "review_state_after": after.get("review_state", ""),
        "stale_before": bool(before.get("stale", False)),
        "stale_after": bool(after.get("stale", False)),
        "stale_reset_count": stale_reset_count,
        "executed_unit_count": executed_count,
        "raw_output_path_count": len(raw_output_paths),
    }


def _status_after_unit_limit(next_command: str, review_progress: dict[str, Any], *, include_validation: bool) -> tuple[str, bool]:
    if review_progress.get("stale"):
        return "stale", False
    if not next_command or next_command.startswith("none"):
        return "complete", True
    if "review-packet" not in next_command and not include_validation:
        return "needs-validation", True
    return "limit-reached", True


def _select_review_loop_command(action_command: str, progress_command: str) -> str:
    if not progress_command:
        return action_command
    if "review-loop" in action_command:
        return progress_command
    if "review-packet" in progress_command and "review-packet" not in action_command:
        return progress_command
    return action_command


def _compact_completion(report: dict[str, Any]) -> dict[str, Any]:
    gates = report.get("gates") if isinstance(report.get("gates"), dict) else {}
    return {
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", "unknown"),
        "completion_supported": bool(report.get("completion_supported", False)),
        "pending_review_unit_count": gates.get("pending_review_unit_count", 0),
        "failed_check_count": gates.get("failed_check_count", 0),
        "missing_evidence": report.get("missing_evidence", []),
        "next_command": report.get("next_command", ""),
    }


def _compact_loop(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", "unknown"),
        "executed_unit_count": report.get("executed_unit_count", 0),
        "planned_unit_count": report.get("planned_unit_count", 0),
        "estimated_review_tokens": report.get("estimated_review_tokens", 0),
        "progress_delta": report.get("progress_delta", {}),
        "next_command": report.get("next_command", ""),
    }


def _completion_needs_review_loop(completion: dict[str, Any]) -> bool:
    next_command = str(completion.get("next_command") or "")
    return any(command in next_command for command in ("review-packet", "review-loop", "review-autopilot"))


def _review_autopilot_elapsed_cycle_floor_ms(max_elapsed_ms: int, timeout_seconds: int) -> int:
    if max_elapsed_ms <= 0:
        return 0
    timeout_ms = max(10, min(int(timeout_seconds or 120), 3600)) * 1000
    quarter_budget_ms = max(1, max_elapsed_ms // 4)
    return min(60_000, timeout_ms, quarter_budget_ms)


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
    completion_factory: CompletionFactory,
    loop_factory: LoopFactory,
) -> dict[str, Any]:
    started = time.perf_counter()
    max_cycles = max(1, min(int(max_cycles or 1), 20))
    max_units_per_cycle = max(1, min(int(max_units_per_cycle or 1), 50))
    max_total_units = max(1, min(int(max_total_units or max_units_per_cycle), 1000))
    timeout_seconds = max(10, min(int(timeout_seconds or 120), 3600))
    max_estimated_tokens = max(0, int(max_estimated_tokens or 0))
    max_elapsed_ms = max(0, int(max_elapsed_ms or 0))
    executed_units = 0
    estimated_review_tokens = 0
    cycles: list[dict[str, Any]] = []
    completion_kwargs: dict[str, Any] = {"deep": bool(deep or release_full), "budget_intent": budget_intent}
    if release_full:
        completion_kwargs["release_full"] = True
    completion = completion_factory(root, **completion_kwargs)
    status = str(completion.get("status", "unknown"))
    ok = bool(completion.get("ok", False))
    next_command = str(completion.get("next_command") or "")
    if completion.get("completion_supported"):
        return {
            "schema_version": 1,
            "tool": "skill-manager.review-autopilot",
            "ok": True,
            "status": status,
            "completion_supported": True,
            "deep": bool(deep),
            "release_full": bool(release_full),
            "budget_intent": budget_intent,
            "dry_run": bool(dry_run),
            "cycle_count": 0,
            "executed_unit_count": 0,
            "estimated_review_tokens": 0,
            "cycles": [],
            "completion": _compact_completion(completion),
            "next_command": next_command,
            "total_elapsed_ms": repo_command_metrics.elapsed_ms_since(started),
            "boundary": (
                "Review autopilot runs bounded review-loop batches and routes to finish. "
                "It does not push, merge, or replace semantic review."
            ),
        }
    if not _completion_needs_review_loop(completion):
        return {
            "schema_version": 1,
            "tool": "skill-manager.review-autopilot",
            "ok": bool(completion.get("ok", False)),
            "status": str(completion.get("status", "needs-validation")),
            "completion_supported": False,
            "deep": bool(deep),
            "release_full": bool(release_full),
            "budget_intent": budget_intent,
            "dry_run": bool(dry_run),
            "cycle_count": 0,
            "executed_unit_count": 0,
            "estimated_review_tokens": 0,
            "cycles": [],
            "completion": _compact_completion(completion),
            "next_command": next_command,
            "total_elapsed_ms": repo_command_metrics.elapsed_ms_since(started),
            "boundary": (
                "Review autopilot only advances review evidence. Follow next_command for validation or other blockers."
            ),
        }
    for cycle_index in range(1, max_cycles + 1):
        elapsed_ms = repo_command_metrics.elapsed_ms_since(started)
        if max_elapsed_ms and elapsed_ms >= max_elapsed_ms:
            status = "elapsed-limit"
            ok = True
            break
        if max_elapsed_ms and cycles:
            remaining_elapsed_ms = max_elapsed_ms - int(elapsed_ms)
            cycle_floor_ms = _review_autopilot_elapsed_cycle_floor_ms(max_elapsed_ms, timeout_seconds)
            if cycle_floor_ms and remaining_elapsed_ms < cycle_floor_ms:
                status = "elapsed-limit"
                ok = True
                break
        remaining_units = max_total_units - executed_units
        if remaining_units <= 0:
            status = "unit-limit"
            ok = True
            break
        remaining_tokens = 0
        if max_estimated_tokens:
            remaining_tokens = max_estimated_tokens - estimated_review_tokens
            if remaining_tokens <= 0:
                status = "token-limit"
                ok = True
                break
        loop = loop_factory(
            root,
            max_units=min(max_units_per_cycle, remaining_units),
            timeout_seconds=timeout_seconds,
            max_estimated_tokens=remaining_tokens if max_estimated_tokens else 0,
            max_elapsed_ms=max(0, max_elapsed_ms - int(elapsed_ms)) if max_elapsed_ms else 0,
            include_validation=include_validation,
            dry_run=dry_run,
            reset_stale=reset_stale,
        )
        loop_summary = _compact_loop(loop)
        cycles.append(
            {
                "index": cycle_index,
                "status": loop.get("status", "unknown"),
                "loop": loop_summary,
            }
        )
        next_command = str(loop.get("next_command") or next_command)
        if dry_run:
            status = "planned"
            ok = bool(loop.get("ok", True))
            break
        if not bool(loop.get("ok", False)):
            status = str(loop.get("status", "failed"))
            ok = False
            break
        cycle_units = _int_value(loop.get("executed_unit_count"))
        executed_units += cycle_units
        estimated_review_tokens += _int_value(loop.get("estimated_review_tokens"))
        if cycle_units <= 0:
            status = str(loop.get("status", "no-progress"))
            ok = status not in {"failed", "stale", "reset-failed", "mark-failed"}
            break
        completion = completion_factory(root, **completion_kwargs)
        next_command = str(completion.get("next_command") or next_command)
        if completion.get("completion_supported"):
            status = str(completion.get("status", "completion-supported"))
            ok = True
            break
        loop_status = str(loop.get("status", "unknown"))
        if loop_status in {"elapsed-limit", "token-limit", "unit-limit"}:
            status = loop_status
            ok = True
            break
        if not _completion_needs_review_loop(completion):
            status = str(completion.get("status", "needs-validation"))
            ok = bool(completion.get("ok", False))
            break
    else:
        status = "cycle-limit"
        ok = True
    return {
        "schema_version": 1,
        "tool": "skill-manager.review-autopilot",
        "ok": ok,
        "status": status,
        "completion_supported": bool(completion.get("completion_supported", False)),
        "deep": bool(deep),
        "release_full": bool(release_full),
        "budget_intent": budget_intent,
        "dry_run": bool(dry_run),
        "max_cycles": max_cycles,
        "max_units_per_cycle": max_units_per_cycle,
        "max_total_units": max_total_units,
        "max_estimated_tokens": max_estimated_tokens,
        "max_elapsed_ms": max_elapsed_ms,
        "cycle_count": len(cycles),
        "executed_unit_count": executed_units,
        "estimated_review_tokens": estimated_review_tokens,
        "cycles": cycles,
        "completion": _compact_completion(completion),
        "next_command": next_command,
        "total_elapsed_ms": repo_command_metrics.elapsed_ms_since(started),
        "boundary": (
            "Review autopilot runs bounded review-loop batches and routes to finish. "
            "It stops on completion support, failed/stale review evidence, non-review blockers, or explicit limits."
        ),
    }


def summarize_review_autopilot_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    cycles = report.get("cycles") if isinstance(report.get("cycles"), list) else []
    output: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.review-autopilot"),
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", "unknown"),
        "completion_supported": bool(report.get("completion_supported", False)),
        "deep": bool(report.get("deep", False)),
        "release_full": bool(report.get("release_full", False)),
        "budget_intent": report.get("budget_intent", "off"),
        "dry_run": bool(report.get("dry_run", False)),
        "cycle_count": report.get("cycle_count", len(cycles)),
        "executed_unit_count": report.get("executed_unit_count", 0),
        "estimated_review_tokens": report.get("estimated_review_tokens", 0),
        "completion": report.get("completion", {}),
        "next_command": report.get("next_command", ""),
        "latency_budget": repo_command_metrics.timing_budget_report(
            "review-autopilot",
            float(report.get("total_elapsed_ms", 0.0) or 0.0),
            budget_ms=_int_value(report.get("max_elapsed_ms")) or None,
        ),
        "boundary": report.get("boundary", ""),
    }
    if not compact:
        output["cycles"] = cycles
        output["limits"] = {
            "max_cycles": report.get("max_cycles", 0),
            "max_units_per_cycle": report.get("max_units_per_cycle", 0),
            "max_total_units": report.get("max_total_units", 0),
            "max_estimated_tokens": report.get("max_estimated_tokens", 0),
            "max_elapsed_ms": report.get("max_elapsed_ms", 0),
        }
    else:
        output["cycles"] = [
            {
                "index": item.get("index", 0),
                "status": item.get("status", "unknown"),
                "executed_unit_count": (
                    item.get("loop", {}).get("executed_unit_count", 0)
                    if isinstance(item.get("loop"), dict)
                    else 0
                ),
                "estimated_review_tokens": (
                    item.get("loop", {}).get("estimated_review_tokens", 0)
                    if isinstance(item.get("loop"), dict)
                    else 0
                ),
            }
            for item in cycles[:5]
            if isinstance(item, dict)
        ]
        output["omitted_cycle_count"] = max(0, len(cycles) - len(output["cycles"]))
    return repo_command_metrics.attach_output_budget(output, "review-autopilot")


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
    next_action_factory: NextActionFactory,
    progress_factory: ProgressFactory,
    runner: Runner | None = None,
    plan_factory: PlanFactory | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    max_units = max(1, min(int(max_units or 1), 50))
    timeout_seconds = max(10, min(int(timeout_seconds or 120), 3600))
    max_estimated_tokens = max(0, int(max_estimated_tokens or 0))
    max_elapsed_ms = max(0, int(max_elapsed_ms or 0))
    command_runner = runner or run_capture_shell
    iterations: list[dict[str, Any]] = []
    status = "limit-reached"
    ok = True
    next_command = ""
    stale_reset_count = 0
    stale_reset_planned_count = 0
    estimated_review_tokens = 0
    forecast: dict[str, Any] = {}
    raw_output_paths: list[str] = []
    progress_before: dict[str, Any] = {}
    progress_after: dict[str, Any] = {}
    for index in range(1, max_units + 1):
        stale_reset_planned_for_iteration = False
        action = next_action_factory(root, fast=True)
        action_command = str(action.get("next_command") or "")
        command = action_command
        next_command = action_command
        review_progress = action.get("review_progress") if isinstance(action.get("review_progress"), dict) else {}
        if not progress_before and review_progress:
            progress_before = repo_review_progress.summarize_review_progress(review_progress)
            progress_after = dict(progress_before)
        progress_command = str(review_progress.get("next_pending_command") or "")
        command = _select_review_loop_command(action_command, progress_command)
        next_command = command
        validation_after = str(action.get("validation_after") or "").strip()
        if review_progress.get("stale") and reset_stale:
            if dry_run:
                stale_reset_planned_count += 1
                stale_reset_planned_for_iteration = True
            else:
                fingerprint_digest = str(review_progress.get("fingerprint_digest") or "").strip()
                state_path = str(review_progress.get("state_path") or "").strip()
                if fingerprint_digest:
                    reset_report = repo_review_progress.reset_review_progress_state(
                        root,
                        fingerprint_digest=fingerprint_digest,
                        state_path=state_path or None,
                        note="reset stale review progress for current changed context",
                    )
                else:
                    reset_report = progress_factory(
                        root,
                        reset=True,
                        note="reset stale review progress for current changed context",
                    )
                stale_reset_count += 1
                if not reset_report.get("ok", True):
                    status = "reset-failed"
                    ok = False
                    iterations.append({
                        "index": index,
                        "status": "reset-failed",
                        "next_command": command,
                        "mark_progress": repo_review_progress.summarize_review_progress(reset_report),
                    })
                    break
            review_progress = dict(review_progress)
            review_progress["stale"] = False
            review_progress["status"] = "needs-review"
            review_progress["review_state"] = "initial"
        if review_progress.get("stale"):
            status = "stale"
            ok = False
            iterations.append({"index": index, "status": "stale", "next_command": command})
            break
        if not command or command.startswith("none"):
            status = "complete"
            iterations.append({"index": index, "status": "complete", "next_command": command})
            break
        command_is_review = "review-packet" in command
        if not command_is_review and not include_validation:
            status = "needs-validation"
            iterations.append(
                {
                    "index": index,
                    "status": "stopped-non-review-command",
                    "next_command": command,
                    "reason": "Pass --include-validation to run non-review next commands.",
                }
            )
            break
        elapsed_ms = repo_command_metrics.elapsed_ms_since(started)
        if max_elapsed_ms and elapsed_ms > max_elapsed_ms:
            status = "elapsed-limit"
            iterations.append(
                {
                    "index": index,
                    "status": "stopped-elapsed-limit",
                    "next_command": command,
                    "elapsed_ms": elapsed_ms,
                    "max_elapsed_ms": max_elapsed_ms,
                    "reason": "Stopped before the next review unit because the elapsed-time cap was reached.",
                }
            )
            break
        current_unit = review_progress.get("current_unit") if isinstance(review_progress.get("current_unit"), dict) else {}
        unit_estimated_tokens = max(0, int(current_unit.get("estimated_changed_tokens", 0) or 0))
        if command_is_review and max_estimated_tokens and estimated_review_tokens + unit_estimated_tokens > max_estimated_tokens:
            status = "token-limit"
            iterations.append(
                {
                    "index": index,
                    "status": "stopped-token-limit",
                    "next_command": command,
                    "estimated_changed_tokens": unit_estimated_tokens,
                    "estimated_review_tokens": estimated_review_tokens,
                    "max_estimated_tokens": max_estimated_tokens,
                    "reason": "Stopped before the next review unit because it would exceed the estimated review token cap.",
                }
            )
            break
        if dry_run:
            status = "planned"
            forecast = {}
            autopilot = action.get("review_autopilot") if isinstance(action.get("review_autopilot"), dict) else {}
            if command_is_review and isinstance(autopilot.get("forecast"), dict):
                forecast = autopilot["forecast"]
            if command_is_review and not repo_review_progress.review_loop_forecast_matches_limits(
                forecast,
                max_units=max_units,
                max_estimated_tokens=max_estimated_tokens,
            ):
                try:
                    dry_context = plan_factory(root) if plan_factory else {}
                    completed_ids: list[str] = []
                    if not stale_reset_planned_for_iteration:
                        try:
                            dry_progress = progress_factory(root)
                            completed_ids = repo_review_progress.completed_unit_ids_from_report(dry_progress)
                        except Exception:  # noqa: BLE001 - dry-run falls back to first unit if progress cannot be read.
                            completed_ids = repo_review_progress.completed_unit_ids_from_report(review_progress)
                    forecast = repo_review_progress.build_review_loop_forecast(
                        dry_context["review_plan"],
                        completed_unit_ids=completed_ids,
                        max_units=max_units,
                        max_estimated_tokens=max_estimated_tokens,
                        include_validation=include_validation,
                    ) if dry_context.get("review_plan") else {}
                except Exception:  # noqa: BLE001 - keep dry-run read-only and bounded on forecast rebuild failures.
                    forecast = {}
            planned_units = [item for item in forecast.get("planned_units", []) if isinstance(item, dict)] if forecast else []
            if planned_units:
                for offset, unit in enumerate(planned_units, start=0):
                    iteration = {
                        "index": index + offset,
                        "status": "planned",
                        "next_command": unit.get("command", ""),
                        "estimated_changed_tokens": unit.get("estimated_changed_tokens", 0),
                    }
                    if stale_reset_planned_for_iteration and offset == 0:
                        iteration["stale_reset"] = "planned"
                    iterations.append(iteration)
                estimated_review_tokens = int(forecast.get("planned_estimated_tokens", 0) or 0)
                next_command = str(forecast.get("next_command_after_planned") or next_command)
            else:
                iteration = {
                    "index": index,
                    "status": "planned",
                    "next_command": command,
                    "estimated_changed_tokens": unit_estimated_tokens,
                }
                if not command_is_review and not progress_command and validation_after and validation_after != command:
                    iteration["follow_up_command"] = validation_after
                    next_command = validation_after
                if stale_reset_planned_for_iteration:
                    iteration["stale_reset"] = "planned"
                iterations.append(iteration)
                forecast = {
                    "status": "planned",
                    "planned_unit_count": 1 if command_is_review else 0,
                    "planned_estimated_tokens": unit_estimated_tokens if command_is_review else 0,
                    "planned_units": [],
                }
            break
        result = command_runner(root, command, timeout=timeout_seconds)
        iteration: dict[str, Any] = {
            "index": index,
            "status": "passed" if result.get("ok") else "failed",
            "next_command": command,
            "estimated_changed_tokens": unit_estimated_tokens,
            "command_result": _compact_command_result(result),
        }
        if not result.get("ok"):
            status = "failed"
            ok = False
            iterations.append(iteration)
            break
        if command_is_review:
            estimated_review_tokens += unit_estimated_tokens
        raw_output_path = str(result.get("raw_output_path") or "").strip()
        if raw_output_path and raw_output_path not in raw_output_paths:
            raw_output_paths.append(raw_output_path)
        if not command_is_review and not progress_command:
            iteration["progress_tracking"] = "not-applicable"
            iterations.append(iteration)
            if validation_after and validation_after != command:
                status = "needs-validation"
                next_command = validation_after
            else:
                status = "complete"
                next_command = "none, untracked validation command completed"
            break
        mark = progress_factory(
            root,
            mark_command=command,
            note="review-loop successful command output",
        )
        mark_summary = repo_review_progress.summarize_review_progress(mark)
        iteration["mark_progress"] = mark_summary
        progress_after = dict(mark_summary)
        if not mark.get("ok", True):
            status = "mark-failed"
            ok = False
            iterations.append(iteration)
            break
        iterations.append(iteration)
    else:
        action = next_action_factory(root, fast=True)
        action_command = str(action.get("next_command") or "")
        review_progress = action.get("review_progress") if isinstance(action.get("review_progress"), dict) else {}
        progress_command = str(review_progress.get("next_pending_command") or "")
        next_command = _select_review_loop_command(action_command, progress_command)
        status, ok = _status_after_unit_limit(next_command, review_progress, include_validation=include_validation)
    total_elapsed_ms = repo_command_metrics.elapsed_ms_since(started)
    executed_unit_count = len([item for item in iterations if item.get("status") == "passed"])
    return {
        "schema_version": 1,
        "tool": "skill-manager.review-loop",
        "ok": ok,
        "status": status,
        "max_units": max_units,
        "max_estimated_tokens": max_estimated_tokens,
        "estimated_review_tokens": estimated_review_tokens,
        "planned_unit_count": len([item for item in iterations if item.get("status") == "planned"]),
        "planned_estimated_tokens": estimated_review_tokens if dry_run else 0,
        "forecast": repo_review_progress.summarize_review_loop_forecast(forecast) if dry_run else {},
        "max_elapsed_ms": max_elapsed_ms,
        "total_elapsed_ms": total_elapsed_ms,
        "executed_unit_count": executed_unit_count,
        "include_validation": include_validation,
        "dry_run": dry_run,
        "reset_stale": bool(reset_stale),
        "stale_reset_count": stale_reset_count,
        "stale_reset_planned_count": stale_reset_planned_count,
        "raw_output_paths": raw_output_paths,
        "progress_delta": _progress_delta(
            progress_before,
            progress_after or progress_before,
            executed_count=executed_unit_count,
            stale_reset_count=stale_reset_count,
            raw_output_paths=raw_output_paths,
        ),
        "iterations": iterations,
        "next_command": next_command,
        "boundary": (
            "Runs compact review-packet commands and records progress only after stale-progress reset "
            "or successful tracked command output. An untracked validation action runs once and routes its "
            "declared follow-up without being marked as review progress. Dry-run never writes progress state. "
            "This marks the routing packet processed; it does not replace validation or semantic code review."
        ),
    }


def summarize_review_loop_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    iterations = []
    for item in report.get("iterations", []) if isinstance(report.get("iterations"), list) else []:
        if not isinstance(item, dict):
            continue
        row = {
            "index": item.get("index", 0),
            "status": item.get("status", "unknown"),
            "next_command": item.get("next_command", ""),
        }
        command_result = item.get("command_result") if isinstance(item.get("command_result"), dict) else {}
        if command_result:
            row["command_status"] = command_result.get("status", 0)
            row["elapsed_seconds"] = command_result.get("elapsed_seconds", 0)
            if command_result.get("raw_output_path"):
                row["raw_output_path"] = command_result.get("raw_output_path")
        if item.get("estimated_changed_tokens"):
            row["estimated_changed_tokens"] = item.get("estimated_changed_tokens", 0)
        if item.get("reason"):
            row["reason"] = item.get("reason", "")
        if item.get("stale_reset"):
            row["stale_reset"] = item.get("stale_reset", "")
        if not compact:
            row["mark_progress"] = item.get("mark_progress", {})
        iterations.append(row)
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.review-loop"),
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", "unknown"),
        "executed_unit_count": report.get("executed_unit_count", 0),
        "planned_unit_count": report.get("planned_unit_count", 0),
        "max_units": report.get("max_units", 0),
        "max_estimated_tokens": report.get("max_estimated_tokens", 0),
        "estimated_review_tokens": report.get("estimated_review_tokens", 0),
        "planned_estimated_tokens": report.get("planned_estimated_tokens", 0),
        "max_elapsed_ms": report.get("max_elapsed_ms", 0),
        "total_elapsed_ms": report.get("total_elapsed_ms", 0),
        "stale_reset_count": report.get("stale_reset_count", 0),
        "stale_reset_planned_count": report.get("stale_reset_planned_count", 0),
        "progress_delta": report.get("progress_delta", {}),
        "raw_output_paths": report.get("raw_output_paths", [])[:12],
        "iterations": iterations,
        "forecast": report.get("forecast", {}),
        "next_command": report.get("next_command", ""),
        "boundary": report.get("boundary", ""),
    }
    output["latency_budget"] = repo_command_metrics.timing_budget_report(
        "review-loop",
        float(report.get("total_elapsed_ms", 0.0) or 0.0),
        budget_ms=_int_value(report.get("max_elapsed_ms")) or None,
    )
    return repo_command_metrics.attach_output_budget(output, "review-loop")


def review_next_report(
    root: Path,
    *,
    timeout_seconds: int = 120,
    include_validation: bool = False,
    dry_run: bool = False,
    next_action_factory: NextActionFactory,
    progress_factory: ProgressFactory,
    runner: Runner | None = None,
    plan_factory: PlanFactory | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    loop = review_loop_report(
        root,
        max_units=1,
        timeout_seconds=timeout_seconds,
        include_validation=include_validation,
        dry_run=dry_run,
        reset_stale=True,
        next_action_factory=next_action_factory,
        progress_factory=progress_factory,
        runner=runner,
        plan_factory=plan_factory,
    )
    iterations = loop.get("iterations") if isinstance(loop.get("iterations"), list) else []
    first = iterations[0] if iterations and isinstance(iterations[0], dict) else {}
    executed_count = int(loop.get("executed_unit_count", 0) or 0)
    status = "passed" if executed_count == 1 and first.get("status") == "passed" else str(loop.get("status", "unknown"))
    total_elapsed_ms = repo_command_metrics.elapsed_ms_since(started)
    return {
        "schema_version": 1,
        "tool": "skill-manager.review-next",
        "ok": bool(loop.get("ok", False)),
        "status": status,
        "total_elapsed_ms": total_elapsed_ms,
        "latency_budget": repo_command_metrics.timing_budget_report("review-next", total_elapsed_ms),
        "executed": executed_count == 1,
        "dry_run": bool(dry_run),
        "review_command": first.get("next_command", ""),
        "iteration": first,
        "next_command": loop.get("next_command", ""),
        "loop_status": loop.get("status", "unknown"),
        "stale_reset_count": loop.get("stale_reset_count", 0),
        "stale_reset_planned_count": loop.get("stale_reset_planned_count", 0),
        "boundary": (
            "Runs at most one compact review unit and records progress only after stale-progress reset or "
            "successful command output. Dry-run never writes progress state. Use review-loop --max-units N for batched review."
        ),
    }


def summarize_review_next_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.review-next"),
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", "unknown"),
        "executed": bool(report.get("executed", False)),
        "dry_run": bool(report.get("dry_run", False)),
        "review_command": report.get("review_command", ""),
        "next_command": report.get("next_command", ""),
        "loop_status": report.get("loop_status", ""),
        "stale_reset_count": report.get("stale_reset_count", 0),
        "stale_reset_planned_count": report.get("stale_reset_planned_count", 0),
        "latency_budget": report.get("latency_budget", {}),
        "boundary": report.get("boundary", ""),
    }
    if not compact:
        output["iteration"] = report.get("iteration", {})
    return repo_command_metrics.attach_output_budget(output, "review-next")
