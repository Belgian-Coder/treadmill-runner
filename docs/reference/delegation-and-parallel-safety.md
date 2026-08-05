---
title: Delegation and Parallel Safety
type: reference
status: active
owner: workflow-manager
audience: agent
updated: 2026-07-19
---

# Delegation and Parallel Safety

The harness defaults to one worker. Delegation is an optimization for explicitly requested or owner-directed, independent read-heavy work; it is never required for correctness.

For a small stateless change, select `implementation-low`: keep one worker, make one scoped pass, run the owning deterministic verifier once, and stop. Escalate only when new evidence changes the scope, the verifier fails, the requested contract is ambiguous, or the change crosses a security, compatibility, or public-contract boundary. This rule is semantic and portable; it does not depend on a Codex TOML setting or a particular provider model.

Model selection is a separate decision. Use [Model Compatibility And Routing](model-compatibility-and-routing.md) for semantic profiles, declared provider/model targets, generation-specific prompt overlays, host availability, and requested-versus-observed evidence.

## Activation Gate

A repository-owned v3 workflow declares `worker_profiles.delegation`. Effective worker count remains one unless all of these facts hold:

1. The task class is `independent-read-heavy` and the selected phase declares a non-serial `parallel_safety` policy.
2. A persisted, no-follow-verified observation for the selected run and current phase attests `native-subagents`, `isolated-worker-runtime`, `complete-thread-tree`, `complete-usage-telemetry`, and `context-inheritance-control` on Codex, GitHub Copilot, or Claude Code.
3. Rollout evidence attests provider, model, reasoning effort, prompt, working directory, rollout hash, usage, parent/child prompt hashes, context-inheritance mode, and the complete root/direct-child thread tree.
4. `delegation-balanced-v1` has at least three provider-telemetry trials per arm, noninferior quality and rework, at least 20% median wall-time improvement, and no more than 25% median provider-token growth.

Run `python -B .agents/manage.py workflow workers --all --summary --compact --format json` to see declared versus effective counts and every fallback blocker. To evaluate native delegation, bind the report to persisted current-run evidence with `--name <workflow> --phase <phase> --run-id <run-id> --delegation-requested`. The report keeps `available_orchestration_mode` separate from `effective_orchestration_mode`; host availability alone never grants authority. Missing, stale, aliased, phase-mismatched, or invalid evidence always means sequential execution. Do not invent or recommend undocumented host feature flags, and do not add project-local named-agent configuration until the host can select and attest it.

## Execution Ownership Modes

- `serial` keeps work and lifecycle ownership in the current task and is the default.
- `direct-child-agent` is parent-owned delegated work. It requires an explicit subagent request or workflow declaration, complete direct-child/model/usage attestation, and isolation before writes.
- `independent-thread` is a separately visible, user-owned task. Use it only when the user explicitly requests a durable workstream; it is not a substitute for requested subagents.

These ownership modes are separate from the parallel-safety modes below. Model tier alone never authorizes delegation, a new task, concurrent writes, or a weaker validation gate. The canonical machine-readable contract is `worker_profiles.json` and its `execution_modes`/`risk_routing` sections.

## Parallel Safety Modes

- `serial` allows exactly one worker.
- `parallel-read-only` allows no repository writes, temporary writes, ports, services, or state-store writes.
- `parallel-isolated` requires every write scope to contain `{worker_id}`, every runtime resource to be per-worker, and effect-validated provision and cleanup command IDs.

Separate worktrees do not isolate databases, environment files, fixed ports, or services. The stdlib fixture at `.agents/skills/workflow-manager/scripts/parallel_safety_fixture.py` proves the shared-resource collisions and the corresponding per-worker isolation case.

## Telemetry Boundary

Delegated benchmark packets use `thread_tree` with one root and direct children only. Every child needs one recorded spawn edge, independent usage evidence, `context_inheritance` of `fresh`, `selected-turns`, or `full`, a parent prompt SHA-256, and a durable exact child-prompt file whose path, SHA-256, byte count, and rollout prompt telemetry all agree. A fresh or selected-turns child also needs a durable bounded evidence packet inside the coordinator root; the verifier reopens it no-follow and checks its SHA-256 and exact byte count. Host baseline instructions may still be unavoidable, so `fresh` means no inherited parent turns—not an empty system context. Missing, unknown, duplicate, shared, unexpected, reused, or recursively spawned evidence invalidates the run. Provider-neutral `TokenMeasurementV1` totals are summed over the entire tree; cache and reasoning details retain their availability states, and heuristic or tokenizer-only measurements cannot promote delegation.

The coordinator output root is a trusted, exclusive benchmark boundary and must remain quiescent while aggregation reads it. Static links, reparse points, hardlinks, changed identities, and outside paths are rejected; portable stdlib checks do not claim protection from a same-user or privileged process racing a parent-directory replacement during the read.

Single-thread benchmark packets remain valid for non-delegated arms. Dollar-cost claims additionally require provider invoice evidence; local token-price multiplication remains an estimate.

## Safe Fallback

Any model mismatch, incomplete thread tree, undeclared effect, unavailable isolation control, exceeded trial cap, or failed economics gate returns to one active-model worker. Record the fallback in run evidence and retain a valid negative benchmark result rather than presenting an unmeasured speed or savings claim.
