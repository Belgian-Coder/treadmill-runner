# Discipline Instructions

## Always Load

Work one phase at a time. Keep `run.json` current and close each phase with Completed, Skipped, Blocked, Failed, Validation, Decisions, Files changed, Evidence paths, Unsupported claims, and Next step. Step shape: Read: inputs/run state. Do: phase action. Write: outputs/evidence. Done when: proof is recorded. If blocked: record blocker, owner decision, and next action.

Strict read-only/offline/no-profile/no-temp/no-write dogfood does not update `run.json`, `REPORT.md`, `discipline-log.md`, or `implementation-packet.md`; no phase is active, no run state is required, and phase text is inspect-only. Confirm owner with `which-workflow`, run only `module.json.strict_read_only_commands`, and treat `next_command` as advisory unless strict-listed. Do not create runs, write evidence, run local AI, inspect profiles, execute lifecycle/temp-fixture evals, or edit templates; report skipped write steps instead.

## Stop Rules

- Stop when the selected owner is unclear, required evidence is missing, or validation cannot support the next claim.
- Record blocked checks, failed commands, skipped checks, and the next action before ending the turn.

## Completion Contract

- Final report lists low-context files, inspected/changed paths, commands, generated artifacts, validation result, failures, blockers, remaining risks, and next action.
- Unsupported claims must be empty or explicitly documented with evidence gaps.


## Phase: Orientation

- [ ] Read `WORKFLOW.md`, `module.json`, active `run.json`, and relevant routing; write task, scope, selected owner, skipped owners, and loaded context in `discipline-log.md`.

## Phase: Scope Control

- [ ] Choose the smallest correct vehicle: skill, workflow, script, eval, docs, generated sync, or no change.
- [ ] Record rejected alternatives, overlap evidence, and why new skill creation is rejected.

## Phase: Implementation Planning

- [ ] Write `implementation-packet.md` and the plan's Bounded Work Packages with exact outcomes, invariants, dependency IDs, non-goals, owner paths, checks, completion criteria, and handoffs. Use one package for bounded work and 3-8 only for genuinely separate outcomes.

## Phase: Execute With Evidence

- [ ] Apply one focused change at a time; record changed paths, source reason, expected check, and generated artifact status.
- [ ] Run generated sync only through declared commands.

## Phase: Failed Validation Debugging

- [ ] Read full failed output, identify first failing fact, reproduce when feasible, inspect recent changes, patch one cause, and rerun.
- [ ] Use local AI only after deterministic evidence exists; accept suggestions only when mapped to cited files or commands.

## Phase: Two-Stage Review

- [ ] Record independent evidence for spec/plan compliance, standards/maintainability, security/authority, and validation/generated artifacts. A pass on one axis never masks a failed, skipped, or blocked axis.

## Phase: Finish With Fresh Evidence

- [ ] Run fresh validation or record each skipped check and reason; no completion claim may depend on stale evidence.
- [ ] Record Plan Variance per package, or one fully filled `No variance` row when execution matched the approved plan.
- [ ] Final report lists low-context files, inspected/changed paths, commands, generated artifacts, validation result, failures, blockers, remaining risks, and next action.

## Phase Handoff

Completed:
Skipped:
Blocked:
Failed:
Validation:
Decisions:
Files changed:
Evidence paths:
Remaining unsupported claims:
Next step:
