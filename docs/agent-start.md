---
title: Agent Start
type: guide
status: active
owner: skill-manager
audience: agent
updated: 2026-07-10
---

# Agent Start

Use this page as the first low-context read in a copied project.

## Read Order

1. `AGENTS.md`
2. `docs/agent-start.md`
3. `docs/project/project-context.md`
4. `automations/navigation/artifacts/maps/HANDOFF.md` when present
5. `.agents/routing.md` only when a reusable skill is needed
6. `automations/routing.md` only when stateful workflow work is needed
7. The selected `module.json` and entry file only

## First Checks

- Run `python -B .agents/manage.py start-here --simple --target . --profile <selected-profile>` when a human or new agent needs the one state-aware primary action. Run that command from the reported working directory and note whether its effect is read-only or writes files.
- Run `python -B .agents/manage.py project-kickoff --target <project>` from the source harness when a human wants one guided first-use plan with source-vs-target command groups.
- Run `python -B .agents/manage.py status --fast`.
- If setup is not ready, run `python -B .agents/manage.py setup --check`.
- If project context or navigation maps are missing, run `python -B .agents/manage.py setup` to generate them.
- If project context exists but is generated, draft-like, or incomplete, run `python -B .agents/manage.py project-context-review --target . --write-review` and resolve `docs/project/review/project-context-review.md` before implementation planning.
- Answer the stable ID blocks in `docs/project/review/project-context-review.md`; that Markdown is authoritative. Run `python -B .agents/manage.py project-context-apply-review --target .` first, then add `--apply` to update `docs/project/project-context.md` and emit normalized JSON evidence. Duplicate, missing, or unknown IDs block all writes.
- If the project context is missing, still says `draft`, or has TODOs, generate/check it and stop before implementation until the missing project facts are explicit.
- If `.agents/harness-install-plan.md` exists, read it before assuming what the install copied or skipped.

## Working Rules

- Route first, then load one owner.
- `setup --check`, `status --fast`, `startup-context`, and `next-action` share the same deterministic navigation readiness result. A fast cache is accepted only for a clean matching Git source tree and an intact generated-map inventory, including owner capsules. Older cache packets without the v2 source kind or map hashes, plus dirty, mismatched, or non-Git states, use the full deterministic check.
- For repository discovery, prefer bounded `rg` output and direct reads; add an owner path when the task names one skill or workflow.
- Read only the docs and scripts needed for the current task.
- Keep workflow runs, local AI caches, model payloads, secrets, portable tools, and generated install plans out of source changes unless the user explicitly asks for evidence.
- Before finishing repo work, report changed paths, validation, skipped checks, blocked checks, remaining risks, and the material skill or workflow used.
