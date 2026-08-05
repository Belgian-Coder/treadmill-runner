"""Start-run checklist helpers for workflow lifecycle commands."""

from __future__ import annotations

from pathlib import Path

import workflow_manager_common as common
from validation_support import manifests as contract_manifests


def unique_list(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def build_start_checklist(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    *,
    manifest: dict[str, object],
    progress_document: str = "",
) -> dict[str, object]:
    validation = manifest.get("validation") if isinstance(manifest.get("validation"), list) else []
    commands = contract_manifests.command_specs(manifest.get("commands"))
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), list) else []
    context_required = any("artifacts/context/context-packet" in str(item) for item in outputs)
    context_evidence = (
        manifest.get("context_evidence")
        if isinstance(manifest.get("context_evidence"), dict)
        else {}
    )
    context_evidence_required = context_evidence.get("required") is True
    run_id = run_dir.name
    first_commands = [
        f"python -B .agents/manage.py workflow hooks --name {workflow_name} --run-id {run_id} --format json",
        *(
            [f"python -B .agents/manage.py workflow context-evidence --name {workflow_name} --run-id {run_id} --event start --check --format json"]
            if context_evidence_required
            else []
        ),
        f"python -B .agents/manage.py workflow resume --name {workflow_name} --run-id {run_id}",
        f"python -B .agents/manage.py workflow checkpoint --name {workflow_name} --run-id {run_id} --write",
    ]
    if context_required:
        first_commands.append(
            f"python -B .agents/manage.py workflow context --name {workflow_name} --run-id {run_id} --write"
        )
    first_commands.extend(str(item) for item in validation)
    evidence_targets = [
        common.relative(root, run_dir / "run.json"),
        common.relative(root, run_dir / "REPORT.md"),
    ]
    if progress_document:
        evidence_targets.append(progress_document)
    return {
        "status": "pending",
        "required_before_work": [
            "Read automations/routing.md, WORKFLOW.md, and module.json.",
            "Inspect resolved workflow hooks before relying on lifecycle automation.",
            "Confirm required context evidence exists or records why it is blocked.",
            "Read only the current phase in instructions.md when present.",
            "Record loaded and skipped context, decisions, blockers, failed checks, and next action in run.json.",
            "Keep REPORT.md and workflow-owned progress documents aligned with run.json.",
        ],
        "required_before_implementation": [
            "Fill the workflow plan or phase checklist with observable actions and evidence targets.",
            "Run workflow plan-check and resolve missing context evidence plus section/row quality issues before implementation.",
            "Run workflow-specific plan checks when declared.",
            "Keep approval pending until plan-check passes, then record explicit approval before implementation.",
        ],
        "required_before_finish": [
            "Run declared validation commands or record skipped/blocked reasons.",
            "Run workflow finish and resolve reported issues.",
            "Refresh run indexes when workflow finish passes.",
        ],
        "first_commands": unique_list(first_commands),
        "declared_validation": [str(item) for item in validation],
        "declared_scripts": [
            command
            for command in commands
            if any(
                argument.endswith(".py") or argument == ".agents/manage.py"
                for argument in contract_manifests.module_contract_v3.command_argv(command)
            )
        ],
        "declared_script_displays": [
            contract_manifests.module_contract_v3.command_display(command)
            for command in commands
            if any(
                argument.endswith(".py") or argument == ".agents/manage.py"
                for argument in contract_manifests.module_contract_v3.command_argv(command)
            )
        ],
        "evidence_targets": evidence_targets,
    }


def build_start_preflight_packet(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    *,
    from_request: str = "",
    start_checklist: dict[str, object],
    context_packet_path: str = "",
    checkpoint_paths: list[str] | None = None,
    next_command: str = "",
) -> dict[str, object]:
    module_dir = root / "automations" / workflow_name
    navigation_handoff = root / "automations" / "navigation" / "artifacts" / "maps" / "HANDOFF.md"
    source_orientation = common.relative(root, navigation_handoff) if navigation_handoff.exists() else ""
    workflow_entry = f"automations/{workflow_name}/{common.workflow_start_relative(module_dir)}"
    read_first = unique_list(
        [
            context_packet_path,
            source_orientation,
            "automations/routing.md",
            workflow_entry,
            f"automations/{workflow_name}/module.json",
        ]
    )
    evidence_targets = [
        str(item)
        for item in start_checklist.get("evidence_targets", [])
        if str(item).strip()
    ]
    if context_packet_path:
        evidence_targets.append(context_packet_path)
    if checkpoint_paths:
        evidence_targets.extend(str(item) for item in checkpoint_paths if str(item).strip())
    first_commands = [
        str(item)
        for item in start_checklist.get("first_commands", [])
        if str(item).strip()
    ]
    declared_validation = [
        str(item)
        for item in start_checklist.get("declared_validation", [])
        if str(item).strip()
    ]
    return {
        "schema_version": 1,
        "tool": "workflow-manager.start-preflight",
        "owner": f"workflow:{workflow_name}",
        "workflow": workflow_name,
        "run_id": run_dir.name,
        "confidence": "routed-from-request" if from_request else "explicit-workflow",
        "read_first": read_first,
        "source_orientation_file": source_orientation,
        "tool_only_inputs": [
            "automations/navigation/artifacts/maps/handoff.json",
            "automations/navigation/artifacts/maps/staleness.json",
            "raw navigation JSON",
        ],
        "next_command": next_command,
        "required_validation_gates": unique_list([*first_commands, *declared_validation]),
        "evidence_targets": unique_list(evidence_targets),
        "stop_conditions": [
            "read_first file is missing",
            "workflow hooks report unsafe required hooks",
            "required context evidence is missing",
            "plan-check fails before implementation",
            "raw navigation JSON would be needed for model context",
        ],
        "boundary": (
            "Workflow start preflight is compact routing evidence for clean-context agents; "
            "load human-readable files and command output, not raw generated navigation JSON."
        ),
    }
