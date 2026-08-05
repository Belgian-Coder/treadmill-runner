#!/usr/bin/env python3
"""Doctor and grouped command helpers for the repository launcher."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from repo_support import repo_common as repo
from repo_support import repo_health
from repo_support.repo_doctor_benchmarks import benchmark_doctor, benchmark_doctor_report, benchmark_group
from repo_support.repo_doctor_checks import (
    git_dirty_state,
    github_actions_status,
    github_hygiene,
    setup_local_ai_readiness,
    tracked_bundle_integrity,
    tracked_payload_hygiene,
)
from repo_support.repo_doctor_clone import (
    fresh_clone_smoke,
    fresh_clone_smoke_report,
    install_harness_smoke,
    install_harness_smoke_report,
)
from repo_support.repo_doctor_groups import skill_group, skill_naming_report, workflow_group

def release_readiness(root: Path) -> dict[str, object]:
    health = repo_health.build_repo_health_report(root)
    generated_ok = all(bool(item.get("ok")) for item in health.get("generated_checks", []))
    payloads = tracked_payload_hygiene(root)
    github = github_hygiene(root)
    actions = github_actions_status(root)
    bundle = tracked_bundle_integrity(root)
    dirty = git_dirty_state(root)
    benchmark = benchmark_doctor_report(root)
    local_ai = setup_local_ai_readiness(root)
    warnings: list[str] = []
    if dirty.get("tracked_dirty"):
        warnings.append("tracked files are modified; release readiness is dirty but may still be validation-clean")
    if isinstance(local_ai, dict) and not local_ai.get("ok"):
        warnings.append("local AI is not fully ready; deterministic fallbacks must remain usable")
    if isinstance(actions, dict) and actions.get("external_blocker"):
        warnings.extend(str(item) for item in actions.get("warnings", []) if str(item))
    checks = [
        {"name": "generated_sync", "ok": generated_ok},
        {"name": "repo_health", "ok": bool(health.get("ok"))},
        {"name": "git_status", "ok": True, "result": dirty},
        {"name": "tracked_payloads", "ok": bool(payloads.get("ok")), "result": payloads},
        {"name": "tracked_bundle_integrity", "ok": bool(bundle.get("ok")), "result": bundle},
        {"name": "benchmark_doctor", "ok": bool(benchmark.get("ok")), "result": benchmark},
        {"name": "local_ai_policy_readiness", "ok": True, "advisory_ok": bool(local_ai.get("ok")), "result": local_ai},
        {"name": "github_hygiene", "ok": bool(github.get("ok")), "result": github},
        {
            "name": "github_actions",
            "ok": bool(actions.get("ok")),
            "external_blocker": bool(actions.get("external_blocker")),
            "result": actions,
        },
        {
            "name": "deep_validation",
            "ok": True,
            "verified": False,
            "result": {"status": "not-verified", "command": "python -B .agents/manage.py check --deep"},
        },
    ]
    preflight_ok = all(bool(item["ok"]) for item in checks)
    github_verified = isinstance(github, dict) and github.get("status") == "clean"
    actions_verified = isinstance(actions, dict) and actions.get("status") == "passed"
    deep_verified = False
    release_ready = bool(preflight_ok and github_verified and actions_verified and bundle.get("ok") and deep_verified)
    status = "ready" if release_ready else "preflight-passed" if preflight_ok else "issues-found"
    return {
        "schema_version": 1,
        "tool": "release-readiness",
        "ok": preflight_ok,
        "release_ready": release_ready,
        "status": status,
        "checks": checks,
        "warnings": warnings,
        "skipped": [
            *(github.get("skipped", []) if isinstance(github, dict) else []),
            *(actions.get("skipped", []) if isinstance(actions, dict) else []),
            "deep validation was not run inside release-readiness; use check --deep before release",
        ],
        "next_action": "run check --deep and confirm GitHub hygiene before release"
        if preflight_ok and not release_ready
        else "resolve release-readiness issues",
    }


def deep_validation_report(root: Path) -> dict[str, object]:
    try:
        completed = subprocess.run(
            [sys.executable, "-B", ".agents/manage.py", "check", "--deep"],
            cwd=root,
            check=False,
            env=repo.child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        return {
            "schema_version": 1,
            "tool": "release-evidence.deep-validation",
            "ok": False,
            "status": "failed",
            "command": "python -B .agents/manage.py check --deep",
            "output_tail": output[-4000:] if isinstance(output, str) else "",
            "issues": ["check --deep timed out inside release evidence"],
        }
    return {
        "schema_version": 1,
        "tool": "release-evidence.deep-validation",
        "ok": completed.returncode == 0,
        "status": "passed" if completed.returncode == 0 else "failed",
        "command": "python -B .agents/manage.py check --deep",
        "output_tail": completed.stdout[-4000:],
    }


def release_evidence_report(
    root: Path,
    *,
    skip_fresh_clone: bool = False,
    source: str = "local",
    include_deep_validation: bool = False,
    github_only: bool = False,
    local_only: bool = False,
) -> dict[str, object]:
    checks: list[dict[str, object]] = []
    if github_only:
        github = github_hygiene(root)
        actions = github_actions_status(root)
        checks = [
            {"name": "github_hygiene", "ok": bool(github.get("ok")), "result": github},
            {"name": "github_actions", "ok": bool(actions.get("ok")), "result": actions},
        ]
    else:
        readiness = (
            {
                "ok": True,
                "status": "skipped",
                "skipped": ["release readiness skipped by --local-only to avoid GitHub hygiene calls"],
            }
            if local_only
            else release_readiness(root)
        )
        benchmark = benchmark_doctor_report(root)
        health = repo_health.build_repo_health_report(root)
        fresh_clone = (
            {
                "ok": True,
                "status": "skipped",
                "skipped": ["fresh clone smoke skipped by request"],
            }
            if skip_fresh_clone
            else fresh_clone_smoke_report(root, source=source)
        )
        checks = [
            {"name": "release_readiness", "ok": bool(readiness.get("ok")), "result": readiness},
            {"name": "repo_health", "ok": bool(health.get("ok")), "result": health},
            {"name": "benchmark_doctor", "ok": bool(benchmark.get("ok")), "result": benchmark},
            {"name": "fresh_clone_smoke", "ok": bool(fresh_clone.get("ok")), "result": fresh_clone},
        ]
        if include_deep_validation:
            deep = deep_validation_report(root)
            checks.append({"name": "deep_validation", "ok": bool(deep.get("ok")), "result": deep})
    issues: list[str] = []
    warnings: list[str] = []
    skipped: list[str] = []
    for check in checks:
        result = check.get("result")
        if isinstance(result, dict):
            issues.extend(str(issue) for issue in result.get("issues", []) if str(issue))
            warnings.extend(str(warning) for warning in result.get("warnings", []) if str(warning))
            skipped.extend(str(item) for item in result.get("skipped", []) if str(item))
    ok = all(bool(check["ok"]) for check in checks)
    return {
        "schema_version": 1,
        "tool": "release-evidence",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "scope": "github-only" if github_only else ("local-only" if local_only else "full"),
        "checks": checks,
        "issues": issues,
        "warnings": sorted(set(warnings)),
        "skipped": sorted(set(skipped)),
        "next_action": "commit/push only after check --deep and release evidence are current" if ok else "resolve release evidence issues",
    }


def summarize_release_evidence_report(report: dict[str, object], *, compact: bool = False) -> dict[str, object]:
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    rows: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        result = check.get("result") if isinstance(check.get("result"), dict) else {}
        row = {
            "name": check.get("name", ""),
            "ok": bool(check.get("ok")),
            "status": result.get("status", ""),
        }
        rows.append(row)
        if not check.get("ok"):
            failed.append({"name": row["name"], "status": row["status"]})
    summary: dict[str, object] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "release-evidence"),
        "ok": bool(report.get("ok")),
        "status": report.get("status", ""),
        "scope": report.get("scope", ""),
        "summary": {
            "check_count": len(rows),
            "failed_check_count": len(failed),
            "issue_count": len(report.get("issues", []) if isinstance(report.get("issues"), list) else []),
            "warning_count": len(report.get("warnings", []) if isinstance(report.get("warnings"), list) else []),
            "skipped_count": len(report.get("skipped", []) if isinstance(report.get("skipped"), list) else []),
        },
        "failed_checks": failed,
        "issues": report.get("issues", []),
        "warnings": report.get("warnings", []),
        "skipped": report.get("skipped", []),
        "next_action": report.get("next_action", ""),
    }
    if not compact:
        summary["checks"] = rows
    else:
        if not summary.get("failed_checks"):
            summary.pop("failed_checks", None)
        if not summary.get("issues"):
            summary.pop("issues", None)
        if not summary.get("warnings"):
            summary.pop("warnings", None)
        summary.pop("skipped", None)
        if bool(summary.get("ok")):
            summary.pop("next_action", None)
    return summary


def render_release_evidence_markdown(report: dict[str, object]) -> str:
    lines = ["# Release Evidence", ""]
    lines.append(f"- Status: {report['status']}")
    lines.append(f"- OK: {str(report['ok']).lower()}")
    lines.append("")
    lines.extend(["## Checks", ""])
    for check in report.get("checks", []):
        if not isinstance(check, dict):
            continue
        result = check.get("result")
        status = result.get("status") if isinstance(result, dict) else ""
        suffix = f" ({status})" if status else ""
        lines.append(f"- {check.get('name')}: {'ok' if check.get('ok') else 'failed'}{suffix}")
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        for warning in report["warnings"]:
            lines.append(f"- {warning}")
    if report.get("issues"):
        lines.extend(["", "## Issues", ""])
        for issue in report["issues"]:
            lines.append(f"- {issue}")
    if report.get("skipped"):
        lines.extend(["", "## Skipped", ""])
        for item in report["skipped"]:
            lines.append(f"- {item}")
    lines.extend(["", f"Next action: {report.get('next_action')}", ""])
    return "\n".join(lines)


def release_evidence(args: argparse.Namespace, root: Path) -> int:
    report = release_evidence_report(
        root,
        skip_fresh_clone=args.skip_fresh_clone or bool(getattr(args, "github_only", False)),
        source=args.source,
        include_deep_validation=bool(getattr(args, "include_deep_validation", False)),
        github_only=bool(getattr(args, "github_only", False)),
        local_only=bool(getattr(args, "local_only", False)),
    )
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        report = summarize_release_evidence_report(report, compact=bool(getattr(args, "compact", False)))
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_release_evidence_markdown(report))
    return 0 if report["ok"] else 1


def add_setup_doctor_actions(report: dict[str, object], root: Path) -> dict[str, object]:
    actions = report.setdefault("actions", {})
    checks = report.setdefault("checks", [])
    skipped = report.setdefault("skipped", [])
    if not isinstance(actions, dict) or not isinstance(checks, list) or not isinstance(skipped, list):
        return report
    actions["local_ai_readiness"] = setup_local_ai_readiness(root)
    actions["harness_health"] = repo_health.build_repo_health_report(root)
    actions["release_readiness"] = release_readiness(root)
    checks.extend(["local AI readiness checked", "harness health checked", "release readiness checked"])
    local_ai = actions["local_ai_readiness"]
    health = actions["harness_health"]
    readiness = actions["release_readiness"]
    if isinstance(local_ai, dict) and not local_ai.get("ok"):
        skipped.append("local AI is not fully ready; deterministic fallbacks remain available")
    if isinstance(readiness, dict):
        skipped.extend(str(item) for item in readiness.get("skipped", []) if str(item))
    report["ok"] = bool(
        report.get("ok")
        and isinstance(health, dict)
        and health.get("ok")
        and isinstance(readiness, dict)
        and readiness.get("ok")
    )
    if report["ok"] and isinstance(readiness, dict):
        report["status"] = str(readiness.get("status") or "preflight-passed")
    else:
        report["status"] = "failed"
    return report
