#!/usr/bin/env python3
"""Prepare or check a target project's human-owned project context."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import navigation_core

PROJECT_CONTEXT_PATH = Path("docs/project/project-context.md")
PROJECT_CONTEXT_DRAFT_PATH = Path("automations/navigation/artifacts/maps/PROJECT_CONTEXT_DRAFT.md")
CONTEXT_CANDIDATES = (
    PROJECT_CONTEXT_PATH,
    Path("docs/ai/project-context.md"),
    Path("PROJECT_CONTEXT.md"),
)
DETAILED_REQUIRED_HEADINGS = (
    "Project Purpose",
    "Technology Stack",
    "Local Run Commands",
    "Validation Commands",
    "Project And Folder Structure",
    "Architecture And Flow",
    "Data And Persistence",
    "Planning Inputs",
    "External Systems And Credentials",
    "Generated Files And Do Not Edit",
    "Agent Workflow Notes",
    "Freshness",
)
GENERATED_REQUIRED_HEADINGS = (
    "Project Information",
    "Technologies",
    "Structure And Responsibilities",
    "Architecture And Workflow Use",
    "Security And Configuration Notes",
    "Validation And Proof",
    "Freshness",
)
PLACEHOLDER_PATTERN = re.compile(
    r"\b(TODO|TBD|UNKNOWN|FILL IN|REPLACE|NOT YET CONFIRMED)\b"
    r"|\[(TODO|TBD|UNKNOWN|FILL IN|REPLACE|NOT YET CONFIRMED)\]",
    re.IGNORECASE,
)
MATERIALIZED_MERMAID_PATTERN = re.compile(r"Source:\s*\[Mermaid\]\([^)]+\.mmd\)", re.IGNORECASE)


def relpath(target: Path, path: Path) -> str:
    return navigation_core.relpath(target, path)


def detected_commands(scan: dict[str, Any], terms: tuple[str, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    commands = scan.get("commands") if isinstance(scan.get("commands"), list) else []
    for item in commands:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command", ""))
        if any(term in command.lower() for term in terms):
            rows.append(
                {
                    "command": command,
                    "path": str(item.get("path", "")),
                    "source": str(item.get("source", "")),
                }
            )
    return rows[:12]


def folder_entries(scan: dict[str, Any]) -> list[dict[str, Any]]:
    entries = scan.get("entries") if isinstance(scan.get("entries"), list) else []
    folders = [item for item in entries if isinstance(item, dict) and item.get("type") == "folder"]
    return folders[:18]


def mermaid_folder_diagram(folders: list[dict[str, Any]]) -> list[str]:
    lines = ["::: mermaid", "    graph TD;", '      root["Repository root"];']
    for index, item in enumerate(folders[:10], start=1):
        path = str(item.get("path", "")).replace('"', "'")
        responsibility = str(item.get("responsibility", "")).replace('"', "'")
        if not path:
            continue
        lines.append(f'      root --> f{index}["{path}"];')
        if responsibility:
            lines.append(f'      f{index} --> r{index}["{responsibility}"];')
    if len(lines) == 3:
        lines.append('      root --> unknown["Folder structure not confirmed"];')
    lines.append(":::")
    return lines


def render_project_context(scan: dict[str, Any]) -> str:
    frameworks = scan.get("frameworks") if isinstance(scan.get("frameworks"), list) else []
    manifests = scan.get("manifests") if isinstance(scan.get("manifests"), list) else []
    folders = folder_entries(scan)
    run_commands = detected_commands(scan, ("run", "start", "serve", "watch"))
    validation_commands = detected_commands(scan, ("test", "build", "lint", "check", "validate"))
    lines = [
        "# Project Context",
        "",
        "This file is the human-owned project profile used by AI workflows before planning implementation work. Keep generated navigation maps as evidence, but keep final project decisions here.",
        "",
        "- Context status: draft",
        "- Treat this file as authoritative only after the context status is changed to `reviewed` by the project owner or run owner.",
        "",
        "## Project Purpose",
        "",
        "- Product or system purpose: TODO",
        "- Primary users or operators: TODO",
        "- In scope for agents: TODO",
        "- Out of scope for agents: TODO",
        "",
        "## Technology Stack",
        "",
        "### Detected Signals",
        "",
    ]
    lines.extend(f"- {item}" for item in frameworks) if frameworks else lines.append("- No framework signals detected.")
    lines.extend(["", "### Confirmed Stack", ""])
    lines.extend(
        [
            "- Runtime and SDK versions: TODO",
            "- Backend frameworks: TODO",
            "- Frontend frameworks: TODO",
            "- Database and persistence: TODO",
            "- Test frameworks: TODO",
            "- Package managers and lockfiles: TODO",
            "",
            "### Manifests",
            "",
        ]
    )
    if manifests:
        for item in manifests[:20]:
            lines.append(f"- `{item.get('path')}` - {item.get('kind')}")
    else:
        lines.append("- No manifests detected.")
    lines.extend(["", "## Local Run Commands", ""])
    lines.extend(["| Action | Command | Working Directory | Notes |", "|---|---|---|---|"])
    if run_commands:
        for item in run_commands:
            lines.append(f"| detected | `{item['command']}` | . | from `{item['path']}` |")
    lines.extend(
        [
            "| restore/install | TODO | . | required before build |",
            "| start app | TODO | . | local developer run |",
            "| database setup | TODO | . | migrations/seeding when applicable |",
            "",
            "## Validation Commands",
            "",
            "| Check | Command | Required Before Implementation? | Evidence Path |",
            "|---|---|---|---|",
        ]
    )
    if validation_commands:
        for item in validation_commands:
            lines.append(f"| detected | `{item['command']}` | confirm | from `{item['path']}` |")
    lines.extend(
        [
            "| build | TODO | yes | TODO |",
            "| unit tests | TODO | yes | TODO |",
            "| integration/UI tests | TODO | when impacted | TODO |",
            "| lint/static/security checks | TODO | when available | TODO |",
            "",
            "## Project And Folder Structure",
            "",
        ]
    )
    if folders:
        lines.extend(["| Path | Responsibility |", "|---|---|"])
        for item in folders:
            lines.append(f"| `{item.get('path')}/` | {item.get('responsibility')} |")
    else:
        lines.append("- Folder structure not confirmed.")
    lines.extend(["", *mermaid_folder_diagram(folders), "", "## Architecture And Flow", ""])
    lines.extend(
        [
            "::: mermaid",
            "    graph TD;",
            '      request["Work request"] --> context["Read project-context.md"];',
            '      context --> plan["Plan with project commands and constraints"];',
            '      plan --> approval["Approval gate"];',
            '      approval --> implementation["Implementation"];',
            '      implementation --> validation["Run project validation commands"];',
            '      validation --> handoff["Record evidence and handoff"];',
            ":::",
            "",
            "- High-level architecture summary: TODO",
            "- Important module/service connections: TODO",
            "- Low-level connections to inspect before edits: TODO",
            "",
            "## Data And Persistence",
            "",
            "- Database engine: TODO",
            "- Known persistent data stores and ownership boundaries: TODO",
            "- Migration command and owner: TODO",
            "- Seed/test data command: TODO",
            "- Root schema or data model documentation: TODO",
            "- Impacted entities and ERD generation: handled in the workflow plan for each specific change, not in this baseline context.",
            "",
            "## Planning Inputs",
            "",
            "- Default project overview for every story, bug, migration, or upgrade: `docs/project/project-context.md`",
            "- First work-item files to read: `ticket-info.md`, `instructions.md`, attachments, and the active run packet when present.",
            "- Root docs to inspect before planning: TODO",
            "- External reference index or local mirrors: TODO",
            "- Reference pattern order: existing project flow first, then approved local reference, then external documentation when allowed.",
            "- Database-related work: inspect the root schema/data documentation first, then put story-specific impacted entities and the Mermaid ERD in `plan.md`.",
            "- Read only the docs, folders, and references relevant to the requested change.",
            "",
            "## External Systems And Credentials",
            "",
            "- Required local services: TODO",
            "- Optional external services: TODO",
            "- Credential names or environment variables, without values: TODO",
            "- Network or external-write boundaries: TODO",
            "",
            "## Generated Files And Do Not Edit",
            "",
            "- Generated folders/files: TODO",
            "- Files agents may edit directly: TODO",
            "- Files requiring generator or migration command: TODO",
            "",
            "## Agent Workflow Notes",
            "",
            "- First files to read after this context: TODO",
            "- Preferred validation order: TODO",
            "- Known slow checks or flaky checks: TODO",
            "- Required diagrams for workflow plans: process, high-level connection, low-level connection, and ERD when the planned change touches data relationships.",
            "",
            "## Freshness",
            "",
            "- Last reviewed: TODO",
            "- Reviewed by: TODO",
            "- Refresh when these files change: project files, dependency manifests, app startup, CI, migrations, database mappings, or run/test commands.",
            "- Navigation evidence command: `python -B automations/navigation/scripts/project_context.py --target . --check`",
        ]
    )
    return "\n".join(lines) + "\n"


def find_context_path(target: Path) -> Path:
    for relative in CONTEXT_CANDIDATES:
        candidate = target / relative
        if candidate.exists():
            return candidate
    return target / PROJECT_CONTEXT_PATH


def missing_headings(text: str, headings: tuple[str, ...]) -> list[str]:
    lower = text.lower()
    return [heading for heading in headings if f"## {heading}".lower() not in lower]


def mermaid_diagram_count(text: str) -> int:
    return text.count("::: mermaid") + len(MATERIALIZED_MERMAID_PATTERN.findall(text))


def context_issues(text: str) -> list[str]:
    issues: list[str] = []
    placeholder_chars = navigation_core.project_policy_int(
        "limits.navigation.project_context_placeholder_chars"
    )
    lower = text.lower()
    detailed_missing = missing_headings(text, DETAILED_REQUIRED_HEADINGS)
    generated_missing = missing_headings(text, GENERATED_REQUIRED_HEADINGS)
    if detailed_missing and generated_missing:
        issues.append(
            "missing required project context headings: "
            + ", ".join(detailed_missing[:6])
            + ("..." if len(detailed_missing) > 6 else "")
        )
    placeholder_lines = []
    in_mermaid = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip().lower()
        if stripped == "::: mermaid":
            in_mermaid = True
            continue
        if in_mermaid and stripped == ":::":
            in_mermaid = False
            continue
        if in_mermaid:
            continue
        if PLACEHOLDER_PATTERN.search(line):
            placeholder_lines.append(f"line {line_number}: {line.strip()[:placeholder_chars]}")
        if len(placeholder_lines) >= 12:
            break
    if placeholder_lines:
        issues.append("placeholder content remains: " + "; ".join(placeholder_lines))
    if mermaid_diagram_count(text) < 2:
        issues.append("expected at least two Mermaid diagrams or materialized Mermaid sources: process/architecture and structure")
    if not re.search(r"context status:\s*(reviewed|generated)\b", lower):
        issues.append("context status must be reviewed or generated before workflow planning")
    if "- Last reviewed:" not in text and "- Last generated:" not in text:
        issues.append("missing freshness review or generation marker")
    return issues


def project_context_report(
    target: Path,
    *,
    write: bool = False,
    overwrite: bool = False,
    check: bool = False,
    max_files: int = 5000,
) -> dict[str, Any]:
    target = target.expanduser().resolve()
    scan = navigation_core.build_scan(target, max_files=max_files)
    draft = render_project_context(scan)
    context_path = find_context_path(target)
    written: list[str] = []
    if write:
        if context_path.exists() and not overwrite:
            draft_path = target / PROJECT_CONTEXT_DRAFT_PATH
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(draft, encoding="utf-8", newline="\n")
            written.append(relpath(target, draft_path))
        else:
            context_path.parent.mkdir(parents=True, exist_ok=True)
            context_path.write_text(draft, encoding="utf-8", newline="\n")
            written.append(relpath(target, context_path))

    exists = context_path.exists()
    text = context_path.read_text(encoding="utf-8-sig") if exists else ""
    issues = context_issues(text) if exists else [f"missing project context: {PROJECT_CONTEXT_PATH.as_posix()}"]
    if not check and write:
        ok = bool(scan.get("ok"))
    else:
        ok = bool(scan.get("ok")) and not issues
    if write:
        next_command = "review and complete docs/project/project-context.md"
        next_command_mode = "human-review"
    elif ok:
        next_command = "python -B automations/navigation/scripts/update_navigation.py --target . --check"
        next_command_mode = "read-only"
    else:
        next_command = "python -B automations/navigation/scripts/project_context.py --target . --write"
        next_command_mode = "write-mode-only"
    return {
        "schema_version": navigation_core.SCHEMA_VERSION,
        "tool": "repo-navigation.project-context",
        "ok": ok,
        "status": "ok" if ok else ("written-with-review-needed" if write else "needs-attention"),
        "target": str(target),
        "context_path": relpath(target, context_path),
        "draft_path": PROJECT_CONTEXT_DRAFT_PATH.as_posix(),
        "written": written,
        "issues": issues,
        "detected": {
            "frameworks": scan.get("frameworks", []),
            "manifest_count": len(scan.get("manifests", [])) if isinstance(scan.get("manifests"), list) else 0,
            "command_count": len(scan.get("commands", [])) if isinstance(scan.get("commands"), list) else 0,
            "folder_count": len(folder_entries(scan)),
        },
        "checks": [
            "required project context headings checked",
            "placeholder content checked",
            "Mermaid process/structure presence checked",
            "freshness marker checked",
        ],
        "skipped": scan.get("skipped", []),
        "next_command": next_command,
        "next_command_mode": next_command_mode,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="read target project root")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write a missing project context or a non-overwriting draft")
    mode.add_argument("--check", action="store_true", help="read-only check: fail when the project context is missing or incomplete")
    parser.add_argument("--overwrite", action="store_true", help="write/overwrite an existing project context when used with --write")
    parser.add_argument("--max-files", type=int, default=5000)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    return parser


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Project Context Check",
        "",
        f"- Target: `{report['target']}`",
        f"- Status: {report['status']}",
        f"- Context: `{report['context_path']}`",
    ]
    written = report.get("written", [])
    if written:
        lines.extend(["", "## Written", ""])
        lines.extend(f"- `{item}`" for item in written)
    issues = report.get("issues", [])
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in issues)
    detected = report.get("detected", {})
    if isinstance(detected, dict):
        lines.extend(["", "## Detected", ""])
        frameworks = detected.get("frameworks", [])
        lines.append(f"- Frameworks: {', '.join(frameworks) if frameworks else 'none'}")
        lines.append(f"- Manifests: {detected.get('manifest_count', 0)}")
        lines.append(f"- Commands: {detected.get('command_count', 0)}")
        lines.append(f"- Folders: {detected.get('folder_count', 0)}")
    label = "Write-mode only next command" if report.get("next_command_mode") == "write-mode-only" else "Next command"
    lines.extend(["", f"{label}: `{report.get('next_command')}`", ""])
    return "\n".join(lines)


def main() -> int:
    navigation_core.require_supported_python()
    args = build_parser().parse_args()
    report = project_context_report(
        Path(args.target),
        write=args.write,
        overwrite=args.overwrite,
        check=args.check,
        max_files=args.max_files,
    )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
