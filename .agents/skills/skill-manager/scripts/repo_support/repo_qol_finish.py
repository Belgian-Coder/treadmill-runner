"""Finish-gate helpers for repository quality-of-life commands."""

from __future__ import annotations

import datetime as dt
import glob
import json
import math
import sys
from pathlib import Path
from typing import Any

import measure_skill_budget
from repo_support import repo_changed
from repo_support import repo_command_metrics
from repo_support import repo_common as repo
from repo_support import repo_fingerprint
from repo_support import repo_optimizations
from repo_support.repo_benchmark import budget_gate_report
from repo_support.repo_qol_capture import run_capture
from repo_support.repo_qol_github import github_validation_advisories, github_validation_trigger_state

FINISH_DEFAULT_TIMEOUT_SECONDS = 180
FINISH_FAST_TIMEOUT_SECONDS = 120
FINISH_DEEP_TIMEOUT_SECONDS = 240
FINISH_CHANGED_DEEP_TIMEOUT_SECONDS = 1800
VALIDATION_RECEIPT_MAX_AGE_SECONDS = 86_400


def json_from_captured_output(root: Path, result: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    candidates: list[tuple[str, str]] = [("output_tail", str(result.get("output_tail") or ""))]
    raw_output_path = str(result.get("raw_output_path") or "").strip()
    raw_issue = ""
    if raw_output_path:
        raw_path = (root / raw_output_path).resolve()
        root_path = root.resolve()
        try:
            raw_path.relative_to(root_path)
        except ValueError:
            raw_issue = "raw output path is outside the repository"
        else:
            try:
                candidates.append(("raw_output_path", raw_path.read_text(encoding="utf-8-sig")))
            except OSError as exc:
                raw_issue = f"could not read raw output: {exc}"
    last_issue = "empty output"
    last_source = ""
    for source, text in candidates:
        if not text.strip():
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            last_issue = str(exc)
            last_source = source
            continue
        if isinstance(payload, dict):
            return payload, source, ""
        last_issue = "captured JSON was not an object"
        last_source = source
    if raw_issue and last_issue == "empty output":
        last_issue = raw_issue
    return {}, last_source or "output_tail", last_issue


def workflows_with_run_folders(root: Path) -> list[str]:
    automations = root / "automations"
    if not automations.exists():
        return []
    workflows: list[str] = []
    for workflow_dir in sorted(path for path in automations.iterdir() if path.is_dir()):
        runs_dir = workflow_dir / "runs"
        if runs_dir.is_dir() and any(item.is_dir() for item in runs_dir.iterdir()):
            workflows.append(workflow_dir.name)
    return workflows


def workflow_run_index_check_commands(root: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    for workflow in workflows_with_run_folders(root):
        commands.append(
            [
                sys.executable,
                "-B",
                ".agents/manage.py",
                "index-workflow-runs",
                "--name",
                workflow,
                "--check",
                "--format",
                "json",
            ]
        )
    return commands


def workflow_eval_all_command() -> list[str]:
    return [
        sys.executable,
        "-B",
        ".agents/manage.py",
        "workflow",
        "eval",
        "--all",
        "--summary",
        "--compact",
        "--format",
        "json",
    ]


def evidence_reference_exists(root: Path, run_dir: Path, reference: str) -> bool:
    if not reference:
        return True
    path = Path(reference)
    candidates = [path] if path.is_absolute() else [root / path, run_dir / path]
    for candidate in candidates:
        if candidate.exists():
            return True
        if any(char in str(candidate) for char in "*?["):
            if glob.glob(str(candidate)):
                return True
    return False


def workflow_run_evidence_reference_report(root: Path) -> dict[str, Any]:
    missing: list[dict[str, str]] = []
    checked_count = 0
    run_count = 0
    for run_json in sorted((root / "automations").glob("*/runs/*/run.json")):
        run_count += 1
        try:
            data = json.loads(run_json.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            missing.append(
                {
                    "run": repo.relative(root, run_json),
                    "field": "run.json",
                    "reference": str(exc),
                }
            )
            continue
        if not isinstance(data, dict):
            continue
        run_dir = run_json.parent
        references: list[tuple[str, str]] = []
        for item in data.get("commands", []) if isinstance(data.get("commands"), list) else []:
            if isinstance(item, dict) and item.get("evidence_path"):
                references.append(("commands.evidence_path", str(item["evidence_path"])))
        for item in data.get("evidence", []) if isinstance(data.get("evidence"), list) else []:
            if isinstance(item, dict) and item.get("source"):
                references.append(("evidence.source", str(item["source"])))
        for item in data.get("evidence_paths", []) if isinstance(data.get("evidence_paths"), list) else []:
            references.append(("evidence_paths", str(item)))
        for field, reference in references:
            checked_count += 1
            if not evidence_reference_exists(root, run_dir, reference):
                missing.append(
                    {
                        "run": repo.relative(root, run_json),
                        "field": field,
                        "reference": reference,
                    }
                )
    return {
        "status": "missing" if missing else "ok",
        "run_count": run_count,
        "checked_count": checked_count,
        "missing_count": len(missing),
        "missing": missing[:20],
    }


def story_bug_out_of_scope_template_report(root: Path) -> dict[str, Any]:
    required = [
        root / "automations" / workflow / "templates" / template
        for workflow in ("bug-ticket-workflow", "user-story-workflow")
        for template in ("ticket-info.md", "plan.md", "pr-description.md")
    ]
    existing_workflows = [path for path in required if path.parents[1].exists()]
    if not existing_workflows:
        return {"status": "not-applicable", "checked_count": 0, "missing_count": 0, "missing": []}
    missing: list[str] = []
    for path in required:
        text = path.read_text(encoding="utf-8-sig", errors="replace") if path.exists() else ""
        if "## Out Of Scope" not in text:
            missing.append(repo.relative(root, path))
    return {
        "status": "missing" if missing else "ok",
        "checked_count": len(required),
        "missing_count": len(missing),
        "missing": missing,
    }


def finish_workflow_eval_summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    command = " ".join(workflow_eval_all_command())
    for check in checks:
        if check.get("command") != command:
            continue
        try:
            payload = json.loads(str(check.get("output_tail") or "{}"))
        except json.JSONDecodeError:
            return {"status": "unparsed", "ok": bool(check.get("ok"))}
        summary = payload.get("summary") if isinstance(payload, dict) and isinstance(payload.get("summary"), dict) else {}
        return {
            "status": payload.get("status", "unknown") if isinstance(payload, dict) else "unknown",
            "workflows": int(summary.get("workflows", 0) or 0),
            "suites": int(summary.get("suites", 0) or 0),
            "passed": int(summary.get("passed", 0) or 0),
            "failed": int(summary.get("failed", 0) or 0),
            "cases": int(summary.get("cases", 0) or 0),
        }
    return {"status": "skipped", "ok": True}


def budget_hotspots_report(root: Path) -> dict[str, Any]:
    current = current_budget_hotspots_report(root)
    if current.get("ok"):
        return current
    command = [
        sys.executable,
        "-B",
        ".agents/manage.py",
        "measure-skill-budget",
        "--all",
        "--baseline-ref",
        "HEAD",
        "--summary",
        "--compact",
        "--format",
        "json",
    ]
    result = run_capture(root, command, timeout=45)
    if not result.get("ok"):
        return {
            "ok": True,
            "status": "timeout" if result.get("status") == 124 else "unavailable",
            "advisory": True,
            "issue": result.get("issue", "budget hotspot command failed"),
            "top": [],
            "delta": {},
            "current_issue": current.get("issue", ""),
            "command": result.get("command", " ".join(command)),
            "raw_output_path": result.get("raw_output_path", ""),
            "output_summary": result.get("output_summary", {}),
        }
    report, parse_source, parse_issue = json_from_captured_output(root, result)
    if parse_issue:
        return {
            "ok": True,
            "status": "unavailable",
            "advisory": True,
            "issue": parse_issue,
            "top": [],
            "delta": {},
            "current_issue": current.get("issue", ""),
            "command": result.get("command", " ".join(command)),
            "raw_output_path": result.get("raw_output_path", ""),
            "output_summary": result.get("output_summary", {}),
            "parse_source": parse_source,
        }
    delta = report.get("delta") if isinstance(report.get("delta"), dict) else {}
    delta_summary = delta.get("summary") if isinstance(delta.get("summary"), dict) else {}
    top = report.get("top") if isinstance(report.get("top"), list) else []
    baseline = report.get("baseline") if isinstance(report.get("baseline"), dict) else {}
    return {
        "ok": True,
        "status": "measured" if baseline.get("ok", True) else "baseline-unavailable",
        "advisory": True,
        "summary": report.get("summary", {}),
        "delta": {
            "summary": delta_summary,
            "skills": delta.get("skills", []) if isinstance(delta.get("skills"), list) else [],
        },
        "top": top[:5],
        "baseline": {
            "ref": baseline.get("ref", "HEAD"),
            "ok": bool(baseline.get("ok", True)),
            "issue_count": len(baseline.get("issues", []) if isinstance(baseline.get("issues"), list) else []),
        },
        "parse_source": parse_source,
    }


def current_budget_hotspots_report(root: Path) -> dict[str, Any]:
    try:
        skill_dirs = measure_skill_budget.common.discover_skill_dirs(root)
        skill_reports = [
            measure_skill_budget.measure_skill(skill_dir, root)
            for skill_dir in skill_dirs
        ]
        report = {
            "version": 1,
            "root": str(root),
            "skills": skill_reports,
        }
        summary_report = measure_skill_budget.summarize_report(report, compact=True)
    except Exception as exc:  # noqa: BLE001 - advisory fallback reports deterministic issue.
        return {
            "ok": False,
            "status": "unavailable",
            "advisory": True,
            "issue": f"in-process budget hotspot measurement failed: {exc}",
            "top": [],
            "delta": {},
        }
    return {
        "ok": True,
        "status": "measured",
        "advisory": True,
        "summary": summary_report.get("summary", {}),
        "delta": {"summary": {}, "skills": []},
        "top": summary_report.get("top", [])[:5] if isinstance(summary_report.get("top"), list) else [],
        "top_by_load_class": summary_report.get("top_by_load_class", {}),
        "baseline": {
            "ref": "HEAD",
            "ok": True,
            "issue_count": 0,
            "status": "not-run",
        },
        "parse_source": "in-process-current",
        "command": "in-process measure_skill_budget current summary",
    }


def compact_finish_checks(checks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    bytes_before = 0
    bytes_after = 0
    elapsed_seconds = 0.0
    failed_count = 0
    reused_check_count = 0
    reused_source_elapsed_seconds = 0.0
    timing_rows: list[dict[str, Any]] = []
    for check in checks:
        item = dict(check)
        output_tail = str(item.get("output_tail", ""))
        output_summary = item.get("output_summary") if isinstance(item.get("output_summary"), dict) else {}
        bytes_before += int(output_summary.get("bytes", 0) or len(output_tail.encode("utf-8")))
        elapsed_seconds += float(item.get("elapsed_seconds", 0.0) or 0.0)
        timing_rows.append(
            {
                "name": str(item.get("phase") or item.get("command") or ""),
                "elapsed_seconds": round(float(item.get("elapsed_seconds", 0.0) or 0.0), 3),
                "execution_mode": str(item.get("execution_mode") or "subprocess"),
            }
        )
        if item.get("execution_mode") == "validation-progress-receipt":
            reused_check_count += 1
            receipt = item.get("validation_receipt") if isinstance(item.get("validation_receipt"), dict) else {}
            reused_source_elapsed_seconds += float(receipt.get("source_elapsed_ms", 0.0) or 0.0) / 1000.0
        if item.get("ok"):
            item["output_tail"] = ""
        else:
            failed_count += 1
            if item.get("distilled_output"):
                item["output_tail"] = str(item.get("distilled_output"))
        bytes_after += len(str(item.get("output_tail", "")).encode("utf-8"))
        compacted.append(item)
    return compacted, {
        "check_count": len(checks),
        "failed_count": failed_count,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "reused_check_count": reused_check_count,
        "reused_source_elapsed_seconds": round(reused_source_elapsed_seconds, 3),
        "reuse_timing_basis": "counterfactual-source-duration",
        "slowest_checks": sorted(
            timing_rows,
            key=lambda item: float(item.get("elapsed_seconds", 0.0) or 0.0),
            reverse=True,
        )[:5],
        "output_tail_bytes_before": bytes_before,
        "output_tail_bytes_after": bytes_after,
        "output_tail_bytes_saved": bytes_before - bytes_after,
    }


def finish_check_specs(
    root: Path,
    *,
    deep: bool,
    release_full: bool = False,
    paths: list[str] | None = None,
    scope: dict[str, object] | None = None,
) -> list[dict[str, Any]]:
    paths = repo_changed.changed_files(root) if paths is None else paths
    scope = repo_changed.changed_scope(paths) if scope is None and paths else scope or {}
    workflow_changed = bool(scope.get("workflows") or scope.get("workflow_generated"))
    startup_context_changed = bool(paths and repo_optimizations.startup_context_inputs_changed(root, paths))
    install_surface_changed = any(
        path.startswith(
            (
                ".agents/harness-",
                ".agents/skills/skill-manager/scripts/install_",
                ".agents/skills/skill-manager/scripts/repo_support/repo_harness",
                ".agents/skills/skill-manager/assets/",
            )
        )
        for path in paths
    )
    benchmark_changed = any(
        path.startswith(("automations/agent-benchmarking/", ".agents/skills/agent-benchmarking/"))
        for path in paths
    )
    specs: list[dict[str, Any]] = []
    if workflow_changed or release_full:
        specs.append(
            {
                "phase": "workflow-hooks",
                "command": [sys.executable, "-B", ".agents/manage.py", "workflow", "hooks", "--all", "--check", "--format", "json"],
                "timeout_seconds": FINISH_FAST_TIMEOUT_SECONDS,
            }
        )
    if (deep and startup_context_changed) or release_full:
        specs.append(
            {
                "phase": "clean-context-proof",
                "command": [
                    sys.executable,
                    "-B",
                    ".agents/manage.py",
                    "clean-context-proof",
                    "--summary",
                    "--compact",
                    "--format",
                    "json",
                ],
                "timeout_seconds": FINISH_FAST_TIMEOUT_SECONDS,
            }
        )
    if (deep and install_surface_changed) or release_full:
        specs.append(
            {
                "phase": "install-harness-smoke-fast",
                "command": [
                    sys.executable,
                    "-B",
                    ".agents/manage.py",
                    "install-harness-smoke",
                    "--fast",
                    "--format",
                    "json",
                ],
                "timeout_seconds": FINISH_DEEP_TIMEOUT_SECONDS,
            }
        )
    if (deep and workflow_changed) or release_full:
        specs.extend(
            [
                {
                    "phase": "user-story-workflow-smoke",
                    "command": [
                        sys.executable,
                        "-B",
                        ".agents/manage.py",
                        "workflow",
                        "smoke",
                        "--name",
                        "user-story-workflow",
                        "--summary",
                        "--compact",
                        "--format",
                        "json",
                    ],
                    "timeout_seconds": FINISH_DEEP_TIMEOUT_SECONDS,
                },
                {
                    "phase": "workflow-evals",
                    "command": workflow_eval_all_command(),
                    "timeout_seconds": FINISH_DEEP_TIMEOUT_SECONDS,
                },
            ]
        )
    if workflow_changed or release_full:
        for command in workflow_run_index_check_commands(root):
            specs.append(
                {
                    "phase": "workflow-run-index",
                    "command": command,
                    "timeout_seconds": FINISH_FAST_TIMEOUT_SECONDS,
                },
            )
    if release_full:
        specs.append(
            {
                "phase": "repo-check",
                "command": [sys.executable, "-B", ".agents/manage.py", "check"],
                "timeout_seconds": FINISH_DEFAULT_TIMEOUT_SECONDS,
            }
        )
    changed_command = [
        sys.executable,
        "-B",
        ".agents/manage.py",
        "check-changed",
        *(["--deep"] if deep or release_full else []),
        "--record-progress",
        "--summary",
        "--compact",
        "--format",
        "json",
    ]
    specs.append(
        {
            "phase": "changed-scope",
            "command": changed_command,
            "timeout_seconds": (
                FINISH_CHANGED_DEEP_TIMEOUT_SECONDS
                if deep or release_full
                else FINISH_DEFAULT_TIMEOUT_SECONDS
            ),
        }
    )
    if (deep and benchmark_changed) or release_full:
        specs.append(
            {
                "phase": "benchmark-doctor",
                "command": [sys.executable, "-B", ".agents/manage.py", "benchmark", "doctor"],
                "timeout_seconds": FINISH_FAST_TIMEOUT_SECONDS,
            }
        )
    return specs


def validation_receipt_reuse_report(
    root: Path,
    specs: list[dict[str, Any]],
    *,
    deep: bool,
    release_full: bool,
    input_fingerprint: dict[str, Any],
    validation_plan: list[dict[str, object]],
    validation_progress: dict[str, Any],
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    _ = root
    profile = "deep" if deep or release_full else "changed"
    validation_progress = validation_progress if isinstance(validation_progress, dict) else {}
    required_check_ids = [
        str(item.get("check_id") or "")
        for item in validation_plan
        if isinstance(item, dict)
        and item.get("required") is not False
        and str(item.get("check_id") or "")
    ]
    digest_value = input_fingerprint.get("digest")
    digest = digest_value.strip() if isinstance(digest_value, str) else ""
    receipt_path_value = validation_progress.get("path")
    receipt_path = receipt_path_value if isinstance(receipt_path_value, str) else ""

    def base_report(reason: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "tool": "skill-manager.finish-validation-reuse",
            "ok": True,
            "status": "not-reused",
            "eligible": False,
            "profile": profile,
            "side_effect_boundary": "",
            "reason": reason,
            "receipt_path": receipt_path,
            "recorded_at": "",
            "age_seconds": None,
            "max_age_seconds": VALIDATION_RECEIPT_MAX_AGE_SECONDS,
            "input_fingerprint_digest": digest,
            "required_check_count": len(required_check_ids),
            "passed_check_count": 0,
            "input_stable": False,
            "source_elapsed_ms": 0.0,
        }

    if release_full:
        return base_report("release-full requires fresh changed-scope execution")
    if not deep:
        return base_report("receipt reuse is limited to the deep profile")

    changed_spec = next(
        (item for item in specs if str(item.get("phase") or "") == "changed-scope"),
        None,
    )
    if changed_spec is None:
        return base_report("changed-scope phase is not selected")
    expected_command_value = changed_spec.get("command")
    expected_command = (
        list(expected_command_value)
        if isinstance(expected_command_value, list)
        and all(isinstance(part, str) and part for part in expected_command_value)
        else []
    )
    if not expected_command:
        return base_report("selected changed-scope command is invalid")

    extra = validation_progress.get("extra") if isinstance(validation_progress.get("extra"), dict) else {}
    recorded_at_value = validation_progress.get("recorded_at")
    recorded_at = recorded_at_value.strip() if isinstance(recorded_at_value, str) else ""
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    age_seconds: float | None = None
    timestamp_issue = ""
    if recorded_at:
        try:
            recorded = dt.datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
            if recorded.tzinfo is None:
                recorded = recorded.replace(tzinfo=dt.timezone.utc)
            age_seconds = (current - recorded.astimezone(dt.timezone.utc)).total_seconds()
            if age_seconds < -300:
                timestamp_issue = "receipt timestamp is in the future"
            elif age_seconds > VALIDATION_RECEIPT_MAX_AGE_SECONDS:
                timestamp_issue = "receipt is older than the maximum reuse age"
        except ValueError:
            timestamp_issue = "receipt timestamp is invalid"
    else:
        timestamp_issue = "receipt timestamp is missing"

    source_schema_ok = (
        type(validation_progress.get("schema_version")) is int
        and validation_progress.get("schema_version") == 1
        and validation_progress.get("tool") == "skill-manager.validation-progress"
    )
    elapsed_value = validation_progress.get("elapsed_ms")
    source_elapsed_ms: float | None = None
    if not isinstance(elapsed_value, bool) and isinstance(elapsed_value, (int, float)):
        try:
            elapsed_candidate = float(elapsed_value)
        except (OverflowError, ValueError):
            elapsed_candidate = float("nan")
        if math.isfinite(elapsed_candidate) and elapsed_candidate >= 0:
            source_elapsed_ms = elapsed_candidate
    command_value = extra.get("command_argv")
    recorded_command = (
        list(command_value)
        if isinstance(command_value, list)
        and all(isinstance(part, str) and part for part in command_value)
        else []
    )
    recorded_required_value = extra.get("required_check_ids")
    passed_value = extra.get("passed_check_ids")
    check_id_lists_valid = (
        isinstance(recorded_required_value, list)
        and isinstance(passed_value, list)
        and all(isinstance(item, str) and item for item in recorded_required_value)
        and all(isinstance(item, str) and item for item in passed_value)
        and len(set(recorded_required_value)) == len(recorded_required_value)
        and len(set(passed_value)) == len(passed_value)
    )
    side_effect_boundary = extra.get("side_effect_boundary")
    receipt_covers_input = repo_command_metrics.validation_progress_covers_input(
        validation_progress,
        input_fingerprint,
        required_check_ids=required_check_ids,
        profile=profile,
    )
    post_digest = extra.get("post_input_fingerprint_digest")
    input_stable = (
        extra.get("input_stable") is True
        and isinstance(post_digest, str)
        and post_digest == digest
    )
    reason = ""
    if not source_schema_ok:
        reason = "receipt source schema or tool identity is invalid"
    elif source_elapsed_ms is None:
        reason = "receipt source elapsed time is invalid"
    elif not check_id_lists_valid:
        reason = "receipt required or passed check IDs are invalid"
    elif side_effect_boundary != "repository-read-only-and-temporary-restored":
        reason = "receipt side-effect boundary is missing or invalid"
    elif timestamp_issue:
        reason = timestamp_issue
    elif not receipt_covers_input:
        reason = "receipt does not cover the exact current fingerprint, profile, and required check set"
    elif not input_stable:
        reason = "receipt does not prove stable pre/post validation input"
    elif recorded_command != expected_command:
        reason = "receipt command argv does not exactly match the selected changed-scope command"
    eligible = not reason
    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": "skill-manager.finish-validation-reuse",
        "ok": True,
        "status": "reused" if eligible else "not-reused",
        "eligible": eligible,
        "profile": profile,
        "side_effect_boundary": (
            side_effect_boundary if isinstance(side_effect_boundary, str) else ""
        ),
        "reason": reason or "exact current deep validation receipt",
        "receipt_path": receipt_path,
        "recorded_at": recorded_at,
        "age_seconds": round(max(0.0, age_seconds or 0.0), 3) if age_seconds is not None else None,
        "max_age_seconds": VALIDATION_RECEIPT_MAX_AGE_SECONDS,
        "input_fingerprint_digest": digest,
        "required_check_count": len(required_check_ids),
        "passed_check_count": len(set(passed_value)) if check_id_lists_valid else 0,
        "input_stable": input_stable,
        "source_elapsed_ms": source_elapsed_ms or 0.0,
    }
    if not eligible:
        return report

    report["check"] = {
        "ok": True,
        "status": 0,
        "returncode": 0,
        "command": " ".join(expected_command),
        "command_argv": expected_command,
        "phase": "changed-scope",
        "timeout_seconds": int(
            changed_spec.get("timeout_seconds") or FINISH_CHANGED_DEEP_TIMEOUT_SECONDS
        ),
        "elapsed_seconds": 0.0,
        "execution_mode": "validation-progress-receipt",
        "output_tail": "",
        "output_summary": {"bytes": 0, "lines": 0},
        "validation_receipt": {
            "schema_version": 1,
            "verified": True,
            "source_schema_version": validation_progress.get("schema_version"),
            "source_tool": validation_progress.get("tool"),
            "path": receipt_path,
            "command_argv": recorded_command,
            "recorded_at": recorded_at,
            "age_seconds": report["age_seconds"],
            "max_age_seconds": VALIDATION_RECEIPT_MAX_AGE_SECONDS,
            "input_fingerprint_digest": digest,
            "environment_fingerprint": input_fingerprint.get("runtime", {}),
            "post_input_fingerprint_digest": post_digest,
            "input_stable": True,
            "profile": profile,
            "side_effect_boundary": side_effect_boundary,
            "failed_check_count": extra.get("failed_check_count"),
            "required_check_ids": required_check_ids,
            "passed_check_ids": sorted(set(passed_value)),
            "source_elapsed_ms": report["source_elapsed_ms"],
        },
    }
    return report


def run_finish_check_specs(
    root: Path,
    specs: list[dict[str, Any]],
    *,
    reusable_checks: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    progress_events: list[dict[str, Any]] = []
    reusable_checks = reusable_checks or {}
    for index, spec in enumerate(specs, start=1):
        command = [str(part) for part in spec.get("command", []) if str(part)]
        timeout = int(spec.get("timeout_seconds") or FINISH_DEFAULT_TIMEOUT_SECONDS)
        phase = str(spec.get("phase") or f"check-{index}")
        command_text = " ".join(command)
        progress_events.append(
            {
                "event": "started",
                "index": index,
                "phase": phase,
                "command": command_text,
                "timeout_seconds": timeout,
            }
        )
        reused = reusable_checks.get(phase)
        result = dict(reused) if isinstance(reused, dict) else run_capture(root, command, timeout=timeout)
        result.setdefault("command", command_text)
        result["command_argv"] = command
        result.setdefault("phase", phase)
        result.setdefault("timeout_seconds", timeout)
        checks.append(result)
        progress_events.append(
            {
                "event": "completed",
                "index": index,
                "phase": phase,
                "command": command_text,
                "ok": bool(result.get("ok")),
                "status": result.get("status"),
                "elapsed_seconds": result.get("elapsed_seconds", 0.0),
                "timeout_seconds": result.get("timeout_seconds", timeout),
                "execution_mode": str(result.get("execution_mode") or "subprocess"),
            }
        )
    return checks, progress_events


def finish_validation_state(
    root: Path,
    *,
    deep: bool,
) -> tuple[list[str], dict[str, object], list[dict[str, object]], dict[str, Any]]:
    paths = repo_changed.changed_files(root)
    scope = repo_changed.changed_scope(paths) if paths else {}
    validation_plan = repo_optimizations.changed_validation_plan(
        root,
        paths,
        scope,
        deep=deep,
    )
    input_fingerprint = repo_fingerprint.input_fingerprint_report(
        root,
        paths,
        validation_plan,
    )
    return paths, scope, validation_plan, input_fingerprint


def finish_input_stability_report(
    *,
    initial_fingerprint: dict[str, Any],
    validated_fingerprint: dict[str, Any],
    final_fingerprint: dict[str, Any],
    validation_plan: list[dict[str, object]],
    validation_progress: dict[str, Any],
    expected_command: list[str],
    changed_check: dict[str, Any],
    profile: str,
) -> dict[str, Any]:
    initial_digest = str(initial_fingerprint.get("digest") or "")
    validated_digest = str(validated_fingerprint.get("digest") or "")
    final_digest = str(final_fingerprint.get("digest") or "")
    required_check_ids = [
        str(item.get("check_id") or "")
        for item in validation_plan
        if isinstance(item, dict)
        and item.get("required") is not False
        and str(item.get("check_id") or "")
    ]
    extra = (
        validation_progress.get("extra")
        if isinstance(validation_progress.get("extra"), dict)
        else {}
    )
    recorded_command = extra.get("command_argv")
    phase_selection_stable = bool(initial_digest) and initial_digest == validated_digest
    post_phase_stable = bool(validated_digest) and validated_digest == final_digest
    execution_mode = str(changed_check.get("execution_mode") or "subprocess")
    if execution_mode == "validation-progress-receipt":
        validation_proof_matches_final = (
            repo_command_metrics.validation_progress_covers_input(
                validation_progress,
                final_fingerprint,
                required_check_ids=required_check_ids,
                profile=profile,
            )
            and extra.get("input_stable") is True
            and extra.get("post_input_fingerprint_digest") == final_digest
            and extra.get("side_effect_boundary")
            == "repository-read-only-and-temporary-restored"
            and isinstance(recorded_command, list)
            and recorded_command == expected_command
        )
    else:
        validation_proof_matches_final = (
            execution_mode == "subprocess"
            and changed_check.get("ok") is True
            and changed_check.get("command_argv") == expected_command
        )
    ok = phase_selection_stable and post_phase_stable and validation_proof_matches_final
    reasons: list[str] = []
    if not phase_selection_stable:
        reasons.append("finish inputs changed after phase selection")
    if not post_phase_stable:
        reasons.append("finish inputs changed after changed-scope validation")
    if not validation_proof_matches_final:
        reasons.append("changed-scope evidence does not prove the final input")
    return {
        "schema_version": 1,
        "tool": "skill-manager.finish-input-stability",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "profile": profile,
        "phase_selection_stable": phase_selection_stable,
        "post_phase_stable": post_phase_stable,
        "validation_proof_matches_final_input": validation_proof_matches_final,
        "changed_scope_execution_mode": execution_mode,
        "initial_input_fingerprint_digest": initial_digest,
        "validated_input_fingerprint_digest": validated_digest,
        "final_input_fingerprint_digest": final_digest,
        "reasons": reasons,
    }


def finish_work_report(
    root: Path,
    *,
    deep: bool = False,
    release_full: bool = False,
    skip_benchmark: bool = False,
    budget_intent: str = "off",
) -> dict[str, Any]:
    paths, scope, validation_plan, input_fingerprint = finish_validation_state(
        root,
        deep=deep or release_full,
    )
    run_index_workflows = workflows_with_run_folders(root)
    specs = finish_check_specs(
        root,
        deep=deep,
        release_full=release_full,
        paths=paths,
        scope=scope,
    )
    if skip_benchmark:
        specs = [item for item in specs if item.get("phase") != "benchmark-doctor"]
    changed_index = next(
        (
            index
            for index, item in enumerate(specs)
            if str(item.get("phase") or "") == "changed-scope"
        ),
        len(specs),
    )
    pre_specs = specs[:changed_index]
    remaining_specs = specs[changed_index:]
    pre_checks, pre_events = run_finish_check_specs(root, pre_specs)
    (
        _validated_paths,
        _validated_scope,
        validated_plan,
        validated_fingerprint,
    ) = finish_validation_state(
        root,
        deep=deep or release_full,
    )
    validation_progress = repo_command_metrics.read_validation_progress(root)
    validation_reuse = validation_receipt_reuse_report(
        root,
        remaining_specs,
        deep=deep,
        release_full=release_full,
        input_fingerprint=validated_fingerprint,
        validation_plan=validated_plan,
        validation_progress=validation_progress,
    )
    phase_selection_stable = (
        bool(input_fingerprint.get("digest"))
        and input_fingerprint.get("digest") == validated_fingerprint.get("digest")
    )
    if not phase_selection_stable:
        validation_reuse = {
            **validation_reuse,
            "status": "not-reused",
            "eligible": False,
            "reason": "finish inputs changed after phase selection; fresh validation required",
        }
        validation_reuse.pop("check", None)
    reusable_checks = (
        {"changed-scope": validation_reuse["check"]}
        if isinstance(validation_reuse.get("check"), dict)
        else {}
    )
    remaining_checks, remaining_events = run_finish_check_specs(
        root,
        remaining_specs,
        reusable_checks=reusable_checks,
    )
    for event in remaining_events:
        if isinstance(event.get("index"), int):
            event["index"] = int(event["index"]) + len(pre_specs)
    checks = [*pre_checks, *remaining_checks]
    progress_events = [*pre_events, *remaining_events]
    (
        _final_paths,
        _final_scope,
        final_validation_plan,
        final_fingerprint,
    ) = finish_validation_state(
        root,
        deep=deep or release_full,
    )
    final_validation_progress = repo_command_metrics.read_validation_progress(root)
    changed_spec = next(
        (item for item in specs if str(item.get("phase") or "") == "changed-scope"),
        {},
    )
    expected_command_value = changed_spec.get("command")
    expected_command = (
        list(expected_command_value)
        if isinstance(expected_command_value, list)
        and all(isinstance(part, str) and part for part in expected_command_value)
        else []
    )
    changed_check = next(
        (item for item in checks if str(item.get("phase") or "") == "changed-scope"),
        {},
    )
    finish_input_stability = finish_input_stability_report(
        initial_fingerprint=input_fingerprint,
        validated_fingerprint=validated_fingerprint,
        final_fingerprint=final_fingerprint,
        validation_plan=final_validation_plan,
        validation_progress=final_validation_progress,
        expected_command=expected_command,
        changed_check=changed_check,
        profile="deep" if deep or release_full else "changed",
    )
    if budget_intent != "off":
        budget_gate = budget_gate_report(
            root,
            baseline_ref="HEAD",
            intent=budget_intent,
            max_total_growth=None,
            max_tool_growth=None,
        )
    else:
        budget_gate = {"status": "skipped", "ok": True}
    workflow_eval_ran = any(item.get("phase") == "workflow-evals" for item in checks)
    workflow_eval = finish_workflow_eval_summary(checks) if workflow_eval_ran else {"status": "not-required", "ok": True}
    evidence_references = workflow_run_evidence_reference_report(root) if workflow_eval_ran else {"status": "not-required", "ok": True}
    out_of_scope_templates = story_bug_out_of_scope_template_report(root) if workflow_eval_ran else {"status": "not-required", "ok": True}
    budget_hotspots = budget_hotspots_report(root)
    checks, check_metrics = compact_finish_checks(checks)
    ok = (
        all(bool(item.get("ok")) for item in checks)
        and bool(finish_input_stability.get("ok"))
        and evidence_references.get("status") != "missing"
        and out_of_scope_templates.get("status") != "missing"
        and bool(budget_gate.get("ok", True))
    )
    github_validation = github_validation_trigger_state(root)
    advisories = github_validation_advisories(github_validation)
    next_command = "python -B .agents/manage.py commit-readiness" if ok else "python -B .agents/manage.py finish --summary --compact --format json"
    if not budget_gate.get("ok", True):
        next_command = "python -B .agents/manage.py measure-skill-budget --all --baseline-ref HEAD --summary --compact --format json"
    return {
        "schema_version": 1,
        "tool": "repo-finish",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "profile": "release-full" if release_full else "deep" if deep else "changed",
        "selected_validation_profile": "deep" if deep or release_full else "changed",
        "selected_phase_ids": [str(item.get("phase") or "") for item in specs],
        "checks": checks,
        "workflow_run_indexes": {
            "checked_count": len(run_index_workflows),
            "workflows": run_index_workflows,
        },
        "workflow_eval": workflow_eval,
        "workflow_evidence_references": evidence_references,
        "story_bug_out_of_scope_templates": out_of_scope_templates,
        "budget_hotspots": budget_hotspots,
        "budget_gate": budget_gate,
        "validation_reuse": validation_reuse,
        "finish_input_stability": finish_input_stability,
        "check_metrics": check_metrics,
        "progress_events": progress_events,
        "github_validation": github_validation,
        "advisories": advisories,
        "next_command": next_command,
    }
