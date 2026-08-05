#!/usr/bin/env python3
"""Benchmark doctor helpers for the repository launcher."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from repo_support import repo_benchmark
from repo_support import repo_common as repo

BENCHMARK_COMMON_PATH = Path(__file__).resolve().parents[3] / "agent-benchmarking" / "scripts"
if BENCHMARK_COMMON_PATH.exists() and str(BENCHMARK_COMMON_PATH) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_COMMON_PATH))
try:
    import benchmark_common
except Exception:  # pragma: no cover - fallback keeps the repo doctor usable if skill paths move.
    benchmark_common = None
try:
    import lesson_promotion
except Exception:  # pragma: no cover - fallback keeps benchmark doctor usable if optional helper moves.
    lesson_promotion = None
try:
    import routing_evidence_eval
except Exception:  # pragma: no cover - fallback keeps benchmark doctor usable if optional helper moves.
    routing_evidence_eval = None

def benchmark_doctor_report(root: Path, *, suite: str | None = None, run: str | None = None) -> dict[str, object]:
    issues: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, object]] = []
    loaded_results: list[tuple[str, Path, dict[str, object]]] = []
    suite_paths = [(root / suite).resolve()] if suite else sorted((root / "automations" / "agent-benchmarking" / "suites").glob("*.json"))
    run_paths: list[Path]
    if run:
        run_paths = [(root / run).resolve()]
    else:
        runs_root = root / "automations" / "agent-benchmarking" / "runs"
        run_paths = sorted(runs_root.glob("*/benchmark-result.json")) if runs_root.exists() else []
    for suite_path in suite_paths:
        suite_label = repo.relative(root, suite_path)
        try:
            suite_data = json.loads(suite_path.read_text(encoding="utf-8"))
            tasks = suite_data.get("tasks", []) if isinstance(suite_data, dict) else []
            if not tasks:
                checks.append({"name": "suite", "ok": True, "path": suite_label, "status": "skipped", "reason": "not a benchmark task suite"})
                continue
            task_ids = [str(task.get("id", "")) for task in tasks if isinstance(task, dict)]
            duplicate_ids = sorted({item for item in task_ids if task_ids.count(item) > 1})
            missing_checks = [
                str(task.get("id", "<unknown>"))
                for task in tasks
                if isinstance(task, dict) and not task.get("expected_checks")
            ]
            if duplicate_ids:
                issues.append(f"duplicate task ids: {', '.join(duplicate_ids)}")
            if missing_checks:
                issues.append(f"tasks missing expected_checks: {', '.join(missing_checks)}")
            checks.append({"name": "suite", "ok": not duplicate_ids and not missing_checks, "path": suite_label})
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"suite could not be read: {suite_label}: {exc}")
            checks.append({"name": "suite", "ok": False, "path": suite_label})
    routing_evidence_suite: dict[str, object] = {
        "status": "skipped",
        "case_count": 0,
        "path": "automations/agent-benchmarking/suites/routing-evidence-real-use.json",
    }
    routing_suite_path = root / "automations" / "agent-benchmarking" / "suites" / "routing-evidence-real-use.json"
    routing_check_paths: list[Path] = []
    if suite:
        resolved_suite = (root / suite).resolve()
        if resolved_suite.name == "routing-evidence-real-use.json":
            routing_check_paths = [resolved_suite]
    elif routing_suite_path.exists():
        routing_check_paths = [routing_suite_path.resolve()]
    if routing_check_paths:
        if routing_evidence_eval is None:
            warnings.append("routing evidence suite check unavailable; routing_evidence_eval.py could not be imported")
            routing_evidence_suite = {"status": "unavailable", "case_count": 0, "path": repo.relative(root, routing_check_paths[0])}
        else:
            for routing_path in routing_check_paths:
                suite_label = repo.relative(root, routing_path)
                try:
                    routing_check = routing_evidence_eval.validate_suite_file(routing_path)
                except (OSError, json.JSONDecodeError, SystemExit) as exc:
                    routing_check = {
                        "ok": False,
                        "status": "failed",
                        "case_count": 0,
                        "issues": [str(exc)],
                    }
                routing_evidence_suite = {
                    "status": str(routing_check.get("status", "unknown")),
                    "case_count": int(routing_check.get("case_count", 0) or 0),
                    "path": suite_label,
                    "issues": routing_check.get("issues", []),
                }
                ok = bool(routing_check.get("ok"))
                checks.append(
                    {
                        "name": "routing-evidence-suite",
                        "ok": ok,
                        "path": suite_label,
                        "case_count": routing_evidence_suite["case_count"],
                    }
                )
                if not ok:
                    for issue in routing_check.get("issues", []):
                        issues.append(f"routing evidence suite invalid: {issue}")
    for run_path in run_paths:
        result_path = run_path / "benchmark-result.json" if run_path.is_dir() else run_path
        run_packet_path = result_path.parent / "run.json"
        run_label = repo.relative(root, result_path.parent)
        ok = result_path.exists() and run_packet_path.exists()
        if not result_path.exists():
            issues.append(f"missing benchmark-result.json: {run_label}")
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if result.get("schema_version") != 1:
                    issues.append(f"benchmark-result.json incompatible schema_version: {result.get('schema_version')!r}")
                    ok = False
                if benchmark_common is not None:
                    shape_issues = benchmark_common.validate_benchmark_result_shape(result)
                    if shape_issues:
                        issues.extend(f"benchmark-result.json {issue}" for issue in shape_issues)
                        ok = False
                    if result.get("run_packet_path") != "run.json":
                        issues.append(
                            f"benchmark-result.json run_packet_path must be run.json: {run_label}"
                        )
                        ok = False
                    elif ok:
                        loaded_results.append((run_label, result_path, result))
            except (OSError, json.JSONDecodeError) as exc:
                issues.append(f"benchmark-result.json could not be read: {exc}")
                ok = False
            if not run_packet_path.exists():
                issues.append(f"missing run.json beside {run_label}")
                ok = False
        checks.append({"name": "run", "ok": ok, "path": run_label})
    if benchmark_common is not None and len(loaded_results) > 1:
        by_task: dict[str, list[tuple[str, Path, dict[str, object]]]] = {}
        for label, path, result in loaded_results:
            task_id = str(result.get("task_id") or result.get("subject") or "<unknown>")
            by_task.setdefault(task_id, []).append((label, path, result))
        for task_id, task_results in sorted(by_task.items()):
            if len(task_results) < 2:
                continue
            baseline_label, _baseline_path, baseline = task_results[0]
            for candidate_label, _candidate_path, candidate in task_results[1:]:
                compare_issues = benchmark_common.comparability_issues(baseline, candidate)
                for issue in compare_issues:
                    warnings.append(
                        "non-comparable retained benchmark run "
                        f"for {task_id}: {baseline_label} vs {candidate_label}: {issue}"
                    )
    comparable_pairs = 0
    latest_comparison_status = "unknown"
    if benchmark_common is None:
        latest_comparison_status = "unavailable"
    elif len(loaded_results) < 2:
        latest_comparison_status = "insufficient-runs"
    else:
        for index, (_left_label, _left_path, left) in enumerate(loaded_results):
            for _right_label, _right_path, right in loaded_results[index + 1:]:
                if not benchmark_common.comparability_issues(left, right):
                    comparable_pairs += 1
        ordered = sorted(loaded_results, key=lambda item: item[1].stat().st_mtime)
        _latest_label, _latest_path, latest = ordered[-1]
        previous = ordered[:-1]
        latest_comparison_status = (
            "comparable"
            if any(not benchmark_common.comparability_issues(result, latest) for _label, _path, result in previous)
            else "not-comparable"
        )
    lesson_promotions: dict[str, object] = {
        "status": "unavailable" if lesson_promotion is None else "insufficient-runs",
        "summary": {
            "candidate_count": 0,
            "report_count": len(loaded_results),
            "min_count": 2,
        },
        "candidates": [],
    }
    if lesson_promotion is not None and loaded_results:
        lesson_promotions = lesson_promotion.build_report_from_loaded(loaded_results, min_count=2)
        candidates = lesson_promotions.get("candidates") if isinstance(lesson_promotions.get("candidates"), list) else []
        if candidates:
            warnings.append(
                f"{len(candidates)} recurring lesson promotion candidate(s); "
                "run `python -B .agents/manage.py benchmark lesson-promotions --format markdown`."
            )
    return {
        "schema_version": 1,
        "tool": "agent-benchmarking.doctor",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "checks": checks,
        "benchmark_summary": {
            "suite_check_count": sum(1 for item in checks if item.get("name") == "suite"),
            "run_check_count": sum(1 for item in checks if item.get("name") == "run"),
            "comparable_run_pair_count": comparable_pairs,
            "latest_comparison_status": latest_comparison_status,
            "lesson_promotion_candidate_count": len(lesson_promotions.get("candidates", []))
            if isinstance(lesson_promotions.get("candidates"), list)
            else 0,
            "routing_evidence_status": routing_evidence_suite.get("status", "skipped"),
            "routing_evidence_case_count": routing_evidence_suite.get("case_count", 0),
        },
        "routing_evidence_suite": routing_evidence_suite,
        "lesson_promotions": lesson_promotions,
        "issues": issues,
        "warnings": warnings,
    }


def summarize_benchmark_doctor_report(report: dict[str, object]) -> dict[str, object]:
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    failed_checks = [item for item in checks if isinstance(item, dict) and not item.get("ok")]
    benchmark_summary = report.get("benchmark_summary") if isinstance(report.get("benchmark_summary"), dict) else {}
    summary = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "agent-benchmarking.doctor"),
        "ok": bool(report.get("ok")),
        "status": report.get("status", ""),
        "summary": {
            "check_count": len(checks),
            "failed_check_count": len(failed_checks),
            "suite_check_count": benchmark_summary.get(
                "suite_check_count",
                sum(1 for item in checks if isinstance(item, dict) and item.get("name") == "suite"),
            ),
            "run_check_count": benchmark_summary.get(
                "run_check_count",
                sum(1 for item in checks if isinstance(item, dict) and item.get("name") == "run"),
            ),
            "comparable_run_pair_count": benchmark_summary.get("comparable_run_pair_count", 0),
            "latest_comparison_status": benchmark_summary.get("latest_comparison_status", "unknown"),
            "lesson_promotion_candidate_count": benchmark_summary.get("lesson_promotion_candidate_count", 0),
            "routing_evidence_status": benchmark_summary.get("routing_evidence_status", "skipped"),
            "routing_evidence_case_count": benchmark_summary.get("routing_evidence_case_count", 0),
            "issue_count": len(report.get("issues", [])) if isinstance(report.get("issues"), list) else 0,
            "warning_count": len(report.get("warnings", [])) if isinstance(report.get("warnings"), list) else 0,
        },
        "failed_checks": failed_checks,
        "issues": report.get("issues", []),
        "warnings": report.get("warnings", []),
    }
    if not summary.get("failed_checks"):
        summary.pop("failed_checks", None)
    if not summary.get("issues"):
        summary.pop("issues", None)
    if not summary.get("warnings"):
        summary.pop("warnings", None)
    return summary


def benchmark_group(args: argparse.Namespace, root: Path) -> int:
    return repo_benchmark.benchmark_group(
        args,
        root,
        doctor_report_func=benchmark_doctor_report,
        doctor_command_func=benchmark_doctor,
    )


def benchmark_doctor(raw_args: list[str], root: Path) -> int:
    parser = argparse.ArgumentParser(prog="python -B .agents/manage.py benchmark doctor")
    parser.add_argument("--suite")
    parser.add_argument("--run")
    parser.add_argument(
        "--retained-runs",
        action="store_true",
        help="explicitly validate retained run schema and comparability; currently included by default",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--summary", action="store_true", help="emit compact counts and failures")
    parsed = parser.parse_args(raw_args)
    report = benchmark_doctor_report(root, suite=parsed.suite, run=parsed.run)
    if parsed.retained_runs:
        report["retained_runs_checked"] = True
    output_report = summarize_benchmark_doctor_report(report) if parsed.summary else report
    if parsed.json:
        print(json.dumps(output_report, indent=2, sort_keys=True))
    elif parsed.summary:
        summary = output_report.get("summary", {}) if isinstance(output_report.get("summary"), dict) else {}
        print("# Benchmark Doctor Summary")
        print(f"- Status: {output_report.get('status')}")
        print(f"- Checks: {summary.get('check_count', 0)}")
        print(f"- Issues/warnings: {summary.get('issue_count', 0)}/{summary.get('warning_count', 0)}")
    else:
        print("# Benchmark Doctor")
        for check in report["checks"]:
            print(f"- {check['name']}: {'ok' if check['ok'] else 'failed'}")
        lesson_summary = report.get("lesson_promotions", {}).get("summary", {}) if isinstance(report.get("lesson_promotions"), dict) else {}
        if lesson_summary:
            print(f"- Lesson promotion candidates: {lesson_summary.get('candidate_count', 0)}")
        routing_suite = report.get("routing_evidence_suite", {}) if isinstance(report.get("routing_evidence_suite"), dict) else {}
        if routing_suite:
            print(
                "- Routing evidence suite: "
                f"{routing_suite.get('status', 'skipped')} ({routing_suite.get('case_count', 0)} cases)"
            )
        if report["issues"]:
            print()
            print("## Issues")
            for issue in report["issues"]:
                print(f"- {issue}")
        if report["warnings"]:
            print()
            print("## Warnings")
            for warning in report["warnings"]:
                print(f"- {warning}")
    return 0 if report["ok"] else 1
