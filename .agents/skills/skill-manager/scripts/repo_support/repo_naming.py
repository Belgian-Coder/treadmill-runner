#!/usr/bin/env python3
"""Skill naming clarity checks for repository doctor commands."""

from __future__ import annotations

import json
import re
from pathlib import Path

from repo_support import repo_policy

KEBAB_CASE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
GENERIC_NAME_TERMS = {"manager", "helper", "processing", "integration", "tool", "tools"}
ACTION_WORDS = {
    "add",
    "analyze",
    "benchmark",
    "check",
    "clone",
    "compare",
    "create",
    "diagnose",
    "export",
    "extract",
    "fetch",
    "import",
    "inspect",
    "manage",
    "orient",
    "pin",
    "prepare",
    "review",
    "route",
    "scan",
    "summarize",
    "validate",
    "validating",
}


def frontmatter_field(skill_md: Path, field: str) -> str:
    try:
        lines = skill_md.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if not lines or lines[0].strip() != "---":
        return ""
    prefix = f"{field}:"
    for line in lines[1:30]:
        if line.strip() == "---":
            return ""
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def manifest_name(skill_dir: Path) -> str:
    try:
        manifest_path = skill_dir / "module.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("id") or data.get("name") or "").strip()


def skill_naming_report(skill_dir: Path) -> dict[str, object]:
    """Return deterministic naming clarity diagnostics for an accepted skill folder."""

    folder_name = skill_dir.name
    skill_md = skill_dir / "SKILL.md"
    fm_name = frontmatter_field(skill_md, "name")
    manifest_skill_name = manifest_name(skill_dir)
    description = frontmatter_field(skill_md, "description")
    words = folder_name.split("-")
    root = repo_policy.project_root(skill_dir)
    min_terms = repo_policy.int_value(root, "limits.skill.name_min_terms")
    max_terms = repo_policy.int_value(root, "limits.skill.name_max_terms")
    warnings: list[str] = []
    checks = {
        "kebab_case": bool(KEBAB_CASE.match(folder_name)),
        "frontmatter_matches_folder": fm_name == folder_name,
        "manifest_matches_folder": manifest_skill_name == folder_name,
        "has_domain_qualifier": len([word for word in words if word not in GENERIC_NAME_TERMS]) >= 1,
        "reasonable_length": min_terms <= len(words) <= max_terms,
        "description_is_routing_trigger": any(word in description.lower() for word in ACTION_WORDS),
    }
    if not checks["kebab_case"]:
        warnings.append("skill folder name must be lowercase kebab-case")
    if fm_name and not checks["frontmatter_matches_folder"]:
        warnings.append("SKILL.md frontmatter name should match the skill folder")
    if manifest_skill_name and not checks["manifest_matches_folder"]:
        warnings.append("module.json id should match the skill folder")
    if not checks["has_domain_qualifier"]:
        warnings.append("generic name needs a domain qualifier")
    if not checks["reasonable_length"]:
        warnings.append(
            f"skill name should normally be {min_terms}-{max_terms} kebab-case terms"
        )
    if description and not checks["description_is_routing_trigger"]:
        warnings.append("description should read like a routing trigger with action words")
    return {
        "name": folder_name,
        "frontmatter_name": fm_name,
        "manifest_name": manifest_skill_name,
        "checks": checks,
        "warnings": warnings,
        "ok": not warnings,
    }
