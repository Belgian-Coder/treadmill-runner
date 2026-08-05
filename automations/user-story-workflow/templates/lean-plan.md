# Lean User Story Plan

## Outcome

## Out Of Scope

- Excluded:

## Clarification Decisions

| Question Or Ambiguity | Decision | Evidence Or Owner | Status |
|---|---|---|---|

## Workflow Inputs And Gates

| Input Or Gate | Type Or State | Evidence | Required |
|---|---|---|---|
| Story request | string | | yes |
| Acceptance criteria | list | | yes |
| Clarification gate | pending | Clarification Decisions | yes |
| Requirements quality gate | pending | Requirements Quality Checklist | yes |
| Cross-artifact coverage gate | pending | Cross-Artifact Coverage Analysis | yes |
| Approval gate | pending | Approval Gate | yes |

## Requirements Quality Checklist

| Check | Result | Evidence Or Gap | Action |
|---|---|---|---|
| Criteria are observable, measurable, and independently testable | | | |
| Edge cases, assumptions, exclusions, NFRs, and integrations are explicit or skipped | | | |

## Cross-Artifact Coverage Analysis

| Requirement Or Decision | Covered By Plan Item | Covered By Validation | Gap Or Follow-Up |
|---|---|---|---|

## Principles And Complexity Gate

| Principle Or Complexity Risk | Decision | Simpler Alternative Or Constraint | Evidence |
|---|---|---|---|
| Smallest correct vehicle | | | |
| Deterministic validation remains authoritative | | | |

## Template And Extension Layering

| Layer | Decision | Evidence Or Override Path | Status |
|---|---|---|---|
| Project override | | | |
| Workflow template | lean user-story plan | automations/user-story-workflow/templates/lean-plan.md | active |
| Skill or workflow extension | | | |
| Core reusable default | | | |

## Acceptance Criteria Mapping

| Acceptance Criterion | Implementation | Validation Evidence | Documentation |
|---|---|---|---|

## Impact Discovery Evidence

| Discovery Item | Evidence | Decision Or Missing Fact |
|---|---|---|
| Candidate files read directly | | |

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

## Approval Gate

- [ ] Stop before implementation.
- Approval status:

## Model And Cost Evidence Plan

| Evidence Question | Planned Source | Boundary |
|---|---|---|
| Which semantic profile should implement the approved bounded plan? | `module.json.worker_profiles`, `workflow workers` output, run notes, and execution log | `implementation-mini` first; escalate to `implementation-medium` for ambiguity, shared-contract edits, or repeated deterministic failures. Treat the declared model target and resolved prompt overlay as separate evidence. |
| What must be recorded? | Run-owned evidence under `runs/<run-id>/artifacts/` | Requested semantic profile, declared provider/model target and reasoning effort, observed provider/model and reasoning effort when available, resolved prompt overlay, attestation status, fallback or escalation reason, elapsed time, command count, retries, validation status, unsupported claims, and estimate-versus-telemetry boundary. |
| How are cost claims supported? | Context packet plus usage ledger when available | Artifact token counts are estimates only until provider telemetry is present. |

## Bounded Work Packages

Use one package for a bounded change and 3-8 only for genuinely separate outcomes. Dependencies name earlier Package IDs or `none`; live status belongs in `run.json.task_status`.

| Package ID | Outcome | Invariant | Depends On | Non-Goals | Owner Paths | Verification | Completion Criteria | Handoff |
|---|---|---|---|---|---|---|---|---|

## Implementation Checklist

| Step | Observable Outcome | Key Change Area | Reference Pattern | Verification | Status |
|---|---|---|---|---|---|
