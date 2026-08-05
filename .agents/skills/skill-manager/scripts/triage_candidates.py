#!/usr/bin/env python3
"""Rank candidate skill folders with compact, offline heuristics."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import skill_manager_common as common
from repo_support import repo_policy
import analyze_location

RISK_TERMS = {
    "credentials": re.compile(r"\b(api[_-]?key|token|secret|password|credential)\b", re.IGNORECASE),
    "destructive": re.compile(r"\b(rm\s+-rf|delete|remove-item|shutil\.rmtree|drop database)\b", re.IGNORECASE),
    "installs": re.compile(r"\b(pip install|npm install|uv add|yarn add|pnpm add)\b", re.IGNORECASE),
    "network": re.compile(r"\b(https?://|curl|wget|fetch\(|requests\.|httpx\.)\b", re.IGNORECASE),
    "production_writes": re.compile(r"\b(production|prod)\b.*\b(write|delete|deploy|publish|migrate)\b", re.IGNORECASE),
    "uploads": re.compile(r"\b(upload|publish|deploy|sync)\b", re.IGNORECASE),
}
BROAD_TRIGGER = re.compile(
    r"\b(always|anything|everything|expert|all tasks|any task|general purpose)\b",
    re.IGNORECASE,
)
TRIGGER_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "use",
    "using",
    "when",
    "with",
}


def iter_skill_files(root: Path, max_candidates: int) -> list[Path]:
    results: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in common.IGNORED_DIRS and not name.endswith(".egg-info")
        ]
        if "SKILL.md" in filenames:
            results.append(Path(current_root) / "SKILL.md")
            if len(results) >= max_candidates:
                break
    return sorted(results, key=lambda path: path.as_posix().lower())


def scan_disallowed_scripts(skill_dir: Path, max_files: int = 120) -> list[str]:
    hits: list[str] = []
    checked = 0
    for path in common.iter_files(skill_dir, max_files=max_files):
        checked += 1
        if path.suffix.lower() in common.DISALLOWED_SCRIPT_SUFFIXES:
            hits.append(common.relative(skill_dir, path))
        if checked >= max_files:
            break
    return hits


def risk_flags(text: str) -> list[str]:
    flags = []
    lowered = text.lower()
    if any(phrase in lowered for phrase in ("do not upload", "must not upload", "never upload")):
        lowered = lowered.replace("upload", "")
    for name, pattern in RISK_TERMS.items():
        if pattern.search(lowered):
            flags.append(name)
    return sorted(set(flags))


def trigger_key(_name: str, description: str) -> str:
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", description.lower())
        if len(token) > 2 and token not in TRIGGER_STOP_WORDS
    ]
    return " ".join(sorted(set(tokens))[:12])


def accepted_skill_triggers(repo_root: Path) -> list[dict[str, str]]:
    skills_root = repo_root / ".agents" / "skills"
    if not skills_root.exists():
        return []
    rows: list[dict[str, str]] = []
    for skill_md in sorted(skills_root.glob("*/SKILL.md"), key=lambda item: item.as_posix().lower()):
        metadata, _error = common.parse_frontmatter_file(skill_md)
        if not metadata:
            continue
        rows.append(
            {
                "name": str(metadata.get("name", skill_md.parent.name)),
                "path": common.relative(repo_root, skill_md.parent),
                "trigger_key": trigger_key(str(metadata.get("name", "")), str(metadata.get("description", ""))),
            }
        )
    return rows


def overlap_scores(candidate: dict[str, Any], accepted: list[dict[str, str]]) -> list[dict[str, Any]]:
    candidate_terms = set(str(candidate.get("trigger_key", "")).split())
    if not candidate_terms:
        return []
    scores: list[dict[str, Any]] = []
    for row in accepted:
        accepted_terms = set(str(row.get("trigger_key", "")).split())
        if not accepted_terms:
            continue
        shared = sorted(candidate_terms & accepted_terms)
        score = round(len(shared) / max(1, min(len(candidate_terms), len(accepted_terms))), 3)
        if score >= 0.25:
            scores.append(
                {
                    "skill": row["name"],
                    "path": row["path"],
                    "score": score,
                    "shared_terms": shared[:8],
                }
            )
    return sorted(scores, key=lambda item: (-item["score"], item["skill"]))[:5]


def score_candidate(skill_file: Path, root: Path, review_profile: str = "basic") -> dict[str, Any]:
    skill_dir = skill_file.parent
    text = common.read_text(skill_file, limit=80_000)
    metadata, error = common.parse_frontmatter_text(text)
    metadata = metadata or {}
    name = metadata.get("name", skill_dir.name)
    description = metadata.get("description", "")
    words = common.word_count(text)
    risks = risk_flags(text)
    disallowed = scan_disallowed_scripts(skill_dir)
    score = 50
    reasons: list[str] = []

    if error:
        score -= 30
        reasons.append(error)
    else:
        score += 15
    name_max_chars = repo_policy.int_value(root, "limits.skill.name_max_chars")
    if common.SKILL_NAME_PATTERN.match(name) and len(name) <= name_max_chars:
        score += 8
    else:
        score -= 12
        reasons.append("invalid skill name")
    description_min_chars = repo_policy.int_value(
        root, "limits.skill.candidate_description_min_chars"
    )
    description_max_chars = repo_policy.int_value(
        root, "limits.skill.candidate_description_max_chars"
    )
    if description_min_chars <= len(description) <= description_max_chars:
        score += 12
    else:
        score -= 8
        reasons.append("description length needs review")
    if BROAD_TRIGGER.search(description):
        score -= 20
        reasons.append("broad trigger language")
    size_status = repo_policy.skill_word_status(root, words)
    if size_status == "fail":
        score -= 20
        reasons.append("oversized SKILL.md")
    elif size_status == "warn":
        score -= 8
        reasons.append("large SKILL.md")
    if risks:
        score -= 12 * len(risks)
        reasons.append(f"risk signals: {', '.join(risks)}")
    if disallowed:
        score -= 20
        reasons.append("disallowed script files")

    candidate = {
        "name": name,
        "path": common.relative(root, skill_dir),
        "score": max(0, min(100, score)),
        "description": description,
        "skill_words": words,
        "risk_flags": risks,
        "disallowed_scripts": disallowed[:8],
        "reasons": reasons,
        "duplicate_key": (
            f"{name.lower()}::{description.lower()[:repo_policy.int_value(root, 'limits.skill.duplicate_description_chars')]}"
        ),
        "trigger_key": trigger_key(str(name), str(description)),
    }
    if review_profile == "import":
        analysis = analyze_location.analyze_target(
            str(skill_dir),
            skill_dir.resolve(),
            max_files=2500,
            max_text_files=160,
            review_profile="import",
        )
        review = analysis.get("import_review", {})
        if isinstance(review, dict):
            candidate["import_review"] = {
                "profile": review.get("profile", "import"),
                "status": review.get("status", "unknown"),
                "warning_count": review.get("warning_count", 0),
                "warning_categories": sorted(
                    {
                        str(item.get("category"))
                        for item in review.get("warnings", [])
                        if isinstance(item, dict)
                    }
                ),
            }
    return candidate


def triage(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).expanduser().resolve()
    repo_root = Path(getattr(args, "repo_root", "") or Path.cwd()).expanduser().resolve()
    accepted = accepted_skill_triggers(repo_root)
    skill_files = iter_skill_files(root, args.max_candidates)
    review_profile = getattr(args, "review_profile", "basic")
    candidates = [score_candidate(path, root, review_profile=review_profile) for path in skill_files]
    duplicates: dict[str, list[str]] = defaultdict(list)
    trigger_duplicates: dict[str, list[str]] = defaultdict(list)
    risks: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        duplicates[candidate["duplicate_key"]].append(candidate["path"])
        if candidate.get("trigger_key"):
            trigger_duplicates[str(candidate["trigger_key"])].append(candidate["path"])
        for risk in candidate["risk_flags"]:
            risks[risk].append(candidate["path"])
    duplicate_groups = [
        {"count": len(paths), "paths": paths[:10]}
        for paths in duplicates.values()
        if len(paths) > 1
    ]
    duplicate_trigger_groups = [
        {"trigger_key": key, "count": len(paths), "paths": paths[:10]}
        for key, paths in trigger_duplicates.items()
        if len(paths) > 1
    ]
    ranked = sorted(candidates, key=lambda item: (-int(item["score"]), item["path"]))
    for candidate in ranked:
        candidate["overlap_scores"] = overlap_scores(candidate, accepted)
        candidate["split_suggestions"] = split_suggestions(candidate)
        candidate.pop("duplicate_key", None)
        candidate.pop("trigger_key", None)
    return {
        "version": 1,
        "root": str(root),
        "review_profile": review_profile,
        "candidates_scanned": len(candidates),
        "returned": min(args.limit, len(ranked)),
        "top_candidates": ranked[: args.limit],
        "duplicate_groups": duplicate_groups[: args.limit],
        "duplicate_trigger_groups": sorted(
            duplicate_trigger_groups,
            key=lambda item: (-int(item["count"]), str(item["trigger_key"])),
        )[: args.limit],
        "risk_groups": {
            key: {"count": len(values), "examples": values[:10]}
            for key, values in sorted(risks.items())
        },
    }


def split_suggestions(candidate: dict[str, Any]) -> list[str]:
    suggestions: list[str] = []
    risks = set(candidate.get("risk_flags", []))
    if len(risks) >= 2:
        suggestions.append("Split high-risk setup/network behavior from low-risk guidance.")
    description = str(candidate.get("description", ""))
    if len(set(trigger_key(str(candidate.get("name", "")), description).split())) >= 10:
        suggestions.append("Review whether broad triggers should become narrower skill or workflow surfaces.")
    if candidate.get("disallowed_scripts"):
        suggestions.append("Group shell/batch/PowerShell conversion into Python helpers before promotion.")
    return suggestions


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Candidate Triage",
        "",
        f"- Root: `{report['root']}`",
        f"- Review profile: {report.get('review_profile', 'basic')}",
        f"- Candidates scanned: {report['candidates_scanned']}",
        "",
        "## Top Candidates",
        "",
    ]
    for candidate in report["top_candidates"]:
        lines.append(
            f"- {candidate['score']:>3} `{candidate['path']}` ({candidate['name']}): "
            f"{'; '.join(candidate['reasons']) or 'ready for deeper review'}"
        )
    if report["risk_groups"]:
        lines.extend(["", "## Risk Groups", ""])
        for key, value in report["risk_groups"].items():
            lines.append(f"- {key}: {value['count']}")
    if report["duplicate_groups"]:
        lines.extend(["", "## Duplicate Groups", ""])
        for group in report["duplicate_groups"][:10]:
            lines.append(f"- {group['count']} candidates: {', '.join(group['paths'][:3])}")
    if report.get("duplicate_trigger_groups"):
        lines.extend(["", "## Duplicate Trigger Groups", ""])
        for group in report["duplicate_trigger_groups"][:10]:
            lines.append(
                f"- {group['count']} candidates share trigger terms "
                f"`{group['trigger_key']}`: {', '.join(group['paths'][:3])}"
            )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="folder containing candidate skill folders")
    parser.add_argument("--limit", type=int, default=50, help="number of top candidates to return")
    parser.add_argument("--max-candidates", type=int, default=5000, help="maximum SKILL.md files to scan")
    parser.add_argument(
        "--review-profile",
        choices=("basic", "import"),
        default="basic",
        help="include richer import-review packet for each ranked candidate",
    )
    parser.add_argument("--repo-root", help="accepted-skills repo root for overlap scoring")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format")
    return parser


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    report = triage(args)
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
