---
title: Repository Search
type: guide
status: active
owner: skill-manager
audience: agent
updated: 2026-07-25
---

# Repository Search

Repository discovery uses scoped direct search followed by direct file reads.
There is no maintained repository index, vector queue, embedding-retrieval
route, or background retrieval worker.

## Default Path

1. Read the compact routing files and the selected owner module.
2. Use `rg -n "<exact-or-structural-pattern>" <scoped-paths>`.
3. Read only the matching source files or bounded snippets.
4. Run the owning deterministic verifier before making a behavior claim.

Use the verified portable `rg` installed by setup when a global binary is not
available. Structural search may use ast-grep when exact text over-selects, but
the reported evidence must still be human-readable `file:line:snippet` output.

## Why The Index Was Removed

A fresh paired benchmark on 2026-07-25 scored 18 natural repository questions.
Golden paths were used only for scoring and were never passed to either search
arm.

| Path | Tasks | Group recall | No-evidence precision | Evidence bytes | Total wall time |
|---|---:|---:|---:|---:|---:|
| Direct scoped `rg` | 18/18 | 100% | 100% | 23,914 | 1.442s |
| SQLite FTS, one process per query | 10/18 | 61.1% | 50% | 21,281 | 5.228s |
| SQLite FTS, batched process | 10/18 | 61.1% | 50% | 21,281 | 2.325s |

The batched index returned 11.01% fewer evidence bytes, but it lost material
quality and abstention accuracy and was roughly 61% slower overall. A fresh
maintenance baseline also retained about 50 MB of ignored cache for 702 files
and 11,330 chunks. The operational index and its validation surface therefore
failed the keep gate.

Embedding generation was not optimized after that result: an embedding-backed
candidate cannot rescue a model-free indexed arm that already loses the
quality, abstention, and latency comparison. Historical embedding profiles
remain available only for explicit model benchmarks.

## Workflow Evidence Is Separate

Workflow start, resume, and finish still write bounded context-evidence packets
from declared workflow, project-context, run, and fallback paths. These packets
are auditable lifecycle evidence; they do not query or maintain a repository
index and do not start local AI.

## Reintroduction Gate

Do not restore repository indexing based on synthetic retrieval accuracy alone.
A candidate must use the same current-tree questions and scoring-only golden
paths, and it must:

- meet or exceed direct-search task success and no-evidence precision;
- improve either evidence size or end-to-end duration by at least 25%;
- include index-build/storage cost and cold/warm behavior;
- keep workflow lifecycle independent of model downloads and cache builds;
- pass the owner suites without adding compatibility-only commands or settings.

The current paired suite is
`automations/agent-benchmarking/suites/repository-search-utility-v1.json`.
