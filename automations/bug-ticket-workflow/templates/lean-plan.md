# Lean Bug Fix Plan

## Defect Statement

## Out Of Scope

- Excluded:

## Clarification Decisions

| Question Or Ambiguity | Decision | Evidence Or Owner | Status |
|---|---|---|---|

## Workflow Inputs And Gates

| Input Or Gate | Type Or State | Evidence | Required |
|---|---|---|---|
| Bug report | string or imported ticket | | yes |
| Reproduction evidence | planned | Reproduction Plan | yes |
| Regression proof | planned | Regression-Proof Decision | yes |
| Clarification gate | pending | Clarification Decisions | yes |
| Requirements quality gate | pending | Requirements Quality Checklist | yes |
| Cross-artifact coverage gate | pending | Cross-Artifact Coverage Analysis | yes |
| Approval gate | pending | Approval Gate | yes |

## Requirements Quality Checklist

| Check | Result | Evidence Or Gap | Action |
|---|---|---|---|
| Actual and expected behavior, affected versions, and reproduction preconditions are explicit | | | |
| Regression proof is output-testable or manual proof is justified | | | |

## Cross-Artifact Coverage Analysis

| Requirement Or Decision | Covered By Plan Item | Covered By Validation | Gap Or Follow-Up |
|---|---|---|---|

## Principles And Complexity Gate

| Principle Or Complexity Risk | Decision | Simpler Alternative Or Constraint | Evidence |
|---|---|---|---|
| Smallest targeted fix | | | |
| Deterministic validation remains authoritative | | | |

## Template And Extension Layering

| Layer | Decision | Evidence Or Override Path | Status |
|---|---|---|---|
| Project override | | | |
| Workflow template | lean bug plan | automations/bug-ticket-workflow/templates/lean-plan.md | active |
| Skill or workflow extension | | | |
| Core reusable default | | | |

## Assess Fix Test Boundaries

| Stage | Allowed Writes | Evidence Artifact | Status |
|---|---|---|---|
| Assess | bug run folder only | | pending |
| Fix | approved affected source files plus workflow evidence | | pending |
| Test | validation evidence only | | pending |

## Triage

| Area | Notes |
|---|---|
| Affected versions | |
| Release-line decision | |

## Reproduction Plan

| Step | Command Or Action | Expected Failure | Evidence |
|---|---|---|---|

## Regression-Proof Decision

- Test before fix:
- Test after fix:
- Manual proof accepted:
- Reason:

## Root Cause Evidence

| Hypothesis | Evidence | Decision |
|---|---|---|

## Context Evidence

| Query | Status | Evidence Path | Decision |
|---|---|---|---|
| workflow-contract | | validation/context-evidence-start.json | |
| project-context | | validation/context-evidence-start.json | |

## Project Context

| Item | Value | Evidence Or Missing Fact |
|---|---|---|
| Context path | | |
| Context check result | | |

## Impact Analysis

| Area | Files Or Components | Expected Change | Regression Risk | Tests Or Evidence |
|---|---|---|---|---|

## Security Impact

| Topic | Decision | Evidence Or Planned Check |
|---|---|---|

## Persistence Impact

| Item | Planned Impact | Evidence Or Skip Reason |
|---|---|---|

## Diagram Plan

| Diagram | Needed | Path | Validation Evidence Or Skip Reason |
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

## Approval Gate

- [ ] Stop before implementation.
- Approval status:

## Bounded Work Packages

Use one package for a bounded fix and 3-8 only for genuinely separate outcomes. Dependencies name earlier Package IDs or `none`; live status belongs in `run.json.task_status`.

| Package ID | Outcome | Invariant | Depends On | Non-Goals | Owner Paths | Verification | Completion Criteria | Handoff |
|---|---|---|---|---|---|---|---|---|
