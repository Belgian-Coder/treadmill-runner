# Benchmark Methodology

Repo-owned benchmarks are the release signal. External suites may name metrics; this harness measures repo-real routing, validation triage, deterministic repository search, brokered tools, document evidence, workflow packets, context savings, and CPU inference.

## Families

| Family | Evidence |
|---|---|
| Inference | TTFT, TPOT/ITL, latency, throughput, memory, CPU, cold/warm |
| Repository search | Task success, required-path recall, top-1 hit rate, no-evidence precision, citation coverage, wall time |
| Routing/tools | Owner accuracy, command choice, forbidden-tool avoidance, retries, verifier evidence |
| Context savings | Skipped broad reads, routed packets, measured token counts when tokenizer support is installed, otherwise explicit estimates |
| Failure recovery | First failing fact, failure type, owner, next command |
| Small-model fit | Compact-doc followability, fallback, low-context start |
| Workflows/docs | Run state, packet validity, extraction, inventory, compare output, unsafe-path blocking, vision facts |
| Agent discipline | pass@1, unsupported claims, failed/skipped validation, overbuild |
| Agentic coding | patch resolution, test outcome, tool trajectory, sandbox, harness/runtime/model config |
| Clean-folder control | Direct no-workflow/no-skill request/result envelope, measured from an isolated folder |
| Skill utility | Paired no-skill and with-skill runs, deterministic verifier, skill metadata, quality/pass/token/cost deltas |

## Report Shape

Comparable runs keep normalized core fields and may add:

- `metrics_standard`: latency, throughput, memory, CPU, cold/warm, distributions.
- `run_config`: model/runtime hashes, backend, threads, context, prompt version, git ref, dirty state.
- `agent_task_metrics`: pass@1, attempts, verifier, tool calls, retries, trajectory, unsupported claims, evidence coverage.
- `trajectory_signals`: cheap local interaction/execution/environment counts such as stagnation, loop, timeout, tool error, and environment exhaustion. An optional content-free `execution_trace_v1` is validated and consumed into `trace_summary`; raw events are not persisted in the normalized result. These are triage attributes, not LLM-judge scores.
- `context_savings` and `advisory_token_estimates.context_saved_tokens_estimated`: packets, skipped reads, measured `tiktoken` counts when available, otherwise explicit chars/4 estimates.

Runs are comparable when suite, task, workflow version, prompt version, and material runtime/model config match or differences are stated. Repository-search comparisons also include exact suite version, exclusions, query-term extraction, evidence cap, and worktree identity.

Every externally executed harness arm also records `host-tool-vocabulary-v1` and `route-resolution-v1`. The first binds the exact host-native tool identifiers allowed for the run; generic labels such as `shell`, `read`, or `write` are not portable substitutes. The second records the requested route plus the observed model. A host process exit of zero or a model-written success statement never substitutes for the independent verifier. If automatic routing serves different observed models across paired arms, keep the individual receipts but reject the comparison.

## Agentic Coding

Agentic coding benchmarks are separate from inference token/s and output-only code-generation suites. A valid agentic coding run records the agent harness and tool execution boundary, not just model weights:

- `metrics_family`: `agentic_coding`
- `dataset_family`: e.g. `swe-bench-verified-mini`, `repo-real-fixture`, or `external-artifact-import`
- `agent_harness`: tool such as Pi, OpenCode, Codex, Claude Code, or a repo-owned workflow
- `toolset`: allowed shell, edit, browser, search, patch, test, and container tools
- `sandbox`: filesystem/network/container/admin permissions and whether Docker or external services were used
- `patch_extraction`: how file edits or diffs were captured and normalized
- `scorer`: deterministic tests, SWE-bench `FAIL_TO_PASS`, external judge, human review, or mixed scorer
- `attempts`, `timeout_seconds`, `pass_at_k`, `resolved_percent`, `duration_seconds`
- `model_quant`, `backend`, `runtime`, `runtime_lane`, `platform`, `memory`, and `result_artifact_url` or `result_artifact_path`

Treat public Pi/OpenCode/SWE-bench Mini reports as methodology references unless the repo has a normalized local run packet with the same agent harness, dataset subset, scorer, timeout, runtime, model quant, and patch extraction method. Do not promote hardware-specific model rows from token/s or external agentic leaderboards alone.

Long-horizon external suites such as LongCLI-Bench and SWE-CI are metadata-import references by default. Normalize external rows through `automations/agent-benchmarking/suites/external-long-horizon-agentic-coding.json`; do not add Docker, `uv`, Conda, Hugging Face downloads, API-key runners, or multi-day workloads to default repo validation.

## Skill Utility

Do not claim a skill improves outcomes from a single with-skill run. Use paired runs with the same suite, task, workflow, prompt, verifier, model/runtime, and sandbox:

- baseline: `run_config.skill_condition = "no-skill"`
- candidate: `run_config.skill_condition = "with-skill"`
- both: same `run_config.skill_name`, deterministic checks, grounding, skipped/failed checks, token estimates, and run packet shape

Then compare:

```shell
python -B .agents/skills/agent-benchmarking/scripts/compare_benchmark_runs.py <no-skill-run> <with-skill-run> --skill-utility-gate --require-comparable --format json
```

The skill utility gate accepts only if quality/pass improves or measured token/cost drops at equal quality, with no failure, skipped-check, hallucination, evidence, or negative-trajectory regression. Metadata-only contracts live in `automations/agent-benchmarking/suites/skill-utility-paired-local.json`.

External skill benchmarks such as SWE-Skills-Bench and SkillsBench are methodology references unless their results are rewritten into repo-owned normalized packets. Their useful lesson is paired evaluation with deterministic verifiers and token overhead, not adopting Docker/API-key runners as local gates.

## Corpus And Baselines

Repository search uses the checked-out files directly and excludes caches, temporary files, workflow runs, fixtures, raw outputs, binaries, and generated registries. Workflow-run context evidence scans only paths declared by the workflow.

Routing evals can pass `--baseline`; regressions, missing current results, and missing baseline rows fail. Improvements do not hide regressions.

## Improvement Claims

A test authored for a candidate change does not prove the candidate improved; it proves the old ref missed a new contract. Report new-contract validation or capability delta, not quality delta.

Objective improvement claims need one unchanged evidence shape: pre-existing suite/prompt/verifier/rubric for both refs, neutral verifier or command matrix, or two normalized reports where `compare_benchmark_runs.py --require-comparable` succeeds.

When baseline capability did not exist, report `missing -> present`, command exit deltas, case/check counts, skipped external boundaries. Use:

```shell
python -B .agents/manage.py benchmark capability-matrix --baseline-root <old-root> --candidate-root <new-root> --format json --compact
```

Use a clean-folder control when the question is "what would this cost without workflow/skill context?" The control writes only direct request/result files and a `summary.json` under a clean folder, with empty workflow/skill/routing context lists:

```shell
python -B .agents/skills/agent-benchmarking/scripts/clean_folder_control.py --suite <suite.json> --output-root <clean-root> --run-id <run-id> --format json
```

When the benchmark compares a direct control against a workflow harness, separate execution input from evaluator scoring. A true no-harness/direct execution folder contains only the target project and ordinary task prompt context. It must not include or reference workflow files, skill docs, `PROCESS.md`, templates, required evidence checklists, context-evidence packets, generated run packets, or benchmark procedure files. Supplying those artifacts creates a procedure-harness control, even when workflow lifecycle commands and skills are disabled.

Same-output parity belongs in the evaluator, not in the direct test folder. After the run, an external evaluator may inspect the direct output and score whether it produced comparable docs, diagrams, decisions, validation proof, skipped/failed-check records, and handoff material. Do not claim a true no-harness cost/quality comparison when the direct arm was given the parity checklist or evidence templates during implementation. Mark such results as `procedure_harness_control` or invalid for plain-direct claims.

Use a benchmark feature card when a workflow agent needs the same benchmark task, validator, and output-contract facts without loading large harness implementation files. The card is safe for planning and handoff, not for editing or debugging verifier code:

```shell
python -B .agents/skills/agent-benchmarking/scripts/benchmark_feature_card.py --suite <suite.json> --output-root <run-artifacts> --run-id <card-run-id> --replace-path <large-paid-context-file> --verifier-path <verifier-file> --format json
```

Use a prompt packet when the workflow needs a planning artifact envelope that is smaller than the replaced benchmark sources. Compare with-local-AI and without-local-AI arms only when `suite_id`, `prompt_version`, `story_hash`, `fixture_hash`, validator commands, output contract, and tokenizer match:

```shell
python -B .agents/skills/agent-benchmarking/scripts/benchmark_prompt_packet.py --feature-card <feature-card-summary.json> --output-root <run-artifacts> --run-id <packet-run-id> --packet-profile condensed --format json
python -B .agents/skills/agent-benchmarking/scripts/compare_prompt_packet_pair.py --without-summary <without-summary.json> --with-summary <with-summary.json> --without-output-path <file> --with-output-path <file> --with-local-ai-path <file> --without-timing-path <file> --with-timing-path <file> --output-root <run-artifacts> --run-id <pair-run-id> --format json
python -B .agents/skills/agent-benchmarking/scripts/compare_three_arm_artifact_tokens.py --plain-summary <clean-control-summary.json> --pair-summary <pair-summary.json> --output-root <run-artifacts> --run-id <three-arm-run-id> --format json
```

Prompt-packet pair reports are not full agent-run token bills. They include only the prompt-packet markdown, explicitly listed saved output artifacts, explicitly listed local-AI artifacts in a separate bucket, and explicitly listed timing files. They exclude full live workflow context, repo/project reads outside the listed artifacts, hidden orchestration prompts, tool-call payloads not saved as listed artifacts, subagent context, provider billing telemetry, and full end-to-end wall-clock time.

For complex code-generation comparisons, use `automations/local-ai-benchmark-workflow/suites/dotnet10-feature-sliced-efcore-project.json`. It fixes one .NET 10 ASP.NET Core Minimal API story across every run: feature-sliced Products, Reservations, and Reports, EF Core SQLite persistence, idempotent reservations, fulfillment, cancellation, low-stock reporting, and xUnit v3 integration tests. Validate the reference fixture with a short `--project-work-root` on Windows so SQLite native assets and generated paths do not exceed filesystem limits:

```shell
python -B .agents/skills/agent-benchmarking/scripts/dotnet_feature_project_fixture.py --output-root <run-artifacts> --project-work-root <short-project-work-root> --run-id <fixture-run-id> --write --run-tests --format json
```

## Token Savings

`token_measurement` uses provider-neutral TokenMeasurementV1. It records provenance, scope, accounting unit, tokenizer or estimator identity, host surface, model provider, input, output, total, and explicit detail objects for cache-read input, cache-write input, and reasoning output. Each detail has a `value` and an availability state: `reported`, `derived`, `estimated`, or `unavailable`. Unavailable values are `null`, never a synthetic zero. Cache-read and cache-write are disjoint input subsets; reasoning output is an output subset; none is added again to the total. Every usage event and aggregate must satisfy `total = input + output`, cache-read plus cache-write no greater than input, and reasoning no greater than output. Aggregation uses the conservative availability lattice `reported > derived > estimated > unavailable`: any unavailable component keeps that aggregate detail unavailable. `complete` is true exactly when `missing` is empty. `advisory_token_estimates` records non-billing estimates and context deltas separately from measured telemetry.

Full-run optimization gates require a complete provider-token measurement from comparable runs, a true `token-total` completeness claim, observed non-unknown host and provider identities, and implemented adapter evidence. The gate reopens bounded no-follow sources, rejects aliases and hard-linked host captures, verifies SHA-256, recomputes usage, and binds the benchmark run, host, provider, observed model label, provenance, accounting unit, and totals. Generic Codex comparisons also require a `codex-usage-ledger-v1` receipt, an out-of-band `--trusted-codex-home`, and a matching run-folder `PROMPT.md`; the ledger row must still match the live read-only state database, sessions rollout, model observation, prompt boundary, and TokenMeasurementV1 record. Claude Code, GitHub Copilot, and direct Responses comparisons require coordinator receipts under an out-of-band `--trusted-host-capture-root`; its independently written `host-capture-index.json` binds run ID, receipt path/hash, nonce, and model label. These are host-state or coordinator-capture consistency proofs, not cryptographic provider signatures. Cost comparison is fail-closed because no trusted invoice adapter exists. Comparability includes scope, provenance, accounting unit, tokenizer/estimator identity, host surface, model provider, and model label. Artifact-scope gates require `artifact-tokenizer-v1`: it reopens every bounded no-follow UTF-8 input/output, verifies hashes, and reproduces exact `tiktoken:<encoding>` counts with the recorded package version. Those are tokenizer proxy counts, not provider-native tokens. Heuristic estimates and self-authored counts cannot prove a measured win. Cache-economics is true only when both cache details are available; reasoning-detail requires available reasoning telemetry. Local price tables produce `local_price_estimate`, never measured provider cost.

Evidence adapters are provider-neutral contracts, not provider guesses:

- `codex-rollout-v1` plus the generic ledger receipt bind the measurement to the durable rollout, live thread state, prompt, and hash when the verifier receives the trusted Codex root outside the report.
- `claude-code-result-v1` reopens a coordinator-owned `claude -p --output-format stream-json --verbose --session-id <uuid>` capture. It requires one successful terminal result, process exit zero, exact session/run binding, complete aggregate and per-model usage, and cache normalization where inclusive input equals ordinary input plus cache reads plus cache creation. Exact reasoning-token detail remains unavailable.
- `openai-responses-usage-v1` reopens one sanitized, ordered receipt covering every direct `/v1/responses` call in the run. It aggregates every call, rejects duplicate response IDs, binds stored continuation to the immediately preceding response, binds stateless replay to the complete preceding history, and validates cross-call program/function/output relationships. The receipt contains hashes, structural relationships, status, model, and usage only—never credentials, prompts, output text, or encrypted reasoning. Capability attestation requires the complete run to demonstrate it.
- `github-copilot-otel-v1` is implemented for one complete GitHub Copilot CLI file-export invocation using the live-tested 1.0.71 flat JSONL envelope. It requires process exit zero, one session UUID, one trace, one root `invoke_agent`, unique child spans, and complete parent linkage. Usage sums unique `chat` spans only and reconciles their core totals with the root, because periodic/final metric records can repeat. Cache-read, cache-creation, and reasoning details stay unavailable unless every chat span reports them. The raw OTel file remains only in the trusted capture root: even with message-content capture disabled it can contain tool definitions and operational events, so it is neither sanitized nor committed. Only the sanitized receipt enters a report. The observed `github` serving provider maps to portable `other`; model vendor is never inferred from the model name.

Live Copilot CLI 1.0.71 validation on Windows resolved the native bounded-edit tool vocabulary to `view`, `edit`, and `powershell`. Supplying unknown generic identifiers can disable the needed tools while the process still exits successfully; supplying `bash` on Windows can allow the edit but prevent the declared verifier from running. Treat this mapping as a versioned host observation, not a universal Copilot constant, and re-attest it after host upgrades or on another platform.

A model label, UI total, report-selected state root, self-authored JSON, or provider name alone never upgrades an adapter. Runtime telemetry is not an invoice.

Use the V1 host matrix to expand, but never launch, the executable serial host baselines:

```shell
python -B .agents/skills/agent-benchmarking/scripts/provider_host_matrix.py --suite automations/agent-benchmarking/suites/provider-host-serial-matrix.json --format json
```

It creates 36 cells: four host surfaces, three bounded task classes, one serial execution arm, and three repetitions. All 36 cells are executable with the current adapters. The former 72 delegation cells were removed because the host adapters cannot yet prove complete usage trees, spawn/context-inheritance evidence, bounded child packets, or separate hosted/local economics. They should return only as a new executable contract after those measurements exist. Promote a routing policy only for the measured task class and only after equivalent quality, no new failures/skips, provider-backed tokens, and wall time all pass.

## Trace-Derived Execution Signals V1

`trajectory_signals.execution_trace_v1` is an optional portable, content-free event envelope. It accepts at most 10,000 strictly ordered events and requires exact V1 fields: sequence, elapsed time, round, event kind, actor and optional spawn target, categorical operation, input/result SHA-256 fingerprints, authorization, context inheritance, scope, and whether the event was a material action. It records hashes and categories—not prompts, commands, file content, model output, credentials, or hidden reasoning.

The verifier derives duplicate commands, unchanged reads, unchanged validations, unauthorized spawns, recursive spawns, unknown context inheritance, and excess-scope events. It also records neutral event/kind/round counts, compactions, time to first material action, and maximum spawn depth. Self-authored counts cannot override different trace-derived values. These measures identify repeated unchanged behavior; they do not label long reasoning as inherently wasteful and do not replace quality, rework, wall-time, or provider-token gates.

Context inheritance uses the same portable vocabulary as delegated evidence: `fresh`, `selected-turns`, `full`, or `unknown`; non-spawn events use `not-applicable`.

## Execution-Harness Experiments V1

Use the offline planner to expand the same repeated experiments across Codex, GitHub Copilot, and Claude Code:

```shell
python -B .agents/skills/agent-benchmarking/scripts/execution_harness_experiments.py --suite automations/agent-benchmarking/suites/execution-harness-experiments-v1.json --format json
```

The planner performs bounded no-follow reads and never launches an agent/model, calls the network, invokes a subprocess, writes files, or promotes an arm. It compares:

- default serial execution with the portable simple-bounded serial contract;
- non-promotional frontier-role and executor-role serial baseline characterization.

Every V1 cell has exactly three repetitions, isolated workspaces, deterministic checks, complete host receipts, provider-token evidence, quality, wall time, and rework requirements. The canonical suite contains 36 cells and all 36 are executable with the current serial evidence adapters. The frontier/executor family is descriptive baseline characterization only: both arms are controls and neither can be promoted in V1. Multi-segment handoff and editor-comparison arms are excluded until their host adapters can prove complete ordered segments or editor events. A route-resolution record must say whether the requested low-cost route was observed or the serial active-model fallback was used; unavailable deliberation controls do not invalidate the portable stopping rule. Ready cells still need externally executed result packets before promotion, and results stay host- and task-class-scoped.

`anchored_edit_v1.py` remains an opt-in dry-run prototype outside the canonical execution-harness suite, not a replacement for native editors. It works only below a workspace containing `.anchored-edit-benchmark-v1.json` with the exact V1 marker. `read` emits full-file SHA-256, UTF-8/BOM and newline facts, and per-line navigation anchors. `apply` requires the full-file digest plus matching line anchors, rejects duplicate JSON keys, links/reparse points/hardlinks, overlapping operations, stale reads, mixed newlines, invalid UTF-8 or non-UTF-8-encodable replacement text, and outside paths, then reports the deterministic result digest and byte count without writing. Short anchors locate lines; only the full SHA-256 authorizes the simulation. V1 intentionally has no write mode because portable stdlib path replacement cannot prove race-free installation against a concurrent workspace writer. The benchmark workspace must be isolated and quiescent while reading: static path/link checks do not defend against a privileged or same-user process swapping parent directories during a read.

For structural-search token claims, measure broad text-search candidate context separately from compact structural-filter output:

```shell
python -B .agents/skills/agent-benchmarking/scripts/structural_search_benchmark.py --allow-npx --format markdown
```

The benchmark treats ast-grep JSON as raw tool output and the post-processed `file:line:snippet` list as review context. Claim savings only for review-context tokens, not provider billing, and report the tokenizer metadata.

## Web Evidence Artifact Efficiency V1

`web_evidence_benchmark.py` compares two offline renderings of the same checked-in normalized page blocks: an `all-blocks-control` and `lexical-block-filter-v1`. Both arms use canonical JSON with `trust_boundary: untrusted-external-data`, `instructions_authorized: false`, complete source provenance, coherent cache age/TTL metadata, atomic blocks, and block SHA-256 values. The whole `sources[]` envelope is untrusted external data, while page-body text remains atomic inside `sources[].blocks[].text`; JSON round-tripping proves structural isolation in the packet shape, not that a consuming model will resist or ignore prompt injection. Behavioral prompt-injection resistance needs a separate live tool-authority evaluation.

The release gate requires every fixed case to execute, all golden blocks and metadata to survive, structured blocks to remain byte-exact, no-evidence cases to abstain, source and byte caps to hold, and aggregate serialized UTF-8 bytes to fall by at least 40%. This offline command intentionally does not load a tokenizer: a cold tokenizer may fetch or cache data and would violate its no-network/no-write contract. Byte reduction is artifact evidence, not live web accuracy, token usage, or billing evidence. A live claim needs a separate same-host, same-model comparison with provider telemetry and an external quality evaluator.

Local AI token savings must count input artifacts and output artifacts separately from local-only advisory artifacts. Cold local-AI runtime is measured, not assumed away. If a repeated workflow uses an exact-input local-AI cache hit, record `cache_hit`, the cache timing, and the uncached run separately; only the measured cached arm can be used for a time-capped artifact-savings claim.

For user-story workflow comparisons, keep three same-story arms before drawing conclusions: a true plain-direct control, the workflow harness without local AI, and the workflow harness with local AI. The true plain-direct arm receives only the normal user story and target project path. It does not receive workflow routing, skill instructions, workflow packets, hidden host context, `PROCESS.md`, templates, evidence checklists, or context-evidence outputs. If parity docs are required, measure them through a post-run evaluator or label the arm as procedure-guided; otherwise the comparison measures a manual harness, not no-harness execution.

For complete feature-implementation cost estimates, artifact envelopes are not enough. Run each arm in its own telemetry-visible Codex thread, then aggregate Codex rollout `last_token_usage` events:

```shell
python -B .agents/skills/agent-benchmarking/scripts/codex_usage_ledger.py --run plain=<thread-id> --run harness_no_ai=<thread-id> --run harness_local_ai=<thread-id> --input-per-million <price> --cached-input-per-million <price> --output-per-million <price> --format json
```

The complete-count comparison sums input, available detail values, output, and total from every recorded model call in each listed thread while preserving unavailable details as unavailable. Codex rollout telemetry maps `cached_input_tokens` to cache-read and preserves cache-write only when the provider event reports it. `output_tokens` already includes reasoning output, so reasoning is not added again for cost. The ledger validates each event and aggregate, and requires rollout and SQLite provider identities to agree before the run is complete.

## Repeated Three-Arm Full Runs

Use `three_arm_full_run.py` as an offline coordinator. It prepares and validates evidence but never launches an agent, provider request, model, local AI process, network call, or subprocess. Live execution remains an external, separately authorized step.

```shell
python -B .agents/skills/agent-benchmarking/scripts/three_arm_full_run.py prepare --definition <definition.json> --output-root <coordinator-artifacts> --write --format json
python -B .agents/skills/agent-benchmarking/scripts/three_arm_full_run.py preflight --protocol <coordinator-artifacts>/protocol.json --live --format json
python -B .agents/skills/agent-benchmarking/scripts/three_arm_full_run.py aggregate --protocol <coordinator-artifacts>/protocol.json --trial-index <coordinator-artifacts>/trial-index.json --format json
```

The protocol fixes `direct`, `harness_no_local_ai`, and `harness_local_ai` and requires at least three fresh, isolated workspaces per arm. The direct arm receives only the ordinary task and pristine fixture. Coordinator files, the harness source, evaluator, rubric, workflow/skill/routing context, context packets, procedure files, and evidence templates stay outside its workspace. The protocol records the exact declared target under `requested_model`. This requested target is configuration, not evidence that the host selected or exposed that model. Preflight validates safe no-follow source hashes, distinct roots, evaluator withholding, and treatment configuration. The coordinator/evidence roots are trusted, exclusive, and quiescent during aggregation; stdlib no-follow and hardlink checks reject static aliases but cannot make a hostile concurrent parent-directory swap impossible on every host. `--live` means “check readiness for external execution”; the report must still say execution, network, model, and subprocess use are false.

Each generated trial template contains `user_prompt_contract.prepared_prompt_path` and `prepared_prompt_sha256`. The file is the exact decoded immutable task text, one canonical newline, and the trial marker. The external executor must submit that complete file unchanged as one text-only user message in a fresh thread and pass it to the ledger with `--execution-prompt-file <label>=<path>`. The usage ledger and aggregator require whole-message equality from parsed `event_msg` user-message text or a `response_item` whose role is `user` and whose content consists only of `input_text`. That prompt must be the first structured user message and no usage event may precede its first representation; duplicate representations of the same prompt are allowed, and all later follow-up/rework usage remains included. Marker-only follow-ups, changed or abbreviated tasks, prior conversation or usage, images/files/attachments/context fields, inline substrings, `turn_context`, assistant/tool messages, and raw event text do not count. Provider, model, and reasoning evidence still comes only from normal `turn_context` fields.

Aggregation reads one deterministic `trial-index.json` with `schema_version`, tool ID `agent-benchmarking.three-arm-full-run-trial-index`, benchmark ID, protocol hash, and an ordered `trial_paths` array. The array must name exactly every protocol-declared packet in fixed arm and replicate order, with at least nine entries; duplicates, omissions, extra entries, globs, directory inputs, or discovery are invalid. Relative paths resolve from the index file and must remain inside the coordinator output root. Aggregation then reads only those explicit packets and bounded no-follow evidence files, re-hashes the task, fixture, harness, evaluator, local provider ledger/rollout, output manifest, preflight/isolation proof, evaluator result, optional local-AI proof, and any invoice-shaped artifact, and compares the output manifest with the actual isolated workspace tree. A per-trial execution nonce and prompt marker cross-link the trace. Missing, linked, outside-root, malformed, truncated, reordered, or mismatched evidence makes the benchmark invalid. This proves local trace consistency only. Because aggregate has no out-of-band host-state or invoice adapter, provider token/cost promotion stays disabled; requested labels and coordinator-authored exports never substitute for trusted evidence.

Delegated trials use `thread_tree`: one root, direct children only, and one durable spawn-event record per child whose SHA-256 and root-rollout binding are rechecked. Every spawn event records `fresh`, `selected-turns`, or `full` parent-turn inheritance plus the parent prompt hash and a durable exact child-prompt path, SHA-256, and byte count. Aggregation reopens the child prompt no-follow and requires the child's rollout prompt telemetry to match it. Fresh and selected-turns children also provide a bounded evidence-packet path, SHA-256, and exact byte count; aggregation reopens that packet no-follow inside the coordinator root. Requested per-thread configurations remain in the protocol; exact provider-reported `observed_model` and reasoning evidence remain in each thread's durable rollout evidence and are compared without alias normalization. Provider, model, reasoning effort, prompt, cwd, rollout hash, context provenance, and complete usage are verified independently for every thread, then `TokenMeasurementV1` is summed across the tree. Missing, unknown, duplicate, shared, unexpected, reused, or recursively spawned threads invalidate the trial. Non-delegated trials use the single-thread packet.

For `delegation-balanced-v1`, compare the same three bounded read-heavy fixtures with at least three trials per arm. Quality and rework cannot regress on any pair; median wall time must improve at least 20%; median provider-token growth cannot exceed 25%; and each trial is capped at 80,000 provider tokens and 600 seconds. Missing model control, attestation, complete thread telemetry, or provider-telemetry provenance keeps the single-agent default.

Live gate status on 2026-08-03: native `spawn_agent` rejected `gpt-5.6-luna`, while standalone Codex CLI 0.146.0 accepted explicit Sol and Luna models through ChatGPT subscription authentication and emitted `turn.completed` usage receipts. Treat native subagents and standalone CLI as separate host adapters; availability in one does not attest availability in the other.

A one-repetition, project-neutral smoke compared direct Luna max, direct Sol high, and Sol xhigh plan → Luna max implementation → Sol high review. All three passed the same extraction, six-test repair, and contract-review checks. External wall time / measured Codex credits were `135.148s / 0.482951`, `74.169s / 9.498250`, and `387.441s / 17.351338` respectively. The three-stage arm improved no scored quality and used `1.83x` the direct-Sol credits and `5.22x` its wall time. These figures characterize that fixture only: one repetition is not routing-promotion evidence, and write-enabled child CLI execution required an explicit bypass because the desktop-launched child inherited a managed read-only policy. Single-agent remains the default; standalone Luna remains a benchmark-only adapter until repeated harder tasks, safe workspace policy, complete receipts, and quality gates pass.

The report gives medians and ranges for quality, rework, elapsed time, and the three comparable total fields. Cache and reasoning details remain availability-qualified. A valid comparison returns success even when the harness uses more tokens, time, rework, or money. Quality-equivalent local improvements remain diagnostic only: `general_savings_claim_eligible`, delegation promotion, and measured provider cost stay false until aggregate receives implemented out-of-band telemetry and invoice adapters. Local price tables and coordinator-authored invoice files remain estimates.

## Rules

- Prefer deterministic verifier scripts and run packets over subjective scoring.
- Penalize false-positive evidence on no-evidence repository-search tasks.
- Record skipped, blocked, failed, missing, unsupported evidence.
- Keep local AI optional; unavailable models record fallback or skipped evidence.
- Tool answers pass only with verifier-backed sources.

## Codex-Safe Local Model Runs

Do not launch long-running `llama.cpp` as foreground Codex child processes. For MTP, multimodal, embedding, or tool-calling runs:

1. Run readiness checks, e.g. `local_ai_mtp_benchmark.py --check --json`.
2. For a benchmark-only CPU arm, set `SKILLS_LOCAL_AI_ALLOW_GPU=0`, `GGML_CUDA=0`, `GGML_VULKAN=0`, and empty CUDA/HIP/SYCL selectors.
3. Launch detached with `Start-Process -WindowStyle Hidden`, redirecting stdout/stderr to `.agents/local-ai/cache/**`.
4. Use non-interactive llama args, e.g. `--single-turn --simple-io`.
5. Monitor redirected logs and `llama*` processes only.
6. Delete incomplete workflow-owned run folders only after path containment is verified.
