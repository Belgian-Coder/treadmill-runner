---
name: local-ai-helper
description: Use when installing, configuring, validating, benchmarking, routing with, or troubleshooting repo-local AI models, llama.cpp runtimes, model bundles, cache, brokered file tools, embedding benchmarks, and vision analysis.
---

# Local AI Helper

## Goal

Maintain optional repo-local AI setup, settings, manifests, routing/cache policy, read-only tools, benchmarks, vision/document helpers, and integrations.

## Workflow

1. Start with `.agents/local-ai/README.md`; open model decisions/benchmarks only when needed.
2. For strict no-write/no-temp/no-profile/offline dogfood, use source reads and `module.json.strict_read_only_commands` only. Do not follow diagnostic suggestions until separately classified safe.
3. For normal read-only diagnostics, after policy allows local settings/profile/cache inspection, check before suggestions:

```shell
python -B .agents/manage.py local-ai readiness --summary --compact --json
python -B .agents/manage.py local-ai policy --summary --compact --json
```

Other normal diagnostics: `doctor --quick`, `runtime doctor`, and `resources`, preferably with `--summary --compact --json`.

4. Configure before bootstrap. `configure` previews detected hardware without downloading; local scope writes ignored machine settings, while `--scope project` writes reviewed team defaults:

```shell
python -B .agents/manage.py local-ai configure
python -B .agents/manage.py local-ai config explain --task skill-routing
python -B .agents/manage.py local-ai configure --scope project --apply
```

Routes in either settings layer must use enabled tasks and validated catalog profile IDs. Only bounded runtime performance fields may be overridden. Bootstrap only when policy and the user allow writes/network:

```shell
python -B .agents/manage.py local-ai bootstrap [--run-model]
python -B .agents/manage.py local-ai models evaluate-candidate --candidate <candidate.json> --summary --compact --json
```

5. Use daily commands:

```shell
python -B .agents/manage.py local-ai task --task changed-files-summary --input <file-or-->
python -B .agents/manage.py local-ai vision describe --image <repo-image>
python -B .agents/manage.py local-ai vision pdf --pdf <repo-pdf> --pages 1-5
```

More task/document/vision/benchmark commands: `docs/reference/commands.md`.

6. For model smoke/bench:

```shell
python -B .agents/manage.py local-ai doctor --run-model --profile nemotron3-nano4b
python -B .agents/manage.py local-ai bench --standard-metrics --repetitions 3
```

Failed checks may cache `.agents/local-ai/cache/last-validation.txt` and auto-run triage; advisory output never changes exit codes.

## Rules

- Local AI is opt-in through `.agents/local-ai.json`, tracked project settings, then ignored machine settings; sync/validate enforce config, hashes, schemas, allowlists, confidence, and freshness.
- Policy lives in `.agents/local-ai/policy.json`; secrets stay in gitignored `.agents/local-ai/secrets.local.json`.
- Bootstrap downloads pinned bundles only when config allows; checks stay read-only.
- Normal diagnostics may inspect settings, profiles, caches, or host/runtime state; exclude them from strict dogfood unless source-reviewed.
- Strict read-only excludes configuration, downloads, mutation, model execution, tasks, document/vision work, and commands with write/cache/profile effects. `config explain` is normal read-only.
- GPU acceleration is an explicit local opt-in recorded in ignored `.agents/local-ai/local.settings.json`; absent settings use the portable CPU fallback. `gpu.mode=off` forces CPU; `auto` may probe pinned CUDA/Vulkan after safe detection, then fallback to CPU/off.
- Models never edit source; Python validates bounded JSON, Markdown drafts, cache, and generated routing fields.
- Brokered tools are read-only (`repo.search`, `repo.read`, `repo.tree`, `repo.generated-status`); reject path escapes, `.git`, model bundle reads, unknown tools, oversized reads, writes.
- Deterministic code owns routing, ordering, retries, date math, and validation; local models only shape bounded evidence. Orchestrating models own planning, implementation, meaningful tests, and final judgment.
- Mermaid diagrams/workflow design belong to the orchestrator, workflow-manager, and mermaid-diagrams-azure-devops; local AI must not generate or validate them.
- Treat empty output, repeated tool drift, unsupported command names, and JSON/schema failures as harness evidence for evals/guards, not prompt-only fixes.
- Task, document, image, PDF, and model benchmark commands write only `.agents/local-ai/cache/**`; they are local-cache writes, not strict read-only.
- Repository discovery uses scoped `rg`/portable `rg` followed by direct reads. The removed index and embedding-retrieval lane must not be recreated without a fresh paired benchmark that beats direct search on quality, abstention, latency, and evidence size.
- Embedding profiles remain optional benchmark candidates only. Default bootstrap does not download them and no workflow depends on them.
- Daily text tasks reuse an exact task/input cache before model calls. Warm-server batch applies to 2+ uncached inputs; single uncached inputs keep one-shot runtime.
- Every model path shares the cache-only exclusive lease; busy paths fall back deterministically, and candidate screening never downloads.
- In workflows, workflow-manager writes declared context evidence from bounded deterministic file scanning; local-ai commands remain optional diagnostics or model benchmarks, not user prerequisites.
- Keep GGUF files/runtimes/downloads/cache out of git. Commit only config, docs, manifests, notices, and scripts.
- Use Python 3.12+ stdlib.
- Optional setup reports skipped/failed steps as non-blocking; continue only when deterministic fallback can proceed.

## Validation

Run `local-ai readiness`, `policy`, `doctor --quick`, helper self-tests, `sync --check`, `validate-agent-compatibility`, and `validate`. Strict dogfood uses only declared strict commands; skip unreviewed write/cache/profile/model behavior. Use full doctor only when the host can safely run `llama.cpp`.

## Completion Contract

Report low-context files used/skipped, local AI paths inspected/changed, model/config decisions, commands, generated artifacts synced/skipped, validation, skipped/blocked/failed checks, and risks.

## Stop Rules

- Stop before downloading/replacing model/runtime payloads unless config allows bootstrap or the user asked.
- Stop before committing machine-specific GPU runtime paths, GPU binaries, or local settings.
- Stop before accepting non-direct downloads, account-gated models, unclear commercial licenses, unvalidated model output.
- Stop before hand-editing generated routing/registry/adapters; run sync instead.
