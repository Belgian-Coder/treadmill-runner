---
title: Evidence Retention
type: policy
status: active
owner: skill-manager
audience: both
updated: 2026-07-25
---

# Evidence Retention

Use evidence to prove behavior, but do not turn the reusable harness into a log archive.

## Defaults

- Do not commit dogfood workflow runs by default.
- Keep workflow smoke and dogfood runs temporary unless the user explicitly asks for a retained evidence packet.
- Promote durable lessons into docs, suites, scripts, templates, fixtures, or small reports.
- Delete raw temporary import evidence after the reviewed behavior has been integrated or rejected.
- Keep benchmark fixtures small and repo-owned; avoid depending on completed run folders for active evals.

## Allowed Tracked Evidence

- Small curated fixtures that active suites or tests read.
- Compact reports that explain why a decision remains relevant.
- License, provenance, manifest, and reference-card files that support active behavior.
- Templates and examples that are copied into new workflow runs.

## Not Tracked By Default

- `automations/*/runs/**` dogfood or smoke output.
- Retired local retrieval indexes, local AI caches, model bundles, downloads, and runtime payloads.
- Raw candidate imports under `evidence/` after review is complete.
- Python bytecode caches such as `__pycache__` and `*.pyc`.

## Validation

Before finalizing retention cleanup, run:

```shell
python -B .agents/manage.py check-additions
python -B .agents/manage.py workflow smoke --all --summary --compact --format json
python -B .agents/manage.py check
```

If a deleted run contained useful proof, replace it with a suite fixture, deterministic self-test, or concise Markdown reference before removing the raw run.
