---
title: Daily Use
type: runbook
status: active
owner: skill-manager
audience: both
updated: 2026-07-22
---

# Daily Use

Use [Start Here](../start-here.md) for the first install or project update. Use this page after the harness is already present in the active project.

```shell
python -B .agents/manage.py status --fast
python -B .agents/manage.py next-action --summary --compact --format json
python -B .agents/manage.py sync
python -B .agents/manage.py finish
```

`status` and `next-action` are optional orientation commands; run `review-autopilot` only when review is requested. Use `changed-evidence` for inspection and a focused owner check while editing; `finish` owns required changed-scope completion validation. Use `status --full` for benchmark/evidence/local-AI detail, `status --capabilities` for proof, `commands --first-time|--daily|--workflow|--release` for indexes, and `--summary --compact --format json` for compact tooling.
