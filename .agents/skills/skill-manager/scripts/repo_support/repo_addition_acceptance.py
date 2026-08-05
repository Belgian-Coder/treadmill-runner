#!/usr/bin/env python3
"""Addition-acceptance checks for changed-file validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import skill_manager_common as skill_common
from repo_support import repo_common as repo

INSTRUCTION_GENERATED = {
    ".aider.conf.yml",
    ".continue/rules/repository-instructions.md",
    ".github/copilot-instructions.md",
    ".claude/CLAUDE.md",
    "GEMINI.md",
}
SKILL_GENERATED = {".agents/routing.md", ".agents/registry.json"}
WORKFLOW_GENERATED = {"automations/routing.md", "automations/registry.json"}
WORKFLOW_GLOBAL_FILES = {"automations/hooks.json"}
INSTRUCTION_ADAPTER_GENERATOR_SOURCES = {
    ".agents/skills/skill-manager/scripts/repo_support/repo_generated.py",
}
SKILL_ROUTING_GENERATOR_SOURCES = {
    ".agents/skills/skill-manager/scripts/sync_skill_routing.py",
}
WORKFLOW_ROUTING_GENERATOR_SOURCES = {
    ".agents/skills/workflow-manager/scripts/sync_automation_routing.py",
}
ADDITION_STATUS_MARKERS = {"A", "?", "R", "C"}
ALLOWED_UNOWNED_NEW_PREFIXES = {"docs/", "evidence/"}
ALLOWED_UNOWNED_NEW_FILES = {
    "AGENTS.md",
    "README.md",
    "orchestration.md",
    "LICENSE.txt",
    "NOTICE.txt",
    ".gitignore",
    ".agents/harness-payload.json",
    ".agents/project-policy.json",
    ".agents/orchestration.json",
}


def normalized_paths(paths: list[str]) -> list[str]:
    return sorted(dict.fromkeys(path.replace("\\", "/") for path in paths if path.strip()))


def skill_name_from_path(path: str) -> str:
    parts = path.split("/")
    return parts[2] if len(parts) >= 3 and path.startswith(".agents/skills/") else ""


def workflow_name_from_path(path: str) -> str:
    parts = path.split("/")
    if len(parts) < 2 or not path.startswith("automations/"):
        return ""
    if path in WORKFLOW_GENERATED or path in WORKFLOW_GLOBAL_FILES:
        return ""
    return parts[1]


def is_generated_path(path: str) -> bool:
    return (
        path in INSTRUCTION_GENERATED
        or path in SKILL_GENERATED
        or path in WORKFLOW_GENERATED
        or path.startswith(".claude/skills/")
    )


def generated_path_has_source(path: str, all_paths: set[str]) -> bool:
    if path in INSTRUCTION_GENERATED:
        return "AGENTS.md" in all_paths or bool(
            INSTRUCTION_ADAPTER_GENERATOR_SOURCES.intersection(all_paths)
        )
    if path in SKILL_GENERATED:
        return any(item.startswith(".agents/skills/") for item in all_paths) or bool(
            SKILL_ROUTING_GENERATOR_SOURCES.intersection(all_paths)
        )
    if path.startswith(".claude/skills/"):
        parts = path.split("/")
        skill_name = parts[2] if len(parts) >= 3 else ""
        return bool(skill_name) and (
            any(item.startswith(f".agents/skills/{skill_name}/") for item in all_paths)
            or bool(INSTRUCTION_ADAPTER_GENERATOR_SOURCES.intersection(all_paths))
        )
    if path in WORKFLOW_GENERATED:
        return any(
            item.startswith("automations/") and item not in WORKFLOW_GENERATED
            for item in all_paths
        ) or bool(WORKFLOW_ROUTING_GENERATOR_SOURCES.intersection(all_paths))
    return False


def is_integration_descriptor_path(path: str) -> bool:
    parts = path.split("/")
    return (
        len(parts) == 4
        and parts[0] == ".agents"
        and parts[1] == "integrations"
        and bool(parts[2])
        and parts[3] == "integration.json"
    )


def issue(path: str, owner: str, category: str, reason: str, next_command: str) -> dict[str, str]:
    return {
        "path": path,
        "owner": owner,
        "category": category,
        "reason": reason,
        "next_command": next_command,
    }


def dotnet_skill_naming_issues(root: Path, skill_name: str) -> list[str]:
    skill_dir = root / ".agents" / "skills" / skill_name
    metadata, _metadata_error = skill_common.parse_frontmatter_file(skill_dir / "SKILL.md")
    manifest, _manifest_error = skill_common.load_skill_manifest(skill_dir)
    description = str((metadata or {}).get("description", ""))
    summary = str(manifest.get("summary", "")) if isinstance(manifest, dict) else ""
    return skill_common.dotnet_skill_naming_errors(skill_name, description, summary)


def addition_acceptance_report(
    root: Path,
    *,
    paths: list[str] | None = None,
    new_paths: list[str] | None = None,
    changed_files_func: Callable[[Path], list[str]] | None = None,
    changed_file_statuses_func: Callable[[Path], dict[str, set[str]]] | None = None,
) -> dict[str, object]:
    if paths is None:
        paths = changed_files_func(root) if changed_files_func else []
    all_paths = normalized_paths(paths)
    status_map = changed_file_statuses_func(root) if new_paths is None and changed_file_statuses_func else {}
    installed_harness_paths = repo.installed_harness_manifest_paths(root)
    additions = normalized_paths(
        new_paths
        if new_paths is not None
        else [
            path
            for path, markers in status_map.items()
            if any(marker in markers for marker in ADDITION_STATUS_MARKERS)
        ]
    )
    all_path_set = set(all_paths)
    issues: list[dict[str, str]] = []

    def deleted_only_module(name: str, prefix: str) -> bool:
        module_paths = [path for path in all_paths if path.startswith(f"{prefix}{name}/")]
        if not module_paths or (root / prefix / name).exists():
            return False
        return all(status_map.get(path, {"M"}) <= {"D"} for path in module_paths)

    skill_names = sorted(
        {
            name
            for path in all_paths
            if (name := skill_name_from_path(path))
            and not deleted_only_module(name, ".agents/skills/")
        }
    )
    workflow_names = sorted(
        {
            name
            for path in all_paths
            if (name := workflow_name_from_path(path))
            and not deleted_only_module(name, "automations/")
        }
    )
    generated_paths = [path for path in all_paths if is_generated_path(path)]

    for skill_name in skill_names:
        skill_dir = root / ".agents" / "skills" / skill_name
        if not (skill_dir / "SKILL.md").exists():
            issues.append(
                issue(
                    f".agents/skills/{skill_name}",
                    "skill-manager",
                    "missing-skill-contract",
                    "missing SKILL.md",
                    f"python -B .agents/manage.py inspect-skill --skill .agents/skills/{skill_name} --fast",
                )
            )
        if not (skill_dir / "module.json").exists():
            issues.append(
                issue(
                    f".agents/skills/{skill_name}",
                    "skill-manager",
                    "missing-skill-contract",
                    "missing module.json",
                    f"python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/{skill_name}",
                )
            )
        for reason in dotnet_skill_naming_issues(root, skill_name):
            issues.append(
                issue(
                    f".agents/skills/{skill_name}",
                    "skill-manager",
                    "dotnet-legacy-naming",
                    reason,
                    "rename the skill to dotnet-legacy or route Framework maintenance through dotnet-legacy",
                )
            )

    for workflow_name in workflow_names:
        workflow_dir = root / "automations" / workflow_name
        if not (workflow_dir / "WORKFLOW.md").exists():
            issues.append(
                issue(
                    f"automations/{workflow_name}",
                    "workflow-manager",
                    "missing-workflow-contract",
                    "missing WORKFLOW.md",
                    f"python -B .agents/manage.py validate-automations --name {workflow_name}",
                )
            )
        if not (workflow_dir / "module.json").exists():
            issues.append(
                issue(
                    f"automations/{workflow_name}",
                    "workflow-manager",
                    "missing-workflow-contract",
                    "missing module.json",
                    f"python -B .agents/manage.py validate-automations --name {workflow_name}",
                )
            )

    for generated_path in generated_paths:
        if not generated_path_has_source(generated_path, all_path_set):
            issues.append(
                issue(
                    generated_path,
                    "skill-manager" if not generated_path.startswith("automations/") else "workflow-manager",
                    "generated-without-source",
                    "generated file changed without matching canonical source change",
                    "python -B .agents/manage.py sync --check",
                )
            )

    for path in additions:
        if (
            skill_name_from_path(path)
            or workflow_name_from_path(path)
            or is_generated_path(path)
            or path in WORKFLOW_GLOBAL_FILES
            or path in ALLOWED_UNOWNED_NEW_FILES
            or is_integration_descriptor_path(path)
            or path in installed_harness_paths
            or repo.is_installed_consumer_owned_path(root, path)
            or any(path.startswith(prefix) for prefix in ALLOWED_UNOWNED_NEW_PREFIXES)
        ):
            continue
        issues.append(
            issue(
                path,
                "skill-manager",
                "unowned-new-file",
                "new file is outside an accepted skill, workflow, generated surface, or allowlisted docs/evidence path",
                "move the file under its owning skill/workflow or document an explicit allowlist",
            )
        )

    return {
        "schema_version": 1,
        "tool": "skill-manager.addition-acceptance",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "summary": {
            "changed_files": len(all_paths),
            "new_files": len(additions),
            "skills_checked": len(skill_names),
            "workflows_checked": len(workflow_names),
            "generated_files": len(generated_paths),
            "issue_count": len(issues),
        },
        "changed_files": all_paths,
        "new_files": additions,
        "skills": skill_names,
        "workflows": workflow_names,
        "generated_files": generated_paths,
        "issues": issues,
    }


def render_addition_acceptance(report: dict[str, object], *, verbose: bool = False) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        f"status={report.get('status', '')}",
        (
            "changed={changed} new={new} skills={skills} workflows={workflows} "
            "generated={generated} issues={issues}"
        ).format(
            changed=summary.get("changed_files", 0),
            new=summary.get("new_files", 0),
            skills=summary.get("skills_checked", 0),
            workflows=summary.get("workflows_checked", 0),
            generated=summary.get("generated_files", 0),
            issues=summary.get("issue_count", 0),
        ),
    ]
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        lines.append("issues:")
        for item in issues[:30]:
            if isinstance(item, dict):
                lines.append(
                    f"- {item.get('path')}: {item.get('reason')} "
                    f"({item.get('owner')}; {item.get('category')})"
                )
    if verbose:
        for key in ("skills", "workflows", "generated_files", "new_files"):
            values = report.get(key) if isinstance(report.get(key), list) else []
            if values:
                lines.append(f"{key}: {', '.join(str(value) for value in values[:40])}")
    return "\n".join(lines)
