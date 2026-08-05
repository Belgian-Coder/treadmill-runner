# Bug Ticket Instructions

## Always Load

Progress contract: keep `run.json` and `execution-log.md` current at every phase boundary. Close each phase with completed, skipped, blocked, failed, validation, decisions, evidence paths, unsupported claims, and next action. Reproduce before fixing when feasible; when not feasible, record why and how regression proof will be obtained.

Read-only/no-start dogfood overrides phase writes: confirm owner with `which-workflow`, run only `module.json.strict_read_only_commands`, treat all `next_command` fields as advisory, and do not create run folders, write context/context-evidence/checkpoint artifacts, call local-AI, inspect profiles, use network, or run start/resume/finish. Inspect eval suites only; report skipped lifecycle, credential, external-service, local-AI, eval, and target-project commands.

Project context gate: before planning implementation, confirm `docs/project/project-context.md` covers technologies, SDK/runtime versions, restore/run/test commands, folder boundaries, generated files, external systems, persistence ownership, and validation expectations.

External service setup gate: before importing Azure DevOps/TFS tickets or exporting SonarQube evidence, run `python -B .agents/manage.py credential-doctor --summary --compact --format json`. If the needed profile is missing, ask the user for profile name, service URL, project/project key, and token source, then run `credential-doctor --configure --service <azure-devops|tfs|sonarqube>` so `.agents/local-ai/secrets.local.json` is created locally and `.gitignore` is repaired. Treat `credential-doctor --configure` as a write/configuration action requiring explicit user approval. During read-only/offline/no-profile dogfood, skip both credential summary and credential configuration, and report that external-service setup was intentionally skipped.

## Stop Rules

Hard stop after planning. Continue to implementation only after explicit user approval is recorded. Stop before planning implementation when `docs/project/project-context.md` is missing, draft, or template-like. Stop when defect-vs-existing-behavior, release/backport impact, root-cause evidence, or validation strategy is unresolved.

## Completion Contract

Final handoff records reproduction, root cause, regression proof, out-of-scope work, changed files, validation, docs and diagram impact, reusable lessons or `No reusable lesson: <reason>`, close-out checklist, skipped/blocked/failed checks, risks, and next action. `pr-description.md` must not contain template placeholders, empty bullets, or empty required sections. Refresh `artifacts/context/context-packet.json` before handoff.


## Phase: Intake And Triage

- [ ] Read: request, imported ticket evidence, `WORKFLOW.md`, `module.json`, and project context status. Do: record title, version, release line, severity, comments, attachments, scope, exclusions, suspected area, and missing facts. Write: `ticket-info.md`, `plan.md`, `run.json`, and `execution-log.md`. Done when: defect ownership is clear or rejected with rationale. If blocked: record the missing source or owner decision.

## Phase: Reproduction Planning

- [ ] Read: bug facts, project context, relevant files, prior failures, and templates. Do: plan the smallest reproduction; Decide whether the bug fix needs process, high-level connection, low-level connection, or ERD diagrams; then create one Bounded Work Package for a bounded fix or 3-8 acyclic packages for genuinely separate outcomes. Fill impact, security, persistence, UI, quality, dependency, completion, and handoff evidence. Write: completed `plan.md` sections and planned commands. Done when: `workflow plan-check --name bug-ticket-workflow --run-id <run-id>` passes with no section/row quality issues. If blocked: set next action to approval, missing context, or owner decision.

## Phase: Approval Checkpoint

- [ ] Read: `plan.md`, reproduction plan, regression proof, risks, and validation plan. Do: present approval choices. Write: approved, revised, duplicate, wont-fix, or blocked decision. Done when: fix implementation is explicitly approved or stopped. If blocked: do not edit target implementation files.

## Phase: Fix Implementation

- [ ] Read: approved plan, repro evidence, next unblocked package, and reference files. Do: reproduce when feasible, apply one package at a time, and update `run.json.task_status`; do not begin a package whose dependencies are unfinished. Write: changed paths, command evidence, root-cause notes, package handoff, and blockers. Done when: approved fix is present or blocked with evidence. If blocked: preserve user work and record first failing fact.

## Phase: Regression And Validation

- [ ] Read: plan, changed paths, repro inputs, tests, quality gates, diagrams, and docs impact. Do: rerun reproduction/regression checks plus planned static/security/browser/coverage/Mermaid validation. Validate changed Mermaid diagrams. Write: evidence under `validation/` plus pass/fail/skipped/blocked status. Done when: every planned check is resolved or accepted as risk. If blocked: isolate the first failing command and next fix.

## Phase: Follow-Up Handling

- [ ] Read: new comments and current evidence. Do: classify follow-ups as in-scope, new bug, duplicate, expected behavior, or blocked. Write: accepted changes or explicit deferrals. Done when: each follow-up has status and evidence. If blocked: record owner decision needed.

## Phase: PR Handoff

- [ ] Read: `run.json`, `REPORT.md`, `execution-log.md`, `plan.md`, validation, and changed paths. Do: record Plan Variance plus independent review evidence for all four axes, prepare final `pr-description.md`, refresh context, and finalize report state. Write: context packet, documentation delta, final report, and unsupported-claims status. Done when: another agent can resume from the packet. If blocked: list missing evidence and next command.
