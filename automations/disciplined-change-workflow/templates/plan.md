# Workflow Plan

## Out Of Scope

- Rejected alternatives and larger vehicles that were considered but not chosen; record why the selected vehicle is the smallest correct one before implementation starts.

## Clarification Decisions

Ask at most five targeted clarification questions before implementation planning when scope, ownership, acceptance signals, validation expectations, generated-file boundaries, or completion criteria are ambiguous. If no clarification is needed, record one explicit row with the evidence used to decide that.

| Question Or Ambiguity | Decision | Evidence Or Owner | Status |
|---|---|---|---|

## Workflow Inputs And Gates

Record typed inputs and gate states before implementation. Required gates must be satisfied or explicitly blocked before approval.

| Input Or Gate | Type Or State | Evidence | Required |
|---|---|---|---|
| Change request | string | | yes |
| Smallest-vehicle decision | pending | Principles And Complexity Gate | yes |
| Clarification gate | pending | Clarification Decisions | yes |
| Requirements quality gate | pending | Requirements Quality Checklist | yes |
| Cross-artifact coverage gate | pending | Cross-Artifact Coverage Analysis | yes |
| Approval gate | pending | Approval Gate | yes |

## Requirements Quality Checklist

Treat this as quality evidence for the requested change, not as implementation testing. Validate clarity, completeness, measurable finish criteria, assumptions, edge cases, and validation expectations before implementation starts.

| Check | Result | Evidence Or Gap | Action |
|---|---|---|---|
| Requested outcome and finish criteria are measurable | | | |
| Ownership and smallest vehicle are explicit | | | |
| Validation expectations and blocked/skipped checks are explicit | | | |
| Generated-file, docs, and extension boundaries are explicit or skipped with reason | | | |

## Cross-Artifact Coverage Analysis

Map every requirement or owner decision to planned work and validation evidence. This is the consistency pass across request, plan rows, implementation checklist, and validation.

| Requirement Or Decision | Covered By Plan Item | Covered By Validation | Gap Or Follow-Up |
|---|---|---|---|

## Principles And Complexity Gate

Record the engineering-principles check before implementation. Any non-smallest approach needs a reason and the simpler alternative that was rejected.

| Principle Or Complexity Risk | Decision | Simpler Alternative Or Constraint | Evidence |
|---|---|---|---|
| Smallest correct vehicle | | | |
| Deterministic validation remains authoritative | | | |
| Complexity requiring justification | | | |

## Template And Extension Layering

Record whether this change uses project overrides, workflow templates, skill extensions, or core defaults. Project-local decisions override reusable defaults only when the plan names the reason.

| Layer | Decision | Evidence Or Override Path | Status |
|---|---|---|---|
| Project override | | | |
| Workflow template | disciplined-change-workflow plan template | automations/disciplined-change-workflow/templates/plan.md | active |
| Skill or workflow extension | | | |
| Core reusable default | | | |

## Context Evidence

| Query | Status | Evidence Path | Decision |
|---|---|---|---|
| workflow-contract | | validation/context-evidence-start.json | |

## Project Context

| Item | Value | Evidence Or Missing Fact |
|---|---|---|
| Context path | | |
| Context check result | | |

## Impact Analysis

| Area | Files Or Components | Expected Change | Tests Or Evidence |
|---|---|---|---|

## Security Impact

| Topic | Decision | Evidence Or Planned Check |
|---|---|---|

## Persistence Impact

| Item | Planned Impact | Evidence Or Skip Reason |
|---|---|---|

## Diagram Plan

| Diagram | Path | Needed Or Skipped Reason | Validation Evidence |
|---|---|---|---|

## UI And Screenshot Evidence

| Scenario | Viewport Or Environment | Evidence Path | Required Or Skipped Reason |
|---|---|---|---|

## Coverage And Quality Targets

| Target | Planned Threshold Or Decision | Evidence |
|---|---|---|

## Planned Validation

| Check | Command Or Method | Expected Evidence | Required |
|---|---|---|---|

## Bounded Work Packages

Use one package for a bounded change and 3-8 only for genuinely separate outcomes. Dependencies name earlier Package IDs or `none`; live status belongs in `run.json.task_status`.

| Package ID | Outcome | Invariant | Depends On | Non-Goals | Owner Paths | Verification | Completion Criteria | Handoff |
|---|---|---|---|---|---|---|---|---|

## REPORT.md and run.json validation entries

Record the fresh validation evidence that must appear in `REPORT.md` and `run.json` before finish.

| Evidence Item | Path Or Command | Status | Notes |
|---|---|---|---|

## Approval Gate

Approval status:
