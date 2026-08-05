#!/usr/bin/env python3
"""Promote recurring benchmark lessons into deterministic test or eval candidates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import benchmark_common as common


DEFAULT_TARGET = {
    "promotion_kind": "eval-case",
    "owner": "agent-benchmarking",
    "target_path": "automations/agent-benchmarking/suites/discipline-pressure-scenarios.json",
    "recommended_change": "Add or extend one benchmark case with explicit expected_checks and failure_taxonomy.",
    "benefit": "Turns a repeated agent mistake into a release-visible signal without adding broad memory.",
    "cost": "Only catches lessons that are recorded in normalized benchmark evidence.",
}

CATEGORY_TARGETS: dict[str, dict[str, str]] = {
    "unsupported-claim": DEFAULT_TARGET,
    "invented-path": DEFAULT_TARGET,
    "invented-command": DEFAULT_TARGET,
    "false-validation-claim": DEFAULT_TARGET,
    "missing-evidence": DEFAULT_TARGET,
    "skipped-validation": DEFAULT_TARGET,
    "overeager-action": DEFAULT_TARGET,
    "unauthorized-install": DEFAULT_TARGET,
    "scope-expansion": DEFAULT_TARGET,
    "context-overload": {
        "promotion_kind": "eval-case",
        "owner": "agent-benchmarking",
        "target_path": "automations/agent-benchmarking/suites/context-savings-real-use.json",
        "recommended_change": "Add a context-budget case or expected check that fails when unnecessary context is loaded.",
        "benefit": "Protects low-token behavior with measurable context evidence.",
        "cost": "Requires runs to record loaded_context and token estimates consistently.",
    },
    "module-contract-miss": {
        "promotion_kind": "validator-test",
        "owner": "owning-module",
        "target_path": "owning skill/workflow validator or scripts/run_self_tests.py",
        "recommended_change": "Add a focused validator assertion or self-test for the missing module contract.",
        "benefit": "Moves repeated contract drift into cheap deterministic validation.",
        "cost": "Needs a human or implementation agent to choose the exact owning module and fixture.",
    },
    "output-schema-error": {
        "promotion_kind": "validator-test",
        "owner": "owning-module",
        "target_path": "owning skill/workflow validator or scripts/run_self_tests.py",
        "recommended_change": "Add a schema fixture that fails on the observed malformed output.",
        "benefit": "Prevents the same malformed evidence packet from being accepted again.",
        "cost": "May need a small fixture to avoid overfitting to one exact output string.",
    },
    "timeout": {
        "promotion_kind": "fail-fast-check",
        "owner": "agent-benchmarking",
        "target_path": ".agents/skills/agent-benchmarking/scripts/benchmark_determinism.py",
        "recommended_change": "Add or extend a timeout/cleanup test with per-case and runner budget evidence.",
        "benefit": "Prevents repeated hangs from consuming run budget and token budget.",
        "cost": "Timeout tests must stay small to avoid making normal validation flaky.",
    },
    "config-error": {
        "promotion_kind": "fail-fast-check",
        "owner": "agent-benchmarking",
        "target_path": ".agents/skills/agent-benchmarking/scripts/benchmark_determinism.py",
        "recommended_change": "Classify the recurring configuration failure as permanent and abort repeated attempts.",
        "benefit": "Stops deterministic setup mistakes after the configured repeat threshold.",
        "cost": "Requires careful classification so transient failures are not treated as permanent.",
    },
    "permanent-error": {
        "promotion_kind": "fail-fast-check",
        "owner": "agent-benchmarking",
        "target_path": ".agents/skills/agent-benchmarking/scripts/benchmark_determinism.py",
        "recommended_change": "Add the fingerprint to permanent-error classification or consecutive-failure handling.",
        "benefit": "Reduces wasted retries on known impossible cases.",
        "cost": "Over-broad fingerprints can hide unrelated failures.",
    },
    "setup-blocker": {
        "promotion_kind": "eval-case",
        "owner": "agent-benchmarking",
        "target_path": "automations/agent-benchmarking/suites/local-ai-failure-modes.json",
        "recommended_change": "Add a cheap setup-blocker case with expected fallback or stop behavior.",
        "benefit": "Keeps environment/setup blockers visible without running expensive model workloads.",
        "cost": "Only useful when the blocker can be represented as a stable local fixture.",
    },
    "tool-failure": {
        "promotion_kind": "fail-fast-check",
        "owner": "agent-benchmarking",
        "target_path": ".agents/skills/agent-benchmarking/scripts/benchmark_determinism.py",
        "recommended_change": "Add failure classification or cleanup coverage for the repeated tool failure.",
        "benefit": "Improves deterministic runner behavior for repeated CLI failures.",
        "cost": "Needs enough stderr/stdout normalization to avoid one-off fingerprints.",
    },
}


def relative_label(root: Path | None, path: Path) -> str:
    resolved = path.resolve()
    if root is not None:
        try:
            return resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return resolved.as_posix()


def benchmark_report_paths(roots: list[Path]) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        candidate = root.expanduser().resolve()
        if candidate.is_file():
            found = [common.run_report_path(candidate)]
        elif (candidate / "benchmark-result.json").exists():
            found = [candidate / "benchmark-result.json"]
        else:
            found = sorted(candidate.rglob("benchmark-result.json")) if candidate.exists() else []
        for path in found:
            key = str(path.resolve())
            if key not in seen:
                seen.add(key)
                paths.append(path)
    return sorted(paths, key=lambda item: item.as_posix())


def load_valid_reports(paths: list[Path], *, root: Path | None = None) -> tuple[list[tuple[str, Path, dict[str, Any]]], list[str]]:
    loaded: list[tuple[str, Path, dict[str, Any]]] = []
    issues: list[str] = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"could not read benchmark report {relative_label(root, path)}: {exc}")
            continue
        if not isinstance(data, dict):
            issues.append(f"benchmark report is not an object: {relative_label(root, path)}")
            continue
        if data.get("schema_version") != common.SCHEMA_VERSION:
            issues.append(
                f"benchmark report has incompatible schema_version {data.get('schema_version')!r}: {relative_label(root, path)}"
            )
            continue
        shape_issues = common.validate_benchmark_result_shape(data)
        if shape_issues:
            issues.append(f"benchmark report skipped {relative_label(root, path)}: {'; '.join(shape_issues)}")
            continue
        ledger = str(data.get("run_packet_path", "")).strip()
        if ledger and not (path.parent / ledger).exists():
            issues.append(f"benchmark report skipped {relative_label(root, path)}: missing run packet {ledger}")
            continue
        loaded.append((relative_label(root, path), path, data))
    return loaded, issues


def add_signal(
    signals: list[dict[str, Any]],
    *,
    report_label: str,
    report: dict[str, Any],
    signal_type: str,
    category: str,
    detail: str,
    evidence_path: str = "",
    fingerprint: str = "",
) -> None:
    clean_category = category.strip() or "other"
    if clean_category in {"none", "other"}:
        return
    if clean_category not in common.FAILURE_TAXONOMY_CATEGORIES:
        return
    clean_detail = " ".join(detail.split())[:500]
    clean_fingerprint = fingerprint.strip() or common.failure_fingerprint(clean_category, clean_detail)
    signals.append(
        {
            "signal_type": signal_type,
            "category": clean_category,
            "fingerprint": clean_fingerprint,
            "detail": clean_detail,
            "evidence_path": evidence_path,
            "run_id": str(report.get("run_id", "")),
            "task_id": str(report.get("task_id", "")),
            "workflow_name": str(report.get("workflow_name", "")),
            "workflow_version": str(report.get("workflow_version", "")),
            "model_label": str(report.get("model_label", "")),
            "report_path": report_label,
        }
    )


def report_signals(report_label: str, report: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for row in common.normalize_failure_taxonomy(report.get("failure_taxonomy", [])):
        add_signal(
            signals,
            report_label=report_label,
            report=report,
            signal_type="failure_taxonomy",
            category=row["category"],
            detail=row["detail"],
            evidence_path=row["evidence"],
        )
    routing = report.get("routing_determinism")
    if isinstance(routing, dict):
        category = str(routing.get("failure_category", "")).strip()
        mismatch = str(routing.get("mismatch_kind", "")).strip()
        fingerprint = str(routing.get("failure_fingerprint", "")).strip()
        if category and category != "none":
            add_signal(
                signals,
                report_label=report_label,
                report=report,
                signal_type="routing_determinism",
                category=category,
                detail=f"routing failure category={category}; mismatch={mismatch or 'unknown'}",
                evidence_path="benchmark-result.json",
                fingerprint=fingerprint,
            )
    grounding = report.get("grounding") if isinstance(report.get("grounding"), dict) else {}
    unsupported = []
    if isinstance(grounding.get("unsupported_claims"), list):
        unsupported.extend(grounding["unsupported_claims"])
    if isinstance(report.get("unsupported_claims"), list):
        unsupported.extend(report["unsupported_claims"])
    for claim in unsupported:
        add_signal(
            signals,
            report_label=report_label,
            report=report,
            signal_type="grounding",
            category="unsupported-claim",
            detail=str(claim),
            evidence_path="benchmark-result.json",
        )
    for skipped in report.get("skipped", []) if isinstance(report.get("skipped"), list) else []:
        add_signal(
            signals,
            report_label=report_label,
            report=report,
            signal_type="skipped",
            category="skipped-validation",
            detail=str(skipped),
            evidence_path="benchmark-result.json",
        )
    return signals


def target_for_category(category: str) -> dict[str, str]:
    target = CATEGORY_TARGETS.get(category, DEFAULT_TARGET)
    return dict(target)


def candidate_from_group(category: str, fingerprint: str, signals: list[dict[str, Any]], *, evidence_limit: int) -> dict[str, Any]:
    target = target_for_category(category)
    evidence = sorted(
        signals,
        key=lambda item: (
            str(item.get("report_path", "")),
            str(item.get("run_id", "")),
            str(item.get("task_id", "")),
        ),
    )[:evidence_limit]
    return {
        "lesson_id": f"lesson-{category}-{fingerprint}",
        "category": category,
        "fingerprint": fingerprint,
        "occurrences": len(signals),
        "distinct_runs": len({str(item.get("run_id", "")) or str(item.get("report_path", "")) for item in signals}),
        "signal_types": sorted({str(item.get("signal_type", "")) for item in signals if item.get("signal_type")}),
        "promotion_kind": target["promotion_kind"],
        "owner": target["owner"],
        "target_path": target["target_path"],
        "recommended_change": target["recommended_change"],
        "benefit": target["benefit"],
        "cost": target["cost"],
        "evidence": [
            {
                "run_id": item.get("run_id", ""),
                "task_id": item.get("task_id", ""),
                "report_path": item.get("report_path", ""),
                "detail": item.get("detail", ""),
                "evidence_path": item.get("evidence_path", ""),
            }
            for item in evidence
        ],
    }


def build_report_from_loaded(
    loaded_results: list[tuple[str, Path, dict[str, Any]]],
    *,
    min_count: int = 2,
    evidence_limit: int = 3,
) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    signal_count = 0
    seen: set[tuple[str, str, str]] = set()
    for label, _path, report in loaded_results:
        for signal in report_signals(label, report):
            dedupe_key = (str(signal["report_path"]), str(signal["category"]), str(signal["fingerprint"]))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            signal_count += 1
            groups.setdefault((str(signal["category"]), str(signal["fingerprint"])), []).append(signal)
    candidates = [
        candidate_from_group(category, fingerprint, signals, evidence_limit=evidence_limit)
        for (category, fingerprint), signals in groups.items()
        if len({str(item.get("report_path", "")) for item in signals}) >= min_count
    ]
    candidates.sort(key=lambda item: (-int(item["occurrences"]), str(item["category"]), str(item["fingerprint"])))
    return {
        "schema_version": 1,
        "tool": f"{common.TOOL_NAME}.lesson-promotion",
        "ok": True,
        "status": "candidates" if candidates else "no-candidates",
        "summary": {
            "report_count": len(loaded_results),
            "signal_count": signal_count,
            "candidate_count": len(candidates),
            "min_count": min_count,
            "automatic_boundary": "detect and route candidates only; source test/eval edits remain explicit",
        },
        "candidates": candidates,
    }


def build_lesson_promotion_report(
    roots: list[Path],
    *,
    root: Path | None = None,
    min_count: int = 2,
    evidence_limit: int = 3,
    dry_run: bool = False,
) -> dict[str, Any]:
    paths = benchmark_report_paths(roots)
    loaded, issues = load_valid_reports(paths, root=root)
    report = build_report_from_loaded(loaded, min_count=max(2, min_count), evidence_limit=max(1, evidence_limit))
    report["summary"]["scanned_path_count"] = len(paths)
    report["issues"] = issues
    if issues and not report["candidates"]:
        report["status"] = "issues"
    if dry_run:
        report["promotion_plan"] = build_promotion_plan(report, root=root)
    return report


def target_exists(root: Path | None, target_path: str) -> bool:
    if root is None:
        return False
    if target_path.startswith("owning "):
        return False
    return (root / target_path).exists()


def validation_commands_for_candidate(candidate: dict[str, Any]) -> list[str]:
    owner = str(candidate.get("owner", ""))
    target = str(candidate.get("target_path", ""))
    if owner == "agent-benchmarking":
        commands = [
            "python -B .agents/skills/agent-benchmarking/scripts/run_self_tests.py",
            "python -B .agents/manage.py eval-skill --skill .agents/skills/agent-benchmarking --suite .agents/skills/agent-benchmarking/suites/agent-benchmarking-evals.json",
        ]
        if target.startswith("automations/"):
            commands.append("python -B .agents/manage.py workflow scorecard --name agent-benchmarking --format json")
        commands.append("python -B .agents/manage.py check-changed --deep --format json")
        return commands
    if owner == "owning-module":
        return [
            "python -B .agents/manage.py route \"lesson owner for repeated benchmark failure\" --format json",
            "python -B .agents/manage.py check-changed --deep --format json",
        ]
    return [
        f"python -B .agents/manage.py review --skill .agents/skills/{owner}" if owner else "python -B .agents/manage.py route \"lesson owner\" --format json",
        "python -B .agents/manage.py check-changed --deep --format json",
    ]


def build_promotion_plan(report: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    candidates = report.get("candidates") if isinstance(report.get("candidates"), list) else []
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        target = str(candidate.get("target_path", ""))
        items.append(
            {
                "lesson_id": candidate.get("lesson_id", ""),
                "owner": candidate.get("owner", ""),
                "promotion_kind": candidate.get("promotion_kind", ""),
                "target_path": target,
                "target_exists": target_exists(root, target),
                "recommended_change": candidate.get("recommended_change", ""),
                "evidence_count": len(candidate.get("evidence", [])) if isinstance(candidate.get("evidence"), list) else 0,
                "validation_commands": validation_commands_for_candidate(candidate),
            }
        )
    return {
        "dry_run": True,
        "status": "planned" if items else "no-candidates",
        "candidate_count": len(items),
        "write_policy": "No source files are modified by lesson_promotion.py; apply candidates explicitly in the owning skill or workflow.",
        "items": items,
    }


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    candidates = report.get("candidates") if isinstance(report.get("candidates"), list) else []
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", f"{common.TOOL_NAME}.lesson-promotion"),
        "ok": bool(report.get("ok", True)),
        "status": report.get("status", ""),
        "summary": {
            "report_count": summary.get("report_count", 0),
            "signal_count": summary.get("signal_count", 0),
            "candidate_count": len(candidates),
            "min_count": summary.get("min_count", 2),
            "issue_count": len(report.get("issues", [])) if isinstance(report.get("issues"), list) else 0,
            "promotion_plan_count": report.get("promotion_plan", {}).get("candidate_count", 0)
            if isinstance(report.get("promotion_plan"), dict)
            else 0,
        },
        "candidates": [
            {
                "lesson_id": item.get("lesson_id", ""),
                "category": item.get("category", ""),
                "promotion_kind": item.get("promotion_kind", ""),
                "owner": item.get("owner", ""),
                "target_path": item.get("target_path", ""),
                "occurrences": item.get("occurrences", 0),
            }
            for item in candidates
            if isinstance(item, dict)
        ],
    }
    if isinstance(report.get("promotion_plan"), dict):
        output["promotion_plan"] = {
            "dry_run": True,
            "status": report["promotion_plan"].get("status", ""),
            "candidate_count": report["promotion_plan"].get("candidate_count", 0),
            "items": [
                {
                    "lesson_id": item.get("lesson_id", ""),
                    "target_path": item.get("target_path", ""),
                    "validation_commands": item.get("validation_commands", []),
                }
                for item in report["promotion_plan"].get("items", [])[:10]
                if isinstance(item, dict)
            ],
        }
    return output


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Recurring Lesson Promotions",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Reports scanned: {summary.get('report_count', 0)}",
        f"- Signals found: {summary.get('signal_count', 0)}",
        f"- Promotion candidates: {summary.get('candidate_count', 0)}",
        f"- Min repeated evidence: {summary.get('min_count', 2)}",
        "- Boundary: detect and route candidates only; source test/eval edits remain explicit.",
    ]
    plan = report.get("promotion_plan") if isinstance(report.get("promotion_plan"), dict) else {}
    if plan:
        lines.append(f"- Dry-run promotion plan: {plan.get('status')} ({plan.get('candidate_count', 0)} candidates)")
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in issues)
    candidates = report.get("candidates") if isinstance(report.get("candidates"), list) else []
    lines.extend(["", "## Candidates", ""])
    if not candidates:
        lines.append("- No promotion candidates met the repeat threshold.")
        return "\n".join(lines)
    for item in candidates:
        lines.append(
            f"- `{item.get('lesson_id', '')}` `{item.get('promotion_kind', '')}` "
            f"`{item.get('category', '')}` occurrences={item.get('occurrences', 0)}"
        )
        lines.append(f"  Target: `{item.get('target_path', '')}` ({item.get('owner', '')})")
        lines.append(f"  Change: {item.get('recommended_change', '')}")
        lines.append(f"  Benefit: {item.get('benefit', '')}")
        lines.append(f"  Cost: {item.get('cost', '')}")
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        for row in evidence:
            if isinstance(row, dict):
                lines.append(
                    f"  Evidence: `{row.get('run_id', '')}` `{row.get('task_id', '')}` "
                    f"`{row.get('report_path', '')}`"
                )
    if plan and plan.get("items"):
        lines.extend(["", "## Dry-Run Promotion Plan", ""])
        lines.append(f"- Write policy: {plan.get('write_policy')}")
        for item in plan.get("items", []):
            if not isinstance(item, dict):
                continue
            lines.append(f"- `{item.get('lesson_id', '')}` -> `{item.get('target_path', '')}`")
            lines.append(f"  Target exists: {str(item.get('target_exists', False)).lower()}")
            for command in item.get("validation_commands", []):
                lines.append(f"  Validate: `{command}`")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "roots",
        nargs="*",
        default=["automations/agent-benchmarking/runs"],
        help="run folders, benchmark-result.json files, or roots containing benchmark-result.json files",
    )
    parser.add_argument("--repo-root", default=".", help="repo root used for relative paths")
    parser.add_argument("--min-count", type=int, default=2, help="minimum distinct reports required for a candidate")
    parser.add_argument("--evidence-limit", type=int, default=3, help="maximum evidence rows per candidate")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    parser.add_argument("--summary", action="store_true", help="with --format json, emit compact candidate rows")
    parser.add_argument("--dry-run", action="store_true", help="include an explicit promotion plan without modifying sources")
    parser.add_argument("--write", action="store_true", help="write JSON report to --output")
    parser.add_argument("--output", help="explicit output path for --write")
    return parser


def main(argv: list[str] | None = None) -> int:
    common.require_supported_python()
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve()
    roots = [(repo_root / item).resolve() if not Path(item).is_absolute() else Path(item).resolve() for item in args.roots]
    report = build_lesson_promotion_report(
        roots,
        root=repo_root,
        min_count=args.min_count,
        evidence_limit=args.evidence_limit,
        dry_run=bool(args.dry_run),
    )
    output_report = summarize_report(report) if args.summary else report
    if args.write:
        if not args.output:
            raise SystemExit("--write requires --output so generated evidence has an explicit owner path.")
        output = Path(args.output)
        if not output.is_absolute():
            output = repo_root / output
        common.write_json(output, output_report)
    if args.output_format == "json":
        print(json.dumps(output_report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
