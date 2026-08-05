"""Guardrails for low-context navigation and generated-index misuse."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from repo_support import repo_changed
from repo_support import repo_command_metrics
from repo_support import repo_common as repo
from repo_support import repo_policy

RAW_NAVIGATION_MARKERS = (
    "project-map.json",
    "code-graph.json",
    "automations/registry.json",
    "registry.json",
    "handoff.json",
    "staleness.json",
)
RAW_DIFF_MARKERS = (
    "git diff",
    "raw diff",
    "full diff",
)

SAFE_NAVIGATION_CONTEXT_MARKERS = (
    "tool-only",
    "do not load",
    "do not read",
    "do not open",
    "never load",
    "never read",
    "never open",
    "status commands",
    "repo_navigation.py focus",
    "repo_navigation.py check",
    "generated map writes",
    "exclude",
    "skip generated",
    "skipped",
    "freshness index",
    "inside deterministic commands",
    "inside the tool",
)

SAFE_RAW_DIFF_CONTEXT_MARKERS = (
    "review-packet",
    "review packet",
    "changed-context",
    "changed context",
    "review-loop",
    "bounded review",
    "git diff --check",
    "git diff whitespace",
    "git diff -- <path>",
    "before broad git diff",
)

SCANNED_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml"}
SKIP_PARTS = {"runs", ".git", "__pycache__", "cache", "downloads", "bundle", "registry.json"}
SKIP_PREFIXES = (
    ".agents/local-ai.json",
    ".agents/harness-payload.json",
    ".agents/registry.json",
    "automations/registry.json",
    "automations/navigation/artifacts/maps/",
)
PROTECTED_CONTEXT_PATHS = (
    "AGENTS.md",
    ".github/copilot-instructions.md",
    ".continue/rules/repository-instructions.md",
    ".claude/CLAUDE.md",
    "GEMINI.md",
    "docs/start-here.md",
    "docs/operations/daily-agent-path.md",
    "docs/reference/commands.md",
    "docs/reference/tools-and-search.md",
)
HANDOFF_PATH = "automations/navigation/artifacts/maps/HANDOFF.md"
RAW_NAVIGATION_JSON_PATHS = {
    "automations/navigation/artifacts/maps/handoff.json",
    "automations/navigation/artifacts/maps/staleness.json",
    "automations/navigation/artifacts/maps/project-map.json",
    "automations/navigation/artifacts/maps/code-graph.json",
}


def _iter_paths(root: Path, paths: list[str] | None = None, *, include_protected: bool = True) -> list[str]:
    selected = {path.replace("\\", "/") for path in paths if path} if paths is not None else set(repo_changed.changed_files(root))
    if include_protected:
        selected.update(path for path in PROTECTED_CONTEXT_PATHS if (root / path).exists())
    return sorted(selected)


def _should_scan(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in SKIP_PREFIXES):
        return False
    if any(part in SKIP_PARTS for part in normalized.split("/")):
        return False
    return Path(normalized).suffix.lower() in SCANNED_SUFFIXES


def _unsafe_navigation_line(line: str) -> bool:
    lower = line.lower()
    if not any(marker in lower for marker in RAW_NAVIGATION_MARKERS):
        return False
    return not any(marker in lower for marker in SAFE_NAVIGATION_CONTEXT_MARKERS)


def _unsafe_raw_diff_line(line: str) -> bool:
    lower = line.lower()
    if not any(marker in lower for marker in RAW_DIFF_MARKERS):
        return False
    if not any(verb in lower for verb in ("read", "load", "open", "inspect", "review", "summarize", "use")):
        return False
    return not any(marker in lower for marker in SAFE_RAW_DIFF_CONTEXT_MARKERS)


def _line_finding(path: str, line: str, lineno: int, *, snippet_chars: int) -> dict[str, Any] | None:
    snippet = line.strip()[:snippet_chars]
    if _unsafe_navigation_line(line):
        if path.replace("\\", "/").endswith("/module.json"):
            return None
        return {
            "path": path,
            "line": lineno,
            "snippet": snippet,
            "issue": (
                "raw generated navigation/registry JSON is referenced without a tool-only or "
                "compact-status guardrail"
            ),
            "fix": (
                "Route agents to HANDOFF.md, NAVIGATION.md, startup-context/status, or "
                "repo_navigation.py focus/check instead of loading raw generated JSON."
            ),
        }
    if _unsafe_raw_diff_line(line):
        return {
            "path": path,
            "line": lineno,
            "snippet": snippet,
            "issue": "broad raw diff reading is referenced without a compact review-packet guardrail",
            "fix": (
                "Route agents to changed-context, review-packet, or review-loop before broad git diff review."
            ),
        }
    return None


def context_guardrail_report(
    root: Path,
    paths: list[str] | None = None,
    *,
    include_protected: bool = True,
) -> dict[str, Any]:
    scanned: list[str] = []
    skipped: list[dict[str, str]] = []
    findings: list[dict[str, Any]] = []
    snippet_chars = repo_policy.int_value(root, "limits.output.evidence_snippet_chars")
    for rel_path in _iter_paths(root, paths, include_protected=include_protected):
        normalized = rel_path.replace("\\", "/")
        if not _should_scan(normalized):
            skipped.append({"path": normalized, "reason": "not agent-facing text or skipped generated/evidence path"})
            continue
        path = root / normalized
        if not path.is_file():
            skipped.append({"path": normalized, "reason": "missing or not a file"})
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            skipped.append({"path": normalized, "reason": f"read failed: {exc.__class__.__name__}"})
            continue
        scanned.append(normalized)
        for lineno, line in enumerate(text.splitlines(), start=1):
            finding = _line_finding(normalized, line, lineno, snippet_chars=snippet_chars)
            if finding:
                findings.append(finding)
    return {
        "schema_version": 1,
        "tool": "skill-manager.context-guardrails",
        "ok": not findings,
        "status": "passed" if not findings else "failed",
        "scanned_count": len(scanned),
        "skipped_count": len(skipped),
        "finding_count": len(findings),
        "findings": findings,
        "scanned": scanned,
        "skipped": skipped[:40],
        "protected_context_paths": [
            path for path in PROTECTED_CONTEXT_PATHS if (root / path).exists()
        ] if include_protected else [],
    }


def summarize_context_guardrail_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.context-guardrails"),
        "ok": bool(report.get("ok", True)),
        "status": report.get("status", "unknown"),
        "scanned_count": report.get("scanned_count", 0),
        "skipped_count": report.get("skipped_count", 0),
        "finding_count": report.get("finding_count", 0),
        "findings": report.get("findings", []),
    }
    if not compact:
        output["scanned"] = report.get("scanned", [])
        output["skipped"] = report.get("skipped", [])
        output["protected_context_paths"] = report.get("protected_context_paths", [])
    elif not output["findings"]:
        output.pop("findings", None)
    return output


def render_context_guardrail_report(report: dict[str, Any], *, compact: bool = False) -> str:
    lines = [
        "# Context Guardrails",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Scanned: {report.get('scanned_count', 0)}",
        f"- Findings: {report.get('finding_count', 0)}",
        "",
    ]
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    if findings:
        lines.append("## Findings")
        lines.append("")
        for item in findings:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('path')}:{item.get('line')}` {item.get('issue')}")
                if not compact:
                    lines.append(f"  Snippet: {item.get('snippet')}")
                    lines.append(f"  Fix: {item.get('fix')}")
    else:
        lines.append("No unsafe raw navigation JSON references found.")
    return "\n".join(lines)


def _default_startup_context(root: Path) -> dict[str, Any]:
    from repo_support import repo_qol_daily

    return repo_qol_daily.startup_context_report(root, compact=True)


def _default_next_action(root: Path) -> dict[str, Any]:
    from repo_support import repo_qol_context

    return repo_qol_context.next_action_report(root, fast=True)


def _default_clean_context(root: Path) -> dict[str, Any]:
    from repo_support import repo_qol_daily

    return repo_qol_daily.clean_context_proof_report(root)


def _owner_capsules(root: Path) -> dict[str, Any]:
    owners_dir = root / "automations" / "navigation" / "artifacts" / "maps" / "owners"
    count = len(list(owners_dir.glob("*.md"))) if owners_dir.is_dir() else 0
    return {
        "status": "present" if count else "missing",
        "count": count,
        "path": repo.relative(root, owners_dir),
    }


def _context_trace(report: dict[str, Any]) -> dict[str, Any]:
    trace = report.get("context_trace")
    if isinstance(trace, dict):
        return trace
    navigation = report.get("navigation")
    if isinstance(navigation, dict):
        return {
            "status": navigation.get("status", "unknown"),
            "read_first": navigation.get("read_first", ""),
            "read_now": ["AGENTS.md", navigation.get("read_first", "")],
            "skip_raw_json": sorted(RAW_NAVIGATION_JSON_PATHS),
            "next_command": navigation.get("next_command", ""),
        }
    return {}


def _trace_issues(label: str, report: dict[str, Any]) -> list[str]:
    trace = _context_trace(report)
    issues: list[str] = []
    read_first = str(trace.get("read_first") or "")
    skip_raw = {str(item).replace("\\", "/") for item in trace.get("skip_raw_json", []) if str(item).strip()} if isinstance(trace.get("skip_raw_json"), list) else set()
    read_now = {str(item).replace("\\", "/") for item in trace.get("read_now", []) if str(item).strip()} if isinstance(trace.get("read_now"), list) else set()
    if read_first != HANDOFF_PATH:
        issues.append(f"{label} context trace does not route first to {HANDOFF_PATH}")
    if not RAW_NAVIGATION_JSON_PATHS.issubset(skip_raw):
        issues.append(f"{label} context trace does not skip raw navigation JSON")
    if "AGENTS.md" not in read_now:
        issues.append(f"{label} context trace does not include AGENTS.md in read_now")
    return issues


def context_use_check_report(
    root: Path,
    *,
    startup_factory: Callable[..., dict[str, Any]] | None = None,
    next_action_factory: Callable[..., dict[str, Any]] | None = None,
    clean_context_factory: Callable[..., dict[str, Any]] | None = None,
    guardrail_factory: Callable[..., dict[str, Any]] | None = None,
    owner_capsule_factory: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    startup = (startup_factory or _default_startup_context)(root)
    next_action = (next_action_factory or _default_next_action)(root)
    clean_context = (clean_context_factory or _default_clean_context)(root)
    guardrails = (guardrail_factory or context_guardrail_report)(root)
    owner_capsules = (owner_capsule_factory or _owner_capsules)(root)
    issues: list[str] = []
    if not bool(startup.get("ok", True)):
        issues.append("startup-context did not pass")
    if not bool(next_action.get("ok", True)):
        issues.append("next-action did not pass")
    if not bool(clean_context.get("ok", True)):
        issues.append("clean-context-proof did not pass")
    if not bool(guardrails.get("ok", True)):
        issues.append("context-guardrails did not pass")
    if owner_capsules.get("status") != "present":
        issues.append("owner capsules are missing")
    issues.extend(_trace_issues("startup-context", startup))
    issues.extend(_trace_issues("next-action", next_action))
    next_command = str(next_action.get("next_command") or "").strip()
    if not next_command:
        issues.append("next-action does not expose a next_command")
    elapsed_ms = repo_command_metrics.elapsed_ms_since(started)
    effective_next_command = (
        "none"
        if not issues
        else "fix context-use-check issues, then rerun python -B .agents/manage.py context-use-check --summary --compact --format json"
    )
    report = {
        "schema_version": 1,
        "tool": "skill-manager.context-use-check",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "summary": {
            "source_orientation": HANDOFF_PATH,
            "startup_status": startup.get("status", "unknown"),
            "next_action_status": next_action.get("status", "unknown"),
            "clean_context_status": clean_context.get("status", "unknown"),
            "guardrail_status": guardrails.get("status", "unknown"),
            "owner_capsule_status": owner_capsules.get("status", "unknown"),
            "owner_capsule_count": owner_capsules.get("count", 0),
            "next_action_next_command": next_command,
            "next_command_scope": "next-action-proof-only",
            "effective_next_command": effective_next_command,
        },
        "issues": list(dict.fromkeys(issues)),
        "checks": [
            {
                "name": "startup-context-trace",
                "status": "passed" if not _trace_issues("startup-context", startup) else "failed",
            },
            {
                "name": "next-action-trace",
                "status": "passed" if not _trace_issues("next-action", next_action) else "failed",
            },
            {"name": "clean-context-proof", "status": clean_context.get("status", "unknown")},
            {"name": "context-guardrails", "status": guardrails.get("status", "unknown")},
            {"name": "owner-capsules", "status": owner_capsules.get("status", "unknown")},
        ],
        "startup_context_trace": _context_trace(startup),
        "next_action_context_trace": _context_trace(next_action),
        "latency_budget": repo_command_metrics.timing_budget_report("context-use-check", elapsed_ms),
        "next_command": effective_next_command,
        "boundary": (
            "This proves command packets expose low-context routing and raw-JSON skips. "
            "It cannot prove a proprietary agent runtime loaded every instruction."
        ),
    }
    return report


def summarize_context_use_check_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.context-use-check"),
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", "unknown"),
        "summary": report.get("summary", {}),
        "issue_count": len(report.get("issues", []) if isinstance(report.get("issues"), list) else []),
        "issues": report.get("issues", []),
        "latency_budget": report.get("latency_budget", {}),
        "next_command": report.get("next_command", ""),
        "boundary": report.get("boundary", ""),
    }
    if not compact:
        output["checks"] = report.get("checks", [])
        output["startup_context_trace"] = report.get("startup_context_trace", {})
        output["next_action_context_trace"] = report.get("next_action_context_trace", {})
    elif not output["issues"]:
        output.pop("issues", None)
    return repo_command_metrics.attach_output_budget(output, "context-use-check")


def render_context_use_check_report(report: dict[str, Any]) -> str:
    lines = [
        "# Context Use Check",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Source orientation: `{report.get('summary', {}).get('source_orientation', '')}`",
        f"- Owner capsules: {report.get('summary', {}).get('owner_capsule_status', 'unknown')}",
        f"- Next command: `{report.get('next_command', '')}`",
    ]
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in issues)
    lines.extend(["", f"Boundary: {report.get('boundary', '')}", ""])
    return "\n".join(lines)
