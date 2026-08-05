---
title: Release
type: runbook
status: active
owner: skill-manager
audience: agent
updated: 2026-07-22
---

# Release

Statuses: `local-ready` = setup/health passed; `preflight-passed` = sync/validation passed; `release-ready` adds deep validation, benchmark doctor, smoke, and GitHub hygiene; `external-blocked` = local evidence is clean but outside proof is blocked.

```shell
python -B .agents/manage.py finish --release-full --budget-intent feature --commit-packet evidence/finish --summary --compact --format json
python -B .agents/manage.py release-evidence --local-only --skip-fresh-clone
python -B .agents/manage.py commit-readiness
```

Use `--budget-intent optimization` instead of `feature` for text-minimization work; it fails by default if total or tool-load words grow against `HEAD`.

Run `determinism-check --all --deep` separately only when deterministic command contracts changed; it replays every declared strict command twice in fresh isolated Git fixtures.

Do not call the repo publishable when external hygiene is blocked; report it separately from local validation.
