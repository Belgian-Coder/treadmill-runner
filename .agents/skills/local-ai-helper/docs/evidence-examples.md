# Local AI Evidence Examples

## Credential And Payload Boundary

- Local AI commands must not require external credentials during normal checks.
- Optional local service/profile secrets live only in `.agents/local-ai/secrets.local.json`.
- Model payloads, runtimes, downloads, rendered pages, and AI reports stay out of git unless they are explicit manifests, notices, or docs.

## Deterministic Fallback Packets

The first two commands are normal read-only diagnostics when policy allows local settings/profile/cache inspection. They are not strict no-profile/no-cache dogfood; strict dogfood uses `module.json.strict_read_only_commands`.

```shell
python -B .agents/manage.py local-ai readiness --json
```

```shell
python -B .agents/manage.py local-ai policy --json
```

```shell
python -B .agents/manage.py local-ai document inspect --file docs/sample.pdf --json
```

Expected evidence includes policy state, selected profiles, disabled or missing model/runtime reasons, deterministic fallback summaries, and document strategy selection. Repository discovery uses exact `rg` searches and direct file reads; it does not start a model or build an index. If a model cannot run, the command should still return a bounded fallback packet instead of editing source files or hiding the failure.
