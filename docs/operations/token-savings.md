---
title: Token Savings
type: guide
status: active
owner: skill-manager
audience: both
updated: 2026-07-25
---

# Token Savings

This repository reduces paid-token use by keeping deterministic commands
authoritative, loading only phase-relevant context, and using local AI only for
bounded evidence shaping after deterministic evidence exists.

## Current Controls

| Control | Default | Why It Helps |
|---|---|---|
| Cost policy | `python -B .agents/manage.py cost-policy --check --summary --compact --format json` | Enforces context budgets and explicit paid-model fallback boundaries. |
| Direct search | `rg -n "<pattern>" <scoped-paths>` | Finds candidate files without loading a repository index or broad folders. |
| Workflow context | `python -B .agents/manage.py workflow context --name <workflow> --run-id <run-id> --write` | Reuses phase state, blockers, validation, and cited evidence. |
| Changed evidence | `python -B .agents/manage.py changed-evidence --summary --compact --format json` | Routes only changed owners and validations. |
| Compact summaries | `--summary --compact --format json` | Omits details that are available in durable evidence artifacts. |
| Review units | `review-packet`, `review-next`, `review-loop` | Keeps large diffs inside the configured review budget. |
| Local evidence shaping | `local-ai task --task <bounded-task> --input <evidence>` | Uses optional local inference only after deterministic input exists. |

## Recommended Path

1. Read `.agents/routing.md`, `automations/routing.md`, and the selected owner
   module.
2. Use scoped exact search and read only matching source files.
3. For workflow work, start or resume the owning workflow so context evidence
   is recorded once and reused.
4. Run the owning deterministic verifier.
5. Ask a model to summarize or reason only over the bounded evidence needed for
   the current decision.

The repository index was removed after the paired benchmark in
[Repository Search](repository-search.md). Direct search achieved 18/18 task
success and 100% no-evidence precision; the batched SQLite arm achieved 10/18
and 50% respectively and was slower overall. Avoiding its roughly 50 MB cache,
maintenance hooks, and large validation surface is a feature and token-budget
simplification, not a missing setup step.

## Web Evidence

When web research is required, store a compact evidence envelope with:

- the exact question and source URL;
- a short relevant excerpt or paraphrase;
- source date and access date;
- whether the source is primary;
- the claim supported and any uncertainty.

Do not preserve full HTML, navigation chrome, repeated comments, or irrelevant
page text. The representative web-evidence benchmark currently passes 4/4
fixtures and reduces the stored evidence envelope from 10,297 to 4,835 bytes
(53.04%). That benchmark proves envelope compaction on its fixtures; it does not
prove provider-token savings, live-search accuracy, or prompt-injection
resistance.

## Decision Rules

- Prefer deterministic search and validation before model interpretation.
- Do not trade task quality or abstention accuracy for small context savings.
- Treat generated context estimates as routing evidence, not provider billing.
- Use a warm local server only for repeated model tasks that share the same
  validated contract; one uncached task stays one-shot.
- Retain a feature or validation only when fresh paired evidence shows material
  quality, latency, risk, or maintenance value.
