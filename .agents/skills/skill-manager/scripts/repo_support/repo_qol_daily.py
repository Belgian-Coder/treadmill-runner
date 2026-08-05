#!/usr/bin/env python3
"""Daily evidence and readiness helpers for repo QoL commands."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from repo_support import repo_changed
from repo_support import repo_command_metrics
from repo_support import repo_common as repo
from repo_support import repo_cost_policy
from repo_support import repo_doctor
from repo_support import repo_health
from repo_support import repo_optimizations
from repo_support.repo_fingerprint import fingerprint_excluded
from repo_support.repo_fingerprint import fingerprint_stale_input_paths
from repo_support.repo_fingerprint import input_fingerprint_report
from repo_support.repo_fingerprint import summarize_input_fingerprint
from repo_support import repo_service_config
from repo_support.repo_navigation_status import navigation_context_trace
from repo_support.repo_navigation_status import navigation_status


def startup_navigation_status(root: Path) -> dict[str, Any]:
    try:
        return navigation_status(root, fast=True)
    except TypeError:
        return navigation_status(root)


def route_attachment(path: str) -> dict[str, Any]:
    suffix = Path(path).suffix.lower()
    groups = {
        ".pdf": ("pdf", f"python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py bundle-evidence --file {path} --output-dir evidence/pdf"),
        ".docx": ("word", f"python -B .agents/skills/document-artifacts/scripts/word/word_tools.py bundle-evidence --file {path} --output-dir evidence/word"),
        ".xlsx": ("excel", f"python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py bundle-evidence --file {path} --output-dir evidence/excel"),
        ".pptx": ("powerpoint", f"python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py bundle-evidence --file {path} --output-dir evidence/powerpoint"),
    }
    if suffix in groups:
        kind, command = groups[suffix]
        optional = f"python -B .agents/manage.py local-ai document inspect --file {path} --json"
        if suffix == ".pdf":
            optional = f"{optional}; python -B .agents/manage.py local-ai vision pdf --pdf {path} --pages 1-5"
        return {
            "kind": kind,
            "deterministic_command": command,
            "optional_local_ai_command": optional,
            "fallback": "Use the deterministic bundle evidence when local AI is disabled.",
            "suggested_next_commands": [
                command,
            ],
        }
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return {
            "kind": "image",
            "deterministic_command": f"python -B .agents/manage.py local-ai document inspect --file {path} --json",
            "optional_local_ai_command": f"python -B .agents/manage.py local-ai vision describe --image {path} --json",
            "fallback": "Use file hash, filename, dimensions when available, and manual visual review.",
            "suggested_next_commands": [
                f"python -B .agents/manage.py local-ai document inspect --file {path} --json",
            ],
        }
    if suffix in {".log", ".txt", ".json", ".xml", ".csv", ".md"}:
        return {
            "kind": "text-or-log",
            "deterministic_command": f"python -B .agents/manage.py local-ai document inspect --file {path} --json",
            "optional_local_ai_command": f"python -B .agents/manage.py local-ai task --task inventory-summary --input {path}",
            "fallback": "Read the deterministic text directly and cite lines.",
            "suggested_next_commands": [
                f"python -B .agents/manage.py local-ai document inspect --file {path} --json",
            ],
        }
    if suffix in {".zip", ".7z", ".rar", ".tar", ".gz"}:
        return {
            "kind": "archive",
            "deterministic_command": "Inspect archive metadata with a contained, explicit extraction plan before opening contents.",
            "optional_local_ai_command": "",
            "fallback": "Do not index or extract archives blindly.",
            "archive_safety_plan": [
                "List archive entries before extraction.",
                "Reject absolute paths, parent traversal, duplicate names, and symlink-like entries.",
                "Extract only into an explicit repo-local evidence folder when a workflow requires it.",
                "Run attachment-route again on extracted files before local AI use.",
            ],
            "suggested_next_commands": [
                "Create a repo-local evidence folder and run a format-specific archive inventory command before extraction.",
            ],
        }
    return {
        "kind": "unknown",
        "deterministic_command": f"python -B .agents/manage.py local-ai document inspect --file {path} --json",
        "optional_local_ai_command": "",
        "fallback": "If unsupported, record hash, size, source, and manual review decision.",
        "suggested_next_commands": [
            f"python -B .agents/manage.py local-ai document inspect --file {path} --json",
        ],
    }


def attachment_route_report(root: Path, path: str) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit("attachment path must stay inside the repository") from exc
    rel = repo.relative(root, resolved)
    return {
        "schema_version": 1,
        "tool": "repo-attachment-route",
        "ok": True,
        "input_path": rel,
        **route_attachment(rel),
    }


def render_attachment_route(report: dict[str, Any]) -> str:
    lines = ["# Attachment Route", ""]
    lines.append(f"- Input: `{report.get('input_path')}`")
    lines.append(f"- Type: {report.get('kind')}")
    lines.append(f"- Deterministic command: `{report.get('deterministic_command')}`")
    if report.get("optional_local_ai_command"):
        lines.append(f"- Optional local AI: `{report.get('optional_local_ai_command')}`")
    lines.append(f"- Fallback: {report.get('fallback')}")
    if report.get("archive_safety_plan"):
        lines.extend(["", "## Archive Safety Plan", ""])
        lines.extend(f"- {item}" for item in report.get("archive_safety_plan", []))
    if report.get("suggested_next_commands"):
        lines.extend(["", "## Suggested Next Commands", ""])
        lines.extend(f"- `{item}`" for item in report.get("suggested_next_commands", []))
    if report.get("artifacts"):
        lines.extend(["", "## Written Plan", ""])
        lines.extend(f"- `{item}`" for item in report.get("artifacts", []))
    return "\n".join(lines) + "\n"


def changed_file_evidence(path: str) -> list[dict[str, str]]:
    suffix = Path(path).suffix.lower()
    lower_path = path.lower()
    commands: list[dict[str, str]] = []
    if "/raw/" in path.replace("\\", "/"):
        return [
            {
                "kind": "workflow-raw-artifact",
                "command": "python -B .agents/manage.py workflow doctor --name agent-benchmarking",
                "owner": "workflow-manager",
            }
        ]
    if path.startswith(".agents/skills/"):
        parts = path.split("/")
        if len(parts) >= 3:
            skill = parts[2]
            commands.append(
                {
                    "kind": "skill-review",
                    "command": f"python -B .agents/manage.py skill doctor --skill .agents/skills/{skill}",
                    "owner": "skill-manager",
                }
            )
    if path.startswith("automations/"):
        parts = path.split("/")
        if len(parts) >= 2:
            workflow = parts[1]
            commands.append(
                {
                    "kind": "workflow-review",
                    "command": f"python -B .agents/manage.py workflow doctor --name {workflow}",
                    "owner": "workflow-manager",
                }
            )
    if suffix in {".py", ".cs", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".xml", ".ps1", ".sh"}:
        commands.append(
            {
                "kind": "security-patterns",
                "command": "python -B .agents/skills/dotnet-security-review/scripts/dotnet_security_review.py scan --changed-only --json",
                "owner": "dotnet-security-review",
            }
        )
    if suffix in {".cs", ".csproj", ".sln", ".props", ".targets"}:
        commands.append(
            {
                "kind": "dotnet-quality",
                "command": "python -B .agents/skills/dotnet-quality-gates/scripts/validate_local_quality.py --changed-files --json",
                "owner": "dotnet-quality-gates",
            }
        )
    if lower_path.endswith((".spec.ts", ".spec.tsx", ".e2e.ts", ".test.ts", ".test.tsx")) or "playwright" in lower_path:
        commands.append(
            {
                "kind": "playwright-readiness",
                "command": "python -B .agents/skills/playwright-integration/scripts/lint_playwright_tests.py --project-root . --changed-files --report-json evidence/playwright-changed.json",
                "owner": "playwright-integration",
            }
        )
    if suffix == ".md":
        commands.append(
            {
                "kind": "mermaid-diagrams",
                "command": "python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/validate_mermaid.py --changed-only --static-only --format json",
                "owner": "mermaid-diagrams-azure-devops",
            }
        )
    if suffix in {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg", ".webp"}:
        route = route_attachment(path)
        commands.append(
            {
                "kind": f"{route.get('kind')}-attachment",
                "command": str(route.get("deterministic_command")),
                "owner": "document-skills",
            }
        )
    return commands


def changed_evidence_report(root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    changed = repo_changed.changed_files(root)
    scope = repo_changed.changed_scope(changed) if changed else {}
    validation_plan = repo_optimizations.changed_validation_plan(root, changed, scope, deep=False) if changed else []
    input_fingerprint = input_fingerprint_report(root, changed, validation_plan)
    navigation = startup_navigation_status(root)
    suggestions: list[dict[str, Any]] = []
    seen_commands: set[str] = set()
    for path in changed:
        for suggestion in changed_file_evidence(path):
            command = suggestion["command"]
            if command not in seen_commands:
                suggestions.append({**suggestion, "paths": []})
                seen_commands.add(command)
            for row in suggestions:
                if row["command"] == command:
                    row.setdefault("_all_paths", []).append(path)
                    break
    for row in suggestions:
        all_paths = row.pop("_all_paths", [])
        if isinstance(all_paths, list):
            row["path_count"] = len(all_paths)
            row["paths"] = all_paths[:20]
            if len(all_paths) > 20:
                row["paths_truncated"] = len(all_paths) - 20
    total_elapsed_ms = repo_command_metrics.elapsed_ms_since(started)
    next_command = validation_plan[0]["command"] if validation_plan else "none, no changed files"
    return {
        "schema_version": 1,
        "tool": "repo-changed-evidence",
        "ok": True,
        "changed_file_count": len(changed),
        "changed_groups": repo_changed.compact_path_groups(changed) if changed else "",
        "suggestions": suggestions,
        "navigation": navigation,
        "context_trace": navigation_context_trace(navigation),
        "latency_budget": repo_command_metrics.timing_budget_report("changed-evidence", total_elapsed_ms),
        "validation_router": {
            "status": "planned" if validation_plan else "no-changes",
            "summary": repo_optimizations.validation_plan_summary(validation_plan),
            "commands": validation_plan,
            "next_command": validation_plan[0]["command"] if validation_plan else "none, no changed files",
        },
        "input_fingerprint": input_fingerprint,
        "quality_packet_schema": {
            "schema_version": 1,
            "packet_kind": "changed-files-evidence",
            "sections": ["quality", "security", "documents", "workflow"],
            "local_ai_policy": "advisory only; consume deterministic JSON/Markdown evidence as input",
        },
        "fallback_command": "python -B .agents/manage.py check-changed --deep",
        "next_command": next_command,
    }


def summarize_changed_evidence_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    suggestions = report.get("suggestions") if isinstance(report.get("suggestions"), list) else []
    compact_suggestions: list[dict[str, Any]] = []
    suggestion_limit = 5 if compact else len(suggestions)
    for item in suggestions[:suggestion_limit]:
        if not isinstance(item, dict):
            continue
        paths = item.get("paths") if isinstance(item.get("paths"), list) else []
        row = {
            "kind": item.get("kind", ""),
            "owner": item.get("owner", ""),
            "command": item.get("command", ""),
            "path_count": int(item.get("path_count", len(paths)) or 0),
            "sample_paths": paths[:3],
        }
        if not compact:
            row["paths"] = paths
            if item.get("paths_truncated"):
                row["paths_truncated"] = item.get("paths_truncated")
        compact_suggestions.append(row)
    router = report.get("validation_router") if isinstance(report.get("validation_router"), dict) else {}
    fingerprint = report.get("input_fingerprint") if isinstance(report.get("input_fingerprint"), dict) else {}
    compact_router: dict[str, Any] = {
        "status": router.get("status", "unknown"),
        "summary": router.get("summary", {}),
        "next_command": router.get("next_command", ""),
    }
    if not compact:
        compact_router["commands"] = router.get("commands", [])
    navigation = report.get("navigation") if isinstance(report.get("navigation"), dict) else {}
    output: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "repo-changed-evidence"),
        "ok": bool(report.get("ok", True)),
        "changed_file_count": report.get("changed_file_count", 0),
        "changed_groups": report.get("changed_groups", ""),
        "suggestions": compact_suggestions,
        "omitted_suggestion_count": max(0, len(suggestions) - len(compact_suggestions)),
        "navigation": navigation,
        "context_trace": report.get("context_trace", navigation_context_trace(navigation)),
        "latency_budget": report.get("latency_budget", {}),
        "validation_router": compact_router,
        "input_fingerprint": summarize_input_fingerprint(fingerprint) if fingerprint else {},
        "fallback_command": report.get("fallback_command", ""),
        "next_command": report.get("next_command", ""),
    }
    if not compact:
        output["quality_packet_schema"] = report.get("quality_packet_schema", {})
    if compact:
        if not output.get("changed_groups"):
            output.pop("changed_groups", None)
        if not output.get("suggestions"):
            output.pop("suggestions", None)
        if not output.get("omitted_suggestion_count"):
            output.pop("omitted_suggestion_count", None)
        if not output.get("navigation"):
            output.pop("navigation", None)
        if not output.get("input_fingerprint"):
            output.pop("input_fingerprint", None)
    return repo_command_metrics.attach_output_budget(output, "changed-evidence")


def render_changed_evidence(report: dict[str, Any]) -> str:
    lines = ["# Changed Files Evidence", ""]
    lines.append(f"- Changed files: {report.get('changed_file_count', 0)}")
    if report.get("changed_groups"):
        lines.append(f"- Changed groups: {report.get('changed_groups')}")
    lines.extend(["", "## Suggested Evidence Commands", ""])
    for item in report.get("suggestions", []):
        paths = item.get("paths", []) if isinstance(item.get("paths"), list) else []
        sample = ", ".join(f"`{path}`" for path in paths[:3])
        path_count = int(item.get("path_count", len(paths)) or 0)
        more = f" (+{path_count - 3} more)" if path_count > 3 else ""
        lines.append(f"- {item.get('kind')} (`{item.get('owner')}`): `{item.get('command')}`")
        if sample:
            lines.append(f"  Paths: {sample}{more}")
    if not report.get("suggestions"):
        lines.append("- No specific evidence commands matched; use the fallback.")
    router = report.get("validation_router") if isinstance(report.get("validation_router"), dict) else {}
    if router and router.get("commands"):
        lines.extend(["", "## Validation Router", ""])
        summary = router.get("summary") if isinstance(router.get("summary"), dict) else {}
        lines.append(f"- Required: {summary.get('required_count', 0)}")
        lines.append(f"- Optional: {summary.get('optional_count', 0)}")
        for item in router.get("commands", [])[:8]:
            if isinstance(item, dict):
                required = "required" if item.get("required", True) else "optional"
                lines.append(f"- `{item.get('owner')}` {required}: `{item.get('command')}`")
    if report.get("quality_packet_schema"):
        lines.extend(["", "## Packet Shape", ""])
        schema = report.get("quality_packet_schema", {})
        if isinstance(schema, dict):
            lines.append(f"- Kind: `{schema.get('packet_kind')}`")
            lines.append("- Sections: " + ", ".join(f"`{item}`" for item in schema.get("sections", [])))
            lines.append(f"- Local AI: {schema.get('local_ai_policy')}")
    fingerprint = report.get("input_fingerprint") if isinstance(report.get("input_fingerprint"), dict) else {}
    if fingerprint:
        lines.extend(["", "## Input Fingerprint", ""])
        lines.append(f"- Digest: `{fingerprint.get('digest', '')}`")
        lines.append(f"- Changed files: `{fingerprint.get('changed_file_count', 0)}`")
        lines.append(f"- Hashed paths: `{fingerprint.get('hashed_path_count', 0)}`")
        lines.append(f"- Commands: `{fingerprint.get('command_count', 0)}`")
        skipped = fingerprint.get("skipped_fingerprint_paths") if isinstance(fingerprint.get("skipped_fingerprint_paths"), list) else []
        if skipped:
            lines.append(f"- Skipped fingerprint paths: `{len(skipped)}`")
        stale_if = fingerprint.get("stale_if") if isinstance(fingerprint.get("stale_if"), list) else []
        if stale_if:
            lines.append("- Stale if: " + "; ".join(str(item) for item in stale_if))
    if report.get("artifacts"):
        lines.extend(["", "## Written Evidence Plan", ""])
        lines.extend(f"- `{item}`" for item in report.get("artifacts", []))
    lines.extend(
        [
            "",
            f"- Fallback: `{report.get('fallback_command')}`",
            f"- Next command: `{report.get('next_command')}`",
            "",
        ]
    )
    return "\n".join(lines)


def _context_report_at_ref(root: Path, ref: str, budget_tokens: int, paths: list[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    total = 0
    for rel in paths:
        try:
            completed = subprocess.run(
                ["git", "show", f"{ref}:{rel}"],
                cwd=root,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return {
                "ref": ref,
                "budget_tokens": budget_tokens,
                "estimated_tokens": 0,
                "within_budget": True,
                "files": [],
                "missing": paths,
                "available": False,
            }
        if completed.returncode != 0:
            missing.append(rel)
            continue
        size = len(completed.stdout)
        tokens = repo_cost_policy.estimate_tokens_from_bytes(size)
        rows.append({"path": rel, "size_bytes": size, "estimated_tokens": tokens})
        total += tokens
    return {
        "ref": ref,
        "budget_tokens": budget_tokens,
        "estimated_tokens": total,
        "within_budget": total <= budget_tokens,
        "files": rows,
        "missing": missing,
        "available": True,
    }


def _startup_context_issues(
    config_error: str | None,
    policy_error: str | None,
    always_loaded: dict[str, Any],
    beginner_loaded: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    if config_error:
        issues.append(f"{repo_cost_policy.LOCAL_AI_CONFIG_PATH} could not be loaded: {config_error}")
    if policy_error and "missing cost_policy" not in policy_error:
        issues.append(policy_error)
    if not always_loaded.get("within_budget", True):
        issues.append(
            "always-loaded context exceeds cost_policy.always_loaded_budget_tokens "
            f"({always_loaded.get('estimated_tokens')} > {always_loaded.get('budget_tokens')})."
        )
    if not beginner_loaded.get("within_budget", True):
        issues.append(
            "beginner-loaded context exceeds cost_policy.beginner_loaded_budget_tokens "
            f"({beginner_loaded.get('estimated_tokens')} > {beginner_loaded.get('budget_tokens')})."
        )
    return issues


def startup_context_baseline_regression(policy: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    max_added_tokens = repo_cost_policy.int_field(
        policy.get("startup_context_max_added_tokens"),
        repo_cost_policy.DEFAULT_STARTUP_CONTEXT_MAX_ADDED_TOKENS,
    )
    max_added_percent = repo_cost_policy.numeric_percent(
        policy.get("startup_context_max_added_percent"),
        repo_cost_policy.DEFAULT_STARTUP_CONTEXT_MAX_ADDED_PERCENT,
    )
    if not baseline:
        return {
            "status": "not-run",
            "ok": True,
            "max_added_tokens": max_added_tokens,
            "max_added_percent": max_added_percent,
        }
    if not baseline.get("available"):
        return {
            "status": "unavailable",
            "ok": False,
            "max_added_tokens": max_added_tokens,
            "max_added_percent": max_added_percent,
            "issue": f"baseline ref could not be fully read: {baseline.get('ref', '')}",
        }

    def row(kind: str) -> dict[str, Any]:
        baseline_tokens = int(baseline.get(f"{kind}_loaded_tokens", 0) or 0)
        delta_tokens = int(baseline.get(f"{kind}_delta_tokens", 0) or 0)
        added_tokens = max(0, delta_tokens)
        allowed_tokens = max(max_added_tokens, round(baseline_tokens * (max_added_percent / 100)))
        added_percent = round((added_tokens / baseline_tokens) * 100, 2) if baseline_tokens else 0.0
        return {
            "kind": kind,
            "baseline_tokens": baseline_tokens,
            "current_tokens": baseline_tokens + delta_tokens,
            "added_tokens": added_tokens,
            "added_percent": added_percent,
            "allowed_added_tokens": allowed_tokens,
            "ok": added_tokens <= allowed_tokens,
        }

    rows = [row("always"), row("beginner")]
    failed = [item for item in rows if not item["ok"]]
    return {
        "status": "passed" if not failed else "regressed",
        "ok": not failed,
        "max_added_tokens": max_added_tokens,
        "max_added_percent": max_added_percent,
        "rows": rows,
        "issue": (
            ""
            if not failed
            else "startup context increased beyond policy: "
            + ", ".join(f"{item['kind']} +{item['added_tokens']} tokens" for item in failed)
        ),
    }


def startup_context_report(root: Path, *, baseline_ref: str | None = None, compact: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    timings: list[dict[str, Any]] = []

    def timed(name: str, callback):
        value, timing = repo_command_metrics.timed_section(name, callback)
        timings.append(timing)
        return value

    _config, config_error = timed("load_local_ai_config", lambda: repo_cost_policy.load_local_ai_config(root))
    policy, policy_error = timed("load_cost_policy", lambda: repo_cost_policy.load_cost_policy(root))
    always_budget = repo_cost_policy.int_field(policy.get("always_loaded_budget_tokens"), 3500)
    beginner_budget = repo_cost_policy.int_field(policy.get("beginner_loaded_budget_tokens"), 5000)
    always_paths = repo_cost_policy.configured_paths(policy, "always_loaded_files", repo_cost_policy.LOW_CONTEXT_FILES)
    beginner_paths = repo_cost_policy.configured_paths(policy, "beginner_loaded_files", repo_cost_policy.BEGINNER_CONTEXT_FILES)
    always_loaded = timed("always_loaded_context", lambda: repo_cost_policy.low_context_report(root, always_budget, always_paths))
    beginner_loaded = timed("beginner_loaded_context", lambda: repo_cost_policy.low_context_report(root, beginner_budget, beginner_paths))
    navigation = timed("navigation_status", lambda: startup_navigation_status(root))
    guidance_savings = timed("guidance_savings", lambda: repo_cost_policy.guidance_savings_report(root, policy))
    issues = _startup_context_issues(config_error, policy_error, always_loaded, beginner_loaded)
    if guidance_savings.get("measurable") and not guidance_savings.get("meets_minimum"):
        issues.append(
            "default guidance packet does not meet cost_policy.min_guidance_saved_percent "
            f"({guidance_savings.get('saved_percent_estimated')}% < {guidance_savings.get('min_saved_percent')}%)."
        )
    top_files = [
        {"load": "always", **row}
        for row in always_loaded.get("files", [])
        if isinstance(row, dict)
    ] + [
        {"load": "beginner", **row}
        for row in beginner_loaded.get("files", [])
        if isinstance(row, dict)
    ]
    top_files = sorted(top_files, key=lambda row: int(row.get("estimated_tokens", 0) or 0), reverse=True)
    baseline: dict[str, Any] | None = None
    if baseline_ref:
        baseline_always = timed("baseline_always_context", lambda: _context_report_at_ref(root, baseline_ref, always_budget, always_paths))
        baseline_beginner = timed("baseline_beginner_context", lambda: _context_report_at_ref(root, baseline_ref, beginner_budget, beginner_paths))
        baseline = {
            "ref": baseline_ref,
            "available": bool(baseline_always.get("available")) and bool(baseline_beginner.get("available")),
            "always_loaded_tokens": baseline_always.get("estimated_tokens", 0),
            "beginner_loaded_tokens": baseline_beginner.get("estimated_tokens", 0),
            "always_delta_tokens": int(always_loaded.get("estimated_tokens", 0) or 0)
            - int(baseline_always.get("estimated_tokens", 0) or 0),
            "beginner_delta_tokens": int(beginner_loaded.get("estimated_tokens", 0) or 0)
            - int(baseline_beginner.get("estimated_tokens", 0) or 0),
            "missing_at_ref": sorted(
                set(baseline_always.get("missing", []))
                | set(baseline_beginner.get("missing", []))
            ),
        }
        if not baseline["available"]:
            issues.append(f"baseline ref could not be fully read: {baseline_ref}")
    baseline_regression = startup_context_baseline_regression(policy, baseline)
    if baseline_ref and not baseline_regression.get("ok") and baseline_regression.get("issue"):
        issue = str(baseline_regression["issue"])
        if issue not in issues:
            issues.append(issue)
    ok = not issues
    total_elapsed_ms = repo_command_metrics.elapsed_ms_since(started)
    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": "repo-startup-context",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "total_elapsed_ms": total_elapsed_ms,
        "timing_sections": timings,
        "latency_budget": repo_command_metrics.timing_budget_report(
            "startup-context",
            total_elapsed_ms,
            timings=timings,
        ),
        "summary": {
            "always_loaded_tokens": always_loaded.get("estimated_tokens", 0),
            "always_loaded_budget_tokens": always_loaded.get("budget_tokens", always_budget),
            "always_within_budget": bool(always_loaded.get("within_budget", True)),
            "beginner_loaded_tokens": beginner_loaded.get("estimated_tokens", 0),
            "beginner_loaded_budget_tokens": beginner_loaded.get("budget_tokens", beginner_budget),
            "beginner_within_budget": bool(beginner_loaded.get("within_budget", True)),
            "routine_skips_beginner_tokens": beginner_loaded.get("estimated_tokens", 0),
            "default_guidance_tokens": guidance_savings.get("default_guidance_tokens", 0),
            "broad_guidance_baseline_tokens": guidance_savings.get("broad_baseline_tokens", 0),
            "guidance_saved_tokens_estimated": guidance_savings.get("saved_tokens_estimated", 0),
            "guidance_saved_percent_estimated": guidance_savings.get("saved_percent_estimated", 0.0),
            "guidance_status": guidance_savings.get("status", "unknown"),
            "issue_count": len(issues),
            "startup_file_count": len(always_loaded.get("files", [])),
            "beginner_file_count": len(beginner_loaded.get("files", [])),
            "navigation_status": navigation.get("status", "unknown"),
            "baseline_regression_status": baseline_regression.get("status", "not-run"),
        },
        "issues": issues,
        "navigation": navigation,
        "context_trace": navigation_context_trace(navigation),
        "guidance_savings": guidance_savings,
        "always_loaded": always_loaded,
        "beginner_loaded": beginner_loaded,
        "top_files": top_files,
        "baseline": baseline,
        "baseline_regression": baseline_regression,
        "next_command": "python -B .agents/manage.py startup-context --summary --compact --format json",
        "next_command_reason": "Re-run after low-context routing or policy changes to refresh the startup budget evidence.",
    }
    if compact:
        report["always_loaded"].pop("files", None)
        report["beginner_loaded"].pop("files", None)
        report["guidance_savings"].get("default_context", {}).pop("files", None)
        report["guidance_savings"].get("broad_baseline", {}).pop("files", None)
        report.pop("top_files", None)
    return report


def summarize_startup_context_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "repo-startup-context"),
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", "unknown"),
        "total_elapsed_ms": report.get("total_elapsed_ms", 0),
        "latency_budget": report.get("latency_budget", {}),
        "summary": report.get("summary", {}),
        "navigation": report.get("navigation", {}),
        "context_trace": report.get(
            "context_trace",
            navigation_context_trace(report.get("navigation", {}) if isinstance(report.get("navigation"), dict) else {}),
        ),
        "guidance_savings": report.get("guidance_savings", {}),
        "issues": report.get("issues", []),
        "baseline": report.get("baseline"),
        "baseline_regression": report.get("baseline_regression"),
        "next_command": report.get("next_command", ""),
        "next_command_reason": report.get("next_command_reason", ""),
    }
    if compact:
        if not output.get("issues"):
            output.pop("issues", None)
        if output.get("baseline") is None:
            output.pop("baseline", None)
        if output.get("baseline_regression", {}).get("status") == "not-run":
            output.pop("baseline_regression", None)
    else:
        output["top_files"] = report.get("top_files", [])
        output["timing_sections"] = report.get("timing_sections", [])
    return repo_command_metrics.attach_output_budget(output, "startup-context")


def clean_context_proof_report(root: Path) -> dict[str, Any]:
    agents_path = root / "AGENTS.md"
    try:
        agents_text = agents_path.read_text(encoding="utf-8")
    except OSError:
        agents_text = ""
    startup = startup_context_report(root, compact=True)
    navigation = startup.get("navigation") if isinstance(startup.get("navigation"), dict) else {}
    checks = [
        {
            "name": "agents-md-present",
            "ok": bool(agents_text.strip()),
            "summary": "AGENTS.md is available" if agents_text.strip() else "AGENTS.md is missing or empty",
        },
        {
            "name": "agents-md-routes-to-handoff",
            "ok": "automations/navigation/artifacts/maps/HANDOFF.md" in agents_text,
            "summary": "AGENTS.md points to HANDOFF.md"
            if "automations/navigation/artifacts/maps/HANDOFF.md" in agents_text
            else "AGENTS.md does not point to HANDOFF.md",
        },
        {
            "name": "raw-navigation-json-tool-only",
            "ok": "raw navigation JSON is tool-only" in agents_text,
            "summary": "AGENTS.md keeps raw navigation JSON out of context"
            if "raw navigation JSON is tool-only" in agents_text
            else "AGENTS.md does not declare raw navigation JSON as tool-only",
        },
        {
            "name": "startup-context-read-first",
            "ok": navigation.get("read_first") == "automations/navigation/artifacts/maps/HANDOFF.md",
            "summary": f"read_first={navigation.get('read_first', '')}",
        },
        {
            "name": "startup-context-next-command",
            "ok": bool(startup.get("next_command") or navigation.get("next_command")),
            "summary": str(startup.get("next_command") or navigation.get("next_command") or ""),
        },
        {
            "name": "finish-discoverable",
            "ok": "manage.py finish" in agents_text,
            "summary": "AGENTS.md exposes finish as the final-claim gate"
            if "manage.py finish" in agents_text
            else "AGENTS.md does not expose finish as the final-claim gate",
        },
    ]
    ok = all(bool(check.get("ok")) for check in checks)
    return {
        "schema_version": 1,
        "tool": "repo-clean-context-proof",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "loaded_context": ["AGENTS.md", "startup-context"],
        "agent_packet": {
            "route_files": ["AGENTS.md", ".agents/routing.md", "automations/routing.md"],
            "source_orientation": navigation.get("read_first", ""),
            "navigation_status": navigation.get("status", "unknown"),
            "next_command": startup.get("next_command") or navigation.get("next_command") or "",
            "completion_command": "python -B .agents/manage.py finish --summary --compact --format json",
            "raw_navigation_json": "tool-only",
        },
        "checks": checks,
        "summary": {
            "check_count": len(checks),
            "failed_check_count": sum(1 for check in checks if not check.get("ok")),
            "source_orientation": navigation.get("read_first", ""),
            "navigation_status": navigation.get("status", "unknown"),
        },
    }


def summarize_clean_context_proof_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    failed = [check for check in checks if isinstance(check, dict) and not check.get("ok")]
    output: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "repo-clean-context-proof"),
        "ok": bool(report.get("ok")),
        "status": report.get("status", "unknown"),
        "summary": report.get("summary", {}),
        "agent_packet": report.get("agent_packet", {}),
        "loaded_context": report.get("loaded_context", []),
    }
    if compact:
        if failed:
            output["failed_checks"] = failed
        return output
    output["checks"] = checks
    return output


def render_clean_context_proof(report: dict[str, Any]) -> str:
    lines = [
        "# Clean Context Proof",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Loaded context: {', '.join(str(item) for item in report.get('loaded_context', []))}",
    ]
    packet = report.get("agent_packet") if isinstance(report.get("agent_packet"), dict) else {}
    if packet:
        lines.extend(
            [
                f"- Source orientation: `{packet.get('source_orientation', '')}`",
                f"- Navigation status: {packet.get('navigation_status', 'unknown')}",
                f"- Next command: `{packet.get('next_command', '')}`",
                f"- Raw navigation JSON: {packet.get('raw_navigation_json', '')}",
            ]
        )
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    if checks:
        lines.extend(["", "## Checks", ""])
        for check in checks:
            status = "ok" if check.get("ok") else "failed"
            lines.append(f"- {check.get('name')}: {status} - {check.get('summary', '')}")
    return "\n".join(lines)


def render_startup_context(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = ["# Startup Context", "", f"- Status: {report.get('status')}"]
    lines.append(
        "- Always-loaded: "
        f"{summary.get('always_loaded_tokens', 0)} / "
        f"{summary.get('always_loaded_budget_tokens', 0)} tokens"
    )
    lines.append(
        "- Beginner-loaded: "
        f"{summary.get('beginner_loaded_tokens', 0)} / "
        f"{summary.get('beginner_loaded_budget_tokens', 0)} tokens"
    )
    lines.append(f"- Routine skips beginner docs: {summary.get('routine_skips_beginner_tokens', 0)} tokens")
    lines.append(
        "- Guidance savings: "
        f"{summary.get('guidance_saved_tokens_estimated', 0)} tokens "
        f"({summary.get('guidance_saved_percent_estimated', 0.0)}%)"
    )
    navigation = report.get("navigation") if isinstance(report.get("navigation"), dict) else {}
    if navigation:
        lines.append(
            "- Navigation maps: "
            f"{navigation.get('status', 'unknown')} "
            f"({navigation.get('summary', '')})"
        )
        if navigation.get("read_first"):
            lines.append(f"- Source orientation: `{navigation.get('read_first')}`")
    baseline = report.get("baseline") if isinstance(report.get("baseline"), dict) else None
    if baseline:
        lines.append(
            "- Baseline delta: "
            f"always {baseline.get('always_delta_tokens', 0):+} tokens, "
            f"beginner {baseline.get('beginner_delta_tokens', 0):+} tokens "
            f"vs `{baseline.get('ref')}`"
        )
    issues = report.get("issues", []) if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in issues)
    top_files = report.get("top_files", []) if isinstance(report.get("top_files"), list) else []
    if top_files:
        lines.extend(["", "## Largest Files", ""])
        for item in top_files[:10]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('path')}` ({item.get('load')}): "
                f"{item.get('estimated_tokens', 0)} tokens"
            )
    lines.extend(["", f"Next command: `{report.get('next_command')}`", ""])
    return "\n".join(lines)


def env_present(names: list[str]) -> list[str]:
    return [name for name in names if os.environ.get(name)]


def secret_store_keys(root: Path) -> list[str]:
    return repo_service_config.secret_store_keys(root)


def credential_doctor_report(root: Path) -> dict[str, Any]:
    azure_status = repo_service_config.service_status(root, "azure-devops")
    tfs_status = repo_service_config.service_status(root, "tfs")
    sonar_status = repo_service_config.service_status(root, "sonarqube")
    profiles = [
        {
            "name": "azure-devops-ticket-intake",
            "owner": "azure-devops-ticket-intake",
            "env_present": env_present(["AZURE_DEVOPS_PAT", "ADO_PAT", "SYSTEM_ACCESSTOKEN", "AZURE_DEVOPS_ORG_URL"]),
            "service_config": azure_status,
            "alternate_service_config": tfs_status,
            "required_for": "Azure DevOps/TFS REST ticket imports and attachment downloads.",
            "fallback": "Use manual or redacted fixture intake folders.",
            "configure_command": "python -B .agents/manage.py credential-doctor --configure --service azure-devops",
        },
        {
            "name": "sonarqube-diagnostics",
            "owner": "sonarqube-diagnostics",
            "env_present": env_present(["SONAR_TOKEN", "SONAR_HOST_URL", "SONAR_PROJECT_KEY"]),
            "service_config": sonar_status,
            "required_for": "Read-only SonarQube export, comparison, and optional publish preflight.",
            "fallback": "Use saved redacted Sonar export fixtures or skip remote diagnostics.",
            "configure_command": "python -B .agents/manage.py credential-doctor --configure --service sonarqube",
        },
        {
            "name": "external-reference-manager",
            "owner": "external-reference-manager",
            "env_present": env_present(["GIT_ASKPASS", "SSH_AUTH_SOCK", "AZURE_DEVOPS_PAT", "ADO_PAT"]),
            "required_for": "Private Git or Azure DevOps reference fetches.",
            "fallback": "Use public references, existing local mirrors, or fixture cards.",
        },
        {
            "name": "local-ai-helper",
            "owner": "local-ai-helper",
            "env_present": env_present(["SKILLS_LOCAL_AI", "SKILLS_LOCAL_AI_ALLOW_DOWNLOADS"]),
            "required_for": "Optional local AI policy overrides, downloads, and advisory analysis.",
            "fallback": "Use deterministic validation, exact repository search, and document evidence.",
        },
    ]
    store_keys = secret_store_keys(root)
    checks = []
    for profile in profiles:
        service_config = profile.get("service_config") if isinstance(profile.get("service_config"), dict) else {}
        alternate_config = (
            profile.get("alternate_service_config")
            if isinstance(profile.get("alternate_service_config"), dict)
            else {}
        )
        configured = (
            bool(profile["env_present"])
            or bool(service_config.get("configured"))
            or bool(alternate_config.get("configured"))
        )
        checks.append({**profile, "configured": configured, "redaction": "token values are not displayed"})
    return {
        "schema_version": 1,
        "tool": "repo-credential-doctor",
        "ok": True,
        "status": "passed",
        "secret_store": ".agents/local-ai/secrets.local.json",
        "secret_store_present": bool(store_keys),
        "secret_store_keys": store_keys,
        "checks": checks,
        "next_command": "python -B .agents/manage.py credential-doctor --configure --service <service>",
    }


def configure_credential_profile(root: Path, args: Any) -> dict[str, Any]:
    return repo_service_config.configure_service_profile(root, args)


def summarize_credential_doctor_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    configured = [item for item in checks if isinstance(item, dict) and item.get("configured")]
    unconfigured = [item for item in checks if isinstance(item, dict) and not item.get("configured")]
    rows = unconfigured if compact else checks
    return {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "repo-credential-doctor"),
        "ok": report.get("ok", True),
        "status": report.get("status", "unknown"),
        "summary": {
            "profile_count": len(checks),
            "configured_count": len(configured),
            "unconfigured_count": len(unconfigured),
            "secret_store_present": bool(report.get("secret_store_present")),
            "secret_store_key_count": len(report.get("secret_store_keys", []))
            if isinstance(report.get("secret_store_keys"), list)
            else 0,
        },
        "checks": [
            {
                "name": item.get("name", ""),
                "owner": item.get("owner", ""),
                "configured": bool(item.get("configured")),
                "env_present_count": len(item.get("env_present", []))
                if isinstance(item.get("env_present"), list)
                else 0,
                "profile_count": int(
                    item.get("service_config", {}).get("profile_count", 0)
                    if isinstance(item.get("service_config"), dict)
                    else 0
                )
                + int(
                    item.get("alternate_service_config", {}).get("profile_count", 0)
                    if isinstance(item.get("alternate_service_config"), dict)
                    else 0
                ),
                "complete_profile_count": int(
                    item.get("service_config", {}).get("complete_profile_count", 0)
                    if isinstance(item.get("service_config"), dict)
                    else 0
                )
                + int(
                    item.get("alternate_service_config", {}).get("complete_profile_count", 0)
                    if isinstance(item.get("alternate_service_config"), dict)
                    else 0
                ),
                "fallback": item.get("fallback", ""),
                "configure_command": item.get("configure_command", ""),
            }
            for item in rows
            if isinstance(item, dict)
        ],
        "next_command": report.get("next_command", ""),
    }


def render_credential_doctor(report: dict[str, Any]) -> str:
    lines = ["# Credential Doctor", ""]
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else None
    if summary:
        lines.append(f"- Profiles: {summary.get('profile_count', 0)}")
        lines.append(f"- Configured: {summary.get('configured_count', 0)}")
        lines.append(f"- Unconfigured: {summary.get('unconfigured_count', 0)}")
        lines.append(f"- Secret store: {'present' if summary.get('secret_store_present') else 'missing'}")
        lines.extend(["", "## Profiles", ""])
        for item in report.get("checks", []):
            lines.append(
                f"- `{item.get('name')}`: "
                f"{'configured signal present' if item.get('configured') else 'no credential signal'}"
            )
            if item.get("configure_command"):
                lines.append(f"  Configure: `{item.get('configure_command')}`")
            lines.append(f"  Fallback: {item.get('fallback')}")
        lines.extend(["", f"Next command: `{report.get('next_command')}`", ""])
        return "\n".join(lines)
    lines.append(f"- Secret store: `{report.get('secret_store')}` ({'present' if report.get('secret_store_present') else 'missing'})")
    if report.get("secret_store_keys"):
        lines.append("- Secret store keys: " + ", ".join(f"`{key}`" for key in report.get("secret_store_keys", [])))
    lines.append("- Values are never printed.")
    lines.extend(["", "## Profiles", ""])
    for item in report.get("checks", []):
        envs = item.get("env_present", []) if isinstance(item.get("env_present"), list) else []
        service_config = item.get("service_config") if isinstance(item.get("service_config"), dict) else {}
        alternate_config = item.get("alternate_service_config") if isinstance(item.get("alternate_service_config"), dict) else {}
        lines.append(f"- `{item.get('name')}`: {'configured signal present' if item.get('configured') else 'no credential signal'}")
        lines.append(f"  Owner: `{item.get('owner')}`")
        lines.append(f"  Env present: {', '.join(f'`{name}`' for name in envs) if envs else 'none'}")
        if service_config or alternate_config:
            complete_count = int(service_config.get("complete_profile_count", 0)) + int(
                alternate_config.get("complete_profile_count", 0)
            )
            profile_count = int(service_config.get("profile_count", 0)) + int(alternate_config.get("profile_count", 0))
            lines.append(
                "  Local profiles: "
                f"{complete_count} complete / "
                f"{profile_count} total"
            )
        if item.get("configure_command"):
            lines.append(f"  Configure: `{item.get('configure_command')}`")
        lines.append(f"  Fallback: {item.get('fallback')}")
    lines.extend(["", f"Next command: `{report.get('next_command')}`", ""])
    return "\n".join(lines)


def render_configure_credential_profile(report: dict[str, Any]) -> str:
    lines = ["# Credential Configure", "", f"- Status: `{report.get('status')}`"]
    lines.append(f"- Service: `{report.get('service', '')}`")
    if report.get("profile_name"):
        lines.append(f"- Profile: `{report.get('profile_name')}`")
    lines.append(f"- Secret store: `{report.get('secret_store', repo_service_config.SECRET_STORE_REL)}`")
    if report.get("action"):
        lines.append(f"- Action: `{report.get('action')}`")
    gitignore = report.get("gitignore") if isinstance(report.get("gitignore"), dict) else {}
    if gitignore:
        lines.append(f"- Gitignore updated: `{bool(gitignore.get('updated'))}`")
        if gitignore.get("added"):
            lines.append("- Gitignore added: " + ", ".join(f"`{item}`" for item in gitignore.get("added", [])))
    if report.get("profile"):
        lines.extend(["", "## Saved Profile", ""])
        for key, value in dict(report.get("profile", {})).items():
            lines.append(f"- `{key}`: `{value}`")
    if report.get("missing"):
        lines.extend(["", "## Missing Inputs", ""])
        lines.extend(f"- `{item}`" for item in report.get("missing", []))
    if report.get("issues"):
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in report.get("issues", []))
    if report.get("next_command"):
        lines.extend(["", f"Next command: `{report.get('next_command')}`", ""])
    return "\n".join(lines)


def commit_readiness_report(root: Path) -> dict[str, Any]:
    staged_status, staged = repo.git_output(root, "diff", "--cached", "--name-only")
    dirty = repo_doctor.git_dirty_state(root)
    risky_patterns = (
        ".agents/local-ai/cache/",
        ".agents/local-ai/downloads/",
        ".agents/local-ai/bundle/models/",
        ".agents/local-ai/bundle/runtimes/",
        ".agents/local-ai/secrets.local.json",
        "temp/",
    )
    risky = [path for path in staged if path.endswith(".gguf") or any(path.startswith(pattern) for pattern in risky_patterns)]
    health = repo_health.build_repo_health_report(root)
    issues: list[str] = []
    if staged_status != 0:
        issues.append("git staged files could not be read")
    if not staged:
        issues.append("no files are staged")
    if risky:
        issues.append("staged files include payload, cache, temp, model, runtime, or secret paths")
    generated_ok = all(bool(item.get("ok")) for item in health.get("generated_checks", []))
    if not generated_ok:
        issues.append("generated artifacts are not in sync")
    return {
        "schema_version": 1,
        "tool": "repo-commit-readiness",
        "ok": not issues,
        "status": "ready" if not issues else "not-ready",
        "staged_files": staged,
        "risky_staged_files": risky,
        "dirty_state": dirty,
        "generated_sync_ok": generated_ok,
        "issues": issues,
        "next_command": "git commit" if not issues else "stage intended files, remove unsafe staged paths, and rerun commit-readiness",
    }


def render_commit_readiness(report: dict[str, Any]) -> str:
    lines = ["# Commit Readiness", "", f"- Status: {report.get('status')}"]
    lines.append(f"- Staged files: {len(report.get('staged_files', []))}")
    lines.append(f"- Generated sync: {'ok' if report.get('generated_sync_ok') else 'stale'}")
    if report.get("risky_staged_files"):
        lines.extend(["", "## Risky Staged Files", ""])
        lines.extend(f"- `{item}`" for item in report["risky_staged_files"])
    if report.get("issues"):
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in report["issues"])
    lines.extend(["", f"Next command: {report.get('next_command')}", ""])
    return "\n".join(lines)
