# Bug Fix Plan

## Defect Statement

## Out Of Scope

- Behaviors, versions, symptoms, or follow-ups deliberately excluded:

## Clarification Decisions

Ask at most five targeted clarification questions before implementation planning when reproduction, affected versions, expected behavior, scope, non-functional expectations, or completion signals are ambiguous. If no clarification is needed, record one explicit row with the evidence used to decide that.

| Question Or Ambiguity | Decision | Evidence Or Owner | Status |
|---|---|---|---|

## Workflow Inputs And Gates

Record typed inputs and gate states before implementation. Required gates must be satisfied or explicitly blocked before approval.

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

Treat this as quality evidence for the defect statement and expected behavior, not as implementation testing. Validate clarity, completeness, measurability, assumptions, edge cases, and regression expectations before implementation starts.

| Check | Result | Evidence Or Gap | Action |
|---|---|---|---|
| Actual and expected behavior are distinguishable | | | |
| Reproduction preconditions and affected versions are captured or explicitly unknown | | | |
| Regression proof is output-testable or manual proof is justified | | | |
| Edge cases, exclusions, and non-functional expectations are explicit or skipped with reason | | | |

## Cross-Artifact Coverage Analysis

Map every defect fact or owner decision to planned fix work and validation evidence. This is the consistency pass across ticket facts, this plan, fix rows, and validation.

| Requirement Or Decision | Covered By Plan Item | Covered By Validation | Gap Or Follow-Up |
|---|---|---|---|

## Principles And Complexity Gate

Record the engineering-principles check before implementation. Use the complexity columns when the selected fix is not the smallest obvious option.

| Principle Or Complexity Risk | Decision | Simpler Alternative Or Constraint | Evidence |
|---|---|---|---|
| Smallest targeted fix | | | |
| Deterministic validation remains authoritative | | | |
| Complexity requiring justification | | | |

## Template And Extension Layering

Record whether this bug fix uses project overrides, workflow templates, skill extensions, or core defaults. Project-local decisions override reusable defaults only when the plan names the reason.

| Layer | Decision | Evidence Or Override Path | Status |
|---|---|---|---|
| Project override | | | |
| Workflow template | bug-ticket-workflow plan template | automations/bug-ticket-workflow/templates/plan.md | active |
| Skill or workflow extension | | | |
| Core reusable default | | | |

## Assess Fix Test Boundaries

Separate assessment, source edits, and validation. Assessment and test stages write workflow evidence only. The fix stage may edit source files, and any change outside the approved affected files must be logged as a deviation before continuing.

| Stage | Allowed Writes | Evidence Artifact | Status |
|---|---|---|---|
| Assess | bug run folder only | assessment notes in plan.md and validation/ | pending |
| Fix | approved affected source files plus workflow evidence | changed-file evidence and execution-log.md | pending |
| Test | validation evidence only | validation/ regression and quality reports | pending |

## Fill Order

1. Fill Context Evidence, Project Context, triage, reproduction, and root-cause evidence from direct file reads.
2. Decide regression proof, affected versions, release line, and validation commands.
3. Fill clarification, workflow gates, requirement-quality, cross-artifact coverage, principles, layering, and assess/fix/test boundary rows.
4. Fill impact, security, persistence, UI, diagram, quality, and validation rows.
5. Run `workflow plan-check`, then stop for approval before implementation.

## Context Evidence

| Query | Status | Evidence Path | Decision |
|---|---|---|---|
| workflow-contract | | validation/context-evidence-start.json | |
| project-context | | validation/context-evidence-start.json | |

## Triage

| Area | Notes |
|---|---|
| Impact | |
| Severity | |
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

## Impact Analysis

| Area | Files Or Components | Expected Change | Regression Risk | Tests Or Evidence |
|---|---|---|---|---|

## Project Context

| Item | Value | Evidence Or Missing Fact |
|---|---|---|
| Context path | | |
| Context check result | | |
| Technologies and SDK/runtime | | |
| Restore/run/build/test commands | | |
| Folder and generated-file boundaries | | |
| Baseline data/persistence context | | |
| Root schema/data documentation | | |

## Security Impact

Answer each row before implementation. If the fix has no impact, write `No impact` and the evidence used to decide that.

| Topic | Decision | Evidence Or Planned Check |
|---|---|---|
| Roles, authorization, or tenant boundaries | | |
| Authentication, tokens, sessions, or cookies | | |
| User input, files, redirects, links, or HTML/script rendering | | |
| PII, secrets, logging, telemetry, or audit trail | | |
| Security tests or scanner checks to add/run | | |

## Persistence Impact

Use this section when the bug fix changes database schema, persistence mappings, migrations, table ownership, stored values, data relationships, or data access semantics. If not applicable, write `No persistence impact` and the evidence used to decide that.

| Item | Planned Impact | Evidence Or Skip Reason |
|---|---|---|
| Impacted entities/tables | | |
| Changed columns/properties/relationships | | |
| Indexes/constraints/stored values | | |
| Migration/backfill/compatibility risk | | |
| Root schema/data documentation update | | |

### Bug-Scoped ERD

Add an Azure DevOps Mermaid `erDiagram` here when persistence changes are planned. Keep it scoped to the impacted entities for this bug fix; update the root schema/data documentation during implementation when the approved plan requires it.

## Bounded Work Packages

Use one package for a bounded fix and 3-8 only for genuinely separate outcomes. Dependencies name earlier Package IDs or `none`; live status belongs in `run.json.task_status`.

| Package ID | Outcome | Invariant | Depends On | Non-Goals | Owner Paths | Verification | Completion Criteria | Handoff |
|---|---|---|---|---|---|---|---|---|

## UI And Screenshot Evidence

Use this section when the fix changes UI, browser behavior, accessibility, navigation, or rendered output. If not applicable, write `No UI impact`.

| Scenario | Viewport Or Environment | Evidence Path | Required Or Skipped Reason |
|---|---|---|---|
| Reproduced failure path | | | |
| Fixed happy path | | | |
| Unhappy or validation path | | | |

## Coverage And Quality Targets

| Target | Planned Threshold Or Decision | Evidence |
|---|---|---|
| Coverage | Minimum 80 percent when coverage is applicable; target 90 percent for changed behavior unless blocked with evidence. | |
| Warnings/static analysis | Zero new warnings unless explicitly accepted with reason. | |
| Generated coverage gap report | Required when coverage tooling exists for the project. | |

## Planned Validation

| Check | Command Or Method | Expected Evidence | Required |
|---|---|---|---|

## Diagram Plan

Use the ERD row for impacted entity discovery when the planned bug fix touches database schema, persistence mappings, migrations, table ownership, stored values, or data relationships.

| Diagram | Needed | Path | Validation Evidence Or Skip Reason |
|---|---|---|---|
| Process flow | | | |
| High-level connections | | | |
| Low-level connections | | | |
| ERD | | | |

## Risks And Decisions

| Decision | Reason | Owner | Date |
|---|---|---|---|

## Approval Gate

- [ ] Stop before implementation.
- Approval status:
- Approver:
- Approval evidence:
