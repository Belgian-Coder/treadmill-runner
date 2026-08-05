# {title}

{summary}

## Start

1. Run or read `python -B .agents/manage.py fresh-agent-packet --summary --compact --format json`; load its source-orientation file before broad source reads.
2. Read `docs/project/project-context.md` and `automations/navigation/artifacts/maps/HANDOFF.md` when present; run `python -B .agents/manage.py setup` first if they are missing.
3. Read `automations/routing.md`, then `module.json`.
4. Read this `WORKFLOW.md`.
5. Read `instructions.md` only when phase details are needed.
6. Create or select `runs/<run-id>/` and keep `run.json` current.
7. Use `workflow resume` and the returned context packet before reopening raw workflow files.

Raw navigation JSON is tool-only. Use `status --fast`, `startup-context`, `context-use-check`, `changed-context`, `review-loop`, `review-autopilot`, and `finish` for compact routing, context-use proof, review, validation, and final-claim evidence instead of broad source or raw diff reads. When compact packets name an affected owner capsule, read only that capsule after `HANDOFF.md`.

Run evidence stays under `automations/<workflow-name>/runs/<run-id>/`; do not create root run state.

## Evidence And Decisions

- Record decisions in `run.json` with reason and evidence path.
- Record command output as evidence handles, not pasted logs.
- Keep skipped, blocked, failed, and unsupported claims explicit.
- Keep out-of-scope work explicit when the run excludes requested or related work.
- Record documentation impact in `run.json.documentation`; `workflow context --write` emits a documentation delta.

## Deterministic Hooks

State-writing workflow commands trigger declared workflow and global hooks. Inspect resolved hooks before the first run:

```shell
python -B .agents/manage.py workflow hooks --name {workflow_name} --format json
python -B .agents/manage.py workflow hooks --all --check --format json
```

`workflow-pre` and `workflow-post` hooks also refresh standard deterministic context-evidence packets.

## Required Context Evidence

Workflow start, resume, and finish write `validation/context-evidence-<event>.json` and `.md`. If an evidence path is unavailable, record deterministic fallback evidence before planning or implementation.

Lifecycle commands also write `artifacts/checkpoint/checkpoint.json` and `.md`. Use `workflow checkpoint --write` for a manual compact snapshot after large state changes.

Local AI is advisory only. Use it for validation triage, changed-file summaries, and handoff drafts when available; correctness, completion, and merge readiness come from deterministic commands and recorded evidence.

```shell
python -B .agents/manage.py workflow context-evidence --name {workflow_name} --run-id <run-id> --event start --write
```

## Example Prompts

- Start: "Start `{workflow_name}`. Use `HANDOFF.md` when present, then read `automations/routing.md`, `module.json`, and `WORKFLOW.md` before creating the workflow run packet."
- Fresh agent: "Start from `fresh-agent-packet --summary --compact --format json`, load the source-orientation file it names, then follow `next_command`."
- Resume: "Resume `{workflow_name}` from the latest run. Load `run.json` first, then the required context it names."
- Handoff: "Prepare a handoff for `{workflow_name}` by updating `run.json` with exact required context and next action."
- Finish: "Finish `{workflow_name}` by checking `run.json`, `REPORT.md`, skipped/blocked/failed checks, unsupported claims, validation status, `context-use-check --summary --compact --format json`, and `finish --summary --compact --format json`."

## Execution Model

Run sequentially by default. Use declared Python scripts for repeatable validation. Reviewer agents may inspect failed evidence, but correctness rests on deterministic evidence.
