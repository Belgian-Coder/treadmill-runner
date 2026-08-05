#!/usr/bin/env python3
"""Repository surface scanners used by skill-manager health checks."""

from __future__ import annotations

import os
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from repo_support import repo_common as repo
from repo_support import repo_policy
from repo_support.repo_health_hidden import hidden_character_warnings

SCRIPT_WARN_LINES = int(repo_policy.default_value("limits.script.warn_lines"))
SCRIPT_WARN_FUNCTIONS = int(repo_policy.default_value("limits.script.warn_functions"))
SCRIPT_WARN_BYTES = int(repo_policy.default_value("limits.script.warn_bytes"))
SCRIPT_WARN_TOP_LEVEL_FILES = int(repo_policy.default_value("limits.script.warn_top_level_files"))
# repo_manager.py owns the public argparse surface. It intentionally stays mostly
# parser/dispatch code; implementation details belong in repo_support modules.
PUBLIC_COMMAND_WARN_LINES = int(repo_policy.default_value("limits.script.public_command_warn_lines"))
SCRIPT_COMPLEXITY_EXEMPT_REL_PATHS = {
    # These are internal implementation modules behind thin public command wrappers.
    ".agents/skills/local-ai-helper/scripts/local_ai_support/routing_impl.py",
    ".agents/skills/local-ai-helper/scripts/local_ai_support/setup_impl.py",
}
GENERATED_SCRIPT_COPY_REL_PATHS = {
    # Installed by repo-navigation into consuming projects; keep the source copy checked.
    "automations/navigation/scripts/navigation_core.py",
    "automations/navigation/scripts/project_context.py",
    "automations/navigation/scripts/update_navigation.py",
}
ROUTING_WARN_CHARS = int(repo_policy.default_value("limits.routing.warn_chars"))
ROUTING_WARN_ROWS = int(repo_policy.default_value("limits.routing.warn_rows"))
AGENTS_WARN_TOKENS = int(repo_policy.default_value("limits.agents.warn_tokens"))
SKILL_WARN_TOKENS = int(repo_policy.default_value("limits.skill.warn_tokens"))
WORKFLOW_WARN_TOKENS = int(repo_policy.default_value("limits.workflow.warn_tokens"))
WORKFLOW_MERMAID_WARN_TOKENS = int(repo_policy.default_value("limits.workflow.mermaid_warn_tokens"))
MODULE_WARN_TOKENS = int(repo_policy.default_value("limits.workflow_module.warn_tokens"))
NAVIGATION_WARN_TOKENS = int(repo_policy.default_value("limits.navigation.warn_tokens"))
MERMAID_BLOCK_RE = re.compile(r"::: mermaid\b.*?:::", re.IGNORECASE | re.DOTALL)
EVAL_FILENAME_RE = re.compile(r"(^|[-_])evals?([-_.]|$)", re.IGNORECASE)
CANDIDATE_IMPORT_ROOTS = {"_candidate-imports", "candidate-imports"}
CANDIDATE_INTAKE_ROOTS = CANDIDATE_IMPORT_ROOTS | {"temp"}
CANDIDATE_DEPENDENCY_LOCKFILES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pipfile.lock",
    "uv.lock",
    "yarn.lock",
}
EVAL_HIGH_RISK_OWNERS = {"agent-benchmarking", "skill-manager", "workflow-manager"}
DOC_FRONTMATTER_REQUIRED_KEYS = {"title", "type", "status", "owner", "audience", "updated"}
DOC_FRONTMATTER_ALLOWED_KEYS = DOC_FRONTMATTER_REQUIRED_KEYS | {"applies_to"}
DOC_FRONTMATTER_TYPES = {
    "guide",
    "reference",
    "policy",
    "runbook",
    "project-context",
    "index",
    "adr",
}
DOC_FRONTMATTER_STATUSES = {"active", "draft", "archived", "generated", "reviewed"}
DOC_FRONTMATTER_AUDIENCES = {"human", "agent", "both"}
PROJECT_CONTEXT_PACKAGE_FILES = {
    "docs/project/project-context.json",
    "docs/project/validation/run_project_validation.py",
    "docs/project/validation/validation-manifest.json",
}
COMPLETION_CONTRACT_HEADING = "## Completion Contract"
COMPLETION_CHECK_REPORTING = "should report completed, skipped, blocked, and failed checks."
SKILL_USED_REPORT = "skill used: <name> - <reason>"
LOW_CONTEXT_COMPLETION_REPORT = "should report low-context files used or skipped in its Completion Contract."
SKILL_EVAL_ASSERTION_TYPES = {
    "budget_skill_words_at_most",
    "compare_change_class",
    "compare_decision",
    "description_contains",
    "completion_contract_terms",
    "compatibility_required",
    "file_absent",
    "file_contains",
    "file_exists",
    "manifest_field_equals",
    "public_command_behavior",
    "python_script_succeeds",
    "repo_command_json_field_equals",
    "repo_file_contains",
    "repo_command_output_contains",
    "repo_command_succeeds",
    "risk_declared",
    "risk_profile_covers_flags",
    "skill_contains",
    "stop_or_fallback_terms",
    "trigger_quality",
    "validation_ok",
}
WORKFLOW_EVAL_ASSERTION_TYPES = {
    "contract_declares_command",
    "contract_contains",
    "contract_declares_output",
    "contract_declares_phase",
    "contract_declares_related_module",
    "contract_declares_worker_profile",
    "contract_local_ai_use_cases",
    "file_absent",
    "file_contains",
    "file_exists",
    "instructions_contains",
    "references_contains",
    "repo_command_succeeds",
    "run_evidence_ledger_valid",
    "run_index_contains",
    "run_index_exists",
    "run_packet_valid",
    "run_resume_state_valid",
    "run_handoff_valid",
    "run_context_packet_valid",
    "start_contains",
    "unsupported_claims_recorded",
    "validation_ok",
    "workflow_lifecycle_smoke_ok",
}
COMMAND_EVAL_ASSERTION_TYPES = {
    "public_command_behavior",
    "repo_command_json_field_equals",
    "repo_command_output_contains",
    "repo_command_succeeds",
}
FILE_SHAPE_EVAL_ASSERTION_TYPES = {
    "contract_contains",
    "description_contains",
    "file_absent",
    "file_contains",
    "file_exists",
    "instructions_contains",
    "references_contains",
    "repo_file_contains",
    "skill_contains",
    "start_contains",
}
STALE_EVAL_PATH_FRAGMENTS = (
    "skill.json",
    "Core/",
    "Runs/",
    "START-",
    "workflow-state.json",
    "context-handoff",
    "evidence-ledger",
    "automation.json",
    "workflow-contract.md",
    "structure.md",
    "references.md",
)


def _walk_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        strings: list[str] = []
        for item in value:
            strings.extend(_walk_strings(item))
        return strings
    if isinstance(value, dict):
        strings = []
        for item in value.values():
            strings.extend(_walk_strings(item))
        return strings
    return []


def _eval_suite_candidates(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for owner_root in (root / ".agents" / "skills", root / "automations"):
        if not owner_root.exists():
            continue
        for folder_name in ("suites", "docs", "Suites"):
            for path in owner_root.glob(f"*/{folder_name}/*.json"):
                if EVAL_FILENAME_RE.search(path.stem):
                    candidates.append(path)
    return sorted(set(candidates), key=lambda item: item.as_posix())


def _eval_suite_owner(root: Path, path: Path) -> tuple[str, str]:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return "unknown", ""
    if len(parts) >= 4 and parts[0] == ".agents" and parts[1] == "skills":
        return "skill", parts[2]
    if len(parts) >= 3 and parts[0] == "automations":
        return "workflow", parts[1]
    return "unknown", ""


def _eval_case_list(data: object) -> list[object] | None:
    if not isinstance(data, dict):
        return None
    cases = data.get("evals")
    if cases is None:
        cases = data.get("cases")
    return cases if isinstance(cases, list) else None


def _assertion_is_intentional_absence(assertion: dict[str, object], text: str) -> bool:
    if assertion.get("type") != "file_absent":
        return False
    path = str(assertion.get("path", ""))
    return text == path or text.replace("\\", "/") == path.replace("\\", "/")


def eval_quality_report(root: Path) -> dict[str, object]:
    """Lint eval suites for drift that makes deep checks less behavior-focused."""
    issues: list[str] = []
    warnings: list[str] = []
    suite_count = 0
    assertion_count = 0
    command_assertion_count = 0
    file_shape_assertion_count = 0

    for suite_path in _eval_suite_candidates(root):
        suite_count += 1
        relative_suite = repo.relative(root, suite_path).replace("\\", "/")
        kind, owner = _eval_suite_owner(root, suite_path)
        relative_parts = Path(relative_suite).parts
        if "docs" in relative_parts or "Suites" in relative_parts:
            issues.append(f"old eval suite layout: {relative_suite}; use suites/")
        try:
            data = json.loads(suite_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"eval suite unreadable: {relative_suite}: {exc}")
            continue
        cases = _eval_case_list(data)
        if cases is None:
            issues.append(f"eval suite missing evals/cases list: {relative_suite}")
            continue
        if not cases:
            issues.append(f"empty eval suite: {relative_suite}")
            continue

        suite_assertions = 0
        suite_command_assertions = 0
        suite_file_shape_assertions = 0
        allowed_assertion_types = (
            WORKFLOW_EVAL_ASSERTION_TYPES if kind == "workflow" else SKILL_EVAL_ASSERTION_TYPES
        )
        for case_index, case in enumerate(cases, start=1):
            if not isinstance(case, dict):
                issues.append(f"eval case must be an object: {relative_suite} case {case_index}")
                continue
            assertions = case.get("assertions")
            if not isinstance(assertions, list):
                issues.append(
                    f"eval case assertions must be a list: {relative_suite} case {case.get('id', case_index)}"
                )
                continue
            if not assertions:
                issues.append(
                    f"empty eval case: {relative_suite} case {case.get('id', case_index)}"
                )
                continue
            for assertion_index, assertion in enumerate(assertions, start=1):
                if not isinstance(assertion, dict):
                    issues.append(
                        f"eval assertion must be an object: {relative_suite} case {case.get('id', case_index)} "
                        f"assertion {assertion_index}"
                    )
                    continue
                assertion_type = str(assertion.get("type", ""))
                suite_assertions += 1
                assertion_count += 1
                if assertion_type not in allowed_assertion_types:
                    issues.append(
                        f"unknown eval assertion type: {relative_suite} case {case.get('id', case_index)} "
                        f"assertion {assertion_index}: {assertion_type or '<missing>'}"
                    )
                if assertion_type in COMMAND_EVAL_ASSERTION_TYPES:
                    suite_command_assertions += 1
                    command_assertion_count += 1
                if assertion_type in FILE_SHAPE_EVAL_ASSERTION_TYPES:
                    suite_file_shape_assertions += 1
                    file_shape_assertion_count += 1
                for text in _walk_strings(assertion):
                    normalized = text.replace("\\", "/")
                    if _assertion_is_intentional_absence(assertion, text):
                        continue
                    for fragment in STALE_EVAL_PATH_FRAGMENTS:
                        if fragment in normalized:
                            issues.append(
                                f"stale eval path: {relative_suite} case {case.get('id', case_index)} "
                                f"uses {text!r}"
                            )
                            break

        if owner in EVAL_HIGH_RISK_OWNERS and suite_command_assertions == 0:
            issues.append(
                f"{owner} eval suite lacks command-level behavior assertion: {relative_suite}"
            )
        if suite_assertions >= 8 and suite_file_shape_assertions / suite_assertions >= 0.7:
            warnings.append(
                f"string-heavy eval suite: {relative_suite}; prefer behavior, schema, and evidence assertions."
            )

    issue_list = sorted(set(issues))
    warning_list = sorted(set(warnings))
    return {
        "schema_version": 1,
        "tool": "eval-quality",
        "ok": not issue_list,
        "status": "passed" if not issue_list else "failed",
        "summary": {
            "suite_count": suite_count,
            "assertion_count": assertion_count,
            "command_assertion_count": command_assertion_count,
            "file_shape_assertion_count": file_shape_assertion_count,
            "issue_count": len(issue_list),
            "warning_count": len(warning_list),
        },
        "issues": issue_list,
        "warnings": warning_list,
    }

def validate_python_only_scripts(root: Path) -> list[str]:
    errors: list[str] = []
    active_roots = [
        root / ".agents" / "skills",
        root / "automations",
        root / ".github",
        root / ".claude",
        root / ".continue",
    ]
    for active_root in active_roots:
        if not active_root.exists():
            continue
        for current_root, dirnames, filenames in os.walk(active_root):
            dirnames[:] = [name for name in dirnames if name not in repo.IGNORED_SCAN_DIRS]
            current = Path(current_root)
            for filename in filenames:
                path = current / filename
                if path.suffix.lower() not in repo.DISALLOWED_SCRIPT_SUFFIXES:
                    continue
                errors.append(
                    f"{repo.relative(root, path)} is not allowed. "
                    "Use a Python 3 entry point instead of PowerShell, shell, batch, or command wrappers."
                )
    return errors

def validate_no_pycache(root: Path) -> list[str]:
    errors: list[str] = []
    active_roots = [
        root / "scripts",
        root / ".agents" / "skills",
        root / "automations",
        root / ".github",
        root / ".claude",
        root / ".continue",
    ]
    for active_root in active_roots:
        if not active_root.exists():
            continue
        for current_root, dirnames, _filenames in os.walk(active_root):
            current = Path(current_root)
            if current.name == "__pycache__":
                errors.append(
                    f"{repo.relative(root, current)} is generated Python bytecode cache; "
                    "remove it and run maintained commands with bytecode disabled."
                )
                dirnames[:] = []
                continue
            dirnames[:] = [name for name in dirnames if name != ".git"]
    return errors

def validate_repo_layout(root: Path) -> list[str]:
    errors: list[str] = []
    launcher = root / ".agents" / "manage.py"
    if not launcher.exists():
        errors.append(".agents/manage.py is required as the thin repository launcher.")
    else:
        text = launcher.read_text(encoding="utf-8", errors="replace")
        subprocess_launcher = "repo_manager.py" in text and "subprocess.run" in text
        runpy_launcher = "repo_manager.py" in text and "runpy.run_path" in text
        if not (subprocess_launcher or runpy_launcher):
            errors.append(".agents/manage.py must only dispatch to skill-manager repo_manager.py.")
        forbidden_launcher_logic = (
            "argparse",
            "validate_skill",
            "create_workflow",
            "sync_skill_routing",
            "sync_automation_routing",
        )
        for fragment in forbidden_launcher_logic:
            if fragment in text:
                errors.append(
                    ".agents/manage.py must stay a thin launcher and not own "
                    f"{fragment} behavior."
                )
    if (root / ".agents" / "lib").exists():
        errors.append(
            ".agents/lib is not used; scripts must stay owned by the skill that directly uses them."
        )
    if (root / ".agents" / "routing.json").exists():
        errors.append(".agents/routing.json is obsolete; use .agents/registry.json.")
    if (root / ".agents" / "commands.md").exists():
        errors.append(
            ".agents/commands.md is obsolete; command details belong in owning skills "
            "and script --help output."
        )
    if (root / "automations" / "routing.json").exists():
        errors.append("automations/routing.json is obsolete; use automations/registry.json.")
    if (root / "docs" / "SKILL_DESIGN_GUIDE.md").exists():
        errors.append(
            "docs/SKILL_DESIGN_GUIDE.md is obsolete; use skill-manager docs directly."
        )
    if (root / "docs" / "SKILL_INVENTORY.md").exists():
        errors.append(
            "docs/SKILL_INVENTORY.md is obsolete; keep source inventories with the source material or in the owning skill docs."
        )
    if (root / "docs").exists():
        docs_files = [
            path
            for path in (root / "docs").rglob("*")
            if path.is_file()
            and path.suffix.lower() != ".md"
            and not repo.is_installed_consumer_generated_path(root, repo.relative(root, path))
            and not repo.is_installed_consumer_owned_path(root, repo.relative(root, path))
            and repo.relative(root, path).replace("\\", "/") not in PROJECT_CONTEXT_PACKAGE_FILES
            and not (
                path.parent.name == "diagrams"
                and path.suffix.lower() in {".mmd", ".svg"}
            )
        ]
        if docs_files:
            errors.append(
                "root docs/ may contain Markdown harness documentation only; "
                f"unexpected files: {', '.join(repo.relative(root, path) for path in docs_files[:5])}."
            )
    if (root / "scripts").exists():
        errors.append(
            "root scripts/ is intentionally unused; repository maintenance commands "
            "belong under .agents/skills/skill-manager/scripts/."
        )
    legacy_manager_name = "skill-" + "maint" + "ainer"
    if (root / ".agents" / "skills" / legacy_manager_name).exists():
        errors.append(
            "legacy skill manager folder is obsolete; use .agents/skills/skill-manager."
        )
    if (root / ".claude" / "skills" / legacy_manager_name).exists():
        errors.append(
            "legacy Claude skill manager adapter is obsolete; regenerate Claude adapters for skill-manager."
        )
    old_scripts = {
        root / ".agents" / "skills" / "skill-manager" / "scripts" / "manage_repo.py",
        root / ".agents" / "skills" / "skill-manager" / "scripts" / "self_test.py",
        root / ".agents" / "skills" / "skill-manager" / "scripts" / "sync_references.py",
        root / ".agents" / "skills" / "skill-manager" / "scripts" / "sync_skill_references.py",
        root / ".agents" / "skills" / "skill-manager" / "scripts" / "skill_common.py",
    }
    for path in sorted(old_scripts, key=lambda item: item.as_posix()):
        if path.exists():
            errors.append(f"{repo.relative(root, path)} is obsolete; use the renamed canonical script.")
    return errors


def parse_markdown_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, "missing frontmatter"
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return metadata, ""
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            return metadata, f"invalid frontmatter line: {line}"
        key, value = line.split(":", 1)
        key = key.strip()
        if key in metadata:
            return metadata, f"duplicate frontmatter key: {key}"
        metadata[key] = value.strip()
    return metadata, "frontmatter is not closed"


def root_docs_frontmatter_errors(root: Path) -> list[str]:
    docs_root = root / "docs"
    if not docs_root.exists():
        return []
    errors: list[str] = []
    for path in sorted(docs_root.rglob("*.md"), key=lambda item: item.as_posix()):
        rel = repo.relative(root, path).replace("\\", "/")
        if repo.is_installed_consumer_generated_path(root, rel) or repo.is_installed_consumer_owned_path(root, rel):
            continue
        metadata, parse_error = parse_markdown_frontmatter(path)
        if parse_error:
            errors.append(f"{rel} {parse_error}; add docs metadata frontmatter.")
            continue
        missing = sorted(DOC_FRONTMATTER_REQUIRED_KEYS - set(metadata))
        if missing:
            errors.append(f"{rel} frontmatter is missing keys: {', '.join(missing)}.")
        extra = sorted(set(metadata) - DOC_FRONTMATTER_ALLOWED_KEYS)
        if extra:
            errors.append(f"{rel} frontmatter has unsupported keys: {', '.join(extra)}.")
        if metadata.get("type") not in DOC_FRONTMATTER_TYPES:
            errors.append(
                f"{rel} frontmatter type must be one of: {', '.join(sorted(DOC_FRONTMATTER_TYPES))}."
            )
        if metadata.get("status") not in DOC_FRONTMATTER_STATUSES:
            errors.append(
                f"{rel} frontmatter status must be one of: {', '.join(sorted(DOC_FRONTMATTER_STATUSES))}."
            )
        if metadata.get("audience") not in DOC_FRONTMATTER_AUDIENCES:
            errors.append(
                f"{rel} frontmatter audience must be one of: {', '.join(sorted(DOC_FRONTMATTER_AUDIENCES))}."
            )
        updated = metadata.get("updated", "")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", updated):
            errors.append(f"{rel} frontmatter updated must use YYYY-MM-DD.")
        owner = metadata.get("owner", "")
        if owner and not re.match(r"^[a-z0-9][a-z0-9-]*$", owner):
            errors.append(f"{rel} frontmatter owner must be a lowercase id.")
        if rel == "docs/project/project-context.md" and metadata.get("status") not in {"generated", "reviewed"}:
            errors.append("docs/project/project-context.md frontmatter status must be generated or reviewed.")
    return errors


def tracked_files(root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if completed.returncode != 0:
        return []
    return [
        line.replace("\\", "/").strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    ]


def tracked_json_files(root: Path, *, include_untracked: bool = True) -> list[Path]:
    files = tracked_and_untracked_files(root) if include_untracked else tracked_files(root)
    return sorted(
        (
            root / rel_path
            for rel_path in files
            if rel_path.lower().endswith(".json")
            and (root / rel_path).exists()
            and not rel_path.replace("\\", "/").startswith(".agents/local-ai/cache/")
            and not rel_path.replace("\\", "/").startswith(".agents/tools/cache/")
        ),
        key=lambda item: item.as_posix(),
    )


def canonical_json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def json_format_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for path in tracked_json_files(root):
        relative_path = repo.relative(root, path).replace("\\", "/")
        try:
            original = path.read_text(encoding="utf-8-sig")
            value = json.loads(original)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{relative_path} is not valid JSON: {exc}")
            continue
        if original != canonical_json_text(value):
            errors.append(
                f"{relative_path} is not pretty-printed JSON; run "
                "`python -B .agents/manage.py format-json`."
            )
    return errors


def format_json_files(root: Path, *, check: bool = False) -> dict[str, object]:
    changed: list[str] = []
    invalid: list[str] = []
    for path in tracked_json_files(root):
        relative_path = repo.relative(root, path).replace("\\", "/")
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            invalid.append(f"{relative_path}: {exc}")
            continue
        formatted = canonical_json_text(value)
        current = path.read_text(encoding="utf-8-sig")
        if current == formatted:
            continue
        changed.append(relative_path)
        if not check:
            path.write_text(formatted, encoding="utf-8", newline="\n")
    return {
        "schema_version": 1,
        "tool": "format-json",
        "ok": not invalid and (not check or not changed),
        "status": "passed" if not invalid and (not check or not changed) else "failed",
        "checked": len(tracked_json_files(root)),
        "changed": changed,
        "invalid": invalid,
    }

def tracked_and_untracked_files(root: Path) -> list[str]:
    files = tracked_files(root)
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if completed.returncode == 0:
        files.extend(
            line.replace("\\", "/").strip()
            for line in completed.stdout.splitlines()
            if line.strip()
        )
    if not files:
        for current_root, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in repo.IGNORED_SCAN_DIRS]
            current = Path(current_root)
            for filename in filenames:
                files.append(repo.relative(root, current / filename).replace("\\", "/"))
    return sorted(set(repo.installed_consumer_validation_paths(root, files)))

def folder_organization_kind(folder: str) -> tuple[str, str]:
    parts = tuple(part for part in folder.split("/") if part)
    lower_parts = tuple(part.lower() for part in parts)
    if "runs" in lower_parts:
        return (
            "workflow evidence",
            "Keep run evidence grouped by run id; do not reorganize historical evidence just to reduce file count.",
        )
    if lower_parts and lower_parts[-1] in {"raw", "raw-v2", "fixtures"}:
        return (
            "fixture or raw evidence",
            "Keep fixture/raw folders stable because benchmark reports and ledgers may point at them.",
        )
    if lower_parts and "templates" in lower_parts[-1]:
        return (
            "template catalog",
            "Keep one template per supported type flat; group only when each type grows multiple variants.",
        )
    if folder == "docs":
        return (
            "root documentation surface",
            "Keep only route-first harness docs here; move deep domain detail to owning skill/workflow docs when it grows.",
        )
    if lower_parts and lower_parts[-1] == "suites":
        return (
            "suite catalog",
            "Keep directly selectable suite names stable; add nested suite support before grouping existing suite files.",
        )
    if lower_parts and lower_parts[-1] == "scripts":
        return (
            "public script surface",
            "Keep command entrypoints at the script root and move only internal helpers to support modules.",
        )
    if lower_parts and (lower_parts[-1].endswith("_support") or lower_parts[-1].endswith("_impl")):
        return (
            "support module package",
            "Split by subdomain only when files stop sharing imports or tests become hard to navigate.",
        )
    return (
        "review",
        "Check whether this folder mixes unrelated responsibilities or needs an index, manifest, or owner-owned subfolder.",
    )

def folder_organization_report(root: Path) -> list[dict[str, object]]:
    counts: dict[str, list[str]] = defaultdict(list)
    for raw_path in tracked_and_untracked_files(root):
        path = Path(raw_path)
        if not path.parts:
            continue
        if any(part in repo.IGNORED_SCAN_DIRS for part in path.parts):
            continue
        parent = path.parent.as_posix() if path.parent.as_posix() != "." else "."
        counts[parent].append(path.name)

    entries: list[dict[str, object]] = []
    for folder, filenames in sorted(counts.items(), key=lambda item: (-len(item[1]), item[0])):
        organization_review_files = repo_policy.int_value(
            root, "limits.organization.review_direct_files"
        )
        if len(filenames) < organization_review_files:
            continue
        kind, recommendation = folder_organization_kind(folder)
        entries.append(
            {
                "folder": folder,
                "direct_files": len(filenames),
                "kind": kind,
                "recommendation": recommendation,
                "sample_files": sorted(filenames)[:8],
            }
        )
    return entries

def validate_candidate_import_hygiene(root: Path) -> list[str]:
    errors: list[str] = []
    files = tracked_files(root)
    for rel_path in files:
        parts = rel_path.split("/")
        if not parts:
            continue
        root_name = parts[0].lower()
        if root_name in CANDIDATE_IMPORT_ROOTS:
            errors.append(
                f"{rel_path} is under a committed candidate import folder; "
                "rewrite useful behavior into accepted skills or workflows and remove candidate payloads."
            )
            continue
        if root_name in CANDIDATE_INTAKE_ROOTS and Path(rel_path).name.lower() in CANDIDATE_DEPENDENCY_LOCKFILES:
            errors.append(
                f"{rel_path} is an external dependency lockfile under a candidate/temp intake path; "
                "do not commit third-party candidate manifests that Dependabot will scan."
            )
    return errors

def validate_manager_self_containment(root: Path) -> list[str]:
    errors: list[str] = []
    workflow_scripts = root / ".agents" / "skills" / "workflow-manager" / "scripts"
    if workflow_scripts.exists():
        for script in sorted(workflow_scripts.glob("*.py"), key=lambda item: item.name):
            text = script.read_text(encoding="utf-8", errors="replace")
            forbidden = (
                "SKILL_MANAGER_SCRIPTS",
                "skill_manager_common",
                "parents[2] / \"skill-manager\"",
                "parents[2] / 'skill-manager'",
            )
            if any(fragment in text for fragment in forbidden):
                errors.append(
                    f"{repo.relative(root, script)} must not import or path into skill-manager scripts."
                )
    skill_self_tests = root / ".agents" / "skills" / "skill-manager" / "scripts" / "run_self_tests.py"
    if skill_self_tests.exists():
        text = skill_self_tests.read_text(encoding="utf-8", errors="replace")
        forbidden_imports = (
            "WORKFLOW_MANAGER_SCRIPTS",
            "import validate_automations",
            "import sync_automation_routing",
            "import create_workflow",
        )
        if any(fragment in text for fragment in forbidden_imports):
            errors.append(
                f"{repo.relative(root, skill_self_tests)} must not import workflow-manager test subjects."
            )
    return errors

def instruction_quality_errors(root: Path) -> list[str]:
    errors: list[str] = []
    agents_file = root / "AGENTS.md"
    if not agents_file.exists():
        return errors
    text = agents_file.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    char_count = len(text)
    fail_chars = repo_policy.int_value(root, "limits.agents.fail_chars")
    if char_count > fail_chars:
        errors.append(
            f"AGENTS.md has {char_count} characters; keep it at or below "
            f"{fail_chars} so always-on repository instructions stay compact."
        )
    lowered = text.lower()
    required_low_context = (
        "low-context",
        ".agents/routing.md",
        "automations/routing.md",
        "module.json",
    )
    if any(fragment not in lowered for fragment in required_low_context):
        errors.append(
            "AGENTS.md must require low-context index reads before opening full "
            "skill or workflow folders."
        )
    return errors

def local_tool_config_paths(root: Path) -> list[Path]:
    candidates = [
        root / ("." + "vscode") / ("settings" + ".json"),
        root / ("." + "claude") / ("settings" + ".json"),
        root / ("." + "mcp" + ".json"),
        root / ("." + "codex") / ("config" + ".toml"),
    ]
    return [path for path in candidates if path.exists()]

def text_word_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").split())
    except OSError:
        return 0

def estimated_tokens(path: Path, *, exclude_mermaid_blocks: bool = False) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    if exclude_mermaid_blocks and path.suffix.lower() == ".md":
        text = MERMAID_BLOCK_RE.sub("", text)
    if path.suffix.lower() == ".json":
        try:
            value = json.loads(text)
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except json.JSONDecodeError:
            pass
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)

def context_budget_warnings(root: Path) -> list[str]:
    warnings: list[str] = []
    checks: list[tuple[Path, int, str, str]] = []
    checks.append((
        root / "AGENTS.md",
        repo_policy.int_value(root, "limits.agents.warn_tokens"),
        "always-loaded repository instructions",
        "health.agents.tokens",
    ))
    skills_root = root / ".agents" / "skills"
    if skills_root.exists():
        checks.extend(
            (
                path,
                repo_policy.int_value(root, "limits.skill.warn_tokens"),
                "trigger-loaded SKILL.md guidance",
                "health.skill.tokens",
            )
            for path in sorted(skills_root.glob("*/SKILL.md"), key=lambda item: item.as_posix())
        )
    automations_root = root / "automations"
    if automations_root.exists():
        checks.extend(
            (
                path,
                repo_policy.int_value(root, "limits.workflow.warn_tokens"),
                "workflow entrypoint",
                "health.workflow.tokens",
            )
            for path in sorted(automations_root.glob("*/WORKFLOW.md"), key=lambda item: item.as_posix())
        )
        checks.extend(
            (
                path,
                repo_policy.int_value(root, "limits.workflow_module.warn_tokens"),
                "workflow module contract",
                "health.workflow-module.tokens",
            )
            for path in sorted(automations_root.glob("*/module.json"), key=lambda item: item.as_posix())
        )
    navigation_map = root / "automations" / "navigation" / "artifacts" / "maps" / "NAVIGATION.md"
    if navigation_map.exists():
        checks.append((
            navigation_map,
            repo_policy.int_value(root, "limits.navigation.warn_tokens"),
            "route-first navigation map",
            "health.navigation.tokens",
        ))

    for path, budget, label, warning_rule in checks:
        if not path.exists():
            continue
        exclude_mermaid = label == "workflow entrypoint"
        if exclude_mermaid:
            try:
                has_mermaid = "::: mermaid" in path.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                has_mermaid = False
            if has_mermaid:
                budget = repo_policy.int_value(root, "limits.workflow.mermaid_warn_tokens")
        tokens = estimated_tokens(path, exclude_mermaid_blocks=exclude_mermaid)
        if tokens > budget:
            qualifier = " excluding Mermaid diagrams" if exclude_mermaid else ""
            warnings.append(repo_policy.tagged_warning(
                warning_rule,
                f"{repo.relative(root, path)} is about {tokens} tokens{qualifier}; keep {label} "
                f"at or below about {budget} estimated tokens and move detail behind lower-context files.",
            ))
    return warnings

def unsupported_memory_claim_warnings(root: Path) -> list[str]:
    warnings: list[str] = []
    broad_memory_terms = ("persistent memory", "long-term memory", "memory layer")
    allowed_terms = (
        "out of scope",
        "not an opaque",
        "not memory",
        "do not add",
        "no broad persistent memory",
        "memory-like evidence",
        "evidence ledger",
        "not an ai memory layer",
    )
    source_truth_terms = ("source truth", "canonical truth")
    source_truth_allow = (
        "canonical source",
        "source files",
        "source truth remains",
        "ledger evidence",
        "source-backed",
        "not source truth",
        "not memory or source truth",
        "review layer",
        "review-layer",
    )
    for path in active_markdown_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        lowered = text.lower()
        if any(term in lowered for term in broad_memory_terms) and not any(term in lowered for term in allowed_terms):
            warnings.append(
                f"{repo.relative(root, path)} mentions a broad memory layer without saying it is out of scope "
                "or backed by workflow-owned evidence."
            )
        if "generated" in lowered and any(term in lowered for term in source_truth_terms) and not any(
            term in lowered for term in source_truth_allow
        ):
            warnings.append(
                f"{repo.relative(root, path)} may imply generated content is source truth; generated estimates "
                "should be treated as review-layer facts unless backed by evidence."
            )
    return warnings

def is_intentional_deep_guide(root: Path, path: Path) -> bool:
    relative = repo.relative(root, path).replace("\\", "/").lower()
    if not relative.startswith(".agents/skills/") or "/docs/" not in relative:
        return False
    return relative.endswith("guide.md") or relative.endswith("-guide.md")

def active_markdown_files(root: Path) -> list[Path]:
    roots = [
        root / "AGENTS.md",
        root / "README.md",
        root / ".agents" / "skills",
        root / "automations",
    ]
    files: list[Path] = []
    for item in roots:
        if item.is_file() and item.suffix.lower() == ".md":
            files.append(item)
        elif item.is_dir():
            files.extend(
                path
                for path in item.rglob("*.md")
                if "__pycache__" not in path.parts
                and "runs" not in path.parts
                and path.name != "routing.md"
            )
    return sorted(set(files), key=lambda path: path.as_posix())

def instruction_adapter_files(root: Path) -> list[Path]:
    return [
        path
        for path in [
            root / "GEMINI.md",
            root / ".github" / "copilot-instructions.md",
            root / ".claude" / "CLAUDE.md",
            root / ".continue" / "rules" / "repository-instructions.md",
        ]
        if path.exists()
    ]

def script_complexity_hotspots(root: Path) -> list[dict[str, object]]:
    hotspots: list[dict[str, object]] = []
    public_command_warn_lines = repo_policy.int_value(root, "limits.script.public_command_warn_lines")
    warn_lines = repo_policy.int_value(root, "limits.script.warn_lines")
    warn_functions = repo_policy.int_value(root, "limits.script.warn_functions")
    warn_bytes = repo_policy.int_value(root, "limits.script.warn_bytes")
    roots = [
        root / ".agents" / "skills",
        root / "automations",
    ]
    for active_root in roots:
        if not active_root.exists():
            continue
        for script in sorted(active_root.rglob("*.py"), key=lambda item: item.as_posix()):
            if "__pycache__" in script.parts:
                continue
            if script.name == "run_self_tests.py":
                continue
            relative_script = repo.relative(root, script).replace("\\", "/")
            if relative_script in SCRIPT_COMPLEXITY_EXEMPT_REL_PATHS:
                continue
            if relative_script in GENERATED_SCRIPT_COPY_REL_PATHS:
                continue
            if not (
                ".agents" in script.parts
                or "Scripts" in script.parts
                or "scripts" in script.parts
            ):
                continue
            text = script.read_text(encoding="utf-8", errors="replace")
            line_count = len(text.splitlines())
            function_count = sum(1 for line in text.splitlines() if line.startswith("def "))
            byte_count = len(text.encode("utf-8"))
            reasons: list[str] = []
            if (
                script.parent.name == "scripts"
                and line_count > public_command_warn_lines
            ):
                reasons.append("public-command-lines")
            if line_count > warn_lines:
                reasons.append("lines")
            if function_count > warn_functions:
                reasons.append("top-level-functions")
            if byte_count > warn_bytes:
                reasons.append("bytes")
            if reasons:
                hotspots.append(
                    {
                        "path": repo.relative(root, script),
                        "lines": line_count,
                        "top_level_functions": function_count,
                        "bytes": byte_count,
                        "public_command_file": script.parent.name == "scripts",
                        "reasons": reasons,
                    }
                )
    return hotspots

def script_complexity_warnings(root: Path) -> list[str]:
    warnings: list[str] = []
    warn_top_level_files = repo_policy.int_value(root, "limits.script.warn_top_level_files")
    roots = [
        root / ".agents" / "skills",
        root / "automations",
    ]
    for active_root in roots:
        if not active_root.exists():
            continue
        for scripts_dir in sorted(active_root.glob("*/scripts"), key=lambda item: item.as_posix()):
            if not scripts_dir.is_dir():
                continue
            top_level_scripts = [
                path
                for path in scripts_dir.glob("*.py")
                if path.name != "run_self_tests.py" and not path.name.endswith("_common.py")
            ]
            if len(top_level_scripts) > warn_top_level_files:
                warnings.append(repo_policy.tagged_warning(
                    "health.script.top-level-files",
                    f"{repo.relative(root, scripts_dir)} has {len(top_level_scripts)} top-level scripts; "
                    "move internal helpers into skill-owned subfolders and keep public command scripts at the top level.",
                ))
    for hotspot in script_complexity_hotspots(root):
        path = str(hotspot["path"])
        line_count = int(hotspot["lines"])
        function_count = int(hotspot["top_level_functions"])
        byte_count = int(hotspot["bytes"])
        reasons = set(hotspot.get("reasons", []))
        if "public-command-lines" in reasons:
            warnings.append(repo_policy.tagged_warning(
                "health.script.lines",
                f"{path} has {line_count} lines in a public command file; "
                "move internal implementation details into a skill-owned support subfolder.",
            ))
        if "lines" in reasons and "public-command-lines" not in reasons:
            warnings.append(repo_policy.tagged_warning(
                "health.script.lines", f"{path} has {line_count} lines; split or compact it when safe."
            ))
        if "top-level-functions" in reasons:
            warnings.append(repo_policy.tagged_warning(
                "health.script.functions",
                f"{path} has {function_count} top-level functions; "
                "consider splitting by responsibility.",
            ))
        if "bytes" in reasons:
            warnings.append(repo_policy.tagged_warning(
                "health.script.bytes", f"{path} is {byte_count} bytes; consider splitting large command logic."
            ))
    return warnings

def routing_budget_warnings(root: Path) -> list[str]:
    warnings: list[str] = []
    warn_chars = repo_policy.int_value(root, "limits.routing.warn_chars")
    warn_rows = repo_policy.int_value(root, "limits.routing.warn_rows")
    for path in (root / ".agents" / "routing.md", root / "automations" / "routing.md"):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        row_count = sum(
            1
            for line in text.splitlines()
            if line.startswith("| ") and "`" in line and not line.startswith("|---")
        )
        if len(text) > warn_chars:
            warnings.append(repo_policy.tagged_warning(
                "health.routing.characters",
                f"{repo.relative(root, path)} has {len(text)} characters; compact routing "
                "descriptions or split large topic areas before it becomes high context.",
            ))
        if row_count > warn_rows:
            warnings.append(repo_policy.tagged_warning(
                "health.routing.rows",
                f"{repo.relative(root, path)} has {row_count} routing rows; keep rows terse "
                "and use full skill docs only after selection.",
            ))
    return warnings

def simplicity_warnings(root: Path) -> list[str]:
    warnings: list[str] = []
    obsolete_patterns = {
        "scripts/manage.py": "root scripts are obsolete; use .agents/manage.py",
        ".agents/skills/skill-manager/scripts/manage_repo.py": "old manager script name is obsolete; use .agents/manage.py or repo_manager.py",
        ".agents/manage.py doctor": "old health command is obsolete; use .agents/manage.py check-repo-health",
        ".agents/commands.md": "central command references are obsolete; use skill-owned SKILL.md files and script --help",
        ".agents/routing.json": "routing JSON is obsolete; use .agents/registry.json",
        "automations/routing.json": "workflow routing JSON is obsolete; use automations/registry.json",
        "skill-maintainer": "old skill-maintainer name is obsolete; use skill-manager",
    }
    markdown_files = sorted(
        set(active_markdown_files(root) + instruction_adapter_files(root)),
        key=lambda item: item.as_posix(),
    )
    for path in markdown_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        for pattern, message in obsolete_patterns.items():
            if pattern in text:
                warnings.append(f"{repo.relative(root, path)} references {pattern}; {message}.")
    warnings.extend(hidden_character_warnings(root))

    agents_file = root / "AGENTS.md"
    if agents_file.exists():
        text = agents_file.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        char_count = len(text.replace("\r\n", "\n"))
        agents_warn_chars = repo_policy.int_value(root, "limits.agents.warn_chars")
        agents_fail_chars = repo_policy.int_value(root, "limits.agents.fail_chars")
        if COMPLETION_CONTRACT_HEADING not in text:
            warnings.append(
                "AGENTS.md is missing a Completion Contract section; final responses "
                f"{COMPLETION_CHECK_REPORTING}"
            )
        if char_count > agents_warn_chars:
            warnings.append(repo_policy.tagged_warning(
                "health.agents.characters",
                f"AGENTS.md has {char_count} characters; keep it under "
                f"{agents_warn_chars} when possible and never above {agents_fail_chars}.",
            ))
        if "do not edit generated" not in lowered:
            warnings.append(
                "AGENTS.md should state that generated files are not edited by hand."
            )
        if "failed command" not in lowered or "skipped:" not in lowered or "blocked" not in lowered:
            warnings.append(
                "AGENTS.md should require explicit reporting for skipped, blocked, "
                "and failed checks."
            )
        if "low-context" not in lowered or ".agents/routing.md" not in lowered:
            warnings.append(
                "AGENTS.md should require low-context routing before full skill "
                "or workflow folder reads."
            )
        if (
            "announce skill use" not in lowered
            or "using <skill> for <concrete reason>" not in lowered
            or "through" not in lowered
            or SKILL_USED_REPORT not in lowered
        ):
            warnings.append(
                "AGENTS.md should define concise skill-use communication: announce "
                "only material skill use with a concrete reason."
            )
        if "python -B .agents/manage.py finish" not in text:
            warnings.append(
                "AGENTS.md should name `python -B .agents/manage.py finish` as "
                "the authoritative completion command."
            )

    for skill_name in repo.MANAGER_SKILL_NAMES:
        skill_file = root / ".agents" / "skills" / skill_name / "SKILL.md"
        if skill_file.exists():
            text = skill_file.read_text(encoding="utf-8", errors="replace")
            if COMPLETION_CONTRACT_HEADING not in text:
                warnings.append(
                    f"{repo.relative(root, skill_file)} is missing a Completion Contract section; "
                    f"manager skills {COMPLETION_CHECK_REPORTING}"
                )
            lowered_skill = text.lower()
            if "low-context" not in lowered_skill or "skipped" not in lowered_skill:
                warnings.append(
                    f"{repo.relative(root, skill_file)} {LOW_CONTEXT_COMPLETION_REPORT}"
                )
            if skill_name == "skill-manager" and SKILL_USED_REPORT not in lowered_skill:
                warnings.append(
                    f"{repo.relative(root, skill_file)} should require reasoned skill-use "
                    "reporting in its Completion Contract."
                )
            if skill_name == "workflow-manager" and (
                "workflow used: <path> - <reason>" not in lowered_skill
                or "skill invoked: <name> - <reason>" not in lowered_skill
            ):
                warnings.append(
                    f"{repo.relative(root, skill_file)} should require reasoned workflow "
                    "and skill reporting in its Completion Contract."
                )

    for path in active_markdown_files(root):
        if path.name in {"SKILL.md", "routing.md"}:
            continue
        if is_intentional_deep_guide(root, path):
            continue
        words = text_word_count(path)
        if words > repo_policy.int_value(root, "limits.documentation.warn_words"):
            warnings.append(repo_policy.tagged_warning(
                "health.documentation.words",
                f"{repo.relative(root, path)} has {words} words; consider compacting or moving "
                "details behind lower-context routing or skill-owned docs.",
            ))
    warnings.extend(script_complexity_warnings(root))
    warnings.extend(routing_budget_warnings(root))
    warnings.extend(context_budget_warnings(root))
    warnings.extend(unsupported_memory_claim_warnings(root))
    return sorted(set(warnings))
