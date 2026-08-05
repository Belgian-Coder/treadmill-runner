# User Story Instructions

## Always Load

Align run/log state at phase boundaries with evidence, claims, and next action. Search exact seeds and verify source.

Read-only/no-start permits only declared strict commands: no writes, credentials, network, AI, or lifecycle. Report missing story/root/approval.

Before planning, confirm project stack, commands, boundaries, persistence, and validation.

Before Azure/TFS/Sonar access, run credential-doctor and configure only from user-provided connection facts.

## Stop Rules

Stop after planning until approval. Stop on unresolved context, scope, persistence/security, validation, or generated boundaries.

## Completion Contract

Handoff records scope, changes, evidence, risks, and next action; refresh context and any required PR description.


## Phase: Intake

- [ ] Read: request, imported ticket evidence, `WORKFLOW.md`, `module.json`, and project context status. Do: record title, owner, acceptance criteria, comments, attachments, scope, exclusions, and missing facts. Write: `ticket-info.md`, `plan.md`, `run.json`, and `execution-log.md`. Done when: the story has one clear planning next action. If blocked: record the missing source or owner decision.

## Phase: Planning

- [ ] Read: ticket facts, project context, relevant files found by exact search, templates, and validation rules. Do: ensure every acceptance criterion has planned code, test, and documentation coverage, then create one Bounded Work Package for bounded work or 3-8 acyclic packages for genuinely separate outcomes. Fill impact, security, persistence, UI, quality, dependency, completion, and handoff evidence. Write: completed `plan.md` sections and planned commands. Done when: `workflow plan-check --name user-story-workflow --run-id <run-id>` passes with no section/row quality issues. If blocked: set next action to approval, missing context, or owner decision.

## Phase: Approval Checkpoint

- [ ] Read: `plan.md`, risk notes, diagram plan, and validation plan. Do: present scope and approval choices. Write: approved, revised, or blocked decision in `run.json` and `execution-log.md`. Done when: implementation is explicitly approved or stopped. If blocked: do not edit target implementation files.

## Phase: Implementation

- [ ] Read: approved plan, next unblocked package, reference files, generated-file boundaries, and the resolved worker-profile header. Do: implement one approved package at a time and update `run.json.task_status`; do not begin a package whose dependencies are unfinished. Use `implementation-mini` first for bounded approved plans and escalate to `implementation-medium` on ambiguity, shared-contract edits, or repeated deterministic failure. Write: changed paths, command evidence, decisions, package handoff, observed provider/model and reasoning effort when available, validation status, and unsupported claims. Done when: approved changes are present or blocked with evidence. If blocked: preserve user work and record the first failing fact.

## Phase: Validation

- [ ] Read: plan, changed paths, quality gates, diagrams, and docs impact. Do: run planned validation. Keep deterministic command exit codes authoritative; local AI is triage only. Write: evidence, pass/fail/skipped/blocked status, unsupported claims, and any model fallback. Done when: checks are resolved or accepted as risk. If blocked: isolate the first failing command and next fix.

## Phase: Follow-Up Handling

- [ ] Read: new comments and current evidence. Do: classify follow-ups as in-scope, out-of-scope, duplicate, or blocked. Write: accepted changes or explicit deferrals. Done when: each follow-up has status and evidence. If blocked: record owner decision needed.

## Phase: PR Handoff

- [ ] Record final variance, four review axes, docs/claims/costs, required PR description, and resume context with blockers/next command.
