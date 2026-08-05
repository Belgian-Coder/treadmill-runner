"""Measure the supported direct-ripgrep repository search path.

The suite intentionally keeps golden paths out of every search request. Golden
paths are used only after retrieval to score evidence. Artifact byte and token
values describe serialized evidence packets, not provider billing. The removed
indexed arms remain documented in historical workflow evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
ARM_DIRECT = "direct-rg"
ALL_ARMS = (ARM_DIRECT,)
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "checks",
    "contains",
    "controlled",
    "documented",
    "do",
    "does",
    "file",
    "files",
    "folder",
    "for",
    "from",
    "how",
    "headings",
    "in",
    "is",
    "it",
    "of",
    "on",
    "operator",
    "operators",
    "or",
    "repo",
    "repository",
    "required",
    "that",
    "the",
    "this",
    "to",
    "tool",
    "what",
    "where",
    "which",
    "with",
    "without",
}
RG_EXCLUDES = (
    "!.agents/local-ai/cache/**",
    "!.agents/tmp/**",
    "!automations/**/runs/**",
    "!**/suites/**",
    "!**/fixtures/**",
    "!**/run_self_tests.py",
    "!**/__pycache__/**",
    "!.git/**",
    "!.agents/registry.json",
    "!automations/registry.json",
)


class BenchmarkError(RuntimeError):
    """Raised for malformed suites or unavailable benchmark arms."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def normalized_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def query_terms(question: str) -> list[str]:
    raw = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", question.lower())
    terms: list[str] = []
    for item in raw:
        pieces = [item]
        if any(separator in item for separator in ("_", "-", ".", "/", ":")):
            pieces.extend(part for part in re.split(r"[_\-./:]+", item) if part)
        for piece in pieces:
            if len(piece) < 3 or piece in STOP_WORDS or piece in terms:
                continue
            terms.append(piece)
    return terms[:12]


def _require_keys(value: dict[str, Any], required: set[str], optional: set[str], label: str) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing or unknown:
        raise BenchmarkError(f"{label} keys invalid: missing={missing}, unknown={unknown}")


def load_suite(path: Path) -> dict[str, Any]:
    try:
        suite = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"cannot read suite: {exc}") from exc
    if not isinstance(suite, dict):
        raise BenchmarkError("suite must be a JSON object")
    _require_keys(
        suite,
        {"schema_version", "suite_id", "description", "thresholds", "cases"},
        {"as_of"},
        "suite",
    )
    if suite["schema_version"] != SCHEMA_VERSION:
        raise BenchmarkError(f"unsupported schema_version: {suite['schema_version']}")
    thresholds = suite["thresholds"]
    if not isinstance(thresholds, dict):
        raise BenchmarkError("thresholds must be an object")
    _require_keys(
        thresholds,
        {"minimum_task_success_rate"},
        set(),
        "thresholds",
    )
    cases = suite["cases"]
    if not isinstance(cases, list) or not cases:
        raise BenchmarkError("cases must be a non-empty list")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise BenchmarkError(f"cases[{index}] must be an object")
        _require_keys(
            case,
            {"id", "question", "expect_status", "required_path_groups", "top_k"},
            {"notes"},
            f"cases[{index}]",
        )
        case_id = str(case["id"])
        if not case_id or case_id in seen:
            raise BenchmarkError(f"case id must be unique and non-empty: {case_id!r}")
        seen.add(case_id)
        if case["expect_status"] not in {"evidence", "no-evidence"}:
            raise BenchmarkError(f"{case_id}: expect_status must be evidence or no-evidence")
        groups = case["required_path_groups"]
        if not isinstance(groups, list) or any(
            not isinstance(group, list)
            or not group
            or any(not isinstance(path_value, str) or not path_value for path_value in group)
            for group in groups
        ):
            raise BenchmarkError(f"{case_id}: required_path_groups must contain non-empty string arrays")
        if case["expect_status"] == "evidence" and not groups:
            raise BenchmarkError(f"{case_id}: evidence cases require path groups")
        if case["expect_status"] == "no-evidence" and groups:
            raise BenchmarkError(f"{case_id}: no-evidence cases cannot require paths")
        if not isinstance(case["top_k"], int) or not 1 <= case["top_k"] <= 10:
            raise BenchmarkError(f"{case_id}: top_k must be in [1, 10]")
    return suite


def _rg_command(terms: list[str]) -> list[str]:
    command = [
        "rg",
        "--json",
        "--hidden",
        "--ignore-case",
        "--fixed-strings",
        "--max-count",
        "8",
        "--max-filesize",
        "1M",
    ]
    for pattern in RG_EXCLUDES:
        command.extend(["--glob", pattern])
    for term in terms:
        command.extend(["-e", term])
    command.append(".")
    return command


def direct_rg_search(root: Path, question: str, top_k: int) -> dict[str, Any]:
    terms = query_terms(question)
    if not terms:
        return {
            "ok": True,
            "evidence": [],
            "duration_ms": 0.0,
            "files_read": None,
            "matched_file_count": 0,
            "terms": [],
            "returncode": 0,
        }
    if shutil.which("rg") is None:
        raise BenchmarkError("direct-rg arm requires rg on PATH")
    started = time.perf_counter()
    completed = subprocess.run(
        _rg_command(terms),
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    duration_ms = (time.perf_counter() - started) * 1000
    if completed.returncode not in {0, 1}:
        raise BenchmarkError(f"rg failed with exit {completed.returncode}: {completed.stderr.strip()}")
    matches: dict[str, dict[str, Any]] = {}
    for raw_line in completed.stdout.splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data", {})
        path_value = normalized_path(str(data.get("path", {}).get("text", "")))
        line_text = str(data.get("lines", {}).get("text", "")).strip()
        if not path_value or not line_text:
            continue
        lowered = line_text.lower()
        matched_terms = {term for term in terms if term in lowered}
        row = matches.setdefault(
            path_value,
            {"matched_terms": set(), "occurrences": 0, "excerpt": "", "best_line_terms": 0},
        )
        row["matched_terms"].update(matched_terms)
        row["occurrences"] += sum(lowered.count(term) for term in matched_terms)
        if len(matched_terms) > row["best_line_terms"]:
            row["best_line_terms"] = len(matched_terms)
            row["excerpt"] = " ".join(line_text.split())[:500]
    ranked: list[dict[str, Any]] = []
    required_coverage = 1.0 if len(terms) <= 2 else 0.5
    for path_value, row in matches.items():
        coverage = len(row["matched_terms"]) / len(terms)
        if coverage < required_coverage:
            continue
        path_lower = path_value.lower()
        path_hits = sum(term in path_lower for term in terms) / len(terms)
        occurrence_score = min(int(row["occurrences"]), 10) / 10
        score = (coverage * 0.75) + (path_hits * 0.15) + (occurrence_score * 0.10)
        ranked.append(
            {
                "path": path_value,
                "excerpt": row["excerpt"],
                "score": round(score, 4),
                "term_coverage": round(coverage, 4),
            }
        )
    ranked.sort(key=lambda item: (-float(item["score"]), str(item["path"])))
    return {
        "ok": True,
        "evidence": ranked[:top_k],
        "duration_ms": round(duration_ms, 3),
        "files_read": None,
        "matched_file_count": len(matches),
        "terms": terms,
        "returncode": completed.returncode,
    }


def _case_score(case: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    evidence = [
        {
            "path": normalized_path(str(item.get("path", ""))),
            "excerpt": str(item.get("excerpt", "")),
            "score": item.get("score"),
        }
        for item in raw.get("evidence", [])
        if isinstance(item, dict) and item.get("path")
    ]
    paths = [item["path"] for item in evidence]
    groups = [
        [normalized_path(str(path_value)) for path_value in group]
        for group in case["required_path_groups"]
    ]
    group_hits = [any(candidate in paths for candidate in group) for group in groups]
    expected_status = str(case["expect_status"])
    actual_status = "evidence" if evidence else "no-evidence"
    task_success = actual_status == expected_status and (
        expected_status == "no-evidence" or all(group_hits)
    )
    expected_paths = {path_value for group in groups for path_value in group}
    golden_hits = sum(path_value in expected_paths for path_value in paths)
    group_recall = 1.0 if not groups else sum(group_hits) / len(groups)
    packet = {"evidence": evidence}
    packet_bytes = len(canonical_bytes(packet))
    return {
        "id": str(case["id"]),
        "question": str(case["question"]),
        "expected_status": expected_status,
        "actual_status": actual_status,
        "task_success": task_success,
        "required_group_recall": round(group_recall, 4),
        "required_group_hits": group_hits,
        "top1_golden_hit": bool(paths and paths[0] in expected_paths),
        "golden_path_precision": round(golden_hits / len(paths), 4) if paths else (1.0 if not expected_paths else 0.0),
        "evidence_count": len(evidence),
        "evidence": evidence,
        "artifact_context_bytes": packet_bytes,
        "artifact_context_tokens_estimated": math.ceil(packet_bytes / 4),
        "duration_ms": raw.get("duration_ms"),
        "engine_duration_ms": raw.get("engine_duration_ms"),
        "files_read": raw.get("files_read"),
        "matched_file_count": raw.get("matched_file_count"),
        "model_starts": raw.get("model_starts", 0),
        "query_wrote_cache": raw.get("query_wrote_cache", False),
        "retrieval_mode": raw.get("retrieval_mode", ARM_DIRECT),
        "terms": raw.get("terms", []),
    }


def _aggregate_arm(arm: str, cases: list[dict[str, Any]], wall_ms: float) -> dict[str, Any]:
    no_evidence = [case for case in cases if case["expected_status"] == "no-evidence"]
    context_bytes = sum(int(case["artifact_context_bytes"]) for case in cases)
    return {
        "arm": arm,
        "case_count": len(cases),
        "passed": sum(bool(case["task_success"]) for case in cases),
        "task_success_rate": round(sum(bool(case["task_success"]) for case in cases) / len(cases), 4),
        "required_group_recall": round(
            sum(float(case["required_group_recall"]) for case in cases) / len(cases),
            4,
        ),
        "no_evidence_precision": round(
            sum(case["actual_status"] == "no-evidence" for case in no_evidence) / len(no_evidence),
            4,
        )
        if no_evidence
        else None,
        "top1_golden_rate": round(sum(bool(case["top1_golden_hit"]) for case in cases) / len(cases), 4),
        "artifact_context_bytes": context_bytes,
        "artifact_context_tokens_estimated": math.ceil(context_bytes / 4),
        "wall_duration_ms": round(wall_ms, 3),
        "model_starts": sum(int(case.get("model_starts") or 0) for case in cases),
        "source_files_read_reported": sum(
            int(case["files_read"]) for case in cases if isinstance(case.get("files_read"), int)
        ),
        "query_cache_write_count": sum(bool(case.get("query_wrote_cache")) for case in cases),
        "cases": cases,
    }


def benchmark_report(root: Path, suite: dict[str, Any], arms: tuple[str, ...]) -> dict[str, Any]:
    cases = list(suite["cases"])
    arm_reports: dict[str, dict[str, Any]] = {}
    if ARM_DIRECT in arms:
        started = time.perf_counter()
        scored = [
            _case_score(case, direct_rg_search(root, str(case["question"]), int(case["top_k"])))
            for case in cases
        ]
        arm_reports[ARM_DIRECT] = _aggregate_arm(
            ARM_DIRECT,
            scored,
            (time.perf_counter() - started) * 1000,
        )
    thresholds = dict(suite["thresholds"])
    checks: dict[str, bool] = {
        "task_success": (
            arm_reports[ARM_DIRECT]["task_success_rate"]
            >= float(thresholds["minimum_task_success_rate"])
        )
    }
    decision: dict[str, Any] = {
        "status": "direct-search-current",
        "keep_indexed_search": False,
        "reason": (
            "Direct rg is the supported repository-search path. The removed indexed arms remain "
            "recorded only in historical workflow evidence."
        ),
    }
    ok = bool(checks) and all(checks.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "agent-benchmarking.repository-search-utility-v1",
        "suite_id": suite["suite_id"],
        "ok": ok,
        "measurement_scope": {
            "current_worktree": True,
            "live_model": False,
            "provider_usage": False,
            "billing_claim": False,
            "artifact_context_estimate": True,
            "golden_paths_hidden_from_retrieval": True,
        },
        "arms": list(arms),
        "thresholds": thresholds,
        "checks": checks,
        "decision": decision,
        "results": arm_reports,
        "supported_claim": (
            "On the fixed current-tree suite, the report compares evidence quality, abstention, "
            "wall time, and serialized evidence context without passing golden paths to retrieval."
        ),
        "unsupported_claims": [
            "complete agent task accuracy",
            "provider-token savings",
            "provider cost reduction",
            "cross-host performance",
            "historical defect detection beyond checked-in fixtures",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Repository Search Utility V1",
        "",
        f"- Result: `{'pass' if report['ok'] else 'decision-needed'}`",
        f"- Decision: `{report['decision']['status']}`",
        "",
        "| Arm | Evidence tasks | Group recall | No-evidence precision | Context bytes | Wall ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for arm in report["arms"]:
        row = report["results"][arm]
        lines.append(
            f"| {arm} | {row['passed']}/{row['case_count']} | "
            f"{row['required_group_recall']:.1%} | "
            f"{row['no_evidence_precision']:.1%} | "
            f"{row['artifact_context_bytes']} | {row['wall_duration_ms']:.3f} |"
        )
    lines.extend(["", f"- Claim boundary: {report['supported_claim']}"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--suite", required=True, help="checked-in repository search suite")
    parser.add_argument("--arms", nargs="+", choices=ALL_ARMS, default=list(ALL_ARMS))
    parser.add_argument("--output", help="optional JSON output path, normally inside workflow run evidence")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args(argv)
    try:
        root = Path(args.root).resolve()
        suite = load_suite(Path(args.suite))
        report = benchmark_report(root, suite, tuple(args.arms))
    except BenchmarkError as exc:
        print(f"repository search benchmark error: {exc}", file=sys.stderr)
        return 2
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
