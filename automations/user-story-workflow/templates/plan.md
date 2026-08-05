# User Story Plan

## Outcome

## Out Of Scope

- Items deliberately excluded from this implementation:

## Clarification Decisions

Ask at most five targeted clarification questions before implementation planning when scope, requirements, acceptance criteria, non-functional expectations, integrations, or completion signals are ambiguous. If no clarification is needed, record one explicit row with the evidence used to decide that.

| Question Or Ambiguity | Decision | Evidence Or Owner | Status |
|---|---|---|---|

## Workflow Inputs And Gates

Record typed inputs and gate states before implementation. Required gates must be satisfied or explicitly blocked before approval.

| Input Or Gate | Type Or State | Evidence | Required |
|---|---|---|---|
| Story request | string | | yes |
| Acceptance criteria | list | | yes |
| Clarification gate | pending | Clarification Decisions | yes |
| Requirements quality gate | pending | Requirements Quality Checklist | yes |
| Cross-artifact coverage gate | pending | Cross-Artifact Coverage Analysis | yes |
| Approval gate | pending | Approval Gate | yes |

## Requirements Quality Checklist

Treat this as quality evidence for the story text, not as implementation testing. Validate clarity, completeness, measurability, independent testability, assumptions, and edge cases before implementation starts.

| Check | Result | Evidence Or Gap | Action |
|---|---|---|---|
| Acceptance criteria are observable and measurable | | | |
| Each story outcome is independently testable | | | |
| Edge cases, assumptions, and exclusions are captured | | | |
| Non-functional and integration expectations are explicit or skipped with reason | | | |

## Cross-Artifact Coverage Analysis

Map every requirement or owner decision to planned work and validation evidence. This is the consistency pass across ticket facts, this plan, task rows, and validation.

| Requirement Or Decision | Covered By Plan Item | Covered By Validation | Gap Or Follow-Up |
|---|---|---|---|

## Principles And Complexity Gate

Record the engineering-principles check before implementation. Use the complexity columns when the selected approach is not the smallest obvious option.

| Principle Or Complexity Risk | Decision | Simpler Alternative Or Constraint | Evidence |
|---|---|---|---|
| Smallest correct vehicle | | | |
| Deterministic validation remains authoritative | | | |
| Complexity requiring justification | | | |

## Template And Extension Layering

Record whether this story uses project overrides, workflow templates, skill extensions, or core defaults. Project-local decisions override reusable defaults only when the plan names the reason.

| Layer | Decision | Evidence Or Override Path | Status |
|---|---|---|---|
| Project override | | | |
| Workflow template | user-story-workflow plan template | automations/user-story-workflow/templates/plan.md | active |
| Skill or workflow extension | | | |
| Core reusable default | | | |

## Fill Order

1. Fill Context Evidence, Project Context, and Impact Discovery Evidence from direct file reads.
2. Map acceptance criteria to implementation, validation evidence, and documentation decisions.
3. Fill clarification, workflow gates, requirement-quality, cross-artifact coverage, principles, and layering rows.
4. Fill impact, security, persistence, UI, diagram, quality, and validation rows.
5. Run `workflow plan-check`, then stop for approval before implementation.

## Context Evidence

| Query | Status | Evidence Path | Decision |
|---|---|---|---|
| workflow-contract | | validation/context-evidence-start.json | |
| project-context | | validation/context-evidence-start.json | |

## Impact Discovery Evidence

Use this section before implementation planning. Start with exact `rg` terms from the story, then broaden deterministic searches when wording is unknown. The final plan must be based on files read directly.

| Discovery Item | Evidence | Decision Or Missing Fact |
|---|---|---|
| Story search seeds | | |
| Exact `rg` searches run | | |
| Broadened deterministic searches run | | |
| Candidate files read directly | | |
| Missing or ambiguous context | | |

## Acceptance Criteria Mapping

| Acceptance Criterion | Implementation | Validation Evidence | Documentation |
|---|---|---|---|

## Impact Analysis

| Area | Files Or Components | Expected Change | Tests Or Evidence |
|---|---|---|---|

## Reference Pattern Selection

| Reference | Files Or Pattern | Adaptation Notes |
|---|---|---|

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

Answer each row before implementation. If the story has no impact, write `No impact` and the evidence used to decide that.

| Topic | Decision | Evidence Or Planned Check |
|---|---|---|
| Roles, authorization, or tenant boundaries | | |
| Authentication, tokens, sessions, or cookies | | |
| User input, files, redirects, links, or HTML/script rendering | | |
| PII, secrets, logging, telemetry, or audit trail | | |
| Security tests or scanner checks to add/run | | |

## Persistence Impact

Use this section when the story changes database schema, persistence mappings, migrations, table ownership, stored values, data relationships, or data access semantics. If not applicable, write `No persistence impact` and the evidence used to decide that.

| Item | Planned Impact | Evidence Or Skip Reason |
|---|---|---|
| Impacted entities/tables | | |
| Changed columns/properties/relationships | | |
| Indexes/constraints/stored values | | |
| Migration/backfill/compatibility risk | | |
| Root schema/data documentation update | | |

### Story-Scoped ERD

Add an Azure DevOps Mermaid `erDiagram` here when persistence changes are planned. Keep it scoped to the impacted entities for this story; update the root schema/data documentation during implementation when the approved plan requires it.

## Diagram Plan

Use the ERD row for impacted entity discovery when the planned story touches database schema, persistence mappings, migrations, table ownership, stored values, or data relationships.

| Diagram | Path | Needed Or Skipped Reason | Validation Evidence |
|---|---|---|---|
| Process | | | |
| High-level connection | | | |
| Low-level connection | | | |
| ERD | | | |

## Implementation Checklist

| Step | Observable Outcome | Key Change Area | Reference Pattern | Verification | Status |
|---|---|---|---|---|---|

## Bounded Work Packages

Use one package for a bounded change and 3-8 only for genuinely separate outcomes. Dependencies name earlier Package IDs or `none`; live status belongs in `run.json.task_status`.

| Package ID | Outcome | Invariant | Depends On | Non-Goals | Owner Paths | Verification | Completion Criteria | Handoff |
|---|---|---|---|---|---|---|---|---|

## UI And Screenshot Evidence

Use this section when the story changes UI, browser behavior, accessibility, navigation, or rendered output. If not applicable, write `No UI impact`.

| Scenario | Viewport Or Environment | Evidence Path | Required Or Skipped Reason |
|---|---|---|---|
| Happy path | | | |
| Unhappy or validation path | | | |
| Responsive/accessibility-sensitive path | | | |

## Coverage And Quality Targets

| Target | Planned Threshold Or Decision | Evidence |
|---|---|---|
| Coverage | Minimum 80 percent when coverage is applicable; target 90 percent for changed behavior unless blocked with evidence. | |
| Warnings/static analysis | Zero new warnings unless explicitly accepted with reason. | |
| Generated coverage gap report | Required when coverage tooling exists for the project. | |

## Model And Cost Evidence Plan

| Evidence Question | Planned Source | Boundary |
|---|---|---|
| Which semantic profile should implement the approved bounded plan? | `module.json.worker_profiles`, `workflow workers` output, `run.json` phase notes, and execution log | Start with `implementation-mini`; escalate to `implementation-medium` on ambiguity, shared-contract edits, or repeated deterministic failures. Treat the declared model target and resolved prompt overlay as separate evidence. |
| What observations must be recorded for the run? | Run-owned evidence under `runs/<run-id>/artifacts/` | Record requested semantic profile, declared provider/model target and reasoning effort, observed provider/model and reasoning effort when available, resolved prompt overlay, attestation status, fallback or escalation reason, elapsed time, command count, retry count, validation status, and unsupported claims. |
| How are cost claims supported? | `artifacts/context/context-packet.json` and, only when available, `codex_usage_ledger.py` output | Artifact/context token values are estimates; provider or rollout telemetry is separate and required for exact cost claims. |
| What counts as an easy fix? | Execution-log follow-up rows and run artifact notes | Promote only repeated or clearly durable gaps into templates, docs, or scripts. |

## Planned Validation

| Check | Command Or Method | Expected Evidence | Required |
|---|---|---|---|

## Risks And Decisions

| Decision | Reason | Owner | Date |
|---|---|---|---|

## Approval Gate

- [ ] Stop before implementation.
- Approval status:
- Approver:
- Approval evidence:
