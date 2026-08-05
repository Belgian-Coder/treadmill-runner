#!/usr/bin/env python3
"""Inspect one accepted skill and emit a compact evidence report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import analyze_location
import attest_skill
import measure_skill_budget
import skill_inventory
import skill_manager_common as common
import validate_skill


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def recommendation_list(report: dict[str, Any]) -> list[str]:
    recommendations: list[str] = []
    validation = report["validation"]
    budget = report["budget"]["skill_md"]
    analysis = report["analysis"]
    inventory = report["inventory"]

    if validation["errors"]:
        recommendations.append("Fix validation errors before syncing generated artifacts.")
    if validation["warnings"]:
        recommendations.append("Review validation warnings and decide whether they need edits.")
    if budget["status"] == "fail":
        recommendations.append("Reduce SKILL.md below the hard size limit or add an explicit exception rationale.")
    elif budget["status"] == "warn":
        recommendations.append("Consider moving non-routing detail from SKILL.md into docs.")
    if analysis.get("disallowed_scripts"):
        recommendations.append("Replace disallowed shell, batch, command, or PowerShell scripts with Python helpers.")
    risk = inventory.get("risk", {})
    if risk.get("detected_evidence_count", 0) and not risk.get("declared_flags"):
        recommendations.append("Review detected risk evidence and declare intended behavior in module.json.")
    if not recommendations:
        recommendations.append("No immediate skill issues found; check generated routing/adapters if source files changed.")
    recommendations.append("Run: python -B .agents/manage.py sync-skill-routing --check")
    recommendations.append("Run: python -B .agents/manage.py sync-claude-skills --check")
    recommendations.append(
        "For strict no-write dogfood, stop at --check drift commands; run sync/check only when writes and failure-triage cache are allowed."
    )
    return recommendations


def component_counts(skill_dir: Path) -> tuple[dict[str, int], list[str]]:
    components: dict[str, int] = {}
    scripts: list[str] = []
    for path in common.iter_files(skill_dir, max_files=5000):
        if not path.is_file():
            continue
        rel = common.relative(skill_dir, path)
        bucket = common.file_bucket(rel)
        components[bucket] = components.get(bucket, 0) + 1
        if rel.startswith("scripts/"):
            scripts.append(rel)
    return dict(sorted(components.items())), sorted(scripts)


def build_fast_inventory(
    skill_dir: Path,
    root: Path,
    manifest: dict[str, Any] | None,
    metadata: dict[str, str] | None,
    budget: dict[str, Any],
    components: dict[str, int],
) -> dict[str, Any]:
    return {
        "name": (metadata or {}).get("name", skill_dir.name),
        "path": common.relative(root, skill_dir) if common.is_inside(skill_dir, root) else str(skill_dir),
        "version": str((manifest or {}).get("version", "")),
        "summary": str((manifest or {}).get("summary", "")),
        "compatibility": (manifest or {}).get("compatibility", {}),
        "dependencies": common.manifest_dependency_labels(manifest),
        "components": components,
        "services": [],
        "risk": {
            "profile": common.manifest_risk_profile(manifest),
            "declared_flags": common.manifest_risk_flags(manifest),
            "detected_evidence_count": 0,
        },
        "quality": common.routing_example_counts(manifest),
        "budget": {
            "skill_md_words": budget["skill_md"]["words"],
            "skill_md_status": budget["skill_md"]["status"],
            "total_text_words": budget["total_text"]["words"],
        },
    }


def build_fast_analysis(components: dict[str, int], scripts: list[str]) -> dict[str, Any]:
    return {
        "type": "skill",
        "mode": "fast",
        "files_scanned": sum(components.values()),
        "dependencies": [],
        "scripts": scripts,
        "disallowed_scripts": [],
        "evidence": [],
        "improvement_opportunities": [],
    }


def build_fast_attestation(root: Path, skill_dir: Path) -> dict[str, Any]:
    status = attest_skill.git_value(root, "status", "--short", "--", str(skill_dir))
    return {
        "git": {
            "commit": attest_skill.git_value(root, "rev-parse", "HEAD"),
            "branch": attest_skill.git_value(root, "branch", "--show-current"),
            "dirty_for_skill": bool(status),
        },
        "file_hashes": {},
        "hash_algorithm": "",
    }


def build_report(skill_dir: Path, root: Path, *, fast: bool = False) -> dict[str, Any]:
    skill_dir = skill_dir.expanduser().resolve()
    manifest, manifest_error = common.load_skill_manifest(skill_dir)
    metadata, metadata_error = common.parse_frontmatter_file(skill_dir / "SKILL.md")
    errors, warnings = validate_skill.validate_skill(skill_dir)
    budget = measure_skill_budget.measure_skill(skill_dir, root)
    if fast:
        components, scripts = component_counts(skill_dir)
        analysis = build_fast_analysis(components, scripts)
        inventory = build_fast_inventory(skill_dir, root, manifest, metadata, budget, components)
        attestation = build_fast_attestation(root, skill_dir)
    else:
        analysis = analyze_location.analyze_target(
            str(skill_dir),
            skill_dir,
            max_files=2500,
            max_text_files=400,
        )
        inventory = skill_inventory.inventory_skill(
            skill_dir,
            root,
            analysis=analysis,
            budget=budget,
        )
        attestation = attest_skill.build_attestation(
            skill_dir,
            root,
            validation=(errors, warnings),
        )
    report = {
        "version": 1,
        "format": "skill-inspection",
        "mode": "fast" if fast else "deep",
        "skill": {
            "name": (metadata or {}).get("name", skill_dir.name),
            "path": common.relative(root, skill_dir) if common.is_inside(skill_dir, root) else str(skill_dir),
            "version": str((manifest or {}).get("version", "")),
            "summary": str((manifest or {}).get("summary", "")),
        },
        "manifest_error": manifest_error,
        "metadata_error": metadata_error,
        "validation": {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
        },
        "analysis": {
            "type": analysis.get("type"),
            "mode": analysis.get("mode", "deep"),
            "files_scanned": analysis.get("files_scanned", 0),
            "dependencies": analysis.get("dependencies", []),
            "scripts": analysis.get("scripts", []),
            "disallowed_scripts": analysis.get("disallowed_scripts", []),
            "evidence_count": len(analysis.get("evidence", []))
            if isinstance(analysis.get("evidence", []), list)
            else 0,
            "improvement_opportunities": analysis.get("improvement_opportunities", []),
        },
        "inventory": inventory,
        "budget": {
            "skill_md": budget["skill_md"],
            "routing_load": budget["routing_load"],
            "guidance_load": budget["guidance_load"],
            "tool_load": budget["tool_load"],
            "largest_files": budget["largest_files"][:5],
        },
        "attestation": {
            "git": attestation["git"],
            "files_hashed": len(attestation["file_hashes"]),
            "hash_algorithm": attestation["hash_algorithm"],
        },
    }
    report["context_budget_impact"] = {
        "skill_md_words": budget["skill_md"]["words"],
        "skill_md_status": budget["skill_md"]["status"],
        "routing_load_words": budget["routing_load"]["words"],
        "guidance_load_words": budget["guidance_load"]["words"],
        "tool_load_words": budget["tool_load"]["words"],
        "largest_files": budget["largest_files"][:3],
        "recommendation": context_budget_recommendation(budget),
    }
    report["recommended_next_steps"] = recommendation_list(report)
    return report


def context_budget_recommendation(budget: dict[str, Any]) -> str:
    if budget["skill_md"]["status"] == "fail":
        return "Reduce trigger-loaded SKILL.md before promotion."
    if budget["skill_md"]["status"] == "warn":
        return "Move non-essential guidance from SKILL.md into docs or scripts."
    if budget["guidance_load"]["words"] > 6000 or budget["tool_load"]["words"] > 25000:
        return "Keep SKILL.md compact and load detailed docs or scripts only when needed."
    return "Current trigger-loaded skill context is acceptable."


def summarize_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    validation = report.get("validation") if isinstance(report.get("validation"), dict) else {}
    analysis = report.get("analysis") if isinstance(report.get("analysis"), dict) else {}
    inventory = report.get("inventory") if isinstance(report.get("inventory"), dict) else {}
    budget = report.get("budget") if isinstance(report.get("budget"), dict) else {}
    risk = inventory.get("risk") if isinstance(inventory.get("risk"), dict) else {}
    skill_md = budget.get("skill_md") if isinstance(budget.get("skill_md"), dict) else {}
    routing_load = budget.get("routing_load") if isinstance(budget.get("routing_load"), dict) else {}
    guidance_load = budget.get("guidance_load") if isinstance(budget.get("guidance_load"), dict) else {}
    tool_load = budget.get("tool_load") if isinstance(budget.get("tool_load"), dict) else {}
    attestation = report.get("attestation") if isinstance(report.get("attestation"), dict) else {}
    git = attestation.get("git") if isinstance(attestation.get("git"), dict) else {}
    summary: dict[str, Any] = {
        "version": report.get("version", 1),
        "format": "skill-inspection-summary",
        "ok": bool(validation.get("ok", False)),
        "mode": report.get("mode", ""),
        "skill": report.get("skill", {}),
        "validation": {
            "ok": bool(validation.get("ok", False)),
            "error_count": len(validation.get("errors", []) if isinstance(validation.get("errors"), list) else []),
            "warning_count": len(validation.get("warnings", []) if isinstance(validation.get("warnings"), list) else []),
            "errors": validation.get("errors", []),
            "warnings": validation.get("warnings", []),
        },
        "analysis_counts": {
            "files_scanned": analysis.get("files_scanned", 0),
            "script_count": len(analysis.get("scripts", []) if isinstance(analysis.get("scripts"), list) else []),
            "disallowed_script_count": len(
                analysis.get("disallowed_scripts", []) if isinstance(analysis.get("disallowed_scripts"), list) else []
            ),
            "evidence_count": analysis.get("evidence_count", 0),
        },
        "risk": {
            "profile": risk.get("profile", ""),
            "declared_flags": risk.get("declared_flags", []),
            "detected_evidence_count": risk.get("detected_evidence_count", 0),
        },
        "budget": {
            "skill_md_words": skill_md.get("words", 0),
            "skill_md_status": skill_md.get("status", ""),
            "routing_load_words": routing_load.get("words", 0),
            "guidance_load_words": guidance_load.get("words", 0),
            "tool_load_words": tool_load.get("words", 0),
        },
        "context_budget_impact": report.get("context_budget_impact", {}),
        "recommended_next_steps": report.get("recommended_next_steps", []),
    }
    if compact:
        if not summary["validation"]["errors"]:
            summary["validation"].pop("errors", None)
        if not summary["validation"]["warnings"]:
            summary["validation"].pop("warnings", None)
        skill = summary.get("skill")
        if isinstance(skill, dict):
            skill.pop("summary", None)
        impact = summary.get("context_budget_impact")
        if isinstance(impact, dict):
            compact_files = [
                {"path": row.get("path", ""), "words": row.get("words", 0)}
                for row in impact.get("largest_files", [])
                if isinstance(row, dict)
            ]
            if compact_files:
                impact["largest_files"] = compact_files
            impact.pop("recommendation", None)
        if summary.get("ok"):
            summary.pop("recommended_next_steps", None)
    if not compact:
        summary["attestation"] = {
            "files_hashed": attestation.get("files_hashed", 0),
            "dirty_for_skill": git.get("dirty_for_skill", False),
        }
        summary["largest_files"] = budget.get("largest_files", [])
        summary["components"] = inventory.get("components", {}) if isinstance(inventory, dict) else {}
    return summary


def render_markdown(report: dict[str, Any]) -> str:
    skill = report["skill"]
    validation = report["validation"]
    analysis = report["analysis"]
    budget = report["budget"]
    inventory = report["inventory"]
    lines = [
        "# Skill Inspection",
        "",
        f"- Skill: `{skill['name']}`",
        f"- Version: `{skill['version'] or 'unversioned'}`",
        f"- Path: `{skill['path']}`",
        f"- Mode: `{report['mode']}`",
        f"- Validation: {'ok' if validation['ok'] else 'failed'}",
        f"- Files scanned: {analysis['files_scanned']}",
        f"- Files hashed: {report['attestation']['files_hashed']}",
        f"- Risk profile: `{inventory['risk']['profile'] or 'unspecified'}`",
        f"- Risk flags: {', '.join(inventory['risk']['declared_flags']) or 'none'}",
        f"- Dependencies: {', '.join(inventory['dependencies']) or 'none'}",
        f"- SKILL.md words: {budget['skill_md']['words']} ({budget['skill_md']['status']})",
        f"- Routing load words: {budget['routing_load']['words']}",
        f"- Guidance load words: {budget['guidance_load']['words']}",
        f"- Tool load words: {budget['tool_load']['words']}",
        "",
    ]
    if validation["errors"]:
        lines.extend(["## Validation Errors", ""])
        lines.extend(f"- {error}" for error in validation["errors"])
        lines.append("")
    if validation["warnings"]:
        lines.extend(["## Validation Warnings", ""])
        lines.extend(f"- {warning}" for warning in validation["warnings"])
    lines.append("")
    impact = report.get("context_budget_impact", {})
    if isinstance(impact, dict):
        lines.extend(["## Context Budget Impact", ""])
        lines.append(f"- SKILL.md status: {impact.get('skill_md_status')}")
        lines.append(f"- Routing load words: {impact.get('routing_load_words')}")
        lines.append(f"- Guidance load words: {impact.get('guidance_load_words')}")
        lines.append(f"- Tool load words: {impact.get('tool_load_words')}")
        lines.append(f"- Recommendation: {impact.get('recommendation')}")
        lines.append("")
    lines.extend(["## Signals", ""])
    lines.append(f"- Scripts: {', '.join(f'`{item}`' for item in analysis['scripts']) or 'none'}")
    lines.append(
        "- Disallowed scripts: "
        + (", ".join(f"`{item}`" for item in analysis["disallowed_scripts"]) or "none")
    )
    lines.append(f"- Evidence records: {analysis['evidence_count']}")
    lines.append("")
    lines.extend(["## Largest Files", ""])
    for item in budget["largest_files"]:
        lines.append(f"- `{item['path']}`: {item['words']} words")
    if not budget["largest_files"]:
        lines.append("- none")
    lines.append("")
    lines.extend(["## Recommended Next Steps", ""])
    lines.extend(f"- {item}" for item in report["recommended_next_steps"])
    return "\n".join(lines).rstrip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to script parent")
    parser.add_argument("--skill", required=True, help="skill folder to inspect")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--fast",
        action="store_true",
        help="run validation and budget checks only; skip text evidence scan and file hashing",
    )
    mode.add_argument(
        "--deep",
        action="store_true",
        help="run the full evidence scan and file hash attestation; default behavior",
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    parser.add_argument("--summary", action="store_true", help="emit inspection counts and budget facts")
    parser.add_argument("--compact", action="store_true", help="with --summary, omit nested evidence blocks")
    return parser


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    root = Path(args.root).expanduser().resolve() if args.root else default_root()
    skill_dir = Path(args.skill).expanduser()
    if not skill_dir.is_absolute():
        skill_dir = root / skill_dir
    report = build_report(skill_dir, root, fast=args.fast)
    if args.summary or args.compact:
        report = summarize_report(report, compact=args.compact)
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report["validation"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
