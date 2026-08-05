# User Story Execution Log

## Progress Update Rules

- Update this file at every phase boundary before moving to the next phase.
- Keep Current State aligned with `run.json`.
- Add one Phase Handoff entry per completed, skipped, blocked, or failed phase.
- Every planned command or check must have a result and evidence path, or an explicit skipped/blocked reason.

## Current State

- Status: not started
- Current phase:
- Last updated:

## Phase Handoffs

### Phase:

- Completed:
- Skipped:
- Blocked:
- Failed:
- Validation:
- Decisions:
- Next step:

## Commands And Evidence

| Time | Command Or Action | Result | Evidence |
|---|---|---|---|

## Implementation Observations

| Field | Value |
|---|---|
| Requested semantic profile | |
| Declared provider/model target | |
| Requested reasoning effort | |
| Observed provider/model | |
| Observed reasoning effort | |
| Resolved prompt overlay | |
| Model attestation status/source | |
| Fallback or escalation reason | |
| Elapsed time | |
| Command count | |
| Retry count | |
| Validation status | |
| Unsupported claims | |
| Artifact/context token estimate boundary | |
| Provider or rollout telemetry | |
| Escalation decision | |

## Easy Fixes

| Trigger | Fix To Try First | Validation | Status |
|---|---|---|---|
| Template guidance gap | Tighten template rows or phase instructions so the next bounded implementation has fewer blank fields. | Template lint and direct review. | |
| Missing command evidence | Add a dedicated run artifact summary or stronger evidence prompts in the templates. | Plan-check and workflow validation. | |
| Telemetry unavailable | Record estimates only and skip exact-cost claims until rollout or provider usage exists. | Execution-log review. | |
| Plan-followability failure | Add an explicit trigger from `implementation-mini` to `implementation-medium` for ambiguity, shared-contract edits, or repeated deterministic failures; record overlay resolution separately. | Instructions review and evals. | |

## Plan Item Progress

| Plan Item | Status | Evidence | Owner Or Decision |
|---|---|---|---|

## Plan Variance

At finish, add one row per variance. If execution matched the approved plan, add one `No variance` row and fill every column.

| Package | Planned | Actual | Reason | Approval Impact | Validation Impact |
|---|---|---|---|---|---|

## Independent Review Evidence

Use `skipped: <substantive reason>` when an axis does not apply; a label without the reason is not evidence.

Each axis is independent: a pass on one axis does not hide a failure, skip, or blocker on another.

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

- Project context path:
- Project context check:
- Missing project facts:
- Low-context files used:
- Detailed files opened:
- Commands run:
- Evidence ledger path:
- Remaining unsupported claims:

## Follow-Ups

| Item | Decision | Owner | Status |
|---|---|---|---|

## Reusable Lessons

- Reusable lesson or `No reusable lesson: <reason>`.

## Reusable Pattern Capture

| Pattern | Source | Reuse Notes |
|---|---|---|
