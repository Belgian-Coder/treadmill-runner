---
title: After Failure
type: runbook
status: active
owner: workflow-manager
audience: agent
updated: 2026-07-21
---

# After Failure

Turn failed output into one next action.

```shell
python -B .agents/manage.py what-now --last --explain-owner
python -B .agents/manage.py what-now --from-command "python -B .agents/manage.py check"
python -B .agents/manage.py what-now --from-command "python -B .agents/manage.py check" --summary --compact --format json
```

The report gives first failing fact, type, owner, next command, and fallback. Deterministic failed output is authoritative. Automatic local-AI validation triage is disabled by the tracked policy; explicit local-AI tasks remain optional and policy-gated.

When `--from-command` passes, stale `.agents/local-ai/cache/last-validation.txt` evidence is cleared so the next triage cannot accidentally reuse an old failure.
