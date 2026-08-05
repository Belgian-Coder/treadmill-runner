#!/usr/bin/env python3
"""Run independent local quality helpers concurrently and write one evidence report."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import re
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from support.local_quality_scans import slop_scan_check, snapshot_artifact_check


SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_DIR = SCRIPT_DIR.parent.parent
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
SKIP_DIRS = {".git", "bin", "obj", "node_modules", "dist", "build", "coverage", "__pycache__", ".venv", "venv"}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def status(ok: bool) -> str:
    return "passed" if ok else "failed"


def normalized_summary_schema() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "tool": "dotnet-quality-gates.validate_local_quality",
        "shared_check_fields": [
            "name",
            "kind",
            "ok",
            "status",
            "duration_seconds",
            "summary",
            "evidence_paths",
            "format",
        ],
    }


def normalize_check(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized.setdefault("kind", "analysis")
    normalized.setdefault("ok", False)
    normalized.setdefault("status", status(bool(normalized.get("ok"))))
    normalized.setdefault("duration_seconds", 0)
    normalized.setdefault("summary", {})
    normalized.setdefault("evidence_paths", [])
    normalized.setdefault("format", "")
    return normalized


def command_check(name: str, command: list[str], timeout_seconds: int, success_tail: int, failure_tail: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") + (exc.stderr or "")) if isinstance(exc.stdout, str) else ""
        return {
            "name": name,
            "kind": "command",
            "ok": False,
            "status": "failed",
            "duration_seconds": round(time.perf_counter() - started, 3),
            "command": command,
            "returncode": None,
            "output_tail": output[-failure_tail:],
            "summary": {"error": f"timed out after {timeout_seconds}s"},
        }
    tail = success_tail if completed.returncode == 0 else failure_tail
    output = completed.stdout[-tail:]
    return {
        "name": name,
        "kind": "command",
        "ok": completed.returncode == 0,
        "status": status(completed.returncode == 0),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "command": command,
        "returncode": completed.returncode,
        "output_tail": output,
        "summary": parse_json_tail(output) or {},
    }


def parse_json_tail(output: str) -> Any:
    start = output.find("{")
    if start < 0:
        return None
    try:
        return json.loads(output[start:])
    except json.JSONDecodeError:
        return None


def iter_markdown_files(targets: list[str]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        path = Path(target).resolve()
        if path.is_file() and path.suffix.lower() == ".md":
            files.append(path)
        elif path.is_dir():
            for item in sorted(path.rglob("*.md")):
                if item.is_file() and not any(part in SKIP_DIRS for part in item.parts):
                    files.append(item)
    return files


def markdown_href(raw: str) -> str:
    text = raw.strip()
    if text.startswith("<") and ">" in text:
        return text[1 : text.index(">")]
    return text.split()[0].strip("<>")


def docs_check(targets: list[str]) -> dict[str, Any]:
    started = time.perf_counter()
    failures: list[dict[str, str]] = []
    files = iter_markdown_files(targets)
    for path in files:
        text = path.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK.finditer(text):
            href = markdown_href(match.group(1))
            if href.startswith(("http://", "https://", "mailto:", "#")):
                continue
            local = urllib.parse.unquote(href.split("#", 1)[0])
            if not local:
                continue
            candidate = Path(local)
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            if not candidate.exists():
                failures.append({"path": str(path), "link": href})
    ok = not failures
    return {
        "name": "docs-link-check",
        "kind": "analysis",
        "ok": ok,
        "status": status(ok),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "summary": {"files_checked": len(files), "broken_links": len(failures)},
        "failures": failures[:100],
    }


TEST_OUTCOME_ALIASES = {
    "passed": "passed",
    "pass": "passed",
    "failed": "failed",
    "fail": "failed",
    "error": "failed",
    "notexecuted": "skipped",
    "skipped": "skipped",
    "skip": "skipped",
    "ignored": "skipped",
    "inconclusive": "skipped",
}


def normalize_test_outcome(value: object) -> str:
    return TEST_OUTCOME_ALIASES.get(compact_key(value), "unknown")


def junit_case_name(node: ET.Element) -> str:
    name = node.attrib.get("name") or "unknown"
    class_name = node.attrib.get("classname") or node.attrib.get("class") or ""
    return f"{class_name}.{name}" if class_name else name


def parse_test_result(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    tag = root.tag.lower()
    if tag.endswith("testrun"):
        counters = root.find(".//{*}Counters")
        cases = [
            {
                "name": item.attrib.get("testName") or item.attrib.get("testId") or "unknown",
                "outcome": normalize_test_outcome(item.attrib.get("outcome")),
            }
            for item in root.findall(".//{*}UnitTestResult")
        ]
        if counters is None:
            return {"path": str(path), "tests": len(cases), "failed": 0, "skipped": 0, "format": "trx", "cases": cases}
        return {
            "path": str(path),
            "tests": int(counters.attrib.get("total", "0")),
            "failed": int(counters.attrib.get("failed", "0")),
            "skipped": int(counters.attrib.get("notExecuted", "0")),
            "format": "trx",
            "cases": cases,
        }
    suites = [root] if tag.endswith("testsuite") else root.findall(".//{*}testsuite")
    if not suites:
        return {"path": str(path), "tests": 0, "failed": 0, "skipped": 0, "format": "unknown", "cases": []}
    tests = failed = skipped = 0
    for suite in suites:
        tests += int(float(suite.attrib.get("tests", "0")))
        failed += int(float(suite.attrib.get("failures", "0"))) + int(float(suite.attrib.get("errors", "0")))
        skipped += int(float(suite.attrib.get("skipped", "0")))
    cases: list[dict[str, str]] = []
    for case in root.findall(".//{*}testcase"):
        outcome = "passed"
        if case.find("./{*}failure") is not None or case.find("./{*}error") is not None:
            outcome = "failed"
        elif case.find("./{*}skipped") is not None:
            outcome = "skipped"
        cases.append({"name": junit_case_name(case), "outcome": outcome})
    return {"path": str(path), "tests": tests, "failed": failed, "skipped": skipped, "format": "junit", "cases": cases}


def test_result_check(paths: list[str]) -> dict[str, Any]:
    started = time.perf_counter()
    parsed = [parse_test_result(Path(path)) for path in paths]
    tests = sum(int(item["tests"]) for item in parsed)
    failed = sum(int(item["failed"]) for item in parsed)
    skipped = sum(int(item["skipped"]) for item in parsed)
    case_outcomes: dict[str, Counter[str]] = {}
    for item in parsed:
        for case in item.get("cases", []) if isinstance(item.get("cases"), list) else []:
            if not isinstance(case, dict):
                continue
            name = str(case.get("name") or "unknown")
            outcome = str(case.get("outcome") or "unknown")
            case_outcomes.setdefault(name, Counter())[outcome] += 1
    flaky_tests = [
        {
            "name": name,
            "outcomes": dict(sorted(outcomes.items())),
        }
        for name, outcomes in sorted(case_outcomes.items())
        if outcomes.get("passed", 0) > 0 and outcomes.get("failed", 0) > 0
    ]
    ok = failed == 0
    return {
        "name": "test-result-parse",
        "kind": "analysis",
        "ok": ok,
        "status": status(ok),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "summary": {
            "files": len(parsed),
            "tests": tests,
            "failed": failed,
            "skipped": skipped,
            "case_count": len(case_outcomes),
            "flaky_candidates": len(flaky_tests),
        },
        "format": ",".join(sorted({str(item.get("format", "unknown")) for item in parsed})),
        "evidence_paths": [str(item["path"]) for item in parsed],
        "results": parsed,
        "flaky_tests": flaky_tests[:50],
    }


def sarif_check(paths: list[str]) -> dict[str, Any]:
    started = time.perf_counter()
    findings = 0
    levels: dict[str, int] = {}
    files: set[str] = set()
    for raw_path in paths:
        data = json.loads(Path(raw_path).read_text(encoding="utf-8-sig"))
        for run in data.get("runs", []) if isinstance(data, dict) else []:
            for result in run.get("results", []) if isinstance(run, dict) else []:
                if not isinstance(result, dict):
                    continue
                findings += 1
                level = str(result.get("level") or "warning")
                levels[level] = levels.get(level, 0) + 1
                for location in result.get("locations", []) or []:
                    uri = (((location.get("physicalLocation") or {}).get("artifactLocation") or {}).get("uri"))
                    if uri:
                        files.add(str(uri))
    ok = levels.get("error", 0) == 0
    return {
        "name": "sarif-parse",
        "kind": "analysis",
        "ok": ok,
        "status": status(ok),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "summary": {"files": len(paths), "findings": findings, "levels": levels, "affected_files": len(files)},
        "format": "sarif",
        "evidence_paths": list(paths),
    }


MUTATION_STATUS_ALIASES = {
    "killed": "killed",
    "survived": "survived",
    "nocoverage": "no_coverage",
    "timeout": "timeout",
    "timedout": "timeout",
    "ignored": "ignored",
    "ignore": "ignored",
    "compileerror": "compile_error",
    "compiletimeerror": "compile_error",
    "runtimeerror": "runtime_error",
    "notrun": "not_run",
}
MUTATION_SCORE_KEYS = {
    "mutationscore",
    "mutationscorepercentage",
    "mutationscorebasedoncoveredcode",
}
MUTATION_FILE_KEYS = {"path", "file", "filename", "sourcefile", "sourcefilepath", "fullpath"}


def compact_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def normalize_mutation_status(value: object) -> str | None:
    return MUTATION_STATUS_ALIASES.get(compact_key(value))


def looks_like_file_path(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > 300:
        return False
    suffix = Path(text.replace("\\", "/")).suffix.lower()
    return suffix in {".cs", ".fs", ".vb", ".cshtml", ".razor"} or "/" in text or "\\" in text


def collect_mutation_statuses(data: Any, current_file: str | None = None) -> tuple[Counter[str], set[str]]:
    counts: Counter[str] = Counter()
    files: set[str] = set()
    if isinstance(data, list):
        for item in data:
            child_counts, child_files = collect_mutation_statuses(item, current_file)
            counts.update(child_counts)
            files.update(child_files)
        return counts, files
    if not isinstance(data, dict):
        return counts, files

    next_file = current_file
    for key, value in data.items():
        if compact_key(key) in MUTATION_FILE_KEYS and looks_like_file_path(value):
            next_file = str(value)
            break

    status_value = data.get("status")
    normalized_status = normalize_mutation_status(status_value) if status_value is not None else None
    if normalized_status:
        counts[normalized_status] += 1
        if next_file:
            files.add(next_file)

    for key, value in data.items():
        if compact_key(key) == "files" and isinstance(value, dict):
            for file_key, file_value in value.items():
                child_file = str(file_key) if looks_like_file_path(file_key) else next_file
                child_counts, child_files = collect_mutation_statuses(file_value, child_file)
                counts.update(child_counts)
                files.update(child_files)
        else:
            child_counts, child_files = collect_mutation_statuses(value, next_file)
            counts.update(child_counts)
            files.update(child_files)
    return counts, files


def find_reported_mutation_scores(data: Any) -> list[float]:
    scores: list[float] = []
    if isinstance(data, list):
        for item in data:
            scores.extend(find_reported_mutation_scores(item))
        return scores
    if not isinstance(data, dict):
        return scores
    for key, value in data.items():
        if compact_key(key) in MUTATION_SCORE_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool):
            scores.append(float(value))
        else:
            scores.extend(find_reported_mutation_scores(value))
    return scores


def mutation_result_check(
    paths: list[str],
    minimum: float | None = None,
    fail_on_survived: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    counts: Counter[str] = Counter()
    mutated_files: set[str] = set()
    reported_scores: list[float] = []
    for raw_path in paths:
        data = json.loads(Path(raw_path).read_text(encoding="utf-8-sig"))
        path_counts, path_files = collect_mutation_statuses(data)
        counts.update(path_counts)
        mutated_files.update(path_files)
        reported_scores.extend(find_reported_mutation_scores(data))

    killed = counts["killed"]
    survived = counts["survived"]
    no_coverage = counts["no_coverage"]
    denominator = killed + survived + no_coverage
    computed_score = round((killed / denominator) * 100, 2) if denominator else None
    reported_score = round(sum(reported_scores) / len(reported_scores), 2) if reported_scores else None
    mutation_score = computed_score if computed_score is not None else reported_score
    mutants = sum(counts.values())
    failures: list[str] = []
    if mutants == 0:
        failures.append("no mutant statuses found")
    if mutation_score is None:
        failures.append("no mutation score could be computed")
    survived_or_no_coverage = survived + no_coverage
    if fail_on_survived and survived_or_no_coverage:
        failures.append(f"survived mutations or no-coverage mutants found: {survived_or_no_coverage}")
    if mutation_score is not None and minimum is not None and mutation_score < minimum:
        failures.append(f"mutation score {mutation_score}% below minimum {minimum}%")
    ok = not failures
    summary = {
        "reports": len(paths),
        "files": len(mutated_files),
        "mutants": mutants,
        "killed": killed,
        "survived": survived,
        "no_coverage": no_coverage,
        "timeout": counts["timeout"],
        "ignored": counts["ignored"],
        "compile_error": counts["compile_error"],
        "runtime_error": counts["runtime_error"],
        "not_run": counts["not_run"],
        "mutation_score": mutation_score,
        "computed_mutation_score": computed_score,
        "reported_mutation_score": reported_score,
        "minimum": minimum,
        "survived_or_no_coverage": survived_or_no_coverage,
    }
    return {
        "name": "mutation-result-parse",
        "kind": "analysis",
        "ok": ok,
        "status": status(ok),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "summary": summary,
        "format": "mutation-json",
        "evidence_paths": list(paths),
        "status_counts": dict(sorted(counts.items())),
        "mutated_files": sorted(mutated_files)[:100],
        "failures": failures,
    }


def as_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if match:
            return float(match.group(0))
    return None


def first_number(mapping: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in mapping:
            parsed = as_number(mapping[key])
            if parsed is not None:
                return parsed
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        if key.lower() in lowered:
            parsed = as_number(lowered[key.lower()])
            if parsed is not None:
                return parsed
    return None


def expand_benchmark_result_paths(paths: list[str]) -> list[Path]:
    expanded: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if path.is_file():
            expanded.append(path)
        elif path.is_dir():
            expanded.extend(sorted(path.rglob("*-report-full.json"), key=lambda item: item.as_posix().lower()))
        else:
            raise FileNotFoundError(f"benchmark result path not found: {path}")
    if not expanded:
        raise FileNotFoundError("no BenchmarkDotNet *-report-full.json files found")
    return expanded


def load_benchmark_results(paths: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    results: dict[str, dict[str, Any]] = {}
    evidence_paths: list[str] = []
    for path in expand_benchmark_result_paths(paths):
        evidence_paths.append(str(path))
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        benchmarks = data.get("Benchmarks") if isinstance(data, dict) else None
        if not isinstance(benchmarks, list):
            benchmarks = data.get("benchmarks") if isinstance(data, dict) else None
        if not isinstance(benchmarks, list):
            continue
        for benchmark in benchmarks:
            if not isinstance(benchmark, dict):
                continue
            name = (
                benchmark.get("FullName")
                or benchmark.get("DisplayInfo")
                or benchmark.get("Method")
                or benchmark.get("Name")
            )
            if not isinstance(name, str) or not name.strip():
                continue
            statistics = benchmark.get("Statistics") or benchmark.get("statistics") or {}
            memory = benchmark.get("Memory") or benchmark.get("memory") or {}
            statistics = statistics if isinstance(statistics, dict) else {}
            memory = memory if isinstance(memory, dict) else {}
            results[name.strip()] = {
                "name": name.strip(),
                "mean_ns": first_number(statistics, "Mean", "MeanNanoseconds", "mean"),
                "median_ns": first_number(statistics, "Median", "median"),
                "stddev_ns": first_number(statistics, "StandardDeviation", "StdDev", "standardDeviation"),
                "allocated_bytes": first_number(
                    memory,
                    "BytesAllocatedPerOperation",
                    "AllocatedBytes",
                    "allocated",
                    "Allocated",
                ),
                "source": str(path),
            }
    return results, evidence_paths


def benchmark_result_check(
    paths: list[str],
    baseline_paths: list[str] | None = None,
    threshold_percent: float = 10.0,
    allocation_threshold_bytes: float | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    current, current_paths = load_benchmark_results(paths)
    baseline: dict[str, dict[str, Any]] = {}
    baseline_evidence_paths: list[str] = []
    if baseline_paths:
        baseline, baseline_evidence_paths = load_benchmark_results(baseline_paths)

    regressions: list[dict[str, Any]] = []
    failures: list[str] = []
    new_benchmarks = sorted(name for name in current if name not in baseline) if baseline else []
    missing_current = sorted(name for name in baseline if name not in current)

    if not current:
        failures.append("no BenchmarkDotNet benchmarks found in current results")
    if baseline_paths and not baseline:
        failures.append("no BenchmarkDotNet benchmarks found in baseline results")

    for name, current_row in sorted(current.items()):
        baseline_row = baseline.get(name)
        if not baseline_row:
            continue
        reasons: list[str] = []
        time_change_pct: float | None = None
        baseline_mean = baseline_row.get("mean_ns")
        current_mean = current_row.get("mean_ns")
        if isinstance(baseline_mean, int | float) and isinstance(current_mean, int | float) and baseline_mean > 0:
            time_change_pct = round(((current_mean - baseline_mean) / baseline_mean) * 100, 2)
            if time_change_pct > threshold_percent:
                reasons.append("time")
        allocation_change: float | None = None
        baseline_alloc = baseline_row.get("allocated_bytes")
        current_alloc = current_row.get("allocated_bytes")
        if isinstance(baseline_alloc, int | float) and isinstance(current_alloc, int | float):
            allocation_change = current_alloc - baseline_alloc
            if allocation_threshold_bytes is not None and allocation_change > allocation_threshold_bytes:
                reasons.append("allocation")
        if reasons:
            regressions.append(
                {
                    "name": name,
                    "reasons": reasons,
                    "baseline_mean_ns": baseline_mean,
                    "current_mean_ns": current_mean,
                    "time_change_pct": time_change_pct,
                    "baseline_allocated_bytes": baseline_alloc,
                    "current_allocated_bytes": current_alloc,
                    "allocation_change_bytes": allocation_change,
                }
            )

    if regressions:
        failures.append(f"{len(regressions)} benchmark regression(s) exceeded configured thresholds")

    ok = not failures
    return {
        "name": "benchmark-result-parse",
        "kind": "analysis",
        "ok": ok,
        "status": status(ok),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "summary": {
            "reports": len(current_paths),
            "benchmarks": len(current),
            "baseline_reports": len(baseline_evidence_paths),
            "baseline_benchmarks": len(baseline),
            "threshold_percent": threshold_percent,
            "allocation_threshold_bytes": allocation_threshold_bytes,
            "regressions": len(regressions),
            "new_benchmarks": len(new_benchmarks),
            "missing_current": len(missing_current),
        },
        "format": "benchmarkdotnet-json",
        "evidence_paths": current_paths + baseline_evidence_paths,
        "regressions": regressions[:50],
        "new_benchmarks": new_benchmarks[:50],
        "missing_current": missing_current[:50],
        "failures": failures,
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Local Quality Evidence",
        "",
        f"- Status: {payload['status']}",
        f"- Checks: {payload['summary']['checks']}",
        f"- Passed: {payload['summary']['passed']}",
        f"- Failed: {payload['summary']['failed']}",
        f"- Skipped: {len(payload['skipped'])}",
        "",
        "## Checks",
        "",
    ]
    for check in payload["checks"]:
        duration = check.get("duration_seconds", 0)
        lines.append(f"- `{check['status']}` {check['name']} ({duration}s)")
        error = check.get("summary", {}).get("error") if isinstance(check.get("summary"), dict) else None
        if error:
            lines.append(f"  - Error: {error}")
    if payload["skipped"]:
        lines.extend(["", "## Skipped", ""])
        lines.extend(f"- {item}" for item in payload["skipped"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def command_runner(args: argparse.Namespace, name: str, command: list[str]):
    return lambda: command_check(
        name,
        command,
        timeout_seconds=args.timeout_seconds,
        success_tail=args.success_output_tail_chars,
        failure_tail=args.failure_output_tail_chars,
    )


def build_checks(args: argparse.Namespace) -> tuple[list[tuple[str, Any]], list[str]]:
    checks: list[tuple[str, Any]] = []
    skipped: list[str] = []
    checks.append(
        (
            "line-endings",
            command_runner(
                args,
                "line-endings",
                [sys.executable, "-B", str(SCRIPT_DIR / "validate_line_endings.py"), args.target, "--format", "json"],
            ),
        )
    )
    if getattr(args, "line_endings_changed_only", False):
        checks[-1] = (
            "line-endings",
            command_runner(
                args,
                "line-endings",
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT_DIR / "validate_line_endings.py"),
                    args.target,
                    "--changed-only",
                    "--format",
                    "json",
                ],
            ),
        )
    if args.coverage:
        command = [sys.executable, "-B", str(SCRIPT_DIR / "validate_coverage.py")]
        for coverage in args.coverage:
            command.extend(["--input", coverage])
        if args.target:
            command.extend(["--project-root", args.target])
        command.extend(["--format", "json"])
        checks.append(("coverage-merge", command_runner(args, "coverage-merge", command)))
    else:
        skipped.append("coverage-merge: no --coverage input")
    if args.solution:
        command = [
            sys.executable,
            "-B",
            str(SCRIPT_DIR / "verify_static_analysis.py"),
            "--project-root",
            args.target,
            "--solution",
            args.solution,
            "--timeout-seconds",
            str(args.timeout_seconds),
        ]
        if getattr(args, "static_analysis_no_restore", False):
            command.append("--no-restore")
        checks.append(
            (
                "static-analysis",
                command_runner(
                    args,
                    "static-analysis",
                    command,
                ),
            )
        )
    else:
        skipped.append("static-analysis: no --solution input")
    if args.run_security:
        security_script = SKILLS_DIR / "dotnet-security-review" / "scripts" / "scanner" / "scan_security_patterns.py"
        command = [sys.executable, "-B", str(security_script)]
        for target in args.security_target or [args.target]:
            command.extend(["--target", target])
        if args.security_changed_only:
            command.append("--changed-only")
        if args.security_fail_on:
            command.extend(["--fail-on", args.security_fail_on])
        if args.output_json:
            security_output = Path(args.output_json).resolve().parent / "security-patterns.json"
            command.extend(["--output-json", str(security_output)])
        checks.append(("security-patterns", command_runner(args, "security-patterns", command)))
    else:
        skipped.append("security-patterns: --run-security not requested")
    if args.docs_target:
        checks.append(("docs-link-check", lambda: docs_check(args.docs_target)))
    else:
        skipped.append("docs-link-check: no --docs-target input")
    if args.test_result:
        checks.append(("test-result-parse", lambda: test_result_check(args.test_result)))
    else:
        skipped.append("test-result-parse: no --test-result input")
    if getattr(args, "mutation_result", None):
        checks.append(
            (
                "mutation-result-parse",
                lambda: mutation_result_check(
                    args.mutation_result,
                    minimum=getattr(args, "mutation_minimum", None),
                    fail_on_survived=getattr(args, "mutation_fail_on_survived", False),
                ),
            )
        )
    else:
        skipped.append("mutation-result-parse: no --mutation-result input")
    if getattr(args, "benchmark_result", None):
        checks.append(
            (
                "benchmark-result-parse",
                lambda: benchmark_result_check(
                    args.benchmark_result,
                    baseline_paths=getattr(args, "benchmark_baseline", None),
                    threshold_percent=getattr(args, "benchmark_threshold_percent", 10.0),
                    allocation_threshold_bytes=getattr(args, "benchmark_allocation_threshold_bytes", None),
                ),
            )
        )
    else:
        skipped.append("benchmark-result-parse: no --benchmark-result input")
    if getattr(args, "run_snapshot_check", False):
        checks.append(
            (
                "snapshot-artifact-check",
                lambda: snapshot_artifact_check(
                    getattr(args, "snapshot_target", None) or [args.target],
                    require_gitignore=getattr(args, "snapshot_require_gitignore", False),
                ),
            )
        )
    else:
        skipped.append("snapshot-artifact-check: --run-snapshot-check not requested")
    if getattr(args, "run_slop_scan", False):
        checks.append(
            (
                "slop-scan",
                lambda: slop_scan_check(
                    getattr(args, "slop_target", None) or [args.target],
                    fail_on=getattr(args, "slop_fail_on", "error"),
                ),
            )
        )
    else:
        skipped.append("slop-scan: --run-slop-scan not requested")
    if getattr(args, "sarif", None):
        checks.append(("sarif-parse", lambda: sarif_check(args.sarif)))
    else:
        skipped.append("sarif-parse: no --sarif input")
    return checks, skipped


def orchestrate(args: argparse.Namespace) -> dict[str, Any]:
    started_at = utc_now()
    checks, skipped = build_checks(args)
    workers = max(1, min(args.max_workers, len(checks)))
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(callable_check): name for name, callable_check in checks}
        for future in concurrent.futures.as_completed(future_map):
            try:
                results.append(normalize_check(future.result()))
            except Exception as exc:
                name = future_map[future]
                results.append(
                    normalize_check({
                        "name": name,
                        "kind": "exception",
                        "ok": False,
                        "status": "failed",
                        "duration_seconds": 0,
                        "summary": {"error": str(exc)},
                    })
                )
    results.sort(key=lambda item: str(item["name"]))
    failed = sum(1 for item in results if not item.get("ok"))
    passed = len(results) - failed
    payload = {
        "schema_version": 1,
        "tool": "dotnet-quality-gates.validate_local_quality",
        "ok": failed == 0,
        "status": "passed" if failed == 0 else "failed",
        "started_at": started_at,
        "finished_at": utc_now(),
        "parallel": {"enabled": workers > 1, "max_workers": workers},
        "summary_schema": normalized_summary_schema(),
        "summary": {"checks": len(results), "passed": passed, "failed": failed},
        "checks": results,
        "skipped": skipped,
        "local_ai_triage": {
            "available": False,
            "command": (
                "python -B .agents/manage.py local-ai task --task validation-triage "
                "--input <local-quality.md-or-json>"
            ),
            "fallback": "Read this combined packet directly when local AI is unavailable.",
        },
    }
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="explicit project or docs root to validate")
    parser.add_argument("--coverage", action="append")
    parser.add_argument("--sarif", action="append", help="SARIF report to include in the combined quality packet")
    parser.add_argument("--solution")
    parser.add_argument(
        "--static-analysis-no-restore",
        action="store_true",
        help="force --no-restore for the static-analysis build; packages.config projects also enable it automatically",
    )
    parser.add_argument("--line-endings-changed-only", action="store_true")
    parser.add_argument("--run-security", action="store_true")
    parser.add_argument("--security-target", action="append")
    parser.add_argument("--security-changed-only", action="store_true")
    parser.add_argument("--security-fail-on", choices=["low", "medium", "high"], default="high")
    parser.add_argument("--docs-target", action="append")
    parser.add_argument("--test-result", action="append")
    parser.add_argument("--mutation-result", action="append", help="Stryker-style mutation JSON report to parse")
    parser.add_argument("--mutation-minimum", type=float, help="minimum mutation score percentage")
    parser.add_argument(
        "--mutation-fail-on-survived",
        action="store_true",
        help="fail when survived or no-coverage mutants are present",
    )
    parser.add_argument(
        "--benchmark-result",
        action="append",
        help="BenchmarkDotNet *-report-full.json file or results directory to parse",
    )
    parser.add_argument(
        "--benchmark-baseline",
        action="append",
        help="optional BenchmarkDotNet baseline JSON file or results directory for comparison",
    )
    parser.add_argument(
        "--benchmark-threshold-percent",
        type=float,
        default=10.0,
        help="allowed mean-time regression percentage when comparing benchmark baselines",
    )
    parser.add_argument(
        "--benchmark-allocation-threshold-bytes",
        type=float,
        help="allowed allocation increase in bytes per operation when comparing benchmark baselines",
    )
    parser.add_argument("--run-snapshot-check", action="store_true", help="scan snapshot-test artifacts for unapproved received files")
    parser.add_argument("--snapshot-target", action="append", help="snapshot artifact path for --run-snapshot-check; defaults to --target")
    parser.add_argument("--snapshot-require-gitignore", action="store_true", help="fail when snapshot files exist but .gitignore does not ignore *.received.*")
    parser.add_argument(
        "--run-slop-scan",
        action="store_true",
        help="scan .NET source/project files for common agent shortcut patterns",
    )
    parser.add_argument("--slop-target", action="append", help="source/project path for --run-slop-scan; defaults to --target")
    parser.add_argument(
        "--slop-fail-on",
        choices=["error", "warning"],
        default="error",
        help="minimum slop finding severity that fails the scan",
    )
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=900, help="timeout for each child command")
    parser.add_argument("--success-output-tail-chars", type=int, default=1000)
    parser.add_argument("--failure-output-tail-chars", type=int, default=8000)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument(
        "--packet-root",
        help="write workflow-ready local-quality.json and local-quality.md under this folder",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.packet_root:
        packet_root = Path(args.packet_root)
        args.output_json = args.output_json or str(packet_root / "local-quality.json")
        args.output_md = args.output_md or str(packet_root / "local-quality.md")
    if not args.output_json:
        print("ERROR: --output-json or --packet-root is required", file=sys.stderr)
        return 2
    payload = orchestrate(args)
    json_path = Path(args.output_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if args.output_md:
        write_markdown(Path(args.output_md), payload)
    for check in payload["checks"]:
        print(f"{check['name']}: {check['status']}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
