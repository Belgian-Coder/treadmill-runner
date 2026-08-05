# {title} Instructions

## Always Load

- Keep `run.json` as the canonical run state and update it at every phase boundary.
- Put recurring workflow rules here; `workflow context` copies them into resume packets.
- Record command output as evidence paths or compact summaries, not pasted logs.

## Stop Rules

- Stop when required approval, context, or validation evidence is missing.
- Record the blocker, owner decision needed, and next action before ending the turn.

## Completion Contract

- Final reports name changed paths, commands, generated artifacts, validation, skipped/blocked/failed checks, risks, and next action.
- Unsupported claims must be empty or explicitly called out with evidence gaps.

## Phase: Intake

- [ ] Read: `WORKFLOW.md` and `module.json`.
  Do: identify request, related modules, expected outputs, risk, out-of-scope work, and required context evidence.
  Write: update `runs/<run-id>/run.json` with loaded context, decisions, skipped/blocked/failed checks, command history, evidence paths, and next action.
  Decision: record selected scope, owner, and rejected alternatives.
  Evidence: link request source, intake file, and `validation/context-evidence-start.json`.
  Done when: one clear next action is recorded.
  If blocked: record the blocker in `run.json` and `REPORT.md`.

## Phase: Execute

- [ ] Read: only phase details and source files needed for the current action.
  Do: run declared commands or make the smallest workflow-owned change.
  Write: command results, evidence paths, and decisions in `run.json`.
  Decision: record material tradeoffs, skipped work, and out-of-scope choices.
  Evidence: link validation, artifacts, or changed files.
  Done when: outputs exist or an explicit blocked/skipped/failed reason is recorded.
  If blocked: preserve the failing command and first failing fact.

## Phase: Finish

- [ ] Read: `run.json`, `REPORT.md`, and validation output.
  Do: verify the evidence supports completion claims.
  Write: final completed/skipped/blocked/failed/validation status in `REPORT.md` and `run.json`.
  Decision: record complete, blocked, or completed-with-findings.
  Evidence: link final validation and context packet paths.
  Done when: next action is explicit and unsupported claims are empty.
  If blocked: leave a resumable next action in `run.json`.
