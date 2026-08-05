#!/usr/bin/env python3
"""Benchmark command presentation helpers for the repository launcher."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

from repo_support import repo_common as repo


DoctorReportFunc = Callable[..., dict[str, object]]
DoctorCommandFunc = Callable[[list[str], Path], int]


def github_validation_trigger_state(root: Path) -> dict[str, object]:
    from repo_support import repo_qol

    return repo_qol.github_validation_trigger_state(root)


def github_validation_advisories(state: dict[str, object]) -> list[str]:
    from repo_support import repo_qol

    return repo_qol.github_validation_advisories(state)


def benchmark_group(
    args: argparse.Namespace,
    root: Path,
    *,
    doctor_report_func: DoctorReportFunc,
    doctor_command_func: DoctorCommandFunc,
) -> int:
    if not args.benchmark_args:
        print("benchmark requires a subcommand: doctor, release-gate, tool-call, routing-eval, capability-matrix, compare-latest, compare-matrix, matrix, lesson-promotions, friction, trend, or summary", file=sys.stderr)
        return 2
    subcommand, *rest = args.benchmark_args
    if subcommand == "doctor":
        return doctor_command_func(rest, root)
    if subcommand == "release-gate":
        return benchmark_release_gate(rest, root, doctor_report_func)
    if subcommand in {"tool-call", "tool-calling"}:
        return benchmark_tool_call(rest, root)
    if subcommand in {"routing-eval", "routing-evidence"}:
        return benchmark_routing_eval(rest, root)
    if subcommand in {"capability-matrix", "capabilities"}:
        parser = argparse.ArgumentParser(prog=f"python -B .agents/manage.py benchmark {subcommand}")
        parser.add_argument("--baseline-root", required=True)
        parser.add_argument("--candidate-root", required=True)
        parser.add_argument("--suite")
        parser.add_argument("--timeout-seconds", type=int, default=120)
        parser.add_argument("--format", choices=("markdown", "json"), default="json")
        parser.add_argument("--compact", action="store_true")
        parsed = parser.parse_args(rest)
        command = [
            "--baseline-root",
            parsed.baseline_root,
            "--candidate-root",
            parsed.candidate_root,
            "--timeout-seconds",
            str(parsed.timeout_seconds),
            "--format",
            parsed.format,
        ]
        if parsed.suite:
            command.extend(["--suite", parsed.suite])
        if parsed.compact:
            command.append("--compact")
        return repo.run_skill_script(root, "agent-benchmarking", "capability_matrix.py", command)
    if subcommand in {"compare-matrix", "matrix"}:
        parser = argparse.ArgumentParser(prog=f"python -B .agents/manage.py benchmark {subcommand}")
        parser.add_argument("runs", nargs="+")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parser.add_argument("--require-comparable", action="store_true")
        parser.add_argument("--optimization-gate", action="store_true")
        parser.add_argument("--allow-quality-drop", type=float, default=0.0)
        parser.add_argument("--no-require-improvement", action="store_true")
        parsed = parser.parse_args(rest)
        command = [*parsed.runs, "--format", parsed.format]
        if parsed.require_comparable:
            command.append("--require-comparable")
        if parsed.optimization_gate:
            command.append("--optimization-gate")
        if parsed.allow_quality_drop:
            command.extend(["--allow-quality-drop", str(parsed.allow_quality_drop)])
        if parsed.no_require_improvement:
            command.append("--no-require-improvement")
        return repo.run_skill_script(root, "agent-benchmarking", "compare_benchmark_runs.py", command)
    if subcommand == "compare-latest":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py benchmark compare-latest")
        parser.add_argument("runs_root")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parser.add_argument("--require-comparable", action="store_true")
        parser.add_argument("--compact", action="store_true", help="with --format json, omit paths and row details")
        parser.add_argument("--optimization-gate", action="store_true")
        parser.add_argument("--allow-quality-drop", type=float, default=0.0)
        parser.add_argument("--no-require-improvement", action="store_true")
        parsed = parser.parse_args(rest)
        command = ["--compare-latest", parsed.runs_root, "--format", parsed.format]
        if parsed.require_comparable:
            command.append("--require-comparable")
        if parsed.compact:
            command.append("--compact")
        if parsed.optimization_gate:
            command.append("--optimization-gate")
        if parsed.allow_quality_drop:
            command.extend(["--allow-quality-drop", str(parsed.allow_quality_drop)])
        if parsed.no_require_improvement:
            command.append("--no-require-improvement")
        return repo.run_skill_script(
            root,
            "agent-benchmarking",
            "compare_benchmark_runs.py",
            command,
        )
    if subcommand in {"lesson-promotions", "promote-lessons"}:
        parser = argparse.ArgumentParser(prog=f"python -B .agents/manage.py benchmark {subcommand}")
        parser.add_argument("roots", nargs="*", default=["automations/agent-benchmarking/runs"])
        parser.add_argument("--min-count", type=int, default=2)
        parser.add_argument("--evidence-limit", type=int, default=3)
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--write", action="store_true")
        parser.add_argument("--output")
        parsed = parser.parse_args(rest)
        command = [
            *parsed.roots,
            "--repo-root",
            str(root),
            "--min-count",
            str(parsed.min_count),
            "--evidence-limit",
            str(parsed.evidence_limit),
            "--format",
            parsed.format,
        ]
        if parsed.summary:
            command.append("--summary")
        if parsed.write:
            command.append("--write")
        if parsed.output:
            command.extend(["--output", parsed.output])
        return repo.run_skill_script(root, "agent-benchmarking", "lesson_promotion.py", command)
    if subcommand in {"friction", "friction-backlog"}:
        return benchmark_friction(raw_args=rest, root=root)
    if subcommand == "trend":
        return benchmark_trend(rest, root)
    if subcommand == "summary":
        return benchmark_pr_summary(rest, root, doctor_report_func)
    print(f"unknown benchmark subcommand: {subcommand}", file=sys.stderr)
    return 2


def workflow_analytics_module():
    workflow_scripts = Path(__file__).resolve().parents[3] / "workflow-manager" / "scripts"
    if workflow_scripts.exists() and str(workflow_scripts) not in sys.path:
        sys.path.insert(0, str(workflow_scripts))
    from workflow_support import analytics as workflow_analytics

    return workflow_analytics


def benchmark_friction_report(root: Path, *, workflow_name: str = "local-ai-benchmark-workflow") -> dict[str, object]:
    analytics = workflow_analytics_module()
    report = analytics.workflow_analytics(root, workflow_names=[workflow_name])
    return {
        "schema_version": 1,
        "tool": "agent-benchmarking.friction-backlog",
        "ok": True,
        "status": "ok",
        "workflow": workflow_name,
        "summary": report.get("summary", {}),
        "friction_backlog": report.get("friction_backlog", []),
        "workflows": report.get("workflows", []),
        "next_command": f"python -B .agents/manage.py workflow analytics --name {workflow_name} --summary --compact --format json",
    }


def summarize_benchmark_friction_report(report: dict[str, object], *, compact: bool = False) -> dict[str, object]:
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "agent-benchmarking.friction-backlog"),
        "ok": bool(report.get("ok", True)),
        "status": report.get("status", "ok"),
        "workflow": report.get("workflow", ""),
        "summary": report.get("summary", {}),
        "friction_backlog": report.get("friction_backlog", []),
        "next_command": report.get("next_command", ""),
    }
    if compact and not output.get("friction_backlog"):
        output.pop("friction_backlog", None)
    if not compact:
        output["workflows"] = report.get("workflows", [])
    return output


def render_benchmark_friction(report: dict[str, object]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Benchmark Friction Backlog",
        "",
        f"- Workflow: `{report.get('workflow', '')}`",
        f"- Retained runs: {summary.get('run_count', 0)}",
        f"- Friction backlog items: {summary.get('friction_backlog_count', 0)}",
    ]
    backlog = report.get("friction_backlog") if isinstance(report.get("friction_backlog"), list) else []
    for group in backlog:
        if not isinstance(group, dict):
            continue
        lines.extend(["", f"## {group.get('classification')}"])
        for item in group.get("items", []) if isinstance(group.get("items"), list) else []:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('run_id')}` {item.get('summary')} -> {item.get('recommended_action')}")
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"


def benchmark_friction(raw_args: list[str], root: Path) -> int:
    parser = argparse.ArgumentParser(prog="python -B .agents/manage.py benchmark friction")
    parser.add_argument("--workflow", default="local-ai-benchmark-workflow")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parsed = parser.parse_args(raw_args)
    report = benchmark_friction_report(root, workflow_name=parsed.workflow)
    output = summarize_benchmark_friction_report(report, compact=parsed.compact) if parsed.summary or parsed.compact else report
    if parsed.format == "json":
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print(render_benchmark_friction(output), end="")
    return 0 if output.get("ok") else 1


def benchmark_routing_eval(raw_args: list[str], root: Path) -> int:
    parser = argparse.ArgumentParser(prog="python -B .agents/manage.py benchmark routing-eval")
    parser.add_argument("--suite", required=True)
    parser.add_argument("--evidence")
    parser.add_argument("--baseline")
    parser.add_argument("--check-suite", action="store_true")
    parser.add_argument("--batch-run-id", default="")
    parser.add_argument("--proof-line-limit", type=int, default=50)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--output")
    parsed = parser.parse_args(raw_args)
    if not parsed.check_suite and not parsed.evidence:
        parser.error("--evidence is required unless --check-suite is used")
    command = ["--suite", parsed.suite, "--format", parsed.format, "--proof-line-limit", str(parsed.proof_line_limit)]
    if parsed.evidence:
        command.extend(["--evidence", parsed.evidence])
    if parsed.baseline:
        command.extend(["--baseline", parsed.baseline])
    if parsed.check_suite:
        command.append("--check-suite")
    if parsed.batch_run_id:
        command.extend(["--batch-run-id", parsed.batch_run_id])
    if parsed.summary:
        command.append("--summary")
    if parsed.output:
        command.extend(["--output", parsed.output])
    return repo.run_skill_script(root, "agent-benchmarking", "routing_evidence_eval.py", command)


def benchmark_tool_call(raw_args: list[str], root: Path) -> int:
    script = root / "automations" / "agent-benchmarking" / "scripts" / "local_ai_tool_call_benchmark.py"
    if not script.exists():
        print(f"tool-call benchmark script not found: {repo.relative(root, script)}", file=sys.stderr)
        return 2
    command = repo.python_command(script, ["--root", str(root), *raw_args])
    return subprocess.run(command, check=False, env=repo.child_env()).returncode


def benchmark_result_files(root: Path, runs_root: str | None = None) -> list[Path]:
    base = (root / runs_root).resolve() if runs_root else root / "automations" / "agent-benchmarking" / "runs"
    if base.is_file():
        return [base]
    return sorted(base.glob("*/benchmark-result.json")) if base.exists() else []


def benchmark_score(data: dict[str, object]) -> float:
    quality = data.get("quality")
    if isinstance(quality, dict):
        try:
            return float(quality.get("score", 0) or 0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def benchmark_trend(raw_args: list[str], root: Path) -> int:
    parser = argparse.ArgumentParser(prog="python -B .agents/manage.py benchmark trend")
    parser.add_argument("runs_root", nargs="?")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    parsed = parser.parse_args(raw_args)
    rows: list[dict[str, object]] = []
    for path in benchmark_result_files(root, parsed.runs_root):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append(
            {
                "run": path.parent.name,
                "path": repo.relative(root, path),
                "task_id": data.get("task_id", ""),
                "model_label": data.get("model_label", ""),
                "status": data.get("status", ""),
                "score": benchmark_score(data),
                "updated_at": data.get("completed_at") or data.get("created_at") or "",
            }
        )
    rows.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
    rows = rows[: max(1, parsed.limit)]
    report = {
        "schema_version": 1,
        "tool": "agent-benchmarking.trend",
        "ok": True,
        "runs": rows,
        "summary": {
            "count": len(rows),
            "best_score": max((float(row.get("score", 0) or 0) for row in rows), default=0.0),
            "latest_score": float(rows[0].get("score", 0) or 0) if rows else 0.0,
        },
    }
    if parsed.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("# Benchmark Trend")
        print(f"- Runs shown: {len(rows)}")
        print(f"- Latest score: {report['summary']['latest_score']}")
        print(f"- Best score: {report['summary']['best_score']}")
        print()
        print("| Run | Task | Model | Score | Status |")
        print("|---|---|---|---:|---|")
        for row in rows:
            print(f"| `{row['run']}` | `{row.get('task_id')}` | `{row.get('model_label')}` | {row.get('score')} | {row.get('status')} |")
    return 0


def benchmark_pr_summary(raw_args: list[str], root: Path, doctor_report_func: DoctorReportFunc) -> int:
    parser = argparse.ArgumentParser(prog="python -B .agents/manage.py benchmark summary")
    parser.add_argument("runs_root", nargs="?", default="automations/agent-benchmarking/runs")
    parser.add_argument("--json", action="store_true")
    parsed = parser.parse_args(raw_args)
    doctor = doctor_report_func(root)
    trend_rows: list[dict[str, object]] = []
    for path in benchmark_result_files(root, parsed.runs_root)[:10]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        trend_rows.append(
            {
                "run": path.parent.name,
                "score": benchmark_score(data),
                "status": data.get("status", ""),
                "unsupported_claims": len(data.get("unsupported_claims", [])) if isinstance(data.get("unsupported_claims"), list) else 0,
            }
        )
    report = {
        "schema_version": 1,
        "tool": "agent-benchmarking.pr-summary",
        "ok": bool(doctor.get("ok")),
        "benchmark_doctor": {"ok": doctor.get("ok"), "issues": doctor.get("issues", []), "warnings": doctor.get("warnings", [])},
        "sections": {
            "model_quality": trend_rows,
            "agent_tool_behavior": "Use tool trajectory suites for required/forbidden call evidence.",
            "routing_determinism": "Use routing-evidence-real-use for required, optional, and disallowed owner evidence.",
            "workflow_behavior": "Use workflow-real-use suite for resume/evidence/final-report checks.",
            "deterministic_fallback_behavior": "Use local-ai-failure-modes release-gate suite.",
        },
        "next_command": "python -B .agents/manage.py benchmark compare-latest automations/agent-benchmarking/runs",
    }
    if parsed.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("# Benchmark Summary")
        print(f"- Doctor: {'ok' if doctor.get('ok') else 'failed'}")
        print("- Sections: model quality, agent/tool behavior, routing determinism, workflow behavior, deterministic fallback behavior")
        print(f"- Next command: `{report['next_command']}`")
    return 0 if report["ok"] else 1


def summarize_release_gate_report(report: dict[str, object], *, compact: bool = False) -> dict[str, object]:
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    compact_checks: list[dict[str, object]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        result = check.get("result") if isinstance(check.get("result"), dict) else {}
        compact_checks.append(
            {
                "name": check.get("name", ""),
                "ok": bool(check.get("ok")),
                "status": result.get("status", ""),
                "check_count": len(result.get("checks", [])) if isinstance(result.get("checks"), list) else 0,
                "issue_count": len(result.get("issues", [])) if isinstance(result.get("issues"), list) else 0,
                "warning_count": len(result.get("warnings", [])) if isinstance(result.get("warnings"), list) else 0,
            }
        )
    github = report.get("github_validation") if isinstance(report.get("github_validation"), dict) else {}
    budget_gate = report.get("budget_gate") if isinstance(report.get("budget_gate"), dict) else None
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "agent-benchmarking.release-gate"),
        "ok": bool(report.get("ok")),
        "status": report.get("status", ""),
        "summary": {
            "suite_count": len(compact_checks),
            "passed": sum(1 for check in compact_checks if check.get("ok")),
            "failed": sum(1 for check in compact_checks if not check.get("ok")),
            "issue_count": len(report.get("issues", [])) if isinstance(report.get("issues"), list) else 0,
            "advisory_count": len(report.get("advisories", [])) if isinstance(report.get("advisories"), list) else 0,
            "skipped_count": len(report.get("skipped", [])) if isinstance(report.get("skipped"), list) else 0,
        },
        "checks": [check for check in compact_checks if not check.get("ok")] if compact else compact_checks,
        "github_validation": {
            "status": github.get("status", ""),
            "automatic_triggers_enabled": bool(github.get("automatic_triggers_enabled", False)),
            "automatic_triggers": github.get("automatic_triggers", []),
            "manual_dispatch_enabled": bool(github.get("manual_dispatch_enabled", False)),
        },
        "advisories": report.get("advisories", []),
        "issues": report.get("issues", []),
        "skipped": report.get("skipped", []),
    }
    if budget_gate:
        output["summary"]["budget_gate_status"] = budget_gate.get("status", "")
        output["budget_gate"] = budget_gate
    if compact:
        if not output.get("checks"):
            output.pop("checks", None)
        github_summary = output.get("github_validation")
        if isinstance(github_summary, dict) and not github_summary.get("automatic_triggers"):
            github_summary.pop("automatic_triggers", None)
        if not output.get("issues"):
            output.pop("issues", None)
        output.pop("skipped", None)
    return output


def budget_gate_report(
    root: Path,
    *,
    baseline_ref: str,
    intent: str,
    max_total_growth: int | None,
    max_tool_growth: int | None,
) -> dict[str, object]:
    import measure_skill_budget

    report = measure_skill_budget.build_report(
        argparse.Namespace(
            root=str(root),
            all=True,
            skill=None,
            summary=True,
            compact=True,
            write_trend=False,
            baseline_ref=baseline_ref,
        )
    )
    baseline = report.get("baseline") if isinstance(report.get("baseline"), dict) else {}
    delta = report.get("delta") if isinstance(report.get("delta"), dict) else {}
    delta_summary = delta.get("summary") if isinstance(delta.get("summary"), dict) else {}
    issues = list(str(issue) for issue in baseline.get("issues", []) if str(issue))
    limits = {
        "total_text_words": 0 if intent == "optimization" and max_total_growth is None else max_total_growth,
        "tool_load_words": 0 if intent == "optimization" and max_tool_growth is None else max_tool_growth,
    }
    for key, limit in limits.items():
        if limit is None:
            continue
        value = int(delta_summary.get(key, 0) or 0)
        if value > int(limit):
            issues.append(f"{intent} budget grew: {key} {value:+} > {int(limit):+}")
    total_delta = int(delta_summary.get("total_text_words", 0) or 0)
    tool_delta = int(delta_summary.get("tool_load_words", 0) or 0)
    if issues:
        status = "failed"
    elif intent == "optimization":
        status = "optimization-reduced" if total_delta < 0 or tool_delta < 0 else "optimization-no-growth"
    else:
        status = "feature-growth-recorded" if total_delta > 0 or tool_delta > 0 else "feature-no-growth"
    return {
        "schema_version": 1,
        "tool": "agent-benchmarking.budget-gate",
        "ok": not issues,
        "status": status,
        "intent": intent,
        "baseline_ref": baseline_ref,
        "baseline_ok": bool(baseline.get("ok", True)),
        "thresholds": limits,
        "delta": {
            "summary": delta_summary,
            "skills": delta.get("skills", []) if isinstance(delta.get("skills"), list) else [],
        },
        "issues": issues,
    }


def benchmark_release_gate(raw_args: list[str], root: Path, doctor_report_func: DoctorReportFunc) -> int:
    parser = argparse.ArgumentParser(prog="python -B .agents/manage.py benchmark release-gate")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--summary", action="store_true", help="emit compact suite counts")
    parser.add_argument("--compact", action="store_true", help="with --summary, omit successful suite rows")
    parser.add_argument("--budget-intent", choices=("off", "feature", "optimization"), default="off")
    parser.add_argument("--budget-baseline-ref", help="git ref for budget delta checks, usually HEAD")
    parser.add_argument("--max-total-text-growth", type=int, help="maximum allowed total_text_words growth")
    parser.add_argument("--max-tool-load-growth", type=int, help="maximum allowed tool_load_words growth")
    parsed = parser.parse_args(raw_args)
    if parsed.budget_intent != "off" and not parsed.budget_baseline_ref:
        parser.error("--budget-baseline-ref is required when --budget-intent is not off")
    required_suites = [
        "automations/agent-benchmarking/suites/local-ai-failure-modes.json",
        "automations/agent-benchmarking/suites/document-skills-real-use.json",
        "automations/agent-benchmarking/suites/repository-search-utility-v1.json",
        "automations/agent-benchmarking/suites/context-savings-real-use.json",
        "automations/agent-benchmarking/suites/tool-trajectory-real-use.json",
        "automations/agent-benchmarking/suites/workflow-real-use.json",
    ]
    run = latest_benchmark_result_run(root)
    suite_shape_only = not run
    checks: list[dict[str, object]] = []
    issues: list[str] = []
    for suite in required_suites:
        report = doctor_report_func(root, suite=suite, run=run if run else None)
        checks.append({"name": suite, "ok": bool(report.get("ok")), "result": report})
        issues.extend(str(issue) for issue in report.get("issues", []) if str(issue))
    budget_gate = None
    if parsed.budget_intent != "off":
        budget_gate = budget_gate_report(
            root,
            baseline_ref=parsed.budget_baseline_ref,
            intent=parsed.budget_intent,
            max_total_growth=parsed.max_total_text_growth,
            max_tool_growth=parsed.max_tool_load_growth,
        )
        issues.extend(str(issue) for issue in budget_gate.get("issues", []) if str(issue))
    github_validation = github_validation_trigger_state(root)
    advisories = github_validation_advisories(github_validation)
    report = {
        "schema_version": 1,
        "tool": "agent-benchmarking.release-gate",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "checks": checks,
        "github_validation": github_validation,
        "advisories": advisories,
        "issues": issues,
        "skipped": [
            "model execution is intentionally out of scope for the cheap release-gate suite",
            *(
                ["retained benchmark result unavailable; release gate performed suite-shape checks only"]
                if suite_shape_only
                else []
            ),
        ],
    }
    if budget_gate:
        report["budget_gate"] = budget_gate
    output_report = summarize_release_gate_report(report, compact=parsed.compact) if parsed.summary else report
    if parsed.json:
        print(json.dumps(output_report, indent=2, sort_keys=True))
    else:
        print("# Benchmark Release Gate")
        print(f"- Status: {output_report['status']}")
        if budget_gate:
            print(f"- Budget gate: {budget_gate['status']} ({budget_gate['intent']})")
        for check in output_report.get("checks", []):
            print(f"- {check['name']}: {'ok' if check['ok'] else 'failed'}")
        if issues:
            print()
            print("## Issues")
            for issue in issues:
                print(f"- {issue}")
        if advisories:
            print()
            print("## Advisories")
            for advisory in advisories:
                print(f"- {advisory}")
    return 0 if report["ok"] else 1


def latest_benchmark_result_run(root: Path) -> str:
    runs_root = root / "automations" / "agent-benchmarking" / "runs"
    results = sorted(
        runs_root.glob("*/benchmark-result.json"),
        key=lambda path: (path.stat().st_mtime, path.parent.name),
        reverse=True,
    ) if runs_root.exists() else []
    if not results:
        return ""
    return repo.relative(root, results[0].parent)
