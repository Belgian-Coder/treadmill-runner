"""Generic workflow run helpers shared by lifecycle commands."""

from __future__ import annotations

import json
import re
from pathlib import Path

import workflow_manager_common as common
import workflow_plan_check
from workflow_context_packet import context_packet_paths
from workflow_support.template_layers import resolved_template_path

TERMINAL_STATUSES = {"done", "complete", "completed", "passed", "skipped", "blocked"}
COMPLETED_RUN_STATUSES = {"done", "complete", "completed", "passed", "skipped"}
SATISFIED_DEPENDENCY_STATUSES = {"done", "complete", "completed", "passed", "skipped"}
LIFECYCLE_OUTPUT_PARTS = (
    "/artifacts/context/",
    "/artifacts/documentation/",
    "/validation/checkpoints/",
    "/validation/hooks/",
    "/validation/context-evidence-",
)


def terminal_status(value: object) -> bool:
    normalized = " ".join(str(value or "").strip().lower().split())
    return normalized in TERMINAL_STATUSES


def dependency_satisfied_status(value: object) -> bool:
    normalized = " ".join(str(value or "").strip().lower().split())
    return normalized in SATISFIED_DEPENDENCY_STATUSES


def normalized_run_health_status(
    run_packet: dict[str, object] | None,
    read_error: str | None = None,
) -> str:
    if read_error:
        return "missing" if read_error.endswith(" not found") else "invalid"
    packet = run_packet if isinstance(run_packet, dict) else {}
    value = packet.get("workflow_status") or packet.get("status")
    normalized = " ".join(str(value or "").strip().lower().split())
    return normalized or "unknown"


def completed_run_status(value: object) -> bool:
    normalized = " ".join(str(value or "").strip().lower().split())
    return normalized in COMPLETED_RUN_STATUSES


def initial_execution_log(workflow_name: str, run_id: str, now: str) -> str:
    return f"""# Workflow Execution Log

## Current State

- Workflow: {workflow_name}
- Run: {run_id}
- Status: partial
- Current phase: orientation
- Last updated: {now}

## Phase Handoffs

### Phase: orientation

- Completed: run scaffold created.
- Skipped: none.
- Blocked: none.
- Failed: none.
- Validation: not run yet.
- Decisions: none yet.
- Next step: fill scaffolded planning files or resume the current workflow phase.

## Commands And Evidence

| Time | Command Or Action | Result | Evidence |
|---|---|---|---|
| {now} | workflow start | scaffolded | run.json, REPORT.md, execution-log.md |

## Plan Item Progress

| Plan Item | Status | Evidence | Owner Or Decision |
|---|---|---|---|

## Plan Variance

At finish, add one row per variance. If execution matched the approved plan, add one `No variance` row and fill every column.

| Package | Planned | Actual | Reason | Approval Impact | Validation Impact |
|---|---|---|---|---|---|

## Independent Review Evidence

Each axis is independent: a pass on one axis does not hide a failure, skip, or blocker on another.
Use `skipped: <substantive reason>` when an axis does not apply; a label without the reason is not evidence.

| Axis | Reviewer Or Method | Result | Evidence | Disposition |
|---|---|---|---|---|
| Spec and plan compliance | | | | |
| Standards and maintainability | | | | |
| Security and authority | | | | |
| Validation and generated artifacts | | | | |

## Validation Evidence Map

| Planned Evidence | Final Evidence | Result |
|---|---|---|

## Reusable Lessons

- Reusable lesson or `No reusable lesson: <reason>`.
""".rstrip() + "\n"


def scaffold_generic_run_files(
    root: Path,
    workflow_name: str,
    module_dir: Path,
    run_dir: Path,
    *,
    now: str,
    profile: str = "default",
    skip_execution_log: bool = False,
) -> list[str]:
    created: list[str] = []
    template_plan = resolved_template_path(root, workflow_name, profile=profile)
    if template_plan is not None and template_plan.exists():
        target = run_dir / "plan.md"
        target.write_text(template_plan.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        created.append(common.relative(root, target))
    if not skip_execution_log:
        target = run_dir / "execution-log.md"
        target.write_text(initial_execution_log(workflow_name, run_dir.name, now), encoding="utf-8", newline="\n")
        created.append(common.relative(root, target))
    return created


def plan_gate(root: Path, workflow_name: str, run_dir: Path) -> dict[str, object]:
    plan_path = run_dir / "plan.md"
    if not plan_path.exists():
        return {}
    report = workflow_plan_check.check_plan(root, workflow_name, run_id=run_dir.name)
    return {
        "status": report.get("status", "unknown"),
        "ok": report.get("ok") is True,
        "plan_path": report.get("plan_path", common.relative(root, plan_path)),
        "quality_summary": report.get("quality_summary", {}),
        "fix_queue": report.get("fix_queue", []),
        "operator_next_action": report.get("operator_next_action", ""),
        "ready_for_approval": report.get("ready_for_approval") is True,
        "implementation_allowed": report.get("implementation_allowed") is True,
        "issues": report.get("issues", []),
    }


def task_statuses(run_packet: dict[str, object]) -> dict[str, str]:
    raw = run_packet.get("task_status")
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    if isinstance(raw, list):
        values: dict[str, str] = {}
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                values[str(item["id"])] = str(item.get("status", ""))
        return values
    return {}


def generic_execution_queue(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    run_packet: dict[str, object],
    manifest: dict[str, object],
) -> list[dict[str, object]]:
    statuses = task_statuses(run_packet)
    tasks = manifest.get("tasks") if isinstance(manifest.get("tasks"), list) else []
    task_section = "Task Graph"
    if not tasks:
        plan_path = run_dir / "plan.md"
        if plan_path.exists():
            sections = workflow_plan_check.parse_sections(common.read_text(plan_path, limit=120_000))
            tasks = [
                {
                    "id": package["id"],
                    "summary": package["outcome"],
                    "depends_on": package["dependencies"],
                    "verification": package["verification"],
                    "handoff": package["handoff"],
                    "status": package.get("status", ""),
                }
                for package in workflow_plan_check.bounded_work_packages(sections)
                if package.get("id")
            ]
            task_section = "Bounded Work Packages"
    effective_statuses = dict(statuses)
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id", ""))
        plan_status = str(task.get("status", ""))
        if task_id and terminal_status(plan_status):
            effective_statuses[task_id] = plan_status
    queue: list[dict[str, object]] = []
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id", ""))
        status = effective_statuses.get(task_id, "")
        if terminal_status(status):
            continue
        dependencies = [str(item) for item in task.get("depends_on", []) if isinstance(item, str)]
        if any(not dependency_satisfied_status(effective_statuses.get(dep, "")) for dep in dependencies):
            continue
        summary = str(task.get("summary") or task_id)
        item = {
            "section": task_section,
            "row": index,
            "id": task_id,
            "phase": str(task.get("phase", "")),
            "step": summary,
            "status": status or "pending",
            "dependencies": dependencies,
            "verification": str(task.get("verification", "")),
            "handoff": str(task.get("handoff", "")),
            "target_path": common.relative(root, run_dir / "run.json"),
            "action": f"Execute task {task_id}: {summary}",
        }
        queue.append(item)
    if queue or tasks:
        return queue

    phase_id = str(run_packet.get("current_phase") or "")
    phase = run_packet.get("phase") if isinstance(run_packet.get("phase"), dict) else {}
    phase_status = phase.get("status") or run_packet.get("status") or ""
    if terminal_status(phase_status):
        return []
    phases = manifest.get("phases") if isinstance(manifest.get("phases"), list) else []
    selected = next((item for item in phases if isinstance(item, dict) and item.get("id") == phase_id), None)
    if selected is None and phases:
        selected = next((item for item in phases if isinstance(item, dict)), None)
    if not isinstance(selected, dict):
        return []
    step = str(selected.get("summary") or selected.get("id") or "Continue current workflow phase.")
    return [
        {
            "section": "Phase",
            "row": 1,
            "id": str(selected.get("id", phase_id)),
            "step": step,
            "status": str(phase_status or "pending"),
            "target_path": common.relative(root, run_dir / "run.json"),
            "action": f"Continue phase {selected.get('id', phase_id)}: {step}",
        }
    ]


def parse_reusable_lesson_section(text: str) -> list[str]:
    match = re.search(r"^##\s+Reusable Lessons\s*$([\s\S]*?)(?=^##\s+|\Z)", text, re.MULTILINE)
    if not match:
        return []
    lessons: list[str] = []
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stripped = stripped.removeprefix("-").strip()
        if not stripped or stripped in {"-", "[ ]"}:
            continue
        if "reusable lesson or `no reusable lesson" in stripped.lower():
            continue
        lessons.append(stripped)
    return lessons


def lesson_candidates(root: Path, run_dir: Path, run_packet: dict[str, object]) -> list[str]:
    candidates: list[str] = []
    for key in ("lesson_candidates", "lessons", "reusable_lessons"):
        values = run_packet.get(key)
        if isinstance(values, list):
            candidates.extend(str(item) for item in values if str(item).strip())
    for filename in ("REPORT.md", "execution-log.md", "pr-description.md"):
        path = run_dir / filename
        if path.exists():
            candidates.extend(parse_reusable_lesson_section(common.read_text(path, limit=80_000)))
    promotions = run_dir / "artifacts" / "lesson-promotions.json"
    if promotions.exists():
        try:
            data = json.loads(promotions.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        values = data.get("lesson_candidates") if isinstance(data, dict) else []
        if isinstance(values, list):
            candidates.extend(str(item) for item in values if str(item).strip())
    return list(dict.fromkeys(candidates))


def declared_output_specs(root: Path, workflow_name: str, run_dir: Path) -> list[dict[str, object]]:
    manifest_path = root / "automations" / workflow_name / "module.json"
    data, _error = common.read_json_file(manifest_path)
    manifest = data if isinstance(data, dict) else {}
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), list) else []
    specs: list[dict[str, object]] = []
    seen: set[str] = set()
    for output in outputs:
        if isinstance(output, dict):
            raw = str(output.get("path") or output.get("name") or "")
            required = output.get("required") is True
        else:
            raw = str(output)
            required = False
        raw = raw.replace("\\", "/").strip()
        if not raw.startswith("runs/<run-id>/"):
            continue
        if "*" in raw or raw.endswith("/"):
            continue
        resolved = raw.replace("<run-id>", run_dir.name)
        if any(part in "/" + resolved for part in LIFECYCLE_OUTPUT_PARTS):
            continue
        if resolved.endswith("/run.json") or resolved.endswith("/REPORT.md"):
            continue
        if not Path(resolved).suffix:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        specs.append({"path": resolved, "required": required})
    return specs


def declared_output_paths(root: Path, workflow_name: str, run_dir: Path) -> list[str]:
    return [str(spec["path"]) for spec in declared_output_specs(root, workflow_name, run_dir)]


def declared_output_proof(root: Path, workflow_name: str, run_dir: Path) -> dict[str, object]:
    checked: list[dict[str, object]] = []
    missing: list[dict[str, object]] = []
    blocking_missing: list[dict[str, object]] = []
    for spec in declared_output_specs(root, workflow_name, run_dir):
        relative_path = str(spec["path"])
        required = spec.get("required") is True
        path = root / "automations" / workflow_name / relative_path
        status = "present" if path.exists() else "missing"
        item = {"path": common.relative(root, path), "status": status, "required": required}
        checked.append(item)
        if status == "missing":
            issue = {
                "section": "Declared Outputs",
                "field": "Output",
                "path": common.relative(root, path),
                "required": required,
                "message": "declared output is missing",
            }
            missing.append(issue)
            if required:
                blocking_missing.append(issue)
    return {
        "checked": checked,
        "checked_count": len(checked),
        "present_count": sum(1 for item in checked if item.get("status") == "present"),
        "missing_count": len(missing),
        "missing": missing,
        "required_missing_count": len(blocking_missing),
        "blocking_missing": blocking_missing,
    }


def proof_gap_summary(missing: list[dict[str, object]]) -> dict[str, object]:
    by_section: dict[str, int] = {}
    for item in missing:
        section = str(item.get("section") or "Proof")
        by_section[section] = by_section.get(section, 0) + 1
    return {"missing_count": len(missing), "by_section": by_section}


def format_proof_issue(issue: dict[str, object]) -> str:
    path = str(issue.get("path") or "")
    if path:
        return f"{issue.get('section', 'Proof')} {issue.get('field', 'Evidence')}: {issue.get('message')} - {path}"
    row = f" row {issue['row']}" if issue.get("row") is not None else ""
    field = f" {issue['field']}" if issue.get("field") else ""
    return f"{issue.get('section', 'Proof')}{row}{field}: {issue.get('message')}"


def finish_proof_report(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    run_packet: dict[str, object],
    *,
    domain_report: dict[str, object] | None = None,
) -> dict[str, object]:
    declared = declared_output_proof(root, workflow_name, run_dir)
    missing = list(declared.get("blocking_missing", []))
    report = dict(domain_report or {})
    domain_missing = report.get("missing_proof") if isinstance(report.get("missing_proof"), list) else []
    missing = [*domain_missing, *missing]
    existing_lessons = report.get("lesson_candidates") if isinstance(report.get("lesson_candidates"), list) else []
    lessons = [
        *[str(item) for item in existing_lessons if str(item).strip()],
        *lesson_candidates(root, run_dir, run_packet),
    ]
    report.update(
        {
            "ok": not missing,
            "status": "ok" if not missing else "failed",
            "declared_outputs": declared,
            "missing_count": len(missing),
            "missing_proof": missing,
            "proof_gap_summary": proof_gap_summary([item for item in missing if isinstance(item, dict)]),
            "lesson_candidates": list(dict.fromkeys(lessons)),
        }
    )
    return report


def evidence_completeness(root: Path, workflow_name: str, run_dir: Path, run_packet: dict[str, object]) -> dict[str, object]:
    core = {
        "run_json": "present" if (run_dir / "run.json").exists() else "missing",
        "report": "present" if (run_dir / "REPORT.md").exists() else "missing",
    }
    context_json, _context_md = context_packet_paths(run_dir)
    declared = declared_output_proof(root, workflow_name, run_dir)
    required_missing_count = int(declared.get("required_missing_count", 0))
    missing_count = sum(1 for status in core.values() if status != "present") + required_missing_count
    evidence_entries = run_packet.get("evidence") if isinstance(run_packet.get("evidence"), list) else []
    evidence_paths = run_packet.get("evidence_paths") if isinstance(run_packet.get("evidence_paths"), list) else []
    unsupported = run_packet.get("unsupported_claims") if isinstance(run_packet.get("unsupported_claims"), list) else []
    return {
        "status": "ok" if missing_count == 0 else "needs-evidence",
        "core": core,
        "declared_outputs": declared,
        "context_packet": {
            "path": common.relative(root, context_json),
            "status": "present" if context_json.exists() else "missing",
        },
        "evidence_entry_count": len(evidence_entries),
        "evidence_path_count": len(evidence_paths),
        "unsupported_claim_count": len(unsupported),
        "external_validation_status": run_packet.get("external_validation_status", "not-recorded"),
        "missing_count": missing_count,
        "optional_missing_count": int(declared.get("missing_count", 0)) - required_missing_count,
    }
