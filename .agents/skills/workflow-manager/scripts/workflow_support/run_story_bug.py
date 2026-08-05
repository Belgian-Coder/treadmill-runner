"""Story and bug workflow run helpers."""

from __future__ import annotations

from pathlib import Path

import workflow_manager_common as common
import workflow_plan_check
from workflow_support import run_common
from workflow_support.template_layers import resolved_template_path

PROGRESS_DOCUMENT_WORKFLOWS = {"user-story-workflow", "bug-ticket-workflow"}
PROGRESS_TEMPLATE_FILE = "templates/execution-log.md"


def imported_ticket_info_path(root: Path, ticket_context: dict[str, object] | None) -> Path | None:
    if not ticket_context:
        return None
    imported_root = root / str(ticket_context.get("path", ""))
    candidate = imported_root / "ticket-info.md"
    return candidate if candidate.exists() and candidate.is_file() else None


def scaffold_story_bug_run_files(
    root: Path,
    workflow_name: str,
    module_dir: Path,
    run_dir: Path,
    ticket_context: dict[str, object] | None,
    *,
    profile: str = "default",
) -> list[str]:
    if workflow_name not in PROGRESS_DOCUMENT_WORKFLOWS:
        return []
    sources = [
        (resolved_template_path(root, workflow_name, profile=profile), run_dir / "plan.md"),
        (
            imported_ticket_info_path(root, ticket_context) or module_dir / "templates" / "ticket-info.md",
            run_dir / "ticket-info.md",
        ),
    ]
    created: list[str] = []
    for source, target in sources:
        if source is None or not source.exists():
            continue
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        created.append(common.relative(root, target))
    return created


def missing_scaffold_fix(root: Path, run_dir: Path, filename: str) -> dict[str, object]:
    return {
        "section": "Run Scaffold",
        "field": filename,
        "action": f"Create or fill {filename} from the workflow template, then run workflow plan-check.",
        "target_path": common.relative(root, run_dir / filename),
    }


def story_bug_plan_gate(root: Path, workflow_name: str, run_dir: Path) -> dict[str, object]:
    if workflow_name not in PROGRESS_DOCUMENT_WORKFLOWS:
        return {}
    missing = [
        filename
        for filename in ("ticket-info.md", "plan.md")
        if not (run_dir / filename).exists()
    ]
    plan_report: dict[str, object] = {}
    if (run_dir / "plan.md").exists():
        plan_report = workflow_plan_check.check_plan(root, workflow_name, run_id=run_dir.name)
    if missing:
        queue = [missing_scaffold_fix(root, run_dir, filename) for filename in missing]
        existing_queue = plan_report.get("fix_queue") if isinstance(plan_report.get("fix_queue"), list) else []
        queue.extend(item for item in existing_queue if isinstance(item, dict))
        return {
            "status": "missing-scaffold",
            "ok": False,
            "plan_path": common.relative(root, run_dir / "plan.md"),
            "quality_summary": plan_report.get("quality_summary", {}),
            "fix_queue": queue,
            "operator_next_action": str(queue[0].get("action", "Create the missing workflow scaffold files.")),
            "ready_for_approval": False,
            "implementation_allowed": False,
            "issues": [
                f"missing scaffolded file: {common.relative(root, run_dir / filename)}"
                for filename in missing
            ] + [str(item) for item in plan_report.get("issues", []) if isinstance(item, str)],
        }
    if not plan_report:
        return {}
    return {
        "status": plan_report.get("status", "unknown"),
        "ok": plan_report.get("ok") is True,
        "plan_path": plan_report.get("plan_path", common.relative(root, run_dir / "plan.md")),
        "quality_summary": plan_report.get("quality_summary", {}),
        "fix_queue": plan_report.get("fix_queue", []),
        "operator_next_action": plan_report.get("operator_next_action", ""),
        "ready_for_approval": plan_report.get("ready_for_approval") is True,
        "implementation_allowed": plan_report.get("implementation_allowed") is True,
        "issues": plan_report.get("issues", []),
    }


def terminal_work_item_status(value: str) -> bool:
    normalized = " ".join(value.strip().lower().split())
    return normalized in {"done", "complete", "completed", "passed", "skipped", "blocked"}


def dependency_satisfied_work_item_status(value: str) -> bool:
    normalized = " ".join(value.strip().lower().split())
    return normalized in {"done", "complete", "completed", "passed", "skipped"}


def story_bug_execution_queue(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    run_packet: dict[str, object],
) -> list[dict[str, object]]:
    if workflow_name not in PROGRESS_DOCUMENT_WORKFLOWS:
        return []
    section = "Bounded Work Packages"
    plan_path = run_dir / "plan.md"
    sections = workflow_plan_check.parse_sections(common.read_text(plan_path, limit=120_000))
    packages = workflow_plan_check.bounded_work_packages(sections)
    statuses = run_common.task_statuses(run_packet)
    validation_records = workflow_plan_check.markdown_table_records(
        sections.get(workflow_plan_check.normalize_heading("Planned Validation"), "")
    )
    planned_validation = [
        {
            "row": row_index,
            "check": record.get("check", ""),
            "command_or_method": record.get("command or method", ""),
            "expected_evidence": record.get("expected evidence", ""),
            "required": record.get("required", ""),
        }
        for row_index, record in validation_records
    ]
    queue: list[dict[str, object]] = []
    for package in packages:
        row_index = int(package["row"])
        package_id = str(package["id"])
        status = statuses.get(package_id, "pending")
        if terminal_work_item_status(status):
            continue
        dependencies = [str(item) for item in package["dependencies"]]
        if any(not dependency_satisfied_work_item_status(statuses.get(dependency, "")) for dependency in dependencies):
            continue
        step = str(package["outcome"])
        verification = str(package["verification"])
        action = f"Execute package {package_id}: {step}"
        if verification:
            action += f"; verify with {verification}"
        item = {
            "section": section,
            "row": row_index,
            "id": package_id,
            "step": step,
            "status": status,
            "dependencies": dependencies,
            "verification": verification,
            "handoff": str(package["handoff"]),
            "target_path": common.relative(root, plan_path),
            "planned_validation": planned_validation,
            "action": action + ".",
        }
        queue.append(item)
    return queue


def initial_progress_log(workflow_name: str, run_id: str, now: str) -> str:
    title = "Bug Execution Log" if workflow_name == "bug-ticket-workflow" else "User Story Execution Log"
    command_heading = "Commands And Validation" if workflow_name == "bug-ticket-workflow" else "Commands And Evidence"
    extra = (
        "\n## Reproduction Evidence\n\n| Time | Command Or Action | Result | Evidence |\n|---|---|---|---|\n"
        if workflow_name == "bug-ticket-workflow"
        else ""
    )
    return f"""# {title}

## Progress Update Rules

- Update this file at every phase boundary before moving to the next phase.
- Keep Current State aligned with `run.json`.
- Add one Phase Handoff entry per completed, skipped, blocked, or failed phase.
- Every planned command or check must have a result and evidence path, or an explicit skipped/blocked reason.

## Current State

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
- Next step: read WORKFLOW.md and module.json.
{extra}
## {command_heading}

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

## Context And Claim Support

- Low-context files used: automations/routing.md; automations/{workflow_name}/WORKFLOW.md; automations/{workflow_name}/module.json
- Detailed files opened: not recorded yet
- Commands run: workflow start
- Evidence ledger path: run.json
- Remaining unsupported claims: none recorded
- Project context path: not checked yet
- Project context check: not run yet
- Missing project facts: not recorded yet

## Follow-Ups

| Item | Decision | Owner | Status |
|---|---|---|---|

## Reusable Lessons

- Reusable lesson or `No reusable lesson: <reason>`.
""".rstrip() + "\n"
