"""Workflow module layout and content validation."""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import workflow_manager_common as common
from automation_validation_rules import (
    ALLOWED_AUTOMATIONS_ROOT_FILES,
    DISALLOWED_AUTOMATIONS_ROOT_DIRS,
    KNOWN_MANAGE_COMMANDS,
    START_MANAGE_COMMAND_PATTERN,
)
from validation_support.discovery import discover_automation_dirs, known_skill_names
from validation_support.manifests import (
    module_contract_v3,
    read_optional_manifest,
    validate_global_hooks_manifest,
    validate_manifest,
)

REQUIRED_INSTRUCTION_SECTIONS = ("Always Load", "Stop Rules", "Completion Contract")
TERMINAL_PHASES = {"complete", "completed", "done", "closed", "finish", "finished"}
UPDATED_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EXTERNAL_METADATA_CORE_FIELDS = {
    "branch_policy",
    "gates",
    "input_schema",
    "integrations",
    "template_layers",
    "updated",
}


def available_manage_commands(root: Path) -> set[str]:
    launcher = root / ".agents" / "manage.py"
    if launcher.exists():
        try:
            completed = subprocess.run(
                [sys.executable, "-B", str(launcher), "--help"],
                cwd=root,
                env=common.child_env(),
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return set(KNOWN_MANAGE_COMMANDS)
        if completed.returncode == 0:
            match = re.search(r"\{([^}]+)\}", completed.stdout)
            if match:
                commands = {item.strip() for item in match.group(1).split(",") if item.strip()}
                if commands:
                    return commands
    return set(KNOWN_MANAGE_COMMANDS)


def has_module_contract(module_dir: Path) -> bool:
    return (module_dir / "module.json").exists()


def external_metadata_errors(module_dir: Path, manifest: dict[str, Any], skills: set[str]) -> tuple[list[str], list[str], dict[str, Any]]:
    metadata_path = manifest.get("metadata_path")
    if metadata_path is None:
        return [], [], manifest
    if not isinstance(metadata_path, str) or not metadata_path.strip():
        return ["metadata_path must be a non-empty relative JSON path when provided."], [], manifest
    metadata_relative = Path(metadata_path)
    if metadata_relative.is_absolute() or ".." in metadata_relative.parts or metadata_relative.suffix.lower() != ".json":
        return ["metadata_path must be a safe workflow-relative JSON file."], [], manifest
    path = module_dir / metadata_relative
    try:
        resolved_path = path.resolve()
        resolved_module = module_dir.resolve()
    except OSError as exc:
        return [f"metadata_path could not be resolved: {exc}"], [], manifest
    if resolved_module not in resolved_path.parents:
        return ["metadata_path must stay inside the workflow module."], [], manifest
    data, error = common.read_json_file(path)
    if error:
        return [f"{metadata_path} could not be loaded: {error}"], [], manifest
    if not isinstance(data, dict):
        return [f"{metadata_path} must contain a JSON object."], [], manifest
    updated = data.get("updated")
    if not isinstance(updated, str) or not UPDATED_DATE_PATTERN.match(updated.strip()):
        return [f"{metadata_path}.updated must be a YYYY-MM-DD date."], [], manifest
    normalized_metadata = dict(data)
    core_metadata = {
        key: value
        for key, value in normalized_metadata.items()
        if key in EXTERNAL_METADATA_CORE_FIELDS
    }
    metadata_extensions = {
        key: value
        for key, value in normalized_metadata.items()
        if key not in EXTERNAL_METADATA_CORE_FIELDS
    }
    merged = {**manifest, **core_metadata, "metadata_path": metadata_path}
    if metadata_extensions:
        extensions = dict(merged.get("extensions", {}))
        extensions["skills-harness/workflow-metadata"] = metadata_extensions
        merged["extensions"] = extensions
    errors, warnings = validate_manifest(module_dir, merged, skills)
    return errors, warnings, merged


def changed_files_since_head(root: Path) -> set[str]:
    try:
        completed = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMRT", "HEAD"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if completed.returncode != 0:
        return set()
    return {line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()}


def validate_workflow_metadata_freshness(root: Path, module_dir: Path, manifest: dict[str, Any] | None) -> list[str]:
    start = module_dir / "WORKFLOW.md"
    start_relative = common.relative(root, start).replace("\\", "/")
    if start_relative not in changed_files_since_head(root):
        return []
    updated = str((manifest or {}).get("updated", "")).strip()
    today = date.today().isoformat()
    if updated == today:
        return []
    metadata_path = str((manifest or {}).get("metadata_path") or "metadata/workflow-metadata.json")
    return [
        f"{start_relative} changed; update {common.relative(root, module_dir / metadata_path)} "
        f"`updated` to {today}."
    ]


def fenced_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    in_block = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if in_block:
                blocks.append("\n".join(current))
                current = []
                in_block = False
            else:
                in_block = True
            continue
        if in_block:
            current.append(line)
    return blocks


def validate_start_copy_blocks(root: Path, start: Path, text: str) -> list[str]:
    errors: list[str] = []
    for block in fenced_blocks(text):
        manage_commands = START_MANAGE_COMMAND_PATTERN.findall(block)
        if len(manage_commands) > 1:
            errors.append(
                f"{common.relative(root, start)} must keep each `.agents/manage.py` "
                "command in a separate fenced block."
            )
    return errors


def validate_example_prompts(root: Path, start: Path, text: str) -> list[str]:
    errors: list[str] = []
    if "## Example Prompts" not in text:
        return [f"{common.relative(root, start)} is missing ## Example Prompts."]
    required_labels = ("Start", "Resume", "Handoff", "Finish")
    for label in required_labels:
        pattern = re.compile(rf"^-\s+{re.escape(label)}\s*:\s*\".+\"$", re.MULTILINE)
        if not pattern.search(text):
            errors.append(
                f"{common.relative(root, start)} Example Prompts must include a copyable {label} prompt."
            )
    return errors


def normalize_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def markdown_heading_titles(text: str) -> list[str]:
    titles: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            titles.append(match.group(2).strip())
    return titles


def phase_heading_matches(title: str, phase: str) -> bool:
    title_normalized = normalize_heading(title.replace(":", " "))
    phase_normalized = normalize_heading(phase)
    if not phase_normalized:
        return False
    wanted = f"phase-{phase_normalized}"
    if title_normalized == wanted or title_normalized.startswith(f"{wanted}-"):
        return True
    title_tokens = set(title_normalized.removeprefix("phase-").split("-"))
    phase_tokens = set(phase_normalized.split("-"))
    return bool(phase_tokens) and phase_tokens.issubset(title_tokens)


def no_phase_reason_recorded(text: str, phase: str) -> bool:
    phase_normalized = normalize_heading(phase)
    if not phase_normalized:
        return False
    for line in text.splitlines():
        line_normalized = normalize_heading(line)
        if phase_normalized in line_normalized and any(
            marker in line_normalized
            for marker in ("no-phase", "no-phase-detail", "phase-exception", "terminal-phase")
        ):
            return True
    return phase_normalized in TERMINAL_PHASES


def manifest_phase_ids(manifest: dict[str, Any]) -> list[str]:
    phases = manifest.get("phases")
    if not isinstance(phases, list):
        return []
    result: list[str] = []
    for phase in phases:
        if isinstance(phase, str):
            value = phase.strip()
        elif isinstance(phase, dict):
            value = str(phase.get("id", "")).strip()
        else:
            value = ""
        if value:
            result.append(value)
    return result


def validate_instruction_contract(
    root: Path,
    module_dir: Path,
    manifest: dict[str, Any] | None,
) -> list[str]:
    instructions = module_dir / "instructions.md"
    if not instructions.exists():
        return []
    text = common.read_text(instructions, limit=80_000)
    headings = markdown_heading_titles(text)
    normalized_headings = {normalize_heading(heading) for heading in headings}
    warnings: list[str] = []
    for section in REQUIRED_INSTRUCTION_SECTIONS:
        if normalize_heading(section) not in normalized_headings:
            warnings.append(f"{common.relative(root, instructions)} missing structured section ## {section}.")
    if isinstance(manifest, dict):
        for phase in manifest_phase_ids(manifest):
            if any(phase_heading_matches(heading, phase) for heading in headings):
                continue
            if no_phase_reason_recorded(text, phase):
                continue
            warnings.append(
                f"{common.relative(root, instructions)} phase '{phase}' has no matching ## Phase heading or no-phase reason."
            )
    return warnings


def manifest_values(manifest: dict[str, Any], key: str) -> list[str]:
    values = manifest.get(key)
    if not isinstance(values, list):
        return []
    if key == "commands":
        return [module_contract_v3.command_display(item) for item in values]
    return [str(item) for item in values]


def resume_evidence_references_context_packet(manifest: dict[str, Any]) -> bool:
    context_evidence = manifest.get("context_evidence")
    if not isinstance(context_evidence, dict):
        return False
    queries = context_evidence.get("resume_queries")
    if not isinstance(queries, list):
        return False
    for query in queries:
        if not isinstance(query, dict):
            continue
        values = [str(query.get("question", ""))]
        fallback = query.get("fallback_paths")
        if isinstance(fallback, list):
            values.extend(str(item) for item in fallback)
        haystack = " ".join(values).lower()
        if "context packet" in haystack or "context-packet" in haystack or "artifacts/context/context-packet" in haystack:
            return True
    return False


def validate_context_declaration_contract(root: Path, module_dir: Path, manifest: dict[str, Any] | None) -> list[str]:
    if not isinstance(manifest, dict):
        return []
    outputs = manifest_values(manifest, "outputs")
    command_argvs = [
        module_contract_v3.command_argv(command)
        for command in manifest.get("commands", [])
        if module_contract_v3.command_argv(command)
    ]
    declares_context_output = any("artifacts/context/context-packet.json" in item.replace("\\", "/") for item in outputs)
    declares_context_command = any(
        ("--write" in argv or (len(argv) == 1 and "--write" in argv[0]))
        and (
            any(
                argv[index : index + 2] == ["workflow", "context"]
                for index in range(len(argv) - 1)
            )
            or (len(argv) == 1 and "workflow context" in argv[0])
        )
        for argv in command_argvs
    )
    references_context = resume_evidence_references_context_packet(manifest)
    warnings: list[str] = []
    label = common.relative(root, module_dir / "module.json")
    if references_context and not declares_context_output:
        warnings.append(f"{label} context_evidence.resume_queries references context packets but outputs do not declare context-packet.json.")
    if declares_context_output and not declares_context_command:
        warnings.append(f"{label} outputs declare context-packet.json but commands do not include workflow context --write.")
    if declares_context_command and not declares_context_output:
        warnings.append(f"{label} commands include workflow context --write but outputs do not declare context-packet.json.")
    if declares_context_output:
        for required in (
            "artifacts/documentation/documentation-delta.json",
            "artifacts/documentation/documentation-delta.md",
        ):
            if not any(required in item.replace("\\", "/") for item in outputs):
                warnings.append(
                    f"{label} outputs declare context-packet.json but do not declare {required.rsplit('/', 1)[-1]}."
                )
    return warnings


def v2_run_packet_errors(root: Path, module_dir: Path) -> list[str]:
    runs_dir = module_dir / "runs"
    if not runs_dir.exists():
        return []
    errors: list[str] = []
    required_fields = {
        "schema_version",
        "tool",
        "workflow",
        "run_id",
        "current_phase",
        "status",
        "decisions",
        "checks",
        "commands",
        "evidence",
        "skipped",
        "blocked",
        "failed",
        "handoff",
        "next_action",
    }
    for run_dir in sorted(runs_dir.iterdir(), key=lambda item: item.name.lower()):
        if not run_dir.is_dir():
            continue
        run_json = run_dir / "run.json"
        report = run_dir / "REPORT.md"
        if not run_json.exists():
            errors.append(f"{common.relative(root, run_dir)} is missing run.json.")
            continue
        data, error = common.read_json_file(run_json)
        if error or not isinstance(data, dict):
            errors.append(f"{common.relative(root, run_json)} must be a JSON object.")
            continue
        if data.get("schema_version") != 2:
            errors.append(f"{common.relative(root, run_json)} schema_version must be 2.")
        if data.get("workflow") != module_dir.name:
            errors.append(f"{common.relative(root, run_json)} workflow must match {module_dir.name}.")
        missing = sorted(required_fields - set(data))
        if missing:
            errors.append(f"{common.relative(root, run_json)} is missing run fields: {', '.join(missing)}.")
        if not isinstance(data.get("checks"), dict):
            errors.append(f"{common.relative(root, run_json)} checks must be an object.")
        if not isinstance(data.get("handoff"), dict):
            errors.append(f"{common.relative(root, run_json)} handoff must be an object.")
        if not report.exists():
            errors.append(f"{common.relative(root, run_dir)} is missing REPORT.md.")
    return errors


def expected_output_classification(module_dir: Path, value: str) -> str:
    normalized = value.strip().strip("`").replace("\\", "/")
    if not normalized or normalized.lower() in {"none", "n/a", "not applicable", "unspecified"}:
        return "invalid"
    if normalized.startswith(("runs/", "scripts/", "templates/", "suites/", "docs/", "assets/", "artifacts/")):
        return "workflow-owned"
    if normalized.startswith(f"automations/{module_dir.name}/"):
        return "workflow-owned"
    if normalized.startswith(("generated/", "dist/", "build/", "coverage/")):
        return "generated"
    if normalized.startswith((".agents/", ".github/", ".claude/", "automations/")):
        return "external"
    if normalized.startswith("/") or ".." in normalized.split("/"):
        return "invalid"
    return "target-project"


def validate_current_module(root: Path, module_dir: Path, skills: set[str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    allowed_files = {"WORKFLOW.md", "module.json", "instructions.md", "LICENSE.txt", "NOTICE.txt"}
    allowed_dirs = {"scripts", "templates", "suites", "runs", "docs", "assets", "artifacts", "diagrams", "metadata"}
    for child in sorted(module_dir.iterdir(), key=lambda item: item.name.lower()):
        if child.is_file() and child.name not in allowed_files:
            errors.append(f"{common.relative(root, child)} is not part of the current workflow layout.")
        elif child.is_dir() and child.name not in allowed_dirs:
            errors.append(f"{common.relative(root, child)} is not part of the current workflow layout.")

    for required in ("WORKFLOW.md", "module.json"):
        if not (module_dir / required).exists():
            errors.append(f"{common.relative(root, module_dir)} is missing {required}.")

    for path in common.iter_files(module_dir, max_files=5000):
        relative_path = common.relative(module_dir, path).replace("\\", "/")
        if path.suffix.lower() in common.DISALLOWED_SCRIPT_SUFFIXES:
            errors.append(
                f"{common.relative(root, path)} is not allowed. "
                "Use a Python 3 entry point instead of shell, batch, or PowerShell."
            )
        if (
            path.suffix.lower() == ".txt"
            and path.name not in {"LICENSE.txt", "NOTICE.txt"}
            and not relative_path.startswith("runs/")
        ):
            errors.append(
                f"{common.relative(root, path)} is not allowed in active workflow definitions. "
                "Use Markdown for human-readable files or JSON for machine evidence; raw .txt is only allowed under runs/ evidence."
            )
        if relative_path == "agents/openai.yaml":
            errors.append(f"{common.relative(root, path)} is not allowed in current workflow modules.")

    manifest, manifest_error = read_optional_manifest(module_dir)
    if manifest_error:
        errors.append(f"module.json could not be loaded: {manifest_error}")
    elif manifest is None:
        errors.append(f"{common.relative(root, module_dir / 'module.json')} is required.")
    else:
        manifest_errors, manifest_warnings = validate_manifest(module_dir, manifest, skills)
        errors.extend(manifest_errors)
        warnings.extend(manifest_warnings)
        metadata_errors, metadata_warnings, manifest = external_metadata_errors(module_dir, manifest, skills)
        errors.extend(metadata_errors)
        warnings.extend(metadata_warnings)

    start = module_dir / "WORKFLOW.md"
    if start.exists():
        text = common.read_text(start, limit=80_000)
        if "module.json" not in text:
            warnings.append(f"{common.relative(root, start)} should reference module.json as the canonical contract.")
        errors.extend(validate_start_copy_blocks(root, start, text))
        errors.extend(validate_example_prompts(root, start, text))

    instructions = module_dir / "instructions.md"
    if instructions.exists():
        text = common.read_text(instructions, limit=80_000).lower()
        for term in ("read:", "do:", "write:", "done when:", "if blocked:"):
            if term not in text:
                warnings.append(f"{common.relative(root, instructions)} should keep phase steps resumable; missing {term}.")
                break
        warnings.extend(validate_instruction_contract(root, module_dir, manifest if isinstance(manifest, dict) else None))

    warnings.extend(validate_context_declaration_contract(root, module_dir, manifest if isinstance(manifest, dict) else None))
    warnings.extend(validate_workflow_metadata_freshness(root, module_dir, manifest if isinstance(manifest, dict) else None))

    errors.extend(v2_run_packet_errors(root, module_dir))
    return sorted(set(errors)), sorted(set(warnings))


def validate_module(
    root: Path,
    module_dir: Path,
    skills: set[str],
    manage_commands: set[str],
    *,
    strict_phase_quality: bool = False,
) -> tuple[list[str], list[str]]:
    _ = manage_commands, strict_phase_quality
    errors: list[str] = []
    warnings: list[str] = []
    if not common.SKILL_NAME_PATTERN.match(module_dir.name):
        errors.append(f"{common.relative(root, module_dir)} must use lowercase letters, digits, and hyphens.")
    if not has_module_contract(module_dir):
        errors.append(f"{common.relative(root, module_dir)} is missing required module.json.")
        return sorted(set(errors)), warnings
    module_errors, module_warnings = validate_current_module(root, module_dir, skills)
    errors.extend(module_errors)
    if strict_phase_quality:
        errors.extend(module_warnings)
    else:
        warnings.extend(module_warnings)
    return sorted(set(errors)), sorted(set(warnings))


def validate_automations(
    root: Path, workflow_name: str | None = None, *, strict_phase_quality: bool = False
) -> tuple[list[str], list[str], list[Path]]:
    errors: list[str] = []
    warnings: list[str] = []

    obsolete_root_files = [root / "automations" / "routing.json"]
    for path in obsolete_root_files:
        if path.exists():
            errors.append(f"{common.relative(root, path)} is obsolete; use automations/routing.md and automations/registry.json.")

    global_hooks = root / "automations" / "hooks.json"
    if global_hooks.exists():
        data, error = common.read_json_file(global_hooks)
        if error or not isinstance(data, dict):
            errors.append(f"{common.relative(root, global_hooks)} must be a JSON object.")
        else:
            hook_errors = validate_global_hooks_manifest(data, Path(common.relative(root, global_hooks)))
            errors.extend(hook_errors)

    automations_root = root / "automations"
    if automations_root.exists():
        for child in sorted(automations_root.iterdir(), key=lambda item: item.name.lower()):
            if child.is_file() and child.name not in ALLOWED_AUTOMATIONS_ROOT_FILES:
                errors.append(f"{common.relative(root, child)} is not allowed; workflow modules must live under automations/<workflow-name>/.")
            elif child.is_dir() and child.name in DISALLOWED_AUTOMATIONS_ROOT_DIRS:
                errors.append(f"{common.relative(root, child)} is not allowed; {DISALLOWED_AUTOMATIONS_ROOT_DIRS[child.name]}")

    skills = known_skill_names(root)
    manage_commands = available_manage_commands(root)
    modules = discover_automation_dirs(root)
    if workflow_name:
        if not common.SKILL_NAME_PATTERN.match(workflow_name):
            errors.append("workflow name must use lowercase letters, digits, and hyphens.")
            modules = []
        else:
            selected = root / "automations" / workflow_name
            if not selected.exists() or not selected.is_dir():
                errors.append(f"automation workflow not found: automations/{workflow_name}.")
                modules = []
            else:
                modules = [selected]

    for module_dir in modules:
        module_errors, module_warnings = validate_module(
            root,
            module_dir,
            skills,
            manage_commands,
            strict_phase_quality=strict_phase_quality,
        )
        errors.extend(module_errors)
        warnings.extend(module_warnings)

    return sorted(set(errors)), sorted(set(warnings)), modules
