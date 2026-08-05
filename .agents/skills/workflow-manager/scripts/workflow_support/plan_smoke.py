#!/usr/bin/env python3
"""Plan-only smoke fixtures for story and bug workflows."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import workflow_manager_common as common
import workflow_plan_check
from workflow_run_support import start_workflow_run

SMOKE_PREFIX = "smoke-local"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, dict) else {}


def smoke_run_id(workflow_name: str, label: str) -> str:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S")
    return f"{SMOKE_PREFIX}-{label}-{workflow_name}-{stamp}-{os.getpid()}"


def cleanup_smoke_run(root: Path, workflow_name: str, run_id: str) -> dict[str, Any]:
    runs_dir = (root / "automations" / workflow_name / "runs").resolve()
    run_dir = (runs_dir / run_id).resolve()
    if not run_dir.exists():
        return {"removed": False, "path": common.relative(root, run_dir), "reason": "not-created"}
    if not run_dir.name.startswith(SMOKE_PREFIX) or runs_dir not in run_dir.parents:
        return {"removed": False, "path": common.relative(root, run_dir), "reason": "outside-smoke-boundary"}
    for child in sorted(run_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if child.is_dir() and not child.is_symlink():
            child.rmdir()
        else:
            child.unlink()
    run_dir.rmdir()
    return {"removed": True, "path": common.relative(root, run_dir), "reason": "cleaned"}


def report_check(name: str, report: dict[str, Any]) -> dict[str, Any]:
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    failed_hooks = [
        item
        for item in report.get("hook_results", [])
        if isinstance(item, dict) and item.get("required") is True and item.get("ok") is not True
    ]
    ok = report.get("ok") is not False and not issues and not failed_hooks
    row: dict[str, Any] = {
        "name": name,
        "kind": "domain-fixture",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "tool": report.get("tool", ""),
    }
    if issues:
        row["issues"] = issues
    if failed_hooks:
        row["failed_hooks"] = failed_hooks
    return row


def md(*sections: str) -> str:
    return "\n\n".join(section.strip() for section in sections).rstrip() + "\n"


APPROVAL_GATE = """## Approval Gate

- [ ] Stop before implementation.
- Approval status: pending
- Approver: pending
- Approval evidence: pending"""

CONTEXT_EVIDENCE = """## Context Evidence

| Query | Status | Evidence Path | Decision |
|---|---|---|---|
| workflow-contract | complete | validation/context-evidence-start.json | use workflow files |"""

PROJECT_CONTEXT = """## Project Context

| Item | Value | Evidence Or Missing Fact |
|---|---|---|
| Context path | docs/project/project-context.md | fallback |
| Context check result | fallback | start context evidence |"""

SPEC_GATES = """## Clarification Decisions

| Question Or Ambiguity | Decision | Evidence Or Owner | Status |
|---|---|---|---|
| Is implementation allowed before approval? | No | WORKFLOW.md | resolved |

## Workflow Inputs And Gates

| Input Or Gate | Type Or State | Evidence | Required |
|---|---|---|---|
| Request | string | fixture | yes |
| Approval gate | pending | Approval Gate | yes |

## Requirements Quality Checklist

| Check | Result | Evidence Or Gap | Action |
|---|---|---|---|
| Requirements are testable | passed | plan-check | no action |

## Cross-Artifact Coverage Analysis

| Requirement Or Decision | Covered By Plan Item | Covered By Validation | Gap Or Follow-Up |
|---|---|---|---|
| Stop before implementation | approval row | plan-check | No gap |

## Principles And Complexity Gate

| Principle Or Complexity Risk | Decision | Simpler Alternative Or Constraint | Evidence |
|---|---|---|---|
| Smallest correct vehicle | plan-only fixture | no source edit | WORKFLOW.md |

## Template And Extension Layering

| Layer | Decision | Evidence Or Override Path | Status |
|---|---|---|---|
| Workflow template | workflow plan template | templates/plan.md | active |"""

SECURITY_IMPACT = """## Security Impact

| Topic | Decision | Evidence Or Planned Check |
|---|---|---|
| Roles, authorization, or tenant boundaries | No security impact | planning only |"""

PERSISTENCE_IMPACT = """## Persistence Impact

No persistence impact.

| Item | Planned Impact | Evidence Or Skip Reason |
|---|---|---|
| Impacted entities/tables | None | no persistence change |"""

DIAGRAM_PLAN = """## Diagram Plan

| Diagram | Path | Needed Or Skipped Reason | Validation Evidence |
|---|---|---|---|
| Process | plan.md | needed | plan review |
| ERD | plan.md | skipped because no persistence impact | explicit skip |"""

UI_EVIDENCE = """## UI And Screenshot Evidence

No UI impact.

| Scenario | Viewport Or Environment | Evidence Path | Required Or Skipped Reason |
|---|---|---|---|
| CLI plan | local shell | none | no UI |"""

PLANNED_VALIDATION = """## Planned Validation

| Check | Command Or Method | Expected Evidence | Required |
|---|---|---|---|
| Plan check | workflow plan-check | pass | yes |"""

COMMON_PLAN_SECTIONS = (
    CONTEXT_EVIDENCE,
    PROJECT_CONTEXT,
    SECURITY_IMPACT,
    PERSISTENCE_IMPACT,
    DIAGRAM_PLAN,
    UI_EVIDENCE,
    """## Coverage And Quality Targets

| Target | Planned Threshold Or Decision | Evidence |
|---|---|---|
| Plan quality | required sections filled | plan-check |""",
    PLANNED_VALIDATION,
    APPROVAL_GATE,
)


def filled_user_story_plan() -> str:
    return md(
        "# User Story Plan",
        "## Outcome\n\nPlan a small workflow improvement with approval before implementation.",
        "## Out Of Scope\n\n- Live external service calls.\n- Source implementation before approval.",
        SPEC_GATES,
        """## Impact Discovery Evidence

| Discovery Item | Evidence | Decision Or Missing Fact |
|---|---|---|
| Candidate files read directly | WORKFLOW.md, module.json | use workflow contract |""",
        """## Acceptance Criteria Mapping

| Acceptance Criterion | Implementation | Validation Evidence | Documentation |
|---|---|---|---|
| Plan exists | Fill plan.md and stop | workflow plan-check passes | WORKFLOW.md |""",
        "## Impact Analysis\n\n| Area | Files Or Components | Expected Change | Tests Or Evidence |\n|---|---|---|---|\n| workflow planning | plan.md | planned only | plan-check |",
        """## Implementation Checklist

| Step | Observable Outcome | Key Change Area | Reference Pattern | Verification | Status |
|---|---|---|---|---|---|
| Request approval | approval gate stays pending | workflow run | WORKFLOW.md | plan-check | planned |""",
        """## Bounded Work Packages

| Package ID | Outcome | Invariant | Depends On | Non-Goals | Owner Paths | Verification | Completion Criteria | Handoff |
|---|---|---|---|---|---|---|---|---|
| WP1 | Request approval | No implementation before approval | none | No source changes | workflow run | plan-check | approval is recorded | Continue with approved implementation |""",
        *COMMON_PLAN_SECTIONS,
        "## Risks And Decisions\n\n| Decision | Reason | Owner | Date |\n|---|---|---|---|\n| Stop before implementation | user approval required | engineering | 2026-05-30 |",
    )


def filled_bug_plan() -> str:
    return md(
        "# Bug Ticket Plan",
        "## Defect Statement\n\nPlan a bug investigation and stop before implementation.",
        "## Out Of Scope\n\n- Live external service calls.\n- Fix implementation before approval.",
        SPEC_GATES,
        """## Assess Fix Test Boundaries

| Stage | Allowed Writes | Evidence Artifact | Status |
|---|---|---|---|
| Assess | bug run folder only | plan.md | planned |
| Fix | approved source files | execution-log.md | planned |
| Test | validation evidence only | validation/regression-quality.md | planned |""",
        """## Triage

| Item | Value | Evidence |
|---|---|---|
| Affected versions | unknown: fixture has no product version | smoke fixture |
| Release-line decision | not required: smoke plan only | no release artifact |""",
        """## Reproduction Plan

| Step | Expected Result | Evidence Path | Status |
|---|---|---|---|
| Reproduce locally | failing evidence captured | validation/reproduction.md | planned |""",
        """## Regression-Proof Decision

- Test before fix: planned failing regression check before implementation.
- Test after fix: planned passing regression check after implementation.
- Manual proof accepted: not required: automated fixture proof is planned.
- Reason: regression proof is required before the fix changes code.""",
        "## Root Cause Evidence\n\n| Hypothesis | Evidence | Decision |\n|---|---|---|\n| Unknown until reproduction | validation/reproduction.md | investigate after approval |",
        "## Impact Analysis\n\n| Area | Files Or Components | Expected Change | Tests Or Evidence |\n|---|---|---|---|\n| bug plan | plan.md | planned only | plan-check |",
        "## Bounded Work Packages\n\n| Package ID | Outcome | Invariant | Depends On | Non-Goals | Owner Paths | Verification | Completion Criteria | Handoff |\n|---|---|---|---|---|---|---|---|---|\n| WP1 | Request approval | No fix before approval | none | No source changes | workflow run | plan-check | approval is recorded | Continue with approved fix |",
        *COMMON_PLAN_SECTIONS,
        "## Risks And Decisions\n\n| Decision | Reason | Owner | Date |\n|---|---|---|---|\n| Stop before implementation | user approval required | engineering | 2026-05-30 |",
    )


def filled_dotnet_upgrade_plan() -> str:
    return md(
        "# .NET Upgrade Plan",
        "## Upgrade Target\n\nUpgrade fixture from .NET 8 to .NET 10 after approval.",
        CONTEXT_EVIDENCE,
        """## Project Context

| Item | Value | Evidence Or Missing Fact |
|---|---|---|
| Context path | docs/project/project-context.md | fallback |
| Context check result | complete | start context evidence |
| Current .NET version | net8.0 | fixture project |
| Target .NET version | net10.0 | user request |""",
        "## Baseline Evidence\n\n| Check | Command Or Source | Result | Evidence Path |\n|---|---|---|---|\n| Restore/build/test | dotnet commands | planned | validation/baseline.md |",
        "## Microsoft Changelog Impact\n\n| Source | Breaking Or Behavioral Change | Impact Decision | Evidence |\n|---|---|---|---|\n| Microsoft .NET 10 release notes | SDK/runtime changes | review before editing | artifacts/changelog.md |",
        "## NuGet Feed And Package Ownership\n\n| Package Or Feed | Owner Mechanism | Current Version Or Source | Target Decision |\n|---|---|---|---|\n| NuGet.config | repo source policy | repo-local config | use configured sources |",
        "## Dependency Resolution Plan\n\n| Dependency | Direct Or Transitive | Allowed Source | Target Version | Blocker |\n|---|---|---|---|---|\n| Microsoft.Extensions.Hosting | direct | repo NuGet.config | net10 compatible | none yet |",
        """## Plan Proof Checklist

| Proof | Required Evidence | Current Status | Stop If Missing |
|---|---|---|---|
| Baseline restore/build/test | command evidence | planned | yes |
| Microsoft changelog decisions | official links | planned | yes |
| NuGet source policy | repo NuGet.config | planned | yes |
| Approval before implementation | approval gate evidence | pending | yes |""",
        "## Impact Analysis\n\n| Area | Files Or Components | Expected Change | Tests Or Evidence |\n|---|---|---|---|\n| target frameworks | project files | net10.0 after approval | build/test |",
        SECURITY_IMPACT,
        PERSISTENCE_IMPACT,
        DIAGRAM_PLAN,
        "## Upgrade Checklist\n\n| Step | Observable Outcome | Package Or Project Owner | Verification | Status |\n|---|---|---|---|---|\n| Confirm approval | approval recorded | workflow plan | plan-check | planned |",
        "## Rollback Plan\n\n| Change | Rollback Action | Evidence Needed | Owner |\n|---|---|---|---|\n| target framework | revert project file | git diff and build | engineering |",
        PLANNED_VALIDATION,
        APPROVAL_GATE,
    )


def filled_dotnet_framework_migration_plan() -> str:
    return md(
        "# .NET Framework Migration Plan",
        "## Migration Target\n\nMigrate .NET Framework 4.8 fixture to .NET 10 after approval.",
        CONTEXT_EVIDENCE,
        """## Project Context

| Item | Value | Evidence Or Missing Fact |
|---|---|---|
| Context path | docs/project/project-context.md | fallback |
| Context check result | complete | start context evidence |
| Source .NET Framework version | v4.8 | fixture project |
| Target .NET version | net10.0 | user request |""",
        "## Legacy Inventory\n\n| Area | Finding | Migration Risk | Evidence |\n|---|---|---|---|\n| project format | old csproj | conversion required | artifacts/legacy-inventory.md |",
        "## Baseline Evidence\n\n| Check | Command Or Source | Result | Evidence Path |\n|---|---|---|---|\n| Build/test | MSBuild and vstest | planned | validation/baseline.md |",
        "## Compatibility Assessment\n\n| Component | Compatibility Decision | Required Change | Evidence |\n|---|---|---|---|\n| packages.config | migrate packages | PackageReference | compatibility-matrix.json |",
        "## Migration Strategy\n\n| Project Or Layer | Order | Strategy | Stop Condition |\n|---|---|---|---|\n| shared library | 1 | migrate first | missing baseline |",
        """## Plan Proof Checklist

| Proof | Required Evidence | Current Status | Stop If Missing |
|---|---|---|---|
| Legacy inventory | project facts | planned | yes |
| Baseline build/test | command evidence | planned | yes |
| Compatibility matrix | unsupported API facts | planned | yes |
| Approval before implementation | approval gate evidence | pending | yes |""",
        "## Impact Analysis\n\n| Area | Files Or Components | Expected Change | Tests Or Evidence |\n|---|---|---|---|\n| project files | legacy csproj | SDK-style after approval | build |",
        SECURITY_IMPACT,
        PERSISTENCE_IMPACT,
        DIAGRAM_PLAN,
        "## Migration Checklist\n\n| Step | Observable Outcome | Project Or Layer | Verification | Status |\n|---|---|---|---|---|\n| Confirm approval | approval recorded | workflow plan | plan-check | planned |",
        "## Rollback Plan\n\n| Change | Rollback Action | Evidence Needed | Owner |\n|---|---|---|---|\n| project conversion | restore old csproj | git diff and build | engineering |",
        PLANNED_VALIDATION,
        APPROVAL_GATE,
    )


def unique_context(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def story_bug_plan_only_smoke(root: Path, workflow_name: str) -> dict[str, Any]:
    if workflow_name not in {"user-story-workflow", "bug-ticket-workflow"}:
        return {
            "name": "plan-only-acceptance",
            "kind": "domain-fixture",
            "ok": True,
            "status": "skipped",
            "reason": "plan-only acceptance smoke only applies to story and bug workflows",
        }
    run_id = smoke_run_id(workflow_name, "plan-only")
    checks: list[dict[str, Any]] = []
    cleanup: dict[str, Any] | None = None
    try:
        start = start_workflow_run(root, workflow_name, run_id=run_id)
        checks.append(report_check("plan-only-start-run", start))
        run_dir = root / "automations" / workflow_name / "runs" / run_id
        plan_text = filled_bug_plan() if workflow_name == "bug-ticket-workflow" else filled_user_story_plan()
        write_text(run_dir / "plan.md", plan_text)
        run_packet = read_json(run_dir / "run.json")
        run_packet["current_phase"] = "planning"
        phase = run_packet.get("phase") if isinstance(run_packet.get("phase"), dict) else {}
        phase["current"] = "planning"
        phase["status"] = "planned"
        run_packet["phase"] = phase
        run_packet["next_action"] = "Stop before implementation and wait for approval."
        handoff = run_packet.get("handoff") if isinstance(run_packet.get("handoff"), dict) else {}
        required = handoff.get("required_next_context") if isinstance(handoff.get("required_next_context"), list) else []
        handoff["required_next_context"] = unique_context([*required, common.relative(root, run_dir / "plan.md")])
        run_packet["handoff"] = handoff
        write_json(run_dir / "run.json", run_packet)
        plan_check = workflow_plan_check.check_plan(root, workflow_name, run_id=run_id)
        checks.append(report_check("plan-check", plan_check))
        lowered = plan_text.lower()
        gate_ok = "approval status: pending" in lowered and "approval status: approved" not in lowered
        checks.append(
            {
                "name": "approval-gate-pending",
                "kind": "domain-fixture",
                "ok": gate_ok,
                "status": "passed" if gate_ok else "failed",
                "plan_path": common.relative(root, run_dir / "plan.md"),
            }
        )
    except SystemExit as exc:
        checks.append({"name": "plan-only-exception", "kind": "domain-fixture", "ok": False, "status": "failed", "issue": str(exc)})
    except Exception as exc:
        checks.append({"name": "plan-only-exception", "kind": "domain-fixture", "ok": False, "status": "failed", "issue": str(exc)})
    finally:
        cleanup = cleanup_smoke_run(root, workflow_name, run_id)
    return {
        "name": "plan-only-acceptance",
        "kind": "domain-fixture",
        "ok": all(row.get("ok") is True for row in checks),
        "status": "passed" if all(row.get("ok") is True for row in checks) else "failed",
        "run_id": run_id,
        "checks": checks,
        "cleanup": cleanup,
    }


def dotnet_plan_ready_smoke(root: Path, workflow_name: str) -> dict[str, Any]:
    plan_by_workflow = {
        "dotnet-upgrade": filled_dotnet_upgrade_plan,
        "dotnet-framework-migration": filled_dotnet_framework_migration_plan,
    }
    plan_factory = plan_by_workflow.get(workflow_name)
    if plan_factory is None:
        return {
            "name": "dotnet-plan-ready",
            "kind": "domain-fixture",
            "ok": True,
            "status": "skipped",
            "reason": "dotnet plan-ready smoke only applies to .NET upgrade workflows",
        }
    run_id = smoke_run_id(workflow_name, "plan-ready")
    checks: list[dict[str, Any]] = []
    cleanup: dict[str, Any] | None = None
    try:
        start = start_workflow_run(root, workflow_name, run_id=run_id)
        checks.append(report_check("dotnet-plan-start-run", start))
        run_dir = root / "automations" / workflow_name / "runs" / run_id
        plan_text = plan_factory()
        write_text(run_dir / "plan.md", plan_text)
        run_packet = read_json(run_dir / "run.json")
        run_packet["current_phase"] = "plan-and-approval"
        phase = run_packet.get("phase") if isinstance(run_packet.get("phase"), dict) else {}
        phase["current"] = "plan-and-approval"
        phase["status"] = "planned"
        run_packet["phase"] = phase
        run_packet["next_action"] = "Review plan.md and record approval before implementation."
        handoff = run_packet.get("handoff") if isinstance(run_packet.get("handoff"), dict) else {}
        required = handoff.get("required_next_context") if isinstance(handoff.get("required_next_context"), list) else []
        handoff["required_next_context"] = unique_context([*required, common.relative(root, run_dir / "plan.md")])
        run_packet["handoff"] = handoff
        write_json(run_dir / "run.json", run_packet)
        plan_check = workflow_plan_check.check_plan(root, workflow_name, run_id=run_id)
        checks.append(report_check("dotnet-run-plan-check", plan_check))
        gate_ok = "Approval status: pending" in plan_text and "Plan Proof Checklist" in plan_text
        checks.append(
            {
                "name": "dotnet-approval-ready",
                "kind": "domain-fixture",
                "ok": gate_ok,
                "status": "passed" if gate_ok else "failed",
                "plan_path": common.relative(root, run_dir / "plan.md"),
            }
        )
    except SystemExit as exc:
        checks.append({"name": "dotnet-plan-exception", "kind": "domain-fixture", "ok": False, "status": "failed", "issue": str(exc)})
    except Exception as exc:
        checks.append({"name": "dotnet-plan-exception", "kind": "domain-fixture", "ok": False, "status": "failed", "issue": str(exc)})
    finally:
        cleanup = cleanup_smoke_run(root, workflow_name, run_id)
    return {
        "name": "dotnet-plan-ready",
        "kind": "domain-fixture",
        "ok": all(row.get("ok") is True for row in checks),
        "status": "passed" if all(row.get("ok") is True for row in checks) else "failed",
        "run_id": run_id,
        "checks": checks,
        "cleanup": cleanup,
    }
