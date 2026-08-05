#!/usr/bin/env python3
"""Portable-constraints checks for changed harness files."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from repo_support import repo_common as repo
from repo_support import repo_policy


TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".txt",
    ".ps1",
    ".sh",
    ".bat",
    ".cmd",
}
SCAN_PREFIXES = (
    "AGENTS.md",
    ".agents/local-ai",
    ".agents/local-ai.json",
    ".agents/skills/local-ai-helper/",
    ".agents/skills/agent-benchmarking/",
    ".agents/skills/skill-manager/scripts/repo_support/repo_portable",
    ".agents/skills/skill-manager/scripts/repo_support/repo_setup",
    ".agents/skills/skill-manager/scripts/repo_support/repo_cli_parser.py",
    "automations/local-ai-benchmark-workflow/",
    "automations/agent-benchmarking/",
    "docs/harness/",
    "docs/operations/token-savings.md",
)
ALLOW_CONTEXT_RE = re.compile(
    r"(benchmark-only|candidate-only|watchlist|explicit local opt-in|not a default|not retained|skipped|blocked|historical|advisory|experimental)",
    re.I,
)
RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "specific-hardware-default",
        "severity": "error",
        "pattern": re.compile(
            r"\b(?:default|defaults|require|requires|required|must|always|only|optimized for|target)\b"
            r"[^\n]{0,100}\b(strix\s+halo|strix|rocm|hip|cuda|nvidia|radeon|apple\s+silicon|metal)\b|"
            r"\b(strix\s+halo|strix)\b",
            re.I,
        ),
        "reason": "hardware-specific language must be benchmark-only, opt-in, or clearly non-default",
        "allow_context": True,
    },
    {
        "id": "personal-absolute-path",
        "severity": "error",
        "pattern": re.compile(r"\b(?:[A-Z]:\\(?:Users\\[^\\\s`]+|Projects\\Skills|AgentValidation)|/home/[^/\s`]+|/Users/[^/\s`]+)", re.I),
        "reason": "personal absolute paths are not portable defaults",
        "allow_context": False,
    },
    {
        "id": "admin-install-command",
        "severity": "error",
        "pattern": re.compile(r"\b(sudo\s+(?:apt|dnf|yum|pacman|zypper)|choco\s+install|winget\s+install(?![^\n]*--scope\s+user)|brew\s+install)\b", re.I),
        "reason": "admin or machine-level install command needs a user-writable or portable alternative",
        "allow_context": True,
    },
    {
        "id": "local-machine-assumption",
        "severity": "warning",
        "pattern": re.compile(r"\b(my machine|this machine|local machine|on my pc|on my workstation)\b", re.I),
        "reason": "local-machine assumptions need a portable constraint or environment probe",
        "allow_context": True,
    },
)


def _changed_files(root: Path) -> list[str]:
    files: set[str] = set()
    for args in (
        ("diff", "--name-only"),
        ("diff", "--cached", "--name-only"),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        status, lines = repo.git_output(root, *args)
        if status == 0:
            files.update(lines)
    return sorted(path.replace("\\", "/") for path in files if not path.startswith(repo.DEFAULT_CHANGED_IGNORE_PREFIXES))


def _is_scannable(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized.startswith("automations/navigation/artifacts/maps/"):
        return False
    if normalized.endswith("/repo_portability.py"):
        return False
    if normalized.endswith("/run_self_tests.py"):
        return False
    if not any(normalized == prefix or normalized.startswith(prefix) for prefix in SCAN_PREFIXES):
        return False
    return Path(normalized).suffix.lower() in TEXT_SUFFIXES or normalized == "AGENTS.md"


def _line_allowed(line: str, *, allow_context: bool) -> bool:
    return allow_context and bool(ALLOW_CONTEXT_RE.search(line))


def _path_allowed(path: str, rule_id: str) -> bool:
    if rule_id == "specific-hardware-default" and "watchlist" in path.lower():
        return True
    return False


def portability_report(root: Path, *, paths: list[str] | None = None) -> dict[str, Any]:
    root = root.resolve()
    selected = [path.replace("\\", "/") for path in (paths or _changed_files(root))]
    scanned: list[str] = []
    skipped: list[dict[str, str]] = []
    findings: list[dict[str, Any]] = []
    for rel in sorted(dict.fromkeys(selected)):
        if not _is_scannable(rel):
            skipped.append({"path": rel, "reason": "outside portable-constraints scan surface"})
            continue
        path = root / rel
        if not path.is_file():
            skipped.append({"path": rel, "reason": "not a readable file"})
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            skipped.append({"path": rel, "reason": "not utf-8 text"})
            continue
        except OSError as exc:
            skipped.append({"path": rel, "reason": str(exc)})
            continue
        scanned.append(rel)
        for line_number, line in enumerate(text.splitlines(), start=1):
            for rule in RULES:
                match = rule["pattern"].search(line)
                rule_id = str(rule["id"])
                if (
                    not match
                    or _path_allowed(rel, rule_id)
                    or _line_allowed(line, allow_context=bool(rule.get("allow_context")))
                ):
                    continue
                findings.append(
                    {
                        "rule": rule_id,
                        "severity": str(rule["severity"]),
                        "path": rel,
                        "line": line_number,
                        "match": match.group(0),
                        "snippet": line.strip()[
                            :repo_policy.int_value(root, "limits.output.evidence_snippet_chars")
                        ],
                        "reason": str(rule["reason"]),
                    }
                )
    error_count = sum(1 for item in findings if item.get("severity") == "error")
    warning_count = sum(1 for item in findings if item.get("severity") == "warning")
    return {
        "schema_version": 1,
        "tool": "skill-manager.portable-constraints",
        "ok": error_count == 0,
        "status": "passed" if error_count == 0 else "failed",
        "summary": {
            "scanned_count": len(scanned),
            "skipped_count": len(skipped),
            "finding_count": len(findings),
            "error_count": error_count,
            "warning_count": warning_count,
        },
        "findings": findings,
        "scanned": scanned,
        "skipped": skipped,
        "next_command": "fix portable-constraints findings, then rerun python -B .agents/manage.py portable-constraints --changed --summary --compact --format json"
        if error_count
        else "none, portable constraints passed",
    }


def summarize_portability_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    output: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.portable-constraints"),
        "ok": bool(report.get("ok")),
        "status": report.get("status", "unknown"),
        "summary": report.get("summary", {}),
        "findings": report.get("findings", []),
        "next_command": report.get("next_command", ""),
    }
    if not compact:
        output["scanned"] = report.get("scanned", [])
        output["skipped"] = report.get("skipped", [])
    return output


def render_portability_report(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Portable Constraints",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Scanned: {summary.get('scanned_count', 0)}",
        f"- Findings: {summary.get('finding_count', 0)} "
        f"({summary.get('error_count', 0)} errors, {summary.get('warning_count', 0)} warnings)",
    ]
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    if findings:
        lines.extend(["", "## Findings", ""])
        for item in findings:
            if isinstance(item, dict):
                lines.append(
                    f"- `{item.get('path')}:{item.get('line')}` {item.get('severity')} "
                    f"{item.get('rule')}: {item.get('reason')}"
                )
                if item.get("snippet"):
                    lines.append(f"  `{item.get('snippet')}`")
    lines.extend(["", f"Next command: `{report.get('next_command')}`", ""])
    return "\n".join(lines)


def portability_command(args: argparse.Namespace, root: Path) -> int:
    paths = [str(item) for item in getattr(args, "paths", None) or []]
    report = portability_report(root, paths=paths or None)
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        report = summarize_portability_report(report, compact=bool(getattr(args, "compact", False)))
    if getattr(args, "output_format", "markdown") == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_portability_report(report))
    return 0 if bool(report.get("ok")) else 1
