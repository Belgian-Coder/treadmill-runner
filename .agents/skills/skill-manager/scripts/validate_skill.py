#!/usr/bin/env python3
"""Validate one accepted agent skill folder."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import analyze_location
import skill_manager_common as common
from repo_support import repo_policy

WARN_SKILL_WORDS = int(repo_policy.default_value("limits.skill.warn_words"))
FAIL_SKILL_WORDS = int(repo_policy.default_value("limits.skill.fail_words"))
RISK_CATEGORY_TO_MANIFEST_KEY = {
    "credentials": "credentials",
    "destructive": "destructive",
    "generated_settings": "generated_settings",
    "installs": "installs",
    "network": "network",
    "production_writes": "production_writes",
    "uploads": "uploads",
}
IDE_TERMS = (
    "vs code",
    "vscode",
    "visual studio code",
    "visual studio",
    "jetbrains",
    "rider",
    "ide preview",
    "ide setup",
)
IDE_SETUP_TERMS = ("setup", "extension", "install", "preview")
IDE_SKIP_TERMS = ("non-blocking", "skipped", "skip_reason", "not applicable", "continue")
IDE_INSTALL_TERMS = ("--install-extension", "auto-install", "install extension")
OPTIONAL_SETUP_TERMS = (
    "setup",
    "install",
    "extension",
    "preview",
    "render",
    "optional",
    "environment",
)
NON_BLOCKING_REPORT_TERMS = ("non-blocking", "skipped", "failed", "continue")
COMPLETION_REPORT_TERMS = ("skipped", "blocked", "failed", "validation")
VALIDATION_TERMS = ("validate", "validation", "self-test", "test")
STOP_FALLBACK_TERMS = ("## stop rules", "stop before", "fallback", "blocked", "blocker")
PARALLEL_GUARDRAIL_TERMS = ("stable", "independent", "shared write")
EXTENSION_BEHAVIOR_TERMS = (
    "workflow extension",
    "workflow-local",
    "extension point:",
)
DESCRIPTION_PROCESS_TERMS = (
    "then",
    "before",
    "after",
    "step",
    "steps",
    "phase",
    "phases",
    "process",
    "workflow",
)
DESCRIPTION_ACTION_PATTERN = re.compile(
    r"\b\w+(?:ing|tion|tions|ment|ments)\b",
    re.IGNORECASE,
)
MINIMAL_SHAPE_GROUPS = {
    "goal": ("## goal", "## overview", "## purpose"),
    "workflow": ("## workflow", "## process", "## steps"),
    "guardrails": ("## rules", "## review rules", "## guardrails", "## safety"),
    "validation": ("validate", "validation", "self-test", "test"),
    "completion contract": ("## completion contract",),
    "stop or fallback": ("## stop rules", "fallback", "blocked", "blocker"),
}
IDE_VERSION_PATTERN = re.compile(
    r"\b(vs code|visual studio code|visual studio|rider|jetbrains)\b[^\n]{0,80}"
    r"(>=|<=|==|\^|version\s+\d|\d+\.\d+)",
    re.IGNORECASE,
)
INVOCATION_REFERENCE_PATTERN = re.compile(r"\[skill:([a-z0-9][a-z0-9-]{0,63})\]")
SCOPE_HEADING_PATTERN = re.compile(r"^##\s+Scope\s*$", re.IGNORECASE | re.MULTILINE)
OUT_OF_SCOPE_HEADING_PATTERN = re.compile(
    r"^##\s+Out(?:\s+|-)?Of\s+Scope\s*$",
    re.IGNORECASE | re.MULTILINE,
)
DOC_H1_PATTERN = re.compile(r"^#\s+\S", re.MULTILINE)
UTF8_BOM = b"\xef\xbb\xbf"


def validate_skill(skill_dir: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    policy_root = repo_policy.project_root(skill_dir)

    if not skill_dir.exists() or not skill_dir.is_dir():
        return [f"Skill folder not found: {skill_dir}"], warnings

    skill_path = skill_dir / "SKILL.md"
    if not skill_path.exists():
        return [f"{skill_dir} is missing SKILL.md."], warnings

    if skill_path.read_bytes().startswith(UTF8_BOM):
        errors.append(
            "SKILL.md starts with a UTF-8 BOM; remove it so cross-agent adapters "
            "parse frontmatter deterministically."
        )

    metadata, parse_error = common.parse_frontmatter_file(skill_path)
    if parse_error:
        errors.append(parse_error)
        metadata = {}

    metadata = metadata or {}
    name = metadata.get("name", "")
    description = metadata.get("description", "")
    metadata_keys = set(metadata.keys())

    if metadata_keys - {"name", "description"}:
        extra = ", ".join(sorted(metadata_keys - {"name", "description"}))
        errors.append(f"SKILL.md frontmatter has unsupported keys: {extra}.")

    if not name:
        errors.append("SKILL.md frontmatter is missing required 'name'.")
    elif name != skill_dir.name:
        errors.append(
            f"SKILL.md frontmatter name '{name}' must match folder name '{skill_dir.name}'."
        )
    elif not common.SKILL_NAME_PATTERN.match(name) or len(name) > repo_policy.int_value(
        policy_root, "limits.skill.name_max_chars"
    ):
        errors.append(
            "Skill name must use lowercase letters, digits, and hyphens, "
            f"and be at most {repo_policy.int_value(policy_root, 'limits.skill.name_max_chars')} characters."
        )

    if not description.strip():
        errors.append("SKILL.md frontmatter is missing required 'description'.")
    else:
        warnings.extend(validate_description_routing_api(str(description), policy_root))

    manifest_errors, manifest_warnings = common.validate_manifest_shape(skill_dir, metadata)
    errors.extend(manifest_errors)
    warnings.extend(manifest_warnings)

    skill_words = common.word_count(common.read_text(skill_path))
    manifest, _ = common.load_skill_manifest(skill_dir)
    manifest_summary = (
        str(manifest.get("summary", "")) if isinstance(manifest, dict) else ""
    )
    errors.extend(
        common.dotnet_skill_naming_errors(
            skill_dir.name,
            str(description),
            manifest_summary,
        )
    )
    size_exception = bool(
        isinstance(manifest, dict)
        and str(manifest.get("size_exception_rationale", "")).strip()
    )
    fail_skill_words = repo_policy.int_value(policy_root, "limits.skill.fail_words")
    warn_skill_words = repo_policy.int_value(policy_root, "limits.skill.warn_words")
    if skill_words > fail_skill_words and not size_exception:
        errors.append(
            f"SKILL.md has {skill_words} words; keep it at or below {fail_skill_words} "
            "or add module.json size_exception_rationale."
        )
    elif skill_words > warn_skill_words:
        warnings.append(repo_policy.tagged_warning(
            "health.skill.words",
            f"SKILL.md has {skill_words} words; prefer moving detail into docs/.",
        ))

    for child in sorted(skill_dir.iterdir(), key=lambda item: item.name.lower()):
        if child.name not in common.ALLOWED_TOP_LEVEL:
            warnings.append(
                f"{child.name} is not part of the standard skill layout. "
                "Keep extra material under docs/, suites/, scripts/, assets/, or module.json."
            )

    readme = skill_dir / "README.md"
    if readme.exists():
        warnings.append("README.md is discouraged inside skill folders; use docs/ instead.")

    if (skill_dir / "agents" / "openai.yaml").exists():
        errors.append(
            "agents/openai.yaml is not part of the maintained skill surface; "
            "avoid tool-specific implicit-invocation metadata."
        )

    for current_root, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        current = Path(current_root)
        for filename in filenames:
            path = current / filename
            if path.suffix.lower() in common.DISALLOWED_SCRIPT_SUFFIXES:
                errors.append(
                    f"{common.relative(skill_dir, path)} is not allowed. "
                    "Use a Python 3 entry point instead of shell, batch, or PowerShell."
                )

    contract_errors, contract_warnings = validate_declared_contract(skill_dir, manifest)
    errors.extend(contract_errors)
    warnings.extend(contract_warnings)
    errors.extend(validate_asset_sizes(skill_dir, policy_root))
    invocation_errors, invocation_warnings = validate_invocation_contract(skill_dir)
    errors.extend(invocation_errors)
    warnings.extend(invocation_warnings)
    docs_errors, docs_warnings = validate_progressive_disclosure_docs(skill_dir)
    errors.extend(docs_errors)
    warnings.extend(docs_warnings)
    warnings.extend(validate_skill_operating_pattern(skill_dir, manifest))
    warnings.extend(validate_ide_capability_reporting(skill_dir, manifest))
    warnings, escalated = repo_policy.classify_warnings(policy_root, warnings)
    errors.extend(escalated)
    return errors, warnings


def validate_description_routing_api(description: str, policy_root: Path) -> list[str]:
    text = " ".join(description.split())
    lowered = text.lower()
    if len(text.split()) < repo_policy.int_value(
        policy_root, "limits.skill.description_process_warn_words"
    ):
        return []
    words = set(re.findall(r"[a-z0-9-]+", lowered))
    process_term_count = sum(1 for term in DESCRIPTION_PROCESS_TERMS if term in words)
    action_term_count = len(DESCRIPTION_ACTION_PATTERN.findall(text))
    punctuation_count = text.count(",") + text.count(";")
    process_terms = repo_policy.int_value(
        policy_root, "limits.skill.description_process_warn_terms"
    )
    action_terms = repo_policy.int_value(
        policy_root, "limits.skill.description_process_warn_actions"
    )
    punctuation = repo_policy.int_value(
        policy_root, "limits.skill.description_process_warn_punctuation"
    )
    if process_term_count >= process_terms and (
        action_term_count >= action_terms or punctuation_count >= punctuation
    ):
        return [
            "SKILL.md description looks like a process summary rather than a routing API; "
            "prefer a concise trigger that says when to use the skill and what capability it owns."
        ]
    return []


def validate_skill_operating_pattern(
    skill_dir: Path, manifest: dict[str, object] | None = None
) -> list[str]:
    skill_path = skill_dir / "SKILL.md"
    if not skill_path.exists():
        return []
    text = common.read_text(skill_path, limit=80_000)
    lowered = text.lower()
    warnings: list[str] = []
    is_accepted = not isinstance(manifest, dict) or str(manifest.get("status", "accepted")) == "accepted"

    if is_accepted:
        missing_shape = [
            label
            for label, terms in MINIMAL_SHAPE_GROUPS.items()
            if not any(term in lowered for term in terms)
        ]
        if missing_shape:
            warnings.append(
                "Accepted skills should satisfy the minimal accepted skill shape; "
                f"missing: {', '.join(missing_shape)}."
            )

        quality = manifest.get("quality") if isinstance(manifest, dict) else None
        eval_suites = quality.get("eval_suites") if isinstance(quality, dict) else None
        eval_gap_rationale = (
            str(quality.get("eval_gap_rationale", "")).strip()
            if isinstance(quality, dict)
            else ""
        )
        if not eval_suites and not eval_gap_rationale:
            warnings.append(
                "Accepted skills should declare module.json quality.eval_suites or "
                "document module.json quality.eval_gap_rationale when deterministic eval coverage is not useful."
            )

    if "## completion contract" not in lowered:
        warnings.append(
            "Accepted skills should include a Completion Contract or equivalent "
            "final-response reporting rules."
        )
    elif any(term not in lowered for term in COMPLETION_REPORT_TERMS):
        warnings.append(
            "Completion reporting should mention skipped, blocked, failed, and "
            "validation checks."
        )

    if not any(term in lowered for term in VALIDATION_TERMS):
        warnings.append(
            "Accepted skills should name validation checks or explain when validation "
            "is intentionally skipped."
        )

    if not any(term in lowered for term in STOP_FALLBACK_TERMS):
        warnings.append(
            "Accepted skills should define stop, fallback, blocked, or non-blocking "
            "continuation behavior."
        )

    has_optional_setup = any(term in lowered for term in OPTIONAL_SETUP_TERMS) and (
        "setup" in lowered or "install" in lowered or "optional" in lowered
    )
    if has_optional_setup and any(term not in lowered for term in NON_BLOCKING_REPORT_TERMS):
        warnings.append(
            "Optional setup or install behavior should explicitly report skipped, "
            "failed, and non-blocking continuation behavior."
        )

    if "parallel" in lowered and any(term not in lowered for term in PARALLEL_GUARDRAIL_TERMS):
        warnings.append(
            "Parallel execution guidance should mention stable inputs, independent "
            "work, and no shared write targets."
        )

    has_extension_behavior = any(term in lowered for term in EXTENSION_BEHAVIOR_TERMS)
    has_extension_section = "## extension points" in lowered
    if has_extension_behavior and not has_extension_section:
        warnings.append(
            "Workflow extension behavior should be declared in a `## Extension Points` "
            "section with explicit inputs, outputs, and stop rules."
        )
    if has_extension_section:
        missing = []
        if "input" not in lowered and not any(flag in lowered for flag in ("--context", "--rules", "--template", "--config")):
            missing.append("inputs")
        if "output" not in lowered and "evidence" not in lowered:
            missing.append("outputs or evidence")
        if "stop" not in lowered and "blocked" not in lowered:
            missing.append("stop rules")
        if missing:
            warnings.append(
                "`## Extension Points` should declare "
                f"{', '.join(missing)}."
            )

    return warnings


def validate_asset_sizes(skill_dir: Path, policy_root: Path) -> list[str]:
    assets_dir = skill_dir / "assets"
    if not assets_dir.exists():
        return []

    errors: list[str] = []
    for current_root, dirnames, filenames in os.walk(assets_dir):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        current = Path(current_root)
        for filename in filenames:
            path = current / filename
            try:
                size = path.stat().st_size
            except OSError as exc:
                errors.append(f"{common.relative(skill_dir, path)} cannot be read: {exc}.")
                continue
            max_asset_bytes = repo_policy.int_value(policy_root, "limits.skill.asset_max_bytes")
            if size > max_asset_bytes:
                size_mb = size / 1024 / 1024
                max_size_mb = max_asset_bytes / 1024 / 1024
                errors.append(
                    f"{common.relative(skill_dir, path)} is {size_mb:.2f} MB; "
                    f"skill assets must be {max_size_mb:g}MB or smaller."
                )
    return errors


def validate_ide_capability_reporting(
    skill_dir: Path, manifest: dict[str, object] | None
) -> list[str]:
    warnings: list[str] = []
    scanned_text = "\n".join(
        common.read_text(path, limit=80_000)
        for path in [
            skill_dir / "SKILL.md",
            *[
                script
                for script in sorted((skill_dir / "scripts").glob("*.py"))
                if script.name != "run_self_tests.py" and "setup" in script.stem
            ],
        ]
        if path.exists()
    )
    lowered = scanned_text.lower()
    has_ide_behavior = any(term in lowered for term in IDE_TERMS) and any(
        term in lowered for term in IDE_SETUP_TERMS
    )
    if not has_ide_behavior:
        return warnings

    if not any(term in lowered for term in IDE_SKIP_TERMS):
        warnings.append(
            "IDE-specific setup behavior should report skipped, unsupported, or failed "
            "steps and continue when the setup is optional."
        )

    has_install_behavior = any(term in lowered for term in IDE_INSTALL_TERMS)
    risk = manifest.get("risk") if isinstance(manifest, dict) else None
    if has_install_behavior and isinstance(risk, dict):
        if risk.get("installs") is not True or risk.get("network") is not True:
            warnings.append(
                "IDE-specific install behavior should declare module.json risk.installs "
                "and risk.network."
            )

    for line in scanned_text.splitlines():
        if IDE_VERSION_PATTERN.search(line):
            warnings.append(
                "IDE-specific behavior should use capability detection instead of fixed "
                f"IDE version requirements: {line.strip()}"
            )
            break

    return warnings


def skill_root_for(skill_dir: Path) -> Path | None:
    for parent in (skill_dir, *skill_dir.parents):
        candidate = parent / ".agents" / "skills"
        if candidate.exists():
            return candidate
        if parent.name == "skills" and parent.parent.name == ".agents":
            return parent
    return None


def validate_invocation_contract(skill_dir: Path) -> tuple[list[str], list[str]]:
    skill_path = skill_dir / "SKILL.md"
    if not skill_path.exists():
        return [], []
    text = common.read_text(skill_path, limit=80_000)
    refs = sorted(set(INVOCATION_REFERENCE_PATTERN.findall(text)))
    errors: list[str] = []
    warnings: list[str] = []
    skills_root = skill_root_for(skill_dir)
    known_skills = (
        sorted(path.name for path in skills_root.iterdir() if (path / "SKILL.md").exists())
        if skills_root and skills_root.exists()
        else []
    )
    for ref in refs:
        if ref == skill_dir.name:
            warnings.append(f"[skill:{ref}] points at the current skill; prefer prose for self-owned behavior.")
            continue
        if skills_root and not (skills_root / ref / "SKILL.md").exists():
            matches = difflib.get_close_matches(ref, known_skills, n=2, cutoff=0.72)
            suffix = f" Similar skills: {', '.join(matches)}." if matches else ""
            errors.append(f"[skill:{ref}] reference does not resolve to .agents/skills/{ref}/SKILL.md.{suffix}")
    has_scope = bool(SCOPE_HEADING_PATTERN.search(text))
    has_out_of_scope = bool(OUT_OF_SCOPE_HEADING_PATTERN.search(text))
    if has_scope and not has_out_of_scope:
        warnings.append("Scope should pair with Out of Scope so invocation boundaries stay explicit.")
    if has_out_of_scope and not has_scope:
        warnings.append("Out of Scope should pair with Scope so invocation boundaries stay explicit.")
    if refs and not (has_scope and has_out_of_scope):
        warnings.append("[skill:<id>] invocation references should be near explicit Scope and Out of Scope sections.")
    return errors, warnings


def extract_unfenced_skill_refs(text: str) -> list[str]:
    refs: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        refs.extend(INVOCATION_REFERENCE_PATTERN.findall(line))
    return refs


def validate_progressive_disclosure_docs(skill_dir: Path) -> tuple[list[str], list[str]]:
    docs_dir = skill_dir / "docs"
    if not docs_dir.exists():
        return [], []

    errors: list[str] = []
    warnings: list[str] = []
    skills_root = skill_root_for(skill_dir)
    known_skills = (
        sorted(path.name for path in skills_root.iterdir() if (path / "SKILL.md").exists())
        if skills_root and skills_root.exists()
        else []
    )

    for doc_path in sorted(docs_dir.rglob("*.md"), key=lambda item: item.as_posix().lower()):
        text = common.read_text(doc_path, limit=120_000)
        rel = common.relative(skill_dir, doc_path)
        if not DOC_H1_PATTERN.search(text):
            errors.append(f"{rel} is missing an H1 heading.")
        if SCOPE_HEADING_PATTERN.search(text) or OUT_OF_SCOPE_HEADING_PATTERN.search(text):
            errors.append(
                f"{rel} must not define Scope or Out Of Scope sections; "
                "keep invocation boundaries in SKILL.md."
            )
        for ref in sorted(set(extract_unfenced_skill_refs(text))):
            if skills_root and not (skills_root / ref / "SKILL.md").exists():
                matches = difflib.get_close_matches(ref, known_skills, n=2, cutoff=0.72)
                suffix = f" Similar skills: {', '.join(matches)}." if matches else ""
                errors.append(f"{rel} has unresolved [skill:{ref}] reference.{suffix}")

    return errors, warnings


def validate_declared_contract(
    skill_dir: Path, manifest: dict[str, object] | None
) -> tuple[list[str], list[str]]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("risk"), dict):
        return [], []

    analysis = analyze_location.analyze_target(
        str(skill_dir),
        skill_dir.resolve(),
        max_files=2500,
        max_text_files=400,
    )
    risk = manifest["risk"]
    assert isinstance(risk, dict)
    errors: list[str] = []
    warnings: list[str] = []

    evidence_items = analysis.get("evidence", [])
    if isinstance(evidence_items, list):
        for item in evidence_items:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category", ""))
            manifest_key = RISK_CATEGORY_TO_MANIFEST_KEY.get(category)
            if not manifest_key or risk.get(manifest_key) is True:
                continue
            location = str(item.get("path", "unknown"))
            line = item.get("line")
            if isinstance(line, int):
                location = f"{location}:{line}"
            errors.append(
                f"{location} has {category} evidence ({item.get('signal')}) but "
                f"module.json risk.{manifest_key} is false."
            )

    declared_dependencies = " ".join(common.manifest_dependency_labels(manifest)).lower()
    repo_owned_modules = repo_owned_python_modules(skill_dir)
    detected_dependencies = analysis.get("dependencies", [])
    if isinstance(detected_dependencies, list):
        for detected in detected_dependencies:
            value = str(detected)
            normalized = value.lower()
            if "no package manifest dependencies detected" in normalized:
                continue
            if "python 3.12+ stdlib only" in normalized and "python" in declared_dependencies:
                continue
            dependency_name = extract_dependency_name(value)
            if dependency_name in repo_owned_modules:
                continue
            if dependency_name and dependency_name.lower() not in declared_dependencies:
                warnings.append(
                    f"Detected dependency may be undeclared in module.json: {value}"
                )

    return sorted(set(errors)), sorted(set(warnings))


def repo_owned_python_modules(skill_dir: Path) -> set[str]:
    modules = {path.stem for path in skill_dir.rglob("*.py") if path.is_file()}
    modules.update(
        path.parent.name
        for path in skill_dir.rglob("__init__.py")
        if path.is_file()
    )
    for parent in (skill_dir, *skill_dir.parents):
        skills_root = parent / ".agents" / "skills"
        if not skills_root.exists():
            continue
        for script in skills_root.glob("*/scripts/**/*.py"):
            modules.add(script.stem)
            if script.name == "__init__.py":
                modules.add(script.parent.name)
        break
    return modules


def extract_dependency_name(value: str) -> str:
    if "`" in value:
        parts = value.split("`")
        if len(parts) >= 3:
            return parts[1].split()[0]
    if ":" in value:
        value = value.split(":", 1)[1]
    return value.strip().split(maxsplit=1)[0] if value.strip() else ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_path", help="path to a skill folder containing SKILL.md")
    parser.add_argument("--format", choices=("text", "json"), default="text", dest="output_format")
    parser.add_argument("--summary", action="store_true", help="emit a compact validation summary")
    parser.add_argument("--compact", action="store_true", help="omit passing detail from JSON output")
    return parser


def validation_report(
    skill_dir: Path,
    errors: list[str],
    warnings: list[str],
    *,
    compact: bool = False,
) -> dict[str, object]:
    display_path = common.relative(Path.cwd().resolve(), skill_dir)
    report: dict[str, object] = {
        "schema_version": 1,
        "tool": "skill-manager.validate-skill",
        "ok": not errors,
        "status": "passed" if not errors else "failed",
        "skill_path": display_path,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }
    if errors or not compact:
        report["errors"] = errors
    if warnings and not compact:
        report["warnings"] = warnings
    return report


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    skill_dir = Path(args.skill_path).expanduser().resolve()

    errors, warnings = validate_skill(skill_dir)
    if args.output_format == "json":
        print(
            json.dumps(
                validation_report(skill_dir, errors, warnings, compact=args.compact or args.summary),
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if not errors else 1

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"Validated skill folder: {common.relative(Path.cwd().resolve(), skill_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
