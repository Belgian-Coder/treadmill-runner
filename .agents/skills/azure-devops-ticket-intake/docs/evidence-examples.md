# Azure DevOps Intake Evidence Examples

## Credential Boundary

- Use `--server-name <profile>` or `AZURE_DEVOPS_PAT`; never write PAT values to workflow evidence.
- Store reusable local profiles only in `.agents/local-ai/secrets.local.json`, which is gitignored.
- Saved `fields.json`, `relations.json`, `comments.json`, and optional `raw_source` redact credential-like keys.

## Deterministic Packet

```shell
python -B .agents/skills/azure-devops-ticket-intake/scripts/import_azure_devops_work_item.py --server-name main --work-item-id 123 --project Project --output-root automations/user-story-workflow/runs --dry-run --format json
```

```shell
python -B .agents/skills/azure-devops-ticket-intake/scripts/summarize_imported_ticket.py automations/user-story-workflow/runs/US-123-example --format json
```

Expected evidence includes the import folder, normalized work item type, required file checks, attachment hashes, attachment type classification, and deterministic follow-up commands for PDFs, Office files, images, logs, traces, and archives.
