---
title: Task and model orchestration
type: reference
status: active
owner: workflow-manager
audience: agents
updated: 2026-08-02
---

# Task and model orchestration

The primary agent remains accountable for scope, integration, validation, and the final answer. It may keep work local or create bounded subagents when the host supports them and delegation is useful. A task route never requires one named agent per responsibility.

Project-owned routes live in `.agents/orchestration.json`. Each task maps to one task set. A task or its task set selects a named chain containing an ordered model preference for each host. Exact task configuration wins over its task set; an unknown task uses `default_task_set`; an unknown host uses the chain's `default` route.

Resolve before selecting a model or delegating:

```powershell
python -B .agents/manage.py workflow route-model --task implementation --host codex --format json
```

The result is preference-only unless the host supplies models it can actually invoke:

```powershell
python -B .agents/manage.py workflow route-model --task implementation --host codex --available-model gpt-5.6-terra --available-model gpt-5.6-sol --format json
```

When an attempted model fails, exclude it and resolve again:

```powershell
python -B .agents/manage.py workflow route-model --task implementation --host codex --available-model gpt-5.6-terra --available-model gpt-5.6-sol --failed-model gpt-5.6-terra --format json
```

Rules:

- Treat order as preference, never as evidence that a model is installed, entitled, or supported by the current surface.
- Each host chain ends in `active` or `inherit`, the portable fallback to the already-running model.
- `orchestrator-decides` means delegation is permitted, not mandatory. Delegate only independent bounded work whose coordination cost is justified.
- `primary` keeps integration responsibility in the primary context; it does not prevent the host from honoring a supported model preference.
- `deterministic` runs scripts or tools without a model route.
- Provider-specific limitations win. Unsupported reasoning controls inherit from the host.
- Do not retry an unchanged failed model. Mark it with `--failed-model` and advance once through the declared chain.
- The project routing file is preserved during harness updates. Change it when project risks or measured cost/quality evidence justify a different order.

Validate configuration with:

```powershell
python -B .agents/manage.py workflow route-model --validate --host default --format json
```
