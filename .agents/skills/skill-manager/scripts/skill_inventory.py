#!/usr/bin/env python3
"""Build a compact SBOM-style inventory for accepted skills."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import analyze_location
import measure_skill_budget
import skill_manager_common as common
import triage_candidates


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def inventory_skill(
    skill_dir: Path,
    root: Path,
    *,
    analysis: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest, _manifest_error = common.load_skill_manifest(skill_dir)
    metadata, _metadata_error = common.parse_frontmatter_file(skill_dir / "SKILL.md")
    if analysis is None:
        analysis = analyze_location.analyze_target(
            str(skill_dir),
            skill_dir.resolve(),
            max_files=5000,
            max_text_files=800,
        )
    files = common.iter_files(skill_dir, max_files=5000)
    components: dict[str, int] = {}
    for path in files:
        if not path.is_file():
            continue
        rel = common.relative(skill_dir, path)
        bucket = common.file_bucket(rel)
        components[bucket] = components.get(bucket, 0) + 1

    evidence = analysis.get("evidence", [])
    services = sorted(
        {
            str(item.get("snippet") or item.get("signal"))
            for item in evidence
            if isinstance(item, dict) and item.get("category") in {"network", "uploads"}
        }
    )
    risk_flags = common.manifest_risk_flags(manifest)
    risk_profile = common.manifest_risk_profile(manifest)
    quality = common.routing_example_counts(manifest)
    if budget is None:
        budget = measure_skill_budget.measure_skill(skill_dir, root)
    largest = budget.get("largest_files", [])[0] if isinstance(budget.get("largest_files"), list) and budget.get("largest_files") else {}

    return {
        "name": (metadata or {}).get("name", skill_dir.name),
        "path": common.relative(root, skill_dir) if common.is_inside(skill_dir, root) else str(skill_dir),
        "version": str((manifest or {}).get("version", "")),
        "summary": str((manifest or {}).get("summary", "")),
        "compatibility": (manifest or {}).get("compatibility", {}),
        "dependencies": common.manifest_dependency_labels(manifest),
        "components": dict(sorted(components.items())),
        "services": services,
        "risk": {
            "profile": risk_profile,
            "declared_flags": risk_flags,
            "detected_evidence_count": len(evidence) if isinstance(evidence, list) else 0,
        },
        "local_ai": common.local_ai_use_case_summary(manifest),
        "quality": quality,
        "budget": {
            "skill_md_words": budget["skill_md"]["words"],
            "skill_md_status": budget["skill_md"]["status"],
            "routing_load_words": budget["routing_load"]["words"],
            "guidance_load_words": budget["guidance_load"]["words"],
            "tool_load_words": budget["tool_load"]["words"],
            "total_text_words": budget["total_text"]["words"],
            "largest_file": str(largest.get("path", "")) if isinstance(largest, dict) else "",
            "largest_file_words": int(largest.get("words", 0) or 0) if isinstance(largest, dict) else 0,
            "tool_hotspots": budget.get("tool_hotspots", []),
        },
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).expanduser().resolve() if args.root else default_root()
    if args.all:
        skill_dirs = common.discover_skill_dirs(root)
    else:
        skill_dirs = [Path(args.skill).expanduser().resolve()]
    skills = [inventory_skill(skill_dir, root) for skill_dir in skill_dirs]
    duplicate_triggers: list[dict[str, Any]] = []
    if args.all:
        groups: dict[str, list[str]] = {}
        for skill_dir in skill_dirs:
            metadata, _error = common.parse_frontmatter_file(skill_dir / "SKILL.md")
            key = triage_candidates.trigger_key(
                skill_dir.name,
                str((metadata or {}).get("description", "")),
            )
            if key:
                groups.setdefault(key, []).append(common.relative(root, skill_dir))
        duplicate_triggers = [
            {"trigger_key": key, "count": len(paths), "paths": paths}
            for key, paths in sorted(groups.items())
            if len(paths) > 1
        ]
    return {
        "version": 1,
        "format": "skill-inventory",
        "root": str(root),
        "skills": skills,
        "duplicate_trigger_groups": duplicate_triggers,
    }


def architecture_recommendations(rows: list[dict[str, Any]], duplicate_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    if duplicate_groups:
        recommendations.append(
            {
                "id": "resolve-duplicate-triggers",
                "decision": "combine-or-merge",
                "reason": "Duplicate trigger groups make routing less deterministic; merge overlap into one owner before adding skills.",
                "evidence_count": len(duplicate_groups),
            }
        )
    else:
        recommendations.append(
            {
                "id": "keep-topology",
                "decision": "rework-existing-skills",
                "reason": "No duplicate trigger groups detected; improve existing skill scripts, tests, docs, and evals before adding or combining skills.",
                "evidence_count": len(rows),
            }
        )

    hotspots: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: int(item.get("tool_load_words", 0) or 0), reverse=True):
        tool_hotspots = row.get("tool_hotspots") if isinstance(row.get("tool_hotspots"), list) else []
        for hotspot in tool_hotspots[:2]:
            if not isinstance(hotspot, dict):
                continue
            hotspots.append(
                {
                    "name": row.get("name", ""),
                    "path": hotspot.get("path", ""),
                    "words": hotspot.get("words", 0),
                    "action": hotspot.get("action", ""),
                }
            )
        if len(hotspots) >= 5:
            break
    if hotspots:
        recommendations.append(
            {
                "id": "split-tool-hotspots",
                "decision": "rework-multiple-files",
                "reason": "Large executable/test surfaces should be split around stable support-module or self-test boundaries when touched.",
                "targets": hotspots[:5],
            }
        )

    if any(str(row.get("skill_md_status", "")) in {"warn", "fail"} for row in rows):
        recommendations.append(
            {
                "id": "trim-skill-md",
                "decision": "move-instructions-to-docs-or-scripts",
                "reason": "At least one SKILL.md exceeds the lightweight routing budget.",
                "targets": [
                    {
                        "name": row.get("name", ""),
                        "skill_md_words": row.get("skill_md_words", 0),
                        "skill_md_status": row.get("skill_md_status", ""),
                    }
                    for row in rows
                    if str(row.get("skill_md_status", "")) in {"warn", "fail"}
                ],
            }
        )

    recommendations.append(
        {
            "id": "new-skill-gate",
            "decision": "add-skill-only-for-new-owner",
            "reason": "Create a new skill only for a distinct trigger, risk/dependency profile, validation path, and stable owner boundary.",
        }
    )
    return recommendations


def summarize_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    skills = report.get("skills") if isinstance(report.get("skills"), list) else []
    duplicate_groups = report.get("duplicate_trigger_groups") if isinstance(report.get("duplicate_trigger_groups"), list) else []
    risk_profiles: dict[str, int] = {}
    dependency_counts: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        risk = skill.get("risk") if isinstance(skill.get("risk"), dict) else {}
        budget = skill.get("budget") if isinstance(skill.get("budget"), dict) else {}
        profile = str(risk.get("profile", "unspecified") or "unspecified")
        risk_profiles[profile] = risk_profiles.get(profile, 0) + 1
        for dependency in skill.get("dependencies", []) if isinstance(skill.get("dependencies"), list) else []:
            label = str(dependency)
            dependency_counts[label] = dependency_counts.get(label, 0) + 1
        rows.append(
            {
                "name": skill.get("name", ""),
                "path": skill.get("path", ""),
                "risk_profile": profile,
                "dependency_count": len(skill.get("dependencies", [])) if isinstance(skill.get("dependencies"), list) else 0,
                "component_count": sum(int(value) for value in (skill.get("components") or {}).values())
                if isinstance(skill.get("components"), dict)
                else 0,
                "skill_md_words": int(budget.get("skill_md_words", 0) or 0),
                "skill_md_status": str(budget.get("skill_md_status", "")),
                "routing_load_words": int(budget.get("routing_load_words", 0) or 0),
                "guidance_load_words": int(budget.get("guidance_load_words", 0) or 0),
                "tool_load_words": int(budget.get("tool_load_words", 0) or 0),
                "total_text_words": int(budget.get("total_text_words", 0) or 0),
                "largest_file": str(budget.get("largest_file", "")),
                "largest_file_words": int(budget.get("largest_file_words", 0) or 0),
                "tool_hotspots": budget.get("tool_hotspots", []),
            }
        )
    top_by_text = sorted(rows, key=lambda item: int(item.get("total_text_words", 0)), reverse=True)[:5]
    risk_rows = [row for row in rows if row.get("risk_profile") not in {"read-only", "local-write", ""}]
    summary: dict[str, Any] = {
        "version": report.get("version", 1),
        "format": "skill-inventory-summary",
        "root": report.get("root", ""),
        "summary": {
            "skill_count": len(rows),
            "duplicate_trigger_group_count": len(duplicate_groups),
            "risk_profiles": dict(sorted(risk_profiles.items())),
            "dependency_count": len(dependency_counts),
        },
        "duplicate_trigger_groups": duplicate_groups,
        "top_by_text": top_by_text,
        "risk_rows": risk_rows,
        "architecture_recommendations": architecture_recommendations(rows, duplicate_groups),
    }
    if not compact:
        summary["skills"] = rows
    else:
        summary.pop("root", None)
        if not duplicate_groups:
            summary.pop("duplicate_trigger_groups", None)
        summary["top_by_text"] = [
            {
                "name": row.get("name", ""),
                "total_text_words": row.get("total_text_words", 0),
                "component_count": row.get("component_count", 0),
            }
            for row in top_by_text
        ]
        summary["risk_rows"] = [
            {
                "name": row.get("name", ""),
                "risk_profile": row.get("risk_profile", ""),
                "component_count": row.get("component_count", 0),
                "dependency_count": row.get("dependency_count", 0),
            }
            for row in risk_rows[:5]
        ]
    return summary


def render_markdown(report: dict[str, Any]) -> str:
    if report.get("format") == "skill-inventory-summary":
        return render_summary_markdown(report)
    lines = ["# Skill Inventory", ""]
    for skill in report["skills"]:
        lines.extend(
            [
                f"## {skill['name']}",
                "",
                f"- Version: `{skill['version'] or 'unversioned'}`",
                f"- Path: `{skill['path']}`",
                f"- Dependencies: {', '.join(skill['dependencies']) or 'none'}",
                f"- Risk profile: `{skill['risk']['profile'] or 'unspecified'}`",
                f"- Risk flags: {', '.join(skill['risk']['declared_flags']) or 'none'}",
                f"- SKILL.md words: {skill['budget']['skill_md_words']} ({skill['budget']['skill_md_status']})",
                f"- Components: {', '.join(f'{key}={value}' for key, value in skill['components'].items()) or 'none'}",
                "",
            ]
        )
    if report.get("duplicate_trigger_groups"):
        lines.extend(["## Duplicate Trigger Groups", ""])
        for group in report["duplicate_trigger_groups"]:
            lines.append(
                f"- {group['count']} skills share `{group['trigger_key']}`: "
                + ", ".join(f"`{path}`" for path in group["paths"][:5])
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def render_summary_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Skill Inventory Summary",
        "",
        f"- Skills: {summary.get('skill_count', 0)}",
        f"- Duplicate trigger groups: {summary.get('duplicate_trigger_group_count', 0)}",
        f"- Dependencies: {summary.get('dependency_count', 0)}",
    ]
    risk_profiles = summary.get("risk_profiles") if isinstance(summary.get("risk_profiles"), dict) else {}
    if risk_profiles:
        lines.append(
            "- Risk profiles: "
            + ", ".join(f"{key}={value}" for key, value in sorted(risk_profiles.items()))
        )

    recommendations = report.get("architecture_recommendations")
    if isinstance(recommendations, list) and recommendations:
        lines.extend(["", "## Architecture Recommendations", ""])
        for item in recommendations:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('id', '')}`: `{item.get('decision', '')}` - {item.get('reason', '')}"
            )

    top_by_text = report.get("top_by_text") if isinstance(report.get("top_by_text"), list) else []
    if top_by_text:
        lines.extend(["", "## Top By Text", ""])
        for item in top_by_text[:5]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('name', '')}`: {item.get('total_text_words', 0)} words"
            )
    skills = report.get("skills") if isinstance(report.get("skills"), list) else []
    if skills:
        lines.extend(
            [
                "",
                "## Skills",
                "",
                "| Skill | Risk | Components | Dependencies | SKILL.md | Total Text |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        for item in skills:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"| `{item.get('name', '')}` | `{item.get('risk_profile', '')}` | "
                f"{item.get('component_count', 0)} | {item.get('dependency_count', 0)} | "
                f"{item.get('skill_md_words', 0)} | {item.get('total_text_words', 0)} |"
            )
    return "\n".join(lines).rstrip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to script parent")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--skill", help="skill folder to inventory")
    target.add_argument("--all", action="store_true", help="inventory all accepted skills")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format")
    parser.add_argument("--summary", action="store_true", help="emit aggregate counts and top rows")
    parser.add_argument("--compact", action="store_true", help="with --summary, omit per-skill rows")
    return parser


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    report = build_report(args)
    if args.summary or args.compact:
        report = summarize_report(report, compact=args.compact)
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
