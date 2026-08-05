#!/usr/bin/env python3
"""Compare two local skill folders without writing files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import analyze_location
import skill_manager_common as common
import validate_skill


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def skill_sections(path: Path) -> dict[str, str]:
    skill_path = path / "SKILL.md"
    if not skill_path.exists():
        return {}
    body = common.strip_frontmatter(common.read_text(skill_path))
    sections: dict[str, list[str]] = {"preamble": []}
    current = "preamble"
    for line in body.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            current = match.group(2).strip()
            sections.setdefault(current, [])
        sections[current].append(line)
    return {name: text_hash("\n".join(lines)) for name, lines in sections.items()}


def skill_snapshot(path: Path) -> dict[str, Any]:
    manifest, manifest_error = common.load_skill_manifest(path)
    metadata, metadata_error = common.parse_frontmatter_file(path / "SKILL.md")
    analysis = analyze_location.analyze_target(
        str(path),
        path.resolve(),
        max_files=5000,
        max_text_files=800,
    )
    validation_errors, validation_warnings = validate_skill.validate_skill(path)
    evidence = analysis.get("evidence", [])
    risk_categories = sorted(
        {
            str(item.get("category"))
            for item in evidence
            if isinstance(item, dict) and str(item.get("category"))
        }
    )
    dependency_labels = common.manifest_dependency_labels(manifest)
    if not dependency_labels:
        dependencies = analysis.get("dependencies", [])
        dependency_labels = [str(item) for item in dependencies] if isinstance(dependencies, list) else []

    return {
        "path": str(path),
        "metadata": metadata or {},
        "metadata_error": metadata_error,
        "manifest": manifest or {},
        "manifest_error": manifest_error,
        "version": str((manifest or {}).get("version", "")),
        "dependencies": sorted(set(dependency_labels)),
        "risk_flags": sorted(set(common.manifest_risk_flags(manifest) + risk_categories)),
        "file_hashes": common.collect_file_hashes(path, max_files=5000),
        "sections": skill_sections(path),
        "validation": {
            "ok": not validation_errors,
            "errors": validation_errors,
            "warnings": validation_warnings,
        },
        "analysis": {
            "network_signals": analysis.get("network_signals", []),
            "credential_signals": analysis.get("credential_signals", []),
            "disallowed_scripts": analysis.get("disallowed_scripts", []),
            "evidence": evidence,
        },
    }


def version_delta(old_version: str, new_version: str) -> dict[str, Any]:
    old_tuple = common.semver_tuple(old_version)
    new_tuple = common.semver_tuple(new_version)
    result: dict[str, Any] = {
        "old": old_version,
        "new": new_version,
        "valid": bool(old_tuple and new_tuple),
        "direction": "unknown",
    }
    if not old_tuple or not new_tuple:
        return result
    if new_tuple > old_tuple:
        result["direction"] = "increased"
    elif new_tuple == old_tuple:
        result["direction"] = "same"
    else:
        result["direction"] = "decreased"

    if new_tuple[0] > old_tuple[0]:
        result["change_class"] = "breaking"
    elif new_tuple[1] > old_tuple[1]:
        result["change_class"] = "feature"
    elif new_tuple[2] > old_tuple[2]:
        result["change_class"] = "fix"
    elif new_tuple == old_tuple:
        result["change_class"] = "metadata"
    else:
        result["change_class"] = "unknown"
    return result


def compare_paths(old_path: Path, new_path: Path) -> dict[str, Any]:
    old = skill_snapshot(old_path)
    new = skill_snapshot(new_path)
    old_hashes = old["file_hashes"]
    new_hashes = new["file_hashes"]
    assert isinstance(old_hashes, dict)
    assert isinstance(new_hashes, dict)

    old_files = set(old_hashes)
    new_files = set(new_hashes)
    added = sorted(new_files - old_files)
    removed = sorted(old_files - new_files)
    changed = sorted(
        path for path in old_files & new_files if old_hashes[path] != new_hashes[path]
    )
    unchanged = sorted(
        path for path in old_files & new_files if old_hashes[path] == new_hashes[path]
    )

    old_deps = set(old["dependencies"])
    new_deps = set(new["dependencies"])
    old_risk = set(old["risk_flags"])
    new_risk = set(new["risk_flags"])
    version = version_delta(str(old["version"]), str(new["version"]))
    change_class = classify_change(version, old_deps, new_deps, old_risk, new_risk, changed)
    section_delta = compare_sections(old["sections"], new["sections"])
    behavior_delta = classify_file_delta(added, removed, changed)
    warnings = semver_warnings(version, change_class, added, removed, changed)
    decision = recommend_decision(new, added, removed, changed, old_deps, new_deps, old_risk, new_risk, version)

    return {
        "old": {
            "path": old["path"],
            "version": old["version"],
            "validation": old["validation"],
        },
        "new": {
            "path": new["path"],
            "version": new["version"],
            "validation": new["validation"],
        },
        "version_delta": version,
        "change_class": change_class,
        "files": {
            "added": added,
            "removed": removed,
            "changed": changed,
            "unchanged_count": len(unchanged),
        },
        "behavior_delta": behavior_delta,
        "section_delta": section_delta,
        "dependencies": {
            "added": sorted(new_deps - old_deps),
            "removed": sorted(old_deps - new_deps),
            "unchanged": sorted(old_deps & new_deps),
        },
        "risk": {
            "added": sorted(new_risk - old_risk),
            "removed": sorted(old_risk - new_risk),
            "unchanged": sorted(old_risk & new_risk),
        },
        "warnings": warnings,
        "recommended_decision": decision,
    }


def compare_sections(old_sections: object, new_sections: object) -> dict[str, list[str]]:
    old = old_sections if isinstance(old_sections, dict) else {}
    new = new_sections if isinstance(new_sections, dict) else {}
    old_keys = set(str(key) for key in old)
    new_keys = set(str(key) for key in new)
    return {
        "added": sorted(new_keys - old_keys),
        "removed": sorted(old_keys - new_keys),
        "changed": sorted(
            key for key in old_keys & new_keys if old.get(key) != new.get(key)
        ),
    }


def classify_file_delta(
    added: list[str], removed: list[str], changed: list[str]
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for label, paths in (("added", added), ("removed", removed), ("changed", changed)):
        buckets: dict[str, int] = {}
        for path in paths:
            bucket = common.file_bucket(path)
            buckets[bucket] = buckets.get(bucket, 0) + 1
        result[label] = dict(sorted(buckets.items()))
    return result


def semver_warnings(
    version: dict[str, Any],
    change_class: str,
    added: list[str],
    removed: list[str],
    changed: list[str],
) -> list[str]:
    warnings: list[str] = []
    version_class = str(version.get("change_class", "unknown"))
    direction = str(version.get("direction", "unknown"))
    has_file_changes = bool(added or removed or changed)
    if has_file_changes and direction == "same" and change_class not in {"metadata", "docs"}:
        warnings.append("Files changed without a SemVer increase.")
    if change_class in {"risk", "dependency"} and version_class not in {"feature", "breaking"}:
        warnings.append(f"{change_class} change should normally be at least a minor SemVer bump.")
    if removed and version_class != "breaking":
        warnings.append("Removed files may be breaking; review whether a major SemVer bump is required.")
    if "SKILL.md" in changed and version_class == "fix":
        warnings.append("SKILL.md changed in a patch release; confirm this is only a compatible fix.")
    return warnings


def classify_change(
    version: dict[str, Any],
    old_deps: set[str],
    new_deps: set[str],
    old_risk: set[str],
    new_risk: set[str],
    changed: list[str],
) -> str:
    if new_risk != old_risk:
        return "risk"
    if new_deps != old_deps:
        return "dependency"
    version_class = str(version.get("change_class", "unknown"))
    if version_class in common.CHANGE_CLASSES and version_class != "metadata":
        return version_class
    if any(path.startswith("docs/") for path in changed) and not any(
        path == "SKILL.md" or path.startswith("scripts/") for path in changed
    ):
        return "docs"
    if "module.json" in changed:
        return "metadata"
    if changed:
        return "unknown"
    return "metadata"


def recommend_decision(
    new: dict[str, Any],
    added: list[str],
    removed: list[str],
    changed: list[str],
    old_deps: set[str],
    new_deps: set[str],
    old_risk: set[str],
    new_risk: set[str],
    version: dict[str, Any],
) -> dict[str, str]:
    validation = new.get("validation", {})
    if isinstance(validation, dict) and validation.get("errors"):
        return {
            "decision": "keep-staged",
            "reason": "new skill version does not pass validation",
        }
    if version.get("direction") == "decreased":
        return {
            "decision": "keep-staged",
            "reason": "new SemVer is lower than old SemVer",
        }
    if removed:
        return {
            "decision": "merge",
            "reason": "new version removes files that may contain local work",
        }
    if new_risk - old_risk:
        return {
            "decision": "merge",
            "reason": "new version introduces additional risk flags",
        }
    if new_deps - old_deps:
        return {
            "decision": "merge",
            "reason": "new version introduces additional dependencies",
        }
    if version.get("change_class") == "breaking":
        return {
            "decision": "merge",
            "reason": "new version is a breaking SemVer change",
        }
    if added or changed:
        return {
            "decision": "override",
            "reason": "new version validates and does not introduce removals, dependencies, or risk flags",
        }
    return {
        "decision": "override",
        "reason": "folders are equivalent",
    }


def render_markdown(report: dict[str, Any]) -> str:
    decision = report["recommended_decision"]
    files = report["files"]
    behavior = report["behavior_delta"]
    sections = report["section_delta"]
    dependencies = report["dependencies"]
    risk = report["risk"]
    lines = [
        "# Skill Version Comparison",
        "",
        f"- Old: `{report['old']['path']}` ({report['old']['version'] or 'unversioned'})",
        f"- New: `{report['new']['path']}` ({report['new']['version'] or 'unversioned'})",
        f"- Change class: `{report['change_class']}`",
        f"- Recommended decision: `{decision['decision']}` - {decision['reason']}",
        "",
        "## Validation",
        "",
        f"- Old valid: {report['old']['validation']['ok']}",
        f"- New valid: {report['new']['validation']['ok']}",
    ]
    new_errors = report["new"]["validation"]["errors"]
    if new_errors:
        lines.extend(f"- New error: {error}" for error in new_errors)
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])

    lines.extend(
        [
            "",
            "## File Delta",
            "",
            f"- Added: {len(files['added'])}",
            f"- Removed: {len(files['removed'])}",
            f"- Changed: {len(files['changed'])}",
            f"- Unchanged: {files['unchanged_count']}",
        ]
    )
    for label in ("added", "removed", "changed"):
        values = files[label][:12]
        if values:
            lines.append(f"- {label.title()} files: " + ", ".join(f"`{item}`" for item in values))
            if len(files[label]) > 12:
                lines.append(f"- ... {len(files[label]) - 12} more {label} files omitted.")

    lines.extend(["", "## Behavior Delta", ""])
    for label in ("added", "removed", "changed"):
        values = behavior.get(label, {})
        compact = ", ".join(f"{key}: {value}" for key, value in values.items()) if values else "none"
        lines.append(f"- {label.title()}: {compact}")

    lines.extend(["", "## Section Delta", ""])
    for label in ("added", "removed", "changed"):
        values = sections.get(label, [])
        lines.append(f"- {label.title()}: {', '.join(values) or 'none'}")

    lines.extend(
        [
            "",
            "## Dependency Delta",
            "",
            f"- Added: {', '.join(dependencies['added']) or 'none'}",
            f"- Removed: {', '.join(dependencies['removed']) or 'none'}",
            "",
            "## Risk Delta",
            "",
            f"- Added: {', '.join(risk['added']) or 'none'}",
            f"- Removed: {', '.join(risk['removed']) or 'none'}",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", help="old skill folder")
    parser.add_argument("new", help="new skill folder")
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    return parser


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    old_path = Path(args.old).expanduser().resolve()
    new_path = Path(args.new).expanduser().resolve()
    if not old_path.exists():
        raise SystemExit(f"old skill folder not found: {old_path}")
    if not new_path.exists():
        raise SystemExit(f"new skill folder not found: {new_path}")

    report = compare_paths(old_path, new_path)
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
