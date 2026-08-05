---
title: Maintenance Policy
type: policy
status: active
owner: skill-manager
audience: agent
updated: 2026-06-22
---

# Maintenance Policy

## Rules

- Improve existing skills, workflows, scripts, evals, or docs before adding owners.
- Add skills only for reusable capabilities; add workflows only for phase/state/evidence orchestration.
- New files must pass `check-additions`: they need an owning skill/workflow, matching contract files, or a generated-source relationship.
- Use Python 3.12+ stdlib. Do not add shell, batch, PowerShell, IDE, trust, MCP, or machine-local settings in active paths.
- Keep tracked JSON pretty-printed; run `python -B .agents/manage.py format-json` after JSON edits and `python -B .agents/manage.py format-json --check` before final validation.
- Regenerate routing, registries, and instruction adapters with `sync`.
- Keep secrets, model/runtime payloads, downloads, dependency payloads, caches, candidates, copied lockfiles, and local service config out of git.
- Keep real run state under `automations/<workflow>/runs/<run-id>/` while active, but do not commit dogfood runs by default; promote durable findings into docs, suites, scripts, templates, fixtures, or compact reports before deleting raw runs.
- Follow [Evidence Retention](evidence-retention.md) for run history, local AI caches, temporary imports, and benchmark fixtures.

## Release Gate

```shell
python -B .agents/manage.py setup --check
python -B .agents/manage.py status --full
python -B .agents/manage.py check-additions
python -B .agents/manage.py sync --check
python -B .agents/manage.py benchmark doctor
python -B .agents/manage.py check --deep
python -B .agents/manage.py finish --deep
```

Before publishing, report generated sync, validation, skipped/failed/blocked checks, and risks.
