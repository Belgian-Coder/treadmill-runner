#!/usr/bin/env python3
"""Audit accepted skills for deterministic, script-backed agent guidance."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import skill_manager_common as common
from repo_support import repo_policy

sys.dont_write_bytecode = True

REQUIRED_HEADINGS = [
    "Goal",
    "Workflow",
    "Rules",
    "Validation",
    "Stop Rules",
    "Completion Contract",
]
COMMAND_PATTERN = re.compile(r"python\s+-B\s+\.agents/manage\.py|python\s+-B\s+\.agents\\manage\.py")


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[4]


def heading_names(text: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(r"^##\s+(.+?)\s*$", text, flags=re.MULTILINE)
    ]


def relative_to_repo(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def quality_eval_count(manifest: dict[str, Any] | None) -> int:
    if not manifest:
        return 0
    quality = manifest.get("quality")
    if not isinstance(quality, dict):
        return 0
    suites = quality.get("eval_suites")
    return len(suites) if isinstance(suites, list) else 0


def fallback_mentions(manifest: dict[str, Any] | None) -> list[str]:
    missing: list[str] = []
    fallback_terms = (
        "fallback",
        "deterministic",
        "advisory",
        "suggestion",
        "suggestions",
        "evidence",
        "cache",
        "unchanged",
        "read-only",
        "report",
        "no source edits",
        "never edits",
    )
    for item in common.local_ai_use_cases(manifest):
        use_case_id = str(item.get("id", "")).strip() or "<unknown>"
        guardrail = str(item.get("guardrail", "")).lower()
        if not any(term in guardrail for term in fallback_terms):
            missing.append(use_case_id)
    return missing


def audit_skill(root: Path, skill_dir: Path) -> dict[str, Any]:
    skill_md = skill_dir / "SKILL.md"
    manifest_path = skill_dir / "module.json"
    issues: list[str] = []
    warnings: list[str] = []

    text = common.read_text(skill_md) if skill_md.exists() else ""
    body = common.strip_frontmatter(text)
    headings = heading_names(body)
    missing_headings = [heading for heading in REQUIRED_HEADINGS if heading not in headings]
    if missing_headings:
        issues.append(f"missing required headings: {', '.join(missing_headings)}")

    manifest, manifest_error = common.load_skill_manifest(skill_dir)
    if manifest_error:
        issues.append(f"{manifest_path.name} could not be loaded: {manifest_error}")

    scripts_dir = skill_dir / "scripts"
    scripts = sorted(
        path
        for path in scripts_dir.rglob("*.py")
        if path.is_file() and "__pycache__" not in path.parts
    ) if scripts_dir.exists() else []
    self_tests = scripts_dir / "run_self_tests.py"
    if not scripts:
        issues.append("no Python scripts found")
    if not self_tests.exists():
        issues.append("missing scripts/run_self_tests.py")

    eval_count = quality_eval_count(manifest)
    if eval_count == 0 and (manifest or {}).get("status") == "accepted":
        issues.append("accepted skill has no quality.eval_suites")

    command_refs = len(COMMAND_PATTERN.findall(body))
    script_refs = len(re.findall(r"\bscripts/", body))
    if command_refs == 0 and script_refs == 0:
        warnings.append("SKILL.md does not reference a repo command or script path")

    missing_local_ai_fallback = fallback_mentions(manifest)
    if missing_local_ai_fallback:
        warnings.append(
            "local_ai guardrails should mention deterministic fallback or advisory use for: "
            + ", ".join(missing_local_ai_fallback)
        )

    word_count = common.word_count(text)
    if word_count > repo_policy.int_value(root, "limits.skill.warn_words"):
        warnings.append(repo_policy.tagged_warning(
            "health.skill.words", f"SKILL.md has {word_count} words; consider moving detail to docs"
        ))

    warnings, escalated = repo_policy.classify_warnings(root, warnings)
    issues.extend(escalated)

    return {
        "name": skill_dir.name,
        "path": relative_to_repo(root, skill_dir),
        "ok": not issues,
        "script_count": len(scripts),
        "self_tests": self_tests.exists(),
        "eval_suites": eval_count,
        "required_headings": {
            "present": [heading for heading in REQUIRED_HEADINGS if heading in headings],
            "missing": missing_headings,
        },
        "command_refs": command_refs,
        "script_refs": script_refs,
        "local_ai_use_cases": common.local_ai_use_case_summary(manifest),
        "issues": issues,
        "warnings": warnings,
    }


def discover_targets(root: Path, skill: str | None, all_skills: bool) -> list[Path]:
    if skill:
        target = Path(skill)
        if not target.is_absolute():
            target = root / target
        return [target]
    if all_skills:
        return common.discover_skill_dirs(root)
    return []


def build_report(root: Path, skill: str | None = None, all_skills: bool = False) -> dict[str, Any]:
    targets = discover_targets(root, skill, all_skills)
    items = [audit_skill(root, target) for target in targets]
    issue_count = sum(len(item["issues"]) for item in items)
    warning_count = sum(len(item["warnings"]) for item in items)
    return {
        "schema_version": 1,
        "tool": "skill-manager.audit-skill-determinism",
        "ok": issue_count == 0,
        "status": "passed" if issue_count == 0 else "issues-found",
        "summary": {
            "skills_checked": len(items),
            "skills_with_issues": sum(1 for item in items if item["issues"]),
            "issue_count": issue_count,
            "warning_count": warning_count,
            "script_count": sum(int(item["script_count"]) for item in items),
        },
        "skills": items,
    }


def summarize_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    skills = report.get("skills") if isinstance(report.get("skills"), list) else []
    issue_rows = [
        item
        for item in skills
        if isinstance(item, dict) and (item.get("issues") or item.get("warnings"))
    ]
    summary: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.audit-skill-determinism"),
        "ok": report.get("ok", False),
        "status": report.get("status", "unknown"),
        "summary": report.get("summary", {}),
        "issues": [
            {"skill": item.get("name", ""), "issue": issue}
            for item in issue_rows
            for issue in item.get("issues", [])
        ],
        "warnings": [
            {"skill": item.get("name", ""), "warning": warning}
            for item in issue_rows
            for warning in item.get("warnings", [])
        ],
    }
    if compact:
        if summary["issues"]:
            summary["skills"] = [
                {
                    "name": item.get("name", ""),
                    "issues": item.get("issues", []),
                    "warnings": item.get("warnings", []),
                }
                for item in issue_rows
            ]
        else:
            summary.pop("issues", None)
            summary.pop("warnings", None)
        return summary
    summary["skills"] = [
        {
            "name": item.get("name", ""),
            "script_count": item.get("script_count", 0),
            "self_tests": item.get("self_tests", False),
            "eval_suites": item.get("eval_suites", 0),
            "issues": item.get("issues", []),
            "warnings": item.get("warnings", []),
        }
        for item in skills
        if isinstance(item, dict)
    ]
    return summary


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Skill Determinism Audit",
        "",
        f"- Status: `{report['status']}`",
        f"- Skills checked: {report['summary']['skills_checked']}",
        f"- Issues: {report['summary']['issue_count']}",
        f"- Warnings: {report['summary']['warning_count']}",
        "",
        "## Skill Summary",
        "",
        "| Skill | Scripts | Self-tests | Evals | Missing Headings | Issues |",
        "|---|---:|---|---:|---|---|",
    ]
    for item in report["skills"]:
        missing = ", ".join(item["required_headings"]["missing"]) or "-"
        issues = "; ".join(item["issues"]) or "-"
        self_tests = "yes" if item["self_tests"] else "no"
        lines.append(
            f"| `{item['name']}` | {item['script_count']} | {self_tests} | "
            f"{item['eval_suites']} | {missing} | {issues} |"
        )
    issue_rows = [
        (item["name"], issue)
        for item in report["skills"]
        for issue in item["issues"]
    ]
    if issue_rows:
        lines.extend(["", "## Issues", ""])
        for skill_name, issue in issue_rows:
            lines.append(f"- `{skill_name}`: {issue}")
    warning_rows = [
        (item["name"], warning)
        for item in report["skills"]
        for warning in item["warnings"]
    ]
    if warning_rows:
        lines.extend(["", "## Warnings", ""])
        for skill_name, warning in warning_rows:
            lines.append(f"- `{skill_name}`: {warning}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to this repo")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--skill", help="skill folder to audit")
    target.add_argument("--all", action="store_true", help="audit all accepted skills")
    parser.add_argument("--strict", action="store_true", help="exit nonzero when issues are found")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    parser.add_argument("--summary", action="store_true", help="emit aggregate counts and issue rows")
    parser.add_argument("--compact", action="store_true", help="with --summary, omit passing skill rows")
    return parser


def main() -> int:
    common.require_supported_python()
    parser = build_parser()
    args = parser.parse_args()
    root = Path(args.root).resolve() if args.root else repo_root_from_script()
    report = build_report(root, skill=args.skill, all_skills=args.all)
    if args.summary or args.compact:
        report = summarize_report(report, compact=args.compact)
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 1 if args.strict and not report["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
