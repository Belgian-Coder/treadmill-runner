---
name: azure-devops-ticket-intake
description: Use when importing Azure DevOps REST API or TFS story, bug, task, feature, or epic tickets into local workflow run folders with PAT authentication, full field and relation capture, downloaded attachments, local description image links, and resumable Markdown state.
---

# Azure DevOps Ticket Intake

## Goal

Create workflow-owned Azure DevOps/TFS intake evidence before implementation. Stories use `automations/user-story-workflow/runs/`; bugs use `automations/bug-ticket-workflow/runs/`; other types use the selected workflow.

## Read-Only Dogfood

For strict read-only/offline/no-profile/no-temp/no-write review, run only `module.json.strict_read_only_commands` plus exact `rg`/reads. Only manual or local-fixture dry-runs are offline-safe; skip them when run-folder planning is prohibited. live-source dry-runs can still call Azure DevOps/TFS. Treat summary follow-ups as advisory; do not run local-AI, `--write`, output, profile/cache, temp, live import, or credential commands unless allowed.

## Workflow

1. Select the target workflow first: stories use user-story runs, bugs use bug-ticket runs.
2. Before REST import, check local service readiness, unless the task is read-only/no-personal-settings:

```shell
python -B .agents/manage.py credential-doctor --summary --compact --format json
```

If no profile exists, ask for profile name, Azure DevOps organization URL or TFS collection/server URL, project, and PAT source. Save it with `credential-doctor --configure` to gitignored `.agents/local-ai/secrets.local.json`; the command also repairs `.gitignore` when needed. Prefer `AZURE_DEVOPS_PAT`; store the PAT only with explicit user acceptance.

```shell
python -B .agents/manage.py credential-doctor --configure --service azure-devops --name customer-a --organization-url https://dev.azure.com/customer-a --project ProjectA --pat-env AZURE_DEVOPS_PAT --format json
python -B .agents/manage.py credential-doctor --configure --service tfs --name legacy-tfs --server-url https://tfs.example/tfs/Collection --project ProjectA --pat-env AZURE_DEVOPS_PAT --format json
```

For REST import, pass `--organization-url`, `--project`, `--work-item-id`, and a PAT through `AZURE_DEVOPS_PAT`, `--pat`, or `--server-name <name>` from `.agents/local-ai/secrets.local.json`. The script uses `$expand=all` and detects `System.WorkItemType`.
3. Live imports download Azure DevOps attachments by default. Use `--skip-attachments` for live no-download runs; `--include-attachments` is for copying fixture/manual attachment files. Use `--include-comments` only when comments matter.
4. Use `--fixture-json` for mocked/exported work items so tests and demos stay offline. Manual intake remains a fallback when access is unavailable.
5. Run `--dry-run` before imports that may write; it shows target folder, duplicate detection, and planned files.
6. Review generated `ticket-info.md`, `intake.json`, `fields.json`, `relations.json`, `comments.json`, and `attachments/manifest.json`.
7. Validate and summarize deterministically:

```shell
python -B .agents/skills/azure-devops-ticket-intake/scripts/summarize_imported_ticket.py automations/user-story-workflow/runs/<run> --output-json automations/user-story-workflow/runs/<run>/intake-summary.json --output-markdown automations/user-story-workflow/runs/<run>/intake-summary.md
python -B .agents/skills/azure-devops-ticket-intake/scripts/import_azure_devops_work_item.py --work-item-id 12345 --organization-url https://dev.azure.com/example --project Project --output-root automations/user-story-workflow/runs --workflow-root automations/user-story-workflow --include-comments --dry-run
python -B .agents/skills/azure-devops-ticket-intake/scripts/import_azure_devops_work_item.py --server-name customer-a --work-item-id 12345 --output-root automations/user-story-workflow/runs --dry-run
python -B .agents/skills/azure-devops-ticket-intake/scripts/import_azure_devops_work_item.py --work-item-id 12345 --work-item-type story --title "Manual title" --output-root automations/user-story-workflow/runs --workflow-root automations/user-story-workflow --dry-run
```

The summary command is read-only unless output paths are provided. It reports missing/partial imports and file/attachment hashes. Local AI may only summarize generated deterministic evidence:

```shell
python -B .agents/manage.py local-ai task --task inventory-summary --input automations/user-story-workflow/runs/<run>/intake-summary.md
```

Fallback without local AI: use `intake-summary.json`, `intake-summary.md`, `ticket-info.md`, `intake.json`, and `attachments/manifest.json`.

## Rules

- Do not write story or bug intake outside the owning workflow folder.
- Do not commit PATs, downloaded secrets, raw unsafe payloads, or local service configuration.
- Server profiles and PAT hints live only in gitignored `.agents/local-ai/secrets.local.json`.
- When config is missing, ask for service details and run `credential-doctor --configure`; do not call Azure DevOps/TFS with guessed URLs, projects, or token sources.
- Attachments are copied project data; save them under `attachments/`, record names, sizes, relative paths, and source URLs.
- Rewrite description image references to local `attachments/<file>` paths when they map to downloaded attachments.
- Keep intake facts, user assumptions, and generated summaries separate in `intake.json` and `ticket-info.md`.
- Summary validation is deterministic: no local model or network calls.
- `--force` is only for intentional regeneration. Duplicate IDs under the same output root are blocked unless `--force` is explicit.
- Manual intake is valid; missing credentials are skipped/failed setup and non-blocking when fixture/manual intake can continue.
- Keep raw service payloads out of source control unless `--include-raw-source` intentionally produced a sanitized fixture; credential-like values are redacted and PATs are never written.
- Destructive risk is declared conservatively because `--force` intentionally regenerates existing intake folders; normal imports block duplicate IDs unless `--force` is explicit.

## Validation

Normal validation only; skip self-tests/evals during strict read-only dogfood and use `module.json.strict_read_only_commands` instead.

```shell
python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/azure-devops-ticket-intake
python -B .agents/skills/azure-devops-ticket-intake/scripts/run_self_tests.py
python -B .agents/skills/azure-devops-ticket-intake/scripts/summarize_imported_ticket.py <import-folder> --format json
```

Self-tests write temporary fixtures only; skip them for strict read-only dogfood.

## Completion Contract

Report output folder, intake mode, generated files, attachment count, comments status, summary status, skipped imports, blocked downloads, failed commands, and data-quality risks. Report `Skill used: azure-devops-ticket-intake - <reason>` when material.

## Stop Rules

- Stop before downloading attachments if destination escapes the workflow-owned root.
- Stop before writing when `--output-root` is outside `--workflow-root`.
- Stop before finalizing if imported type conflicts with the selected workflow.
- Stop before using raw Azure DevOps payloads as evidence if they may contain credentials or private data.
