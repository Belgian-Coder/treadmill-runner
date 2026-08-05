---
title: Model Compatibility And Routing
type: reference
status: active
owner: workflow-manager
audience: agent
updated: 2026-08-02
applies_to: Codex, GitHub Copilot, Claude Code, direct model APIs, and local AI
---

# Model Compatibility and Routing

The portable orchestration contract is [`orchestration.md`](../../orchestration.md). A consumer may own `.agents/orchestration.json`, which maps concrete tasks to task sets and each task set to an ordered, host-specific model chain. Resolve it with:

```shell
python -B .agents/manage.py workflow route-model --task implementation --host codex --format json
```

This project layer answers “which model should this task try first, second, and last?” It does not create named agents and does not force delegation. The primary orchestrator decides whether a bounded subagent is useful. The deterministic resolver never invokes a model and reports `preference-only` until the host supplies `--available-model` evidence. `--failed-model` advances through the chain, whose final `active` or `inherit` candidate preserves a portable fallback for Codex, GitHub Copilot, Claude, and unknown hosts.

The project-owned task chain complements rather than replaces the catalog below. The project file owns concrete responsibilities and preferred fallback order. `worker_profiles.json` continues to own reusable semantic execution profiles, prompt overlays, context budgets, validation gates, and capability evidence. Host capability and current model availability still must be observed; neither catalog proves them.

The harness routes work on three independent axes. Never infer one axis from another.

| Axis | Owns | Evidence source | Safe fallback |
|---|---|---|---|
| Semantic task profile | Purpose, consequence, tools, context budget, output, validation, and authority | Workflow phase/task assignment | Keep the selected profile |
| Model-provider overlay | Small model-family prompt-delivery adjustments | Trusted observed `model_provider` and `model` | `generic-v1` |
| Host-surface adapter | Instruction files, orchestration, caching, continuation, subagents, hooks, and usage collection | Trusted `host_surface` plus explicitly attested capabilities | Direct tools, durable checkpoint, serial active model |

This separation prevents two common mistakes: treating Codex as the OpenAI API, and treating GitHub Copilot or Claude Code as a model identity. A host may use different models over time; a model may be reachable through several hosts with different controls.

The authoritative catalog is [`worker_profiles.json`](../../.agents/skills/workflow-manager/scripts/workflow_support/worker_profiles.json). Workflows extend `portable-default` and assign semantic profiles. The catalog's surface routes are preferences, not proof of availability or selection.

## Resolution Order

1. Resolve the project task or task set to its ordered host-specific preference chain.
2. Choose a semantic profile from task consequence and workflow phase.
3. Accept runtime identity and availability only from a strict, durable observation or the current host boundary.
4. Resolve a prompt overlay from the exact observed model-provider pair. Unknown identity uses `generic-v1`.
5. Resolve a surface adapter from the observed host and its attested capabilities.
6. Resolve the exact host-native tool vocabulary before allowing a benchmark arm to edit or validate; portable tool categories do not imply portable CLI identifiers.
7. Use a preferred model only when the host can select it. On failure, advance once through the task chain; when selection is unsupported or exhausted, retain the semantic profile and run the active model serially.
8. Keep deterministic command output and recorded evidence authoritative over model-written validation summaries.

Model overlays and surface adapters cannot change authority, tools, validation gates, output contracts, or delegation permission.

For native subagents, the adapter reports host capability as `available_orchestration_mode`; it becomes `effective_orchestration_mode: native-subagents` only when a persisted current-phase host observation also attests isolated workers, complete thread/usage telemetry, and `context-inheritance-control`, the workflow phase is parallel-safe, the task class is eligible, provider-backed economics passes, and the request or owner explicitly authorizes delegation. Otherwise the effective mode is `direct-tools` and every blocker is reported. This conjunction is identical for Codex, GitHub Copilot, and Claude Code.

## First-Class Host Surfaces

| Surface | Instruction surfaces | Capability examples | Default when unattested |
|---|---|---|---|
| Codex | `AGENTS.md`, `.agents/skills` | Native subagents, session continuation | Direct tools and durable workflow checkpoints |
| GitHub Copilot | `.github/copilot-instructions.md`, `AGENTS.md`, `.agents/skills` | Custom agents or subagents on supporting products | Generic overlay; do not infer the underlying model |
| Claude Code | `CLAUDE.md`, `.claude/rules`, `.claude/skills` | Subagents, hooks, session resume | Direct tools and durable workflow checkpoints |
| OpenAI Responses API | API request | Prompt-cache telemetry, reasoning continuation, hosted programmatic orchestration | Ordinary direct tool calls |
| Anthropic Messages API | API request | Explicit prompt caching and cache telemetry | Ordinary direct tool calls |
| Local AI | `.agents/skills`, broker packets | Repo-local advisory inference | Deterministic validation or paid-model fallback |

Inspect installed CLI surfaces explicitly when local host facts matter:

```shell
python -B .agents/manage.py validate-agent-compatibility --installed-hosts --summary --compact --format json
```

This opt-in mode runs only fixed version/help commands plus GitHub Copilot's JSON skill listing. It does not invoke a model, authenticate, update a CLI, inspect credentials, or prove that an advertised capability succeeds at runtime. The default compatibility command remains deterministic and installation-independent; provider-backed live evidence stays in `agent-benchmarking`.

Provider-specific optimizations belong only in their surface adapter:

- OpenAI prompt caching benefits from stable prefixes; place static instructions and tools before volatile user/run data. Read cache usage as telemetry, not as assumed savings. See [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).
- OpenAI reasoning continuation may preserve provider reasoning state across compatible Responses API calls. Store opaque continuation handles, never hidden reasoning text, and fall back to durable summaries/checkpoints on other surfaces. See [Reasoning across calls](https://developers.openai.com/api/docs/guides/reasoning#preserve-reasoning-across-calls).
- OpenAI programmatic tool calling can reduce repeated model round trips when the direct API surface attests hosted program orchestration. It does not authorize additional tools or side effects. See [Programmatic tool calling](https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling).
- ChatGPT/Codex customization layers should remain modular: stable repository policy, reusable skills, and task-specific context. Do not copy every instruction into every prompt. See [Customization overview](https://learn.chatgpt.com/docs/customization/overview).
- Claude Code hooks, subagents, and resume controls are host capabilities. Anthropic API prompt-cache controls do not automatically exist in Claude Code.
- Copilot instructions and custom agents are host capabilities. The selected Copilot model remains unknown unless the host reports it.
- GitHub Copilot CLI supports explicit `--model` selection and documents its current CLI model identifiers. Treat that list as availability guidance only: plans, clients, and versions can differ, so keep `active` as the final fallback and re-attest before execution. See [Copilot CLI supported models](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference#supported-models) and [Copilot model availability](https://docs.github.com/en/copilot/reference/ai-models/supported-models).

Repository behavior does not require personal host configuration. Do not commit Codex TOML, Copilot settings, Claude settings, trust state, or credentials. A host may map `implementation-low` to a supported low-deliberation route, but a missing control keeps the same semantic task contract on the active serial model.

## Runtime Observation v1

A host integration may write a packet under the selected run's `validation/` directory and refresh the context packet with it. Use the [v1 schema](../../.agents/skills/workflow-manager/assets/schemas/runtime-observation-input-v1.schema.json) and [fixture](../../.agents/skills/workflow-manager/assets/fixtures/runtime-observation-v1.json).

```json
{
  "schema_version": 1,
  "tool": "workflow-manager.runtime-observation",
  "workflow": "disciplined-change-workflow",
  "run_id": "provider-neutral-routing-20260719",
  "phase": "implementation",
  "host": {
    "attested": true,
    "source": "host-runtime",
    "surface": "codex",
    "capabilities": ["model-selection", "session-resume"]
  },
  "model": {
    "attested": true,
    "source": "host-runtime",
    "provider": "openai",
    "model": "gpt-5.6-sol",
    "observed_deliberation": "high"
  }
}
```

```shell
python -B .agents/manage.py workflow context --name <workflow-name> --run-id <run-id> --runtime-observation-file automations/<workflow-name>/runs/<run-id>/validation/runtime-observation.json --write
```

The packet is at most 16 KiB, rejects unknown fields and capabilities, requires workflow/run/phase identity, and is re-opened before persisted evidence is trusted. Host and model sections are independently optional and attested: a Copilot host can select its surface adapter without inventing a model, while model-only evidence can select an overlay without inventing host capabilities. `observed_deliberation` and host capabilities are optional evidence; absence is not converted to a default or a false capability. A stale, moved, changed, malformed, or mismatched packet fails closed to `unattested-active`, `generic-v1`, and serial active-model execution.

The packet validates shape and durable location. It is not a cryptographic signature. Copy only machine-reported identity/capability values; never put prompts, response content, credentials, hidden reasoning, or access tokens in it.

## Semantic Profiles and Surface Routes

Choose profiles by consequence, not provider or perceived model prestige:

| Profile | Intended work |
|---|---|
| `planning-high` | Architecture, ambiguity, risks, validation, and approval gates |
| `implementation-low` | Clear, reversible, single-pass work with one owning verifier and explicit escalation triggers |
| `implementation-mini` | Clear, bounded implementation with deterministic checks |
| `implementation-medium` | Bounded implementation needing broader context |
| `test-authoring-medium` | Behavior-defining tests and fixtures |
| `validation-local` | Deterministic validation with advisory local triage |
| `validation-mini` | Focused hosted validation triage |
| `coordination-low-cost` | Read-only routing, monitoring, and summarization |
| `review-high` | High-consequence or conflicting-evidence review |
| `evidence-mini` / `handoff-mini` | Compact evidence collection and handoff |
| `general-medium` | Normal work without a justified specialized profile |

Each profile references a `route_set`. Route sets can contain separate ordered candidates for Codex, GitHub Copilot, Claude Code, direct APIs, or local AI. There is no cross-provider primary/fallback chain. An observed Claude Code model is never treated as a fallback from a Codex route, and an unknown Copilot model never inherits an OpenAI overlay.

## Deliberation Boundary

The portable semantic tiers are `low`, `medium`, `high`, and `xhigh`. They express intended task depth and may be mapped to provider controls when supported. They are not proof that a host accepted a parameter. Preserve the exact observed provider value separately in `observed_deliberation`; an absent value remains absent.

## TokenMeasurementV1

All benchmark token measurements carry:

- provenance, scope, tokenizer/estimator, `host_surface`, and `model_provider`;
- required input, output, and total tokens, where total equals input plus output;
- cache-read, cache-write, and reasoning-output detail as `{value, availability}`;
- availability of `reported`, `derived`, `estimated`, or `unavailable`;
- separate completeness claims for `token-total`, `cache-economics`, and `reasoning-detail`.

Unavailable detail uses `null`, never zero. Cache-read and cache-write are disjoint subsets of input; reasoning output is a subset of output. Each usage event and the aggregate must satisfy those bounds plus `total = input + output`.

Full-run optimization gates accept only an implemented evidence adapter. They reopen the bounded no-follow source, verify its SHA-256, recompute usage, and bind the benchmark run, host, provider, observed model, provenance, accounting unit, and totals. `codex-rollout-v1` uses live Codex state plus an out-of-band trusted Codex home and normalizes custom raw providers to portable `other` without discarding the raw observation. `claude-code-result-v1` uses a coordinator-owned successful terminal stream result and normalizes ordinary input plus cache reads plus cache creation into inclusive input. `github-copilot-otel-v1` sums unique chat spans from one complete file-export trace and reconciles them with its root; repeated metrics are not summed. `openai-responses-usage-v1` aggregates an ordered all-call sanitized receipt for direct Responses use. All non-Codex adapters require an out-of-band trusted host-capture root and index. Copilot's observed `github` provider remains `other`; no provider is inferred from a model name. Provider invoices require their own adapter rather than masquerading as runtime telemetry.

For direct Responses integration, construct the request in memory with stable instructions/tools before volatile task data, keep opaque continuation state outside repository evidence, and emit only hashed identity plus structural and usage facts. A direct receipt may attest `prompt-cache-control`, `prompt-cache-telemetry`, `reasoning-continuation`, or `hosted-program-orchestration` only after the exact completed response demonstrates it. It never activates those controls for Codex, Copilot, or Claude Code merely because they use an OpenAI model.

The controlled cross-host plan is read-only and external-execution-only:

```shell
python -B .agents/skills/agent-benchmarking/scripts/provider_host_matrix.py --suite automations/agent-benchmarking/suites/provider-host-serial-matrix.json --format json
```

It contains 36 executable serial active-model cells across four hosts, three task classes, and three repetitions; all 36 are ready. Same-host subagent and local-agent delegation cells are excluded, rather than retained as permanently blocked rows. Reintroduce those arms only after complete orchestration evidence and separate hybrid-accounting adapters exist. Promotion remains scoped to the measured task class.

The V1 execution-harness planner contains two executable offline experiment families without launching agents or models:

```shell
python -B .agents/skills/agent-benchmarking/scripts/execution_harness_experiments.py --suite automations/agent-benchmarking/suites/execution-harness-experiments-v1.json --format json
```

All 36 canonical cells are runnable on Codex, GitHub Copilot, and Claude Code with current serial evidence adapters: 18 simple-bounded cells and 18 frontier-role/executor-role cells. The latter are non-promotional baseline characterization: both are controls, so their results describe the roles without selecting a default. Guided handoff and editor-comparison arms are not part of the suite; reintroduce them only after host-specific multi-segment or editor-event evidence exists. An experimental arm never changes normal routing.

Every external cell must now carry both `host-tool-vocabulary-v1` and `route-resolution-v1`. Record the exact host tool IDs, CLI version, platform, requested route, and observed model before treating the cell as comparable. Automatic routing is acceptable for exploratory evidence, but two auto-routed arms that observe different models are not a valid pair. On the currently validated Windows Copilot CLI 1.0.71 surface, the bounded file-edit vocabulary is `view`, `edit`, and `powershell`; re-attest rather than copying that mapping to another host version or platform.

## Corrections Become Reviewed Eval Candidates

Recurring failures can be recorded in the local feedback ledger. A reviewed correction packet can then be converted deterministically into provider-neutral eval cases:

```shell
python -B .agents/manage.py feedback review-digest --corrections evidence/corrections.json --format json
python -B .agents/manage.py feedback eval-packet --corrections evidence/corrections.json --output evidence/correction-evals.json
```

The correction event records task class, host surface, model provider, semantic profile, prompt, corrected behavior, acceptance criteria, and source references. Start with `review_state: review-input`. `review-digest` binds `reviewed_by` to the event set, sorts events by ID, recursively sorts object keys, serializes compact JSON with non-ASCII characters preserved, encodes UTF-8, and returns SHA-256. Then set `review_state: reviewed` and copy the digest. Generation refuses unreviewed, changed, oversized, or aliased packets, and publishes a new candidate atomically without overwriting. Output cases are candidates; suite promotion still requires deterministic review.

This adapts the durable lesson from OpenAI's [self-improving tax-agent case study](https://openai.com/index/building-self-improving-tax-agents-with-codex/): turn observed corrections into repeatable evals and improve the system against them. It does not permit autonomous prompt mutation or silent suite promotion.

Community reports about combining hosted and local agents, including the linked [Codex/local-agent discussion](https://www.reddit.com/r/codex/comments/1v082kh/combining_codex_56_with_local_agents_for_80_token/), are hypotheses for benchmark design—not evidence of an 80% saving in this repository. Promote hybrid routing only after equivalent-quality, provider-backed trials include manager and worker usage, local inference costs, cache state, retries, and rework.

## Evidence and Promotion Rules

Record the semantic profile, observed host, observed model-provider identity, resolved overlay, resolved surface adapter, capabilities, fallback reason, validation result, and complete delegated usage tree when applicable.

Public documentation proves documented product behavior; it does not prove active-host availability or selection. Runtime attestation proves one execution boundary; it does not prove general superiority. Benchmark evidence is scoped to the measured host, provider, task class, execution mode, cache state, and quality gate.

Inspect the current catalog with:

```shell
python -B .agents/manage.py workflow workers --profiles --compact --format json
python -B .agents/manage.py workflow workers --all --summary --compact --format json
python -B .agents/manage.py workflow route-model --validate --host default --format json
```
