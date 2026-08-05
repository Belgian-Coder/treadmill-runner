#!/usr/bin/env python3
"""Core orchestration for skill-manager location analysis."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True

from analysis_support import analysis_common as common
from analysis_support.audit import audit_candidate
from analysis_support.briefing import build_review_packet
from analysis_support.dependency_parsers import dependency_report, summarize_purpose
from analysis_support.import_review import scan_import_review
from analysis_support.report_rendering import render_report_from_analysis
from analysis_support.signal_scanner import improvement_suggestions, scan_text_signals, script_report
import skill_manager_common as skill_common


def classify_skill_or_workflow_fit(root: Path, files: list[Path]) -> dict[str, object]:
    relative_paths = {common.relative(root if root.is_dir() else root.parent, path).replace("\\", "/") for path in files}
    workflow_signals = 0
    skill_signals = 0
    reasons: list[str] = []

    if "WORKFLOW.md" in relative_paths:
        workflow_signals += 2
        reasons.append("WORKFLOW.md suggests a workflow entry point")
    if "START.md" in relative_paths:
        workflow_signals += 2
        reasons.append("root START.md suggests an obsolete workflow entry point")
    if "module.json" in relative_paths:
        workflow_signals += 2
        skill_signals += 2
        reasons.append("module.json suggests canonical module metadata")
    if any(path.startswith(("runs/", "templates/")) for path in relative_paths):
        workflow_signals += 1
        reasons.append("workflow-owned run/template folders are present")
    if "SKILL.md" in relative_paths:
        skill_signals += 3
        reasons.append("SKILL.md suggests a reusable skill candidate")
    if any(path.startswith("scripts/") for path in relative_paths):
        skill_signals += 1

    if workflow_signals > skill_signals:
        fit = "workflow"
        recommendation = "Use workflow-manager or merge into an existing workflow before creating a skill."
    elif skill_signals > workflow_signals:
        fit = "skill"
        recommendation = "Review overlap with accepted skills before creating or promoting a skill."
    elif workflow_signals and skill_signals:
        fit = "mixed"
        recommendation = "Split workflow orchestration from reusable skill behavior before promotion."
    else:
        fit = "source"
        recommendation = "Classify the source before deciding whether it belongs in a skill, workflow, docs, or neither."

    return {
        "fit": fit,
        "skill_signals": skill_signals,
        "workflow_signals": workflow_signals,
        "reasons": reasons[:6],
        "recommendation": recommendation,
    }


def promotion_decision_options() -> list[dict[str, str]]:
    return [
        {
            "decision": "reject",
            "use_when": "Unsafe, unlicensed, too broad, or not useful enough.",
            "next_step": "Do not import; record the blocker.",
        },
        {
            "decision": "keep-staged",
            "use_when": "Potentially useful but missing facts, validation, or ownership clarity.",
            "next_step": "Leave outside accepted skills until evidence improves.",
        },
        {
            "decision": "merge",
            "use_when": "Behavior overlaps an accepted skill.",
            "next_step": "Patch the existing skill instead of creating a new one.",
        },
        {
            "decision": "rewrite-first",
            "use_when": "External/generated source is useful but not repo-shaped.",
            "next_step": "Rewrite into the accepted operating pattern before promotion.",
        },
        {
            "decision": "promote",
            "use_when": "Narrow, safe, validated, non-duplicative skill candidate.",
            "next_step": "Add manifest, run validation, then sync generated artifacts.",
        },
    ]


def analyze_target(
    target: str,
    root: Path | None,
    max_files: int,
    max_text_files: int,
    review_profile: str = "basic",
) -> dict[str, object]:
    if common.is_url(target):
        location_type = common.classify_location(target, None)
        return {
            "version": 2,
            "location": target,
            "type": location_type,
            "result": (
                "Remote locations are not fetched by this offline analyzer. Create or provide "
                "a local review copy first, then analyze the local folder."
            ),
            "suggested_next_steps": [
                "Clone, download, or otherwise stage the source locally.",
                "Preserve license and notice files.",
                "Re-run this analyzer against the local staged folder.",
            ],
        }

    assert root is not None
    files = common.iter_files(root, max_files=max_files)
    base = root if root.is_dir() else root.parent
    location_type = common.classify_location(target, root)
    suffix_counts = Counter(path.suffix.lower() or "<none>" for path in files)
    top_dirs = Counter(
        common.relative(base, path).split("/", 1)[0] if "/" in common.relative(base, path) else "."
        for path in files
    )
    manifests, dependencies = dependency_report(root, files)
    scripts, disallowed_scripts, conversion_plans = script_report(root, files)
    security, network, evidence = scan_text_signals(root, files, max_text_files=max_text_files)
    static_audit = audit_candidate(root, files, max_text_files=max_text_files)
    manifest, _manifest_error = (
        skill_common.load_skill_manifest(root)
        if root.is_dir() and (root / "module.json").exists()
        else (None, None)
    )
    declared_risks = set(skill_common.manifest_risk_flags(manifest))
    for item in evidence:
        if isinstance(item, dict):
            item["declared"] = str(item.get("category")) in declared_risks
    suggestions = improvement_suggestions(
        root, manifests, dependencies, disallowed_scripts, security, network, evidence
    )

    analysis_report = {
        "version": 2,
        "location": str(root),
        "type": location_type,
        "review_profile": review_profile,
        "files_scanned": len(files),
        "purpose": summarize_purpose(root, files),
        "review_packet": build_review_packet(
            root,
            files,
            manifests,
            dependencies,
            max_files=max_files,
        ),
        "structure": {
            "top_level_areas": common.counter_items(top_dirs, limit=8),
            "common_file_types": common.counter_items(suffix_counts, limit=8),
            "manifests": manifests,
        },
        "dependencies": dependencies,
        "scripts": scripts,
        "disallowed_scripts": disallowed_scripts,
        "conversion_plans": conversion_plans,
        "network_signals": network,
        "credential_signals": security,
        "evidence": evidence,
        "static_audit": static_audit,
        "skill_or_workflow_fit": classify_skill_or_workflow_fit(root, files),
        "promotion_decision_options": promotion_decision_options(),
        "improvement_opportunities": suggestions,
        "recommended_review_decision": [
            "Use this report as input, not as the final decision.",
            "Promote only after manual review against `.agents/skills/skill-manager/docs/skill-design-guide.md` and `.agents/skills/skill-manager/docs/intake-and-review.md`.",
            "Prefer rewrite-first for external or generated candidates; merge into an existing skill when triggers overlap.",
        ],
    }
    if review_profile == "import":
        analysis_report["import_review"] = scan_import_review(
            root,
            files,
            disallowed_scripts=disallowed_scripts,
            evidence=evidence,
            max_text_files=max_text_files,
        )
    return analysis_report


def summarize_report(analysis: dict[str, object], *, compact: bool = False) -> dict[str, object]:
    if "result" in analysis:
        return {
            "version": analysis.get("version", 2),
            "format": "skill-manager.analysis-summary",
            "location": analysis.get("location", ""),
            "type": analysis.get("type", ""),
            "result": analysis.get("result", ""),
            "suggested_next_steps": analysis.get("suggested_next_steps", []),
        }
    structure = analysis.get("structure") if isinstance(analysis.get("structure"), dict) else {}
    static_audit = analysis.get("static_audit") if isinstance(analysis.get("static_audit"), dict) else {}
    fit = analysis.get("skill_or_workflow_fit") if isinstance(analysis.get("skill_or_workflow_fit"), dict) else {}
    dependencies = analysis.get("dependencies") if isinstance(analysis.get("dependencies"), list) else []
    scripts = analysis.get("scripts") if isinstance(analysis.get("scripts"), list) else []
    disallowed = analysis.get("disallowed_scripts") if isinstance(analysis.get("disallowed_scripts"), list) else []
    network = analysis.get("network_signals") if isinstance(analysis.get("network_signals"), list) else []
    credentials = analysis.get("credential_signals") if isinstance(analysis.get("credential_signals"), list) else []
    evidence = analysis.get("evidence") if isinstance(analysis.get("evidence"), list) else []
    opportunities = (
        analysis.get("improvement_opportunities")
        if isinstance(analysis.get("improvement_opportunities"), list)
        else []
    )
    findings = static_audit.get("findings") if isinstance(static_audit.get("findings"), list) else []
    summary: dict[str, object] = {
        "version": analysis.get("version", 2),
        "format": "skill-manager.analysis-summary",
        "location": analysis.get("location", ""),
        "type": analysis.get("type", ""),
        "review_profile": analysis.get("review_profile", ""),
        "files_scanned": analysis.get("files_scanned", 0),
        "dependency_count": len(dependencies),
        "script_count": len(scripts),
        "disallowed_script_count": len(disallowed),
        "network_signal_count": len(network),
        "credential_signal_count": len(credentials),
        "evidence_count": len(evidence),
        "improvement_count": len(opportunities),
        "manifest_count": len(structure.get("manifests", []) if isinstance(structure.get("manifests"), list) else []),
        "static_audit_verdict": static_audit.get("verdict", ""),
        "static_audit_finding_count": len(findings),
        "skill_or_workflow_fit": {
            "fit": fit.get("fit", ""),
            "skill_signals": fit.get("skill_signals", 0),
            "workflow_signals": fit.get("workflow_signals", 0),
            "recommendation": fit.get("recommendation", ""),
        },
        "recommended_review_decision": analysis.get("recommended_review_decision", []),
    }
    if compact:
        summary.pop("location", None)
        fit_summary = summary.get("skill_or_workflow_fit")
        if isinstance(fit_summary, dict):
            fit_summary.pop("recommendation", None)
        if summary.get("recommended_review_decision"):
            summary["recommended_review_decision_count"] = len(
                summary.get("recommended_review_decision", [])
                if isinstance(summary.get("recommended_review_decision"), list)
                else []
            )
            summary.pop("recommended_review_decision", None)
    if not compact:
        summary.update(
            {
                "purpose": analysis.get("purpose", []),
                "top_level_areas": structure.get("top_level_areas", []),
                "common_file_types": structure.get("common_file_types", []),
                "manifests": structure.get("manifests", []),
                "static_audit": {
                    "verdict": static_audit.get("verdict", ""),
                    "finding_count": len(findings),
                    "summary": static_audit.get("summary", ""),
                },
                "dependencies": dependencies[:40],
                "scripts": scripts[:40],
                "disallowed_scripts": disallowed[:40],
                "improvement_opportunities": opportunities,
            }
        )
    return summary


def render_summary_markdown(summary: dict[str, object]) -> str:
    if "result" in summary:
        return "\n".join(
            [
                "# Location Analysis Summary",
                "",
                f"- Location: `{summary.get('location', '')}`",
                f"- Type: {summary.get('type', '')}",
                f"- Result: {summary.get('result', '')}",
            ]
        )
    fit = summary.get("skill_or_workflow_fit") if isinstance(summary.get("skill_or_workflow_fit"), dict) else {}
    lines = [
        "# Location Analysis Summary",
        "",
        f"- Location: `{summary.get('location', '')}`",
        f"- Type: {summary.get('type', '')}",
        f"- Files scanned: {summary.get('files_scanned', 0)}",
        f"- Scripts: {summary.get('script_count', 0)}",
        f"- Dependencies: {summary.get('dependency_count', 0)}",
        f"- Evidence entries: {summary.get('evidence_count', 0)}",
        f"- Static audit: {summary.get('static_audit_verdict', '')} ({summary.get('static_audit_finding_count', 0)} findings)",
        f"- Fit: {fit.get('fit', '')}",
        f"- Recommendation: {fit.get('recommendation', '')}",
    ]
    return "\n".join(lines)


def render_report(
    target: str,
    root: Path | None,
    max_files: int,
    max_text_files: int,
    review_profile: str = "basic",
) -> str:
    analysis = analyze_target(
        target,
        root,
        max_files=max_files,
        max_text_files=max_text_files,
        review_profile=review_profile,
    )
    return render_report_from_analysis(analysis)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze a local folder or file as a skill candidate without "
            "fetching remote content or writing files."
        )
    )
    parser.add_argument("location", help="local folder/file path, or remote URL to stage first")
    parser.add_argument("--max-files", type=int, default=2500)
    parser.add_argument("--max-text-files", type=int, default=400)
    parser.add_argument(
        "--review-profile",
        choices=("basic", "import"),
        default="basic",
        help="review strictness; import adds supply-chain-oriented warnings",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
        help="output format; default: markdown",
    )
    parser.add_argument("--summary", action="store_true", help="emit counts and decision facts")
    parser.add_argument("--compact", action="store_true", help="with --summary, omit nested evidence sections")
    parser.add_argument("--output", help="optional report path")
    return parser


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()

    root: Path | None = None
    if not common.is_url(args.location):
        root = Path(args.location).expanduser().resolve()
        if not root.exists():
            print(f"ERROR: local path not found: {root}", file=sys.stderr)
            return 1

    analysis = analyze_target(
        args.location,
        root,
        args.max_files,
        args.max_text_files,
        review_profile=args.review_profile,
    )
    if args.summary or args.compact:
        analysis = summarize_report(analysis, compact=args.compact)
    if args.output_format == "json":
        report = json.dumps(analysis, indent=2, sort_keys=True)
    elif args.summary or args.compact:
        report = render_summary_markdown(analysis)
    else:
        report = render_report_from_analysis(analysis)

    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report + "\n", encoding="utf-8", newline="\n")
        print(f"Wrote {args.output_format} analysis report: {output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
