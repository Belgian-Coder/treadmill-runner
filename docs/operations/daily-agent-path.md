---
title: Daily Agent Path
type: runbook
status: active
owner: workflow-manager
audience: agent
updated: 2026-07-25
---

# Daily Agent Path

Smallest owner, fresh evidence.

Core loop: `status --fast`, route, edit the smallest owner, run focused checks, then `finish`. `setup --check`, `status --fast`, `startup-context`, and `next-action` consume the same navigation readiness result; the fast cache is valid only for a clean matching Git source tree plus verified generated-map hashes. Older cache packets and dirty, mismatched, or non-Git states fall back to the full deterministic check. `status` and `startup-context` report `guidance_savings` so low-context routing stays measurable by default.

## Flow

1. Route through `.agents/routing.md` or `automations/routing.md`; when the user describes workflow-shaped work without naming a command, prefer `python -B .agents/manage.py workflow start --from-request "<request>" --summary --compact --format json`. It starts only when module routing metadata meets both its score threshold and winner margin. Medium confidence never auto-starts, and generic words such as `harness`, `workflow`, or `skill` do not activate a workflow alone. For read-only discovery or ambiguous requests, run `python -B .agents/manage.py which-workflow "<request>" --summary --compact --format json` and follow its `next_command` only when it names a specific workflow.
2. If the task maps to a workflow, use `workflow start`, `workflow start --from-request`, or `workflow resume`; do not ask the user to run internal local-AI commands. Workflow lifecycle commands create checkpoints, deterministic context evidence and packets, documentation deltas, and validation gates.
3. For project implementation work, read `docs/project/project-context.md` before planning and load `automations/navigation/artifacts/maps/HANDOFF.md` when present; when the next source area is still broad, run `python -B .agents/skills/repo-navigation/scripts/repo_navigation.py focus --target . --query "<task>" --format markdown` before opening full maps or folders. If context or maps are missing, run `python -B .agents/manage.py setup` and stop before implementation until critical project facts are explicit.
4. Open selected `module.json` and human entry only; on resume or handoff, run `python -B .agents/manage.py workflow context-audit --name <workflow-name> --run-id <run-id> --summary --compact --format json`, then load the returned context packet and required next context before raw workflow files.
5. Choose the smallest vehicle: skill, workflow, script, eval, docs, generated sync, or no change.
6. Run addition acceptance when files are added; new files need an owning skill/workflow, generated-source match, or allowlisted docs/evidence path. For Python syntax checks, use `python -B .agents/manage.py syntax-check --paths .agents/skills automations --format json`; do not use `py_compile` because it can create `__pycache__` cache regressions.
7. For larger changes, use `automations/disciplined-change-workflow/WORKFLOW.md`.
8. For changed files, inspect the validation router in `status --no-local-ai --summary --compact --format json`, run focused owner tests while editing, then run `finish --summary --compact --format json` once. Finish executes the changed-scope plan, records per-check proof, and emits the final claim receipt. Use `finish --deep` for impact-selected integration checks and `finish --release-full` only for exhaustive release evidence. `changed-evidence`, `check-changed`, and `finish --commit-packet` include an input fingerprint so stale proof is visible when files, validation commands, lockfiles, schemas, migrations, or environment contracts change.
9. Report context, changes, commands, generated artifacts, validation, skipped checks, blockers, and risk.

## Related Commands

Use `docs/reference/commands.md` for command variants. Common owners: `repo-navigation` for orientation/project context, `external-reference-manager` for references, `attachment-route` for attachments, and `credential-doctor --summary --compact --format json` for credentials. If an Azure DevOps/TFS/SonarQube profile is missing, ask the user for the required details and run `credential-doctor --configure --service <service>` so the local profile is saved to gitignored `.agents/local-ai/secrets.local.json`.
