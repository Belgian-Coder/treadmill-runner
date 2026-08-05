#!/usr/bin/env python3
"""Evaluate static routing evidence against deterministic owner-routing cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import benchmark_common as common


TOOL_NAME = f"{common.TOOL_NAME}.routing-evidence-eval"
OUTPUT_EXCERPT_LIMIT = 800
DEFAULT_PROOF_LINE_LIMIT = 50
PRIMARY_TIER = 1
DERIVED_TIER = 2
ADVISORY_TIER = 3


def normalize_text(value: object) -> str:
    return str(value or "").replace("\\", "/").lower()


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    rows = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in rows if str(item).strip()]


def case_id(case: dict[str, Any]) -> str:
    return str(case.get("id") or case.get("case_id") or "").strip()


def token_is_path(value: str) -> bool:
    return "/" in value.replace("\\", "/")


def direct_owner_patterns(target: str) -> list[str]:
    normalized = normalize_text(target)
    return [
        f"[skill:{normalized}]",
        f"[workflow:{normalized}]",
        f'"skill":"{normalized}"',
        f'"skill": "{normalized}"',
        f"launching skill: {normalized}",
    ]


def entry_file_patterns(target: str) -> list[str]:
    normalized = normalize_text(target)
    return [
        f".agents/skills/{normalized}/skill.md",
        f".claude/skills/{normalized}/skill.md",
        f"automations/{normalized}/workflow.md",
    ]


def owner_directory_patterns(target: str) -> list[str]:
    normalized = normalize_text(target)
    return [
        f".agents/skills/{normalized}/",
        f".claude/skills/{normalized}/",
        f"automations/{normalized}/",
    ]


def base_directory_line_matches(target: str, line: str) -> bool:
    if token_is_path(target):
        return False
    normalized = normalize_text(line)
    if "base directory for this skill:" not in normalized:
        return False
    needle = f"/{normalize_text(target)}"
    return normalized.rstrip("/").endswith(needle) or f"{needle}/" in normalized


def classify_line_tier(target: str, line: str) -> int | None:
    if not target.strip():
        return None
    normalized_line = normalize_text(line)
    normalized_target = normalize_text(target)
    if not token_is_path(target):
        if any(pattern in normalized_line for pattern in direct_owner_patterns(target)):
            return PRIMARY_TIER
        if any(pattern in normalized_line for pattern in entry_file_patterns(target)):
            return PRIMARY_TIER
        if base_directory_line_matches(target, line):
            return PRIMARY_TIER
        if any(pattern in normalized_line for pattern in owner_directory_patterns(target)):
            return DERIVED_TIER
    elif normalized_target in normalized_line:
        return DERIVED_TIER
    if normalized_target and normalized_target in normalized_line:
        return ADVISORY_TIER
    return None


def best_evidence_hit(target: str, output_text: str) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for line_no, line in enumerate(output_text.splitlines() or [output_text], start=1):
        tier = classify_line_tier(target, line)
        if tier is None:
            continue
        hit = {"token": target, "tier": tier, "line": line.strip(), "line_no": line_no}
        if best is None or tier < int(best["tier"]):
            best = hit
    return best


def required_skill_tokens(case: dict[str, Any]) -> list[str]:
    tokens = as_list(case.get("required_skills"))
    expected = str(case.get("expected_owner", "")).strip()
    if expected and expected not in tokens:
        tokens.insert(0, expected)
    return list(dict.fromkeys(tokens))


def output_for_case(evidence: dict[str, Any], cid: str) -> dict[str, Any]:
    rows = evidence.get("results") or evidence.get("cases") or []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and str(row.get("case_id") or row.get("id")) == cid:
                return row
    raw = evidence.get(cid)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return {"case_id": cid, "output_text": raw}
    outputs = evidence.get("outputs")
    if isinstance(outputs, dict):
        raw_output = outputs.get(cid)
        if isinstance(raw_output, dict):
            return raw_output
        if isinstance(raw_output, str):
            return {"case_id": cid, "output_text": raw_output}
    return {"case_id": cid, "output_text": ""}


def output_text(row: dict[str, Any]) -> str:
    if isinstance(row.get("output_text"), str):
        return str(row["output_text"])
    stdout = str(row.get("stdout", ""))
    stderr = str(row.get("stderr", ""))
    text = str(row.get("text", ""))
    return "\n".join(item for item in (text, stdout, stderr) if item)


def collect_hits(tokens: list[str], text: str) -> dict[str, dict[str, Any]]:
    hits: dict[str, dict[str, Any]] = {}
    for token in tokens:
        hit = best_evidence_hit(token, text)
        if hit is not None:
            hits[token] = hit
    return hits


def proof_lines_from_hits(hits: list[dict[str, Any]], *, limit: int) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for hit in sorted(hits, key=lambda item: (int(item.get("tier", 99)), int(item.get("line_no", 0)))):
        line = str(hit.get("line", "")).strip()
        if line and line not in seen:
            seen.add(line)
            lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def proof_lines_from_output(tokens: list[str], text: str, *, limit: int) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines() or [text]:
        clean = line.strip()
        if not clean or clean in seen:
            continue
        if any(classify_line_tier(token, clean) is not None for token in tokens):
            seen.add(clean)
            lines.append(clean)
        if len(lines) >= limit:
            break
    return lines


def tier_name(tier: int) -> str:
    if tier == PRIMARY_TIER:
        return "primary"
    if tier == DERIVED_TIER:
        return "derived"
    return "advisory"


def normalize_status(row: dict[str, Any]) -> tuple[bool, str | None]:
    if row.get("timed_out") is True:
        return False, "timeout"
    exit_code = row.get("exit_code")
    if exit_code in (126, 127):
        return False, "transport"
    return True, None


def classify_failure(
    *,
    should_activate: bool,
    missing: list[str],
    all_hits: dict[str, dict[str, Any]],
    optional_hits: list[str],
    disallowed_gating_hits: list[str],
) -> str | None:
    if not should_activate:
        return "negative_false_positive" if disallowed_gating_hits else None
    if disallowed_gating_hits and missing:
        return "mixed"
    if disallowed_gating_hits:
        return "disallowed_hit"
    if missing:
        if optional_hits:
            return "optional_only"
        if all_hits and all(int(hit.get("tier", 99)) >= ADVISORY_TIER for hit in all_hits.values()):
            return "weak_evidence_only"
        return "missing_required"
    return None


def failure_category(failure_kind: str | None) -> str | None:
    if failure_kind is None:
        return None
    if failure_kind == "timeout":
        return "timeout"
    if failure_kind == "transport":
        return "tool-failure"
    return "assertion-mismatch"


def taxonomy_category(failure_kind: str | None) -> str:
    if failure_kind in {"missing_required", "weak_evidence_only", "optional_only"}:
        return "missing-evidence"
    if failure_kind == "timeout":
        return "timeout"
    if failure_kind == "transport":
        return "tool-failure"
    if failure_kind in {"disallowed_hit", "negative_false_positive", "mixed"}:
        return "assertion-mismatch"
    return "other"


def evaluate_case(
    case: dict[str, Any],
    evidence_row: dict[str, Any],
    *,
    batch_run_id: str,
    proof_line_limit: int = DEFAULT_PROOF_LINE_LIMIT,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    cid = case_id(case)
    text = output_text(evidence_row)
    should_activate = bool(case.get("should_activate", True))
    advisory = bool(case.get("advisory", False))
    started_ok, infra_failure = normalize_status(evidence_row)
    required_skills = required_skill_tokens(case) if should_activate else []
    required_files = as_list(case.get("required_files"))
    required_evidence = as_list(case.get("required_evidence"))
    optional_skills = as_list(case.get("optional_skills"))
    disallowed_skills = as_list(case.get("disallowed_skills"))
    disallowed_min_tier = int(case.get("disallowed_min_tier", DERIVED_TIER) or DERIVED_TIER)

    skill_hits = collect_hits(required_skills, text)
    file_hits = collect_hits(required_files, text)
    evidence_hits = collect_hits(required_evidence, text)
    optional_hit_map = collect_hits(optional_skills, text)
    disallowed_hit_map = collect_hits(disallowed_skills, text)
    disallowed_gating_hits = [
        token for token, hit in disallowed_hit_map.items() if int(hit["tier"]) <= disallowed_min_tier
    ]

    matched: list[str] = []
    missing: list[str] = []
    for token in required_skills:
        hit = skill_hits.get(token)
        if hit and int(hit["tier"]) <= PRIMARY_TIER:
            matched.append(token)
        else:
            missing.append(token)
    for token in required_files:
        hit = file_hits.get(token)
        if hit and int(hit["tier"]) <= DERIVED_TIER:
            matched.append(token)
        else:
            missing.append(token)
    for token in required_evidence:
        if token in evidence_hits:
            matched.append(token)
        else:
            missing.append(token)

    all_hits = {**skill_hits, **file_hits, **evidence_hits, **optional_hit_map, **disallowed_hit_map}
    optional_hits = sorted(optional_hit_map)
    if not started_ok:
        failure_kind_value = infra_failure
    else:
        failure_kind_value = classify_failure(
            should_activate=should_activate,
            missing=missing,
            all_hits={key: all_hits[key] for key in missing if key in all_hits},
            optional_hits=optional_hits,
            disallowed_gating_hits=disallowed_gating_hits,
        )
    if failure_kind_value is None:
        status = "pass"
    elif advisory and failure_kind_value not in {"timeout", "transport"}:
        status = "advisory"
    else:
        status = "infra_error" if failure_kind_value == "transport" else "fail"
    row = {
        "unit_run_id": f"{batch_run_id}:{cid}",
        "case_id": cid,
        "category": str(case.get("category", "")),
        "status": status,
        "expected_owner": str(case.get("expected_owner", "")),
        "should_activate": should_activate,
        "advisory": advisory,
        "matched_evidence": matched,
        "missing_evidence": missing if should_activate else [],
        "optional_hits": optional_hits,
        "disallowed_hits": sorted(disallowed_hit_map),
        "disallowed_gating_hits": sorted(disallowed_gating_hits),
        "failure_kind": failure_kind_value,
        "failure_category": failure_category(failure_kind_value),
        "timed_out": bool(evidence_row.get("timed_out", False)),
        "artifact_directory": str(evidence_row.get("artifact_directory") or evidence_row.get("artifact_dir") or ""),
        "proof_lines": proof_lines_from_output(list(all_hits), text, limit=proof_line_limit),
        "output_excerpt": text[:OUTPUT_EXCERPT_LIMIT],
    }
    evidence_items = [
        {"tier": tier_name(int(hit["tier"])), "path": str(hit["token"]), "claim": cid}
        for hit in all_hits.values()
    ]
    return row, evidence_items


def load_routing_suite(path: Path) -> dict[str, Any]:
    data = common.read_json(path)
    if not isinstance(data, dict):
        raise SystemExit("routing suite must be a JSON object.")
    return data


def validate_suite(suite: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    tasks = suite.get("tasks") or suite.get("cases")
    if not isinstance(tasks, list) or not tasks:
        return ["routing suite must contain a non-empty tasks or cases list"]
    seen: set[str] = set()
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            issues.append(f"case {index} must be an object")
            continue
        cid = case_id(task)
        if not cid:
            issues.append(f"case {index} is missing id")
        elif cid in seen:
            issues.append(f"duplicate case id: {cid}")
        seen.add(cid)
        should_activate = bool(task.get("should_activate", True))
        if should_activate and not (
            task.get("expected_owner") or task.get("required_skills") or task.get("required_files") or task.get("required_evidence")
        ):
            issues.append(f"{cid or index} must declare expected_owner or required evidence")
        if not task.get("expected_checks"):
            issues.append(f"{cid or index} is missing expected_checks")
        if "disallowed_min_tier" in task:
            try:
                tier = int(task["disallowed_min_tier"])
            except (TypeError, ValueError):
                issues.append(f"{cid or index} disallowed_min_tier must be an integer")
            else:
                if tier < PRIMARY_TIER or tier > ADVISORY_TIER:
                    issues.append(f"{cid or index} disallowed_min_tier must be 1, 2, or 3")
    return issues


def load_evidence(path: Path) -> dict[str, Any]:
    data = common.read_json(path)
    if isinstance(data, dict):
        return data
    raise SystemExit("routing evidence must be a JSON object.")


def metrics_from_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    true_positive = 0
    false_positive = 0
    false_negative = 0
    for row in results:
        if row.get("should_activate"):
            if row.get("status") == "pass":
                true_positive += 1
            elif row.get("missing_evidence"):
                false_negative += 1
        elif row.get("failure_kind") == "negative_false_positive":
            false_positive += 1
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    return {
        "route_true_positive": true_positive,
        "route_false_positive": false_positive,
        "route_false_negative": false_negative,
        "route_precision": round(true_positive / precision_denominator, 4) if precision_denominator else 1.0,
        "route_recall": round(true_positive / recall_denominator, 4) if recall_denominator else 1.0,
        "negative_false_positive_count": sum(1 for row in results if row.get("failure_kind") == "negative_false_positive"),
        "disallowed_hit_count": sum(len(row.get("disallowed_gating_hits", [])) for row in results),
        "optional_hit_count": sum(len(row.get("optional_hits", [])) for row in results),
        "weak_evidence_only_count": sum(1 for row in results if row.get("failure_kind") == "weak_evidence_only"),
    }


STATUS_RANK = {"infra_error": 0, "fail": 1, "advisory": 2, "pass": 3}


def normalize_baseline_rows(data: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    rows = data.get("results")
    if not isinstance(rows, list):
        rows = data.get("cases")
    if not isinstance(rows, list):
        rows = []
    normalized: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            issues.append(f"baseline row {index} must be an object")
            continue
        cid = case_id(row)
        if not cid:
            issues.append(f"baseline row {index} is missing case_id")
            continue
        if cid in normalized:
            issues.append(f"duplicate baseline case_id: {cid}")
            continue
        status = str(row.get("expected_status") or row.get("status") or "pass")
        if status not in STATUS_RANK:
            issues.append(f"{cid} baseline status is not supported: {status}")
            status = "fail"
        normalized[cid] = {
            "case_id": cid,
            "status": status,
            "failure_kind": row.get("expected_failure_kind", row.get("failure_kind")),
            "timed_out": bool(row.get("timed_out", False)),
        }
    return normalized, issues


def load_baseline(path: Path) -> dict[str, dict[str, Any]]:
    data = common.read_json(path.expanduser().resolve())
    if not isinstance(data, dict):
        raise SystemExit("routing baseline must be a JSON object.")
    rows, issues = normalize_baseline_rows(data)
    if issues:
        raise SystemExit(f"routing baseline is invalid: {'; '.join(issues)}")
    return rows


def compare_results_to_baseline(results: list[dict[str, Any]], baseline: dict[str, dict[str, Any]]) -> dict[str, Any]:
    current = {str(row.get("case_id", "")): row for row in results if str(row.get("case_id", "")).strip()}
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    status_changes: list[dict[str, Any]] = []
    for cid, expected in sorted(baseline.items()):
        actual = current.get(cid)
        if actual is None:
            continue
        expected_status = str(expected.get("status", "pass"))
        actual_status = str(actual.get("status", "fail"))
        expected_rank = STATUS_RANK.get(expected_status, 1)
        actual_rank = STATUS_RANK.get(actual_status, 1)
        row = {
            "case_id": cid,
            "expected_status": expected_status,
            "actual_status": actual_status,
            "expected_failure_kind": expected.get("failure_kind"),
            "actual_failure_kind": actual.get("failure_kind"),
        }
        if actual_rank < expected_rank:
            regressions.append(row)
        elif actual_rank > expected_rank:
            improvements.append(row)
        elif expected.get("failure_kind") != actual.get("failure_kind"):
            status_changes.append(row)
    missing_results = sorted(case for case in baseline if case not in current)
    missing_baseline = sorted(case for case in current if case not in baseline)
    ok = not regressions and not missing_results and not missing_baseline
    return {
        "schema_version": common.SCHEMA_VERSION,
        "tool": f"{TOOL_NAME}.baseline-comparison",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "summary": {
            "baseline_case_count": len(baseline),
            "current_case_count": len(current),
            "regression_count": len(regressions),
            "improvement_count": len(improvements),
            "status_change_count": len(status_changes),
            "missing_result_count": len(missing_results),
            "missing_baseline_count": len(missing_baseline),
        },
        "regressions": regressions,
        "improvements": improvements,
        "status_changes": status_changes,
        "missing_results": missing_results,
        "missing_baseline": missing_baseline,
    }


def evaluate_routing_suite(
    *,
    suite_path: Path,
    evidence_path: Path,
    baseline_path: Path | None = None,
    batch_run_id: str = "",
    proof_line_limit: int = DEFAULT_PROOF_LINE_LIMIT,
) -> dict[str, Any]:
    suite_path = suite_path.expanduser().resolve()
    evidence_path = evidence_path.expanduser().resolve()
    suite = load_routing_suite(suite_path)
    issues = validate_suite(suite)
    if issues:
        raise SystemExit(f"routing suite is invalid: {'; '.join(issues)}")
    evidence = load_evidence(evidence_path)
    run_id = batch_run_id.strip() or f"routing-{common.failure_fingerprint(suite_path, evidence_path)}"
    results: list[dict[str, Any]] = []
    evidence_items: list[dict[str, str]] = []
    for task in suite.get("tasks") or suite.get("cases") or []:
        cid = case_id(task)
        row, row_evidence = evaluate_case(
            task,
            output_for_case(evidence, cid),
            batch_run_id=run_id,
            proof_line_limit=proof_line_limit,
        )
        results.append(row)
        evidence_items.extend(row_evidence)
    metrics = metrics_from_results(results)
    failures = [row for row in results if row.get("status") not in {"pass", "advisory"}]
    advisory_rows = [row for row in results if row.get("status") == "advisory"]
    mismatch_kind = str(failures[0].get("failure_kind", "none")) if failures else "none"
    failure_rows = [
        {
            "category": taxonomy_category(str(row.get("failure_kind", ""))),
            "detail": f"{row.get('case_id')}: {row.get('failure_kind')}",
            "evidence": "routing evidence output",
        }
        for row in failures
    ]
    baseline_comparison = (
        compare_results_to_baseline(results, load_baseline(baseline_path))
        if baseline_path is not None
        else {"available": False, "reason": "no baseline supplied"}
    )
    ok = not failures and bool(baseline_comparison.get("ok", True))
    report = {
        "schema_version": common.SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "ok": ok,
        "status": "passed" if ok else "failed",
        "batch_run_id": run_id,
        "suite": str(suite.get("suite", suite_path.stem)),
        "suite_path": str(suite_path),
        "evidence_path": str(evidence_path),
        "baseline_path": str(baseline_path.expanduser().resolve()) if baseline_path is not None else "",
        "summary": {
            "case_count": len(results),
            "passed": sum(1 for row in results if row.get("status") == "pass"),
            "failed": len(failures),
            "advisory": len(advisory_rows),
        },
        "metrics": metrics,
        "routing_determinism": {
            "failure_category": "none" if not failures else "assertion-mismatch",
            "mismatch_kind": mismatch_kind,
            "failure_fingerprint": "" if not failures else common.failure_fingerprint(mismatch_kind, failures),
            "batch_run_id": run_id,
        },
        "failure_taxonomy": common.normalize_failure_taxonomy(failure_rows),
        "evidence_tiers": common.normalize_evidence_tiers(evidence_items),
        "baseline_comparison": baseline_comparison,
        "results": results,
    }
    if baseline_path is not None and not baseline_comparison.get("ok", True) and not failures:
        report["routing_determinism"]["failure_category"] = "assertion-mismatch"
        report["routing_determinism"]["mismatch_kind"] = "baseline_comparison"
        report["routing_determinism"]["failure_fingerprint"] = common.failure_fingerprint(baseline_comparison)
        report["failure_taxonomy"] = common.normalize_failure_taxonomy(
            [
                {
                    "category": "assertion-mismatch",
                    "detail": "routing baseline comparison failed",
                    "evidence": "routing baseline",
                }
            ]
        )
    return report


def validate_suite_file(path: Path) -> dict[str, Any]:
    suite = load_routing_suite(path)
    issues = validate_suite(suite)
    tasks = suite.get("tasks") if isinstance(suite.get("tasks"), list) else suite.get("cases")
    return {
        "schema_version": common.SCHEMA_VERSION,
        "tool": f"{TOOL_NAME}.suite-check",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "suite": str(suite.get("suite", path.stem)),
        "case_count": len(tasks) if isinstance(tasks, list) else 0,
        "summary": {
            "case_count": len(tasks) if isinstance(tasks, list) else 0,
            "issue_count": len(issues),
        },
        "issues": issues,
    }


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "schema_version": report.get("schema_version", common.SCHEMA_VERSION),
        "tool": report.get("tool", TOOL_NAME),
        "ok": bool(report.get("ok", True)),
        "status": report.get("status", ""),
        "summary": report.get("summary", {}),
        "metrics": report.get("metrics", {}),
        "routing_determinism": report.get("routing_determinism", {}),
        "failure_taxonomy": report.get("failure_taxonomy", []),
    }
    baseline = report.get("baseline_comparison")
    if isinstance(baseline, dict):
        summary["baseline_comparison"] = {
            "available": baseline.get("available", True),
            "ok": baseline.get("ok", True),
            "status": baseline.get("status", ""),
            "summary": baseline.get("summary", {}),
        }
    return summary


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    metrics = report.get("metrics", {}) if isinstance(report.get("metrics"), dict) else {}
    lines = [
        "# Routing Evidence Eval",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Cases: {summary.get('case_count', 0)}",
        f"- Passed/failed: {summary.get('passed', 0)}/{summary.get('failed', 0)}",
        f"- Route precision: {metrics.get('route_precision', 0)}",
        f"- Route recall: {metrics.get('route_recall', 0)}",
        f"- Negative false positives: {metrics.get('negative_false_positive_count', 0)}",
        f"- Disallowed hits: {metrics.get('disallowed_hit_count', 0)}",
    ]
    baseline = report.get("baseline_comparison") if isinstance(report.get("baseline_comparison"), dict) else {}
    if baseline and baseline.get("available", True) is not False:
        baseline_summary = baseline.get("summary") if isinstance(baseline.get("summary"), dict) else {}
        lines.extend(
            [
                f"- Baseline regressions: {baseline_summary.get('regression_count', 0)}",
                f"- Missing baseline/results: {baseline_summary.get('missing_baseline_count', 0)}/{baseline_summary.get('missing_result_count', 0)}",
            ]
        )
    lines.extend(["", "| Case | Status | Failure |", "|---|---|---|"])
    for row in report.get("results", []):
        if isinstance(row, dict):
            lines.append(f"| `{row.get('case_id', '')}` | {row.get('status', '')} | {row.get('failure_kind') or ''} |")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="routing evidence suite JSON")
    parser.add_argument("--evidence", help="static evidence JSON with result rows")
    parser.add_argument("--baseline", help="optional prior routing eval report or baseline JSON")
    parser.add_argument("--check-suite", action="store_true", help="validate suite schema only")
    parser.add_argument("--batch-run-id", default="")
    parser.add_argument("--proof-line-limit", type=int, default=DEFAULT_PROOF_LINE_LIMIT)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    parser.add_argument("--summary", action="store_true", help="with --format json, emit compact fields")
    parser.add_argument("--output", help="optional output path")
    return parser


def main(argv: list[str] | None = None) -> int:
    common.require_supported_python()
    args = build_parser().parse_args(argv)
    suite_path = Path(args.suite)
    if args.check_suite:
        report = validate_suite_file(suite_path)
    else:
        if not args.evidence:
            raise SystemExit("--evidence is required unless --check-suite is used")
        report = evaluate_routing_suite(
            suite_path=suite_path,
            evidence_path=Path(args.evidence),
            baseline_path=Path(args.baseline) if args.baseline else None,
            batch_run_id=args.batch_run_id,
            proof_line_limit=max(1, int(args.proof_line_limit)),
        )
    output_report = summarize_report(report) if args.summary else report
    if args.output:
        common.write_json(Path(args.output), output_report)
    if args.output_format == "json":
        print(json.dumps(output_report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
