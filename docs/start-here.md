---
title: Start Here
type: guide
status: active
owner: skill-manager
audience: both
updated: 2026-07-22
---

# Start Here

This is the short entry point. Use the linked docs when you need detail.

## Choose A Path

| Need | Start With |
|---|---|
| No Python or no admin rights | [No Python Or No Admin](harness/no-python.md); use `AGENTS_PYTHON` for a shared runtime across folders. |
| First-use project kickoff | `python -B .agents/manage.py project-kickoff --target D:/Projects/NewProject` |
| First-time guided install | `python -B .agents/manage.py start-here --simple --target D:/Projects/NewProject --profile standard` |
| Pick install options | `python -B .agents/manage.py install-wizard --target D:/Projects/NewProject` |
| First copy only | `python -B .agents/manage.py install-harness --target D:/Projects/NewProject --profile standard` |
| Check/update an installed consumer | `python -B .agents/manage.py harness-status --check-upstream`, then `harness-update --to latest` |
| Promote consumer harness edits back | `python -B .agents/manage.py harness-promote --target D:/Projects/NewProject --dry-run` |
| Existing project with local edits | Add `--dry-run` first. |
| Daily agent work | [Daily Agent Path](operations/daily-agent-path.md) |
| Workflow work | [Workflow Quickstart](workflow/workflow-quickstart.md) |
| Something failed | `python -B .agents/manage.py what-now --from-command "<failed command>"` |
| Finish routine work | `python -B .agents/manage.py finish` |
| Validate a release | `python -B .agents/manage.py finish --release-full` |

Replace `D:/Projects/NewProject` with the target project path.

## First 10 Minutes

1. From this harness repo, run `python -B .agents/manage.py start-here --target D:/Projects/NewProject --profile standard` to get the one state-aware primary next action, including its command, working directory, and read/write effect.
2. Apply the kickoff with `python -B .agents/manage.py project-kickoff --target D:/Projects/NewProject --apply`, or run the lower-level install command above.
3. Open the target project in a fresh agent session.
4. Ask the agent to read `AGENTS.md` and [Agent Start](agent-start.md).
5. Run `python -B .agents/manage.py setup` in the target project when navigation maps or project context are missing.
6. Confirm [Project Context](project/project-context.md) exists and load `automations/navigation/artifacts/maps/HANDOFF.md` when present. Run `python -B .agents/manage.py project-context-review --target . --write-review`, answer the stable structured blocks in `docs/project/review/project-context-review.md`, then preview and apply that authoritative Markdown with `python -B .agents/manage.py project-context-apply-review --target .`. Apply writes normalized JSON evidence beside the Markdown.
7. For larger work, ask for a workflow plan before code changes.
8. Approve implementation only after the plan lists impacted files, validation, risks, and stop conditions.
9. If interrupted, ask the next agent to run workflow resume and load the returned context packet.

## Beginner Defaults

Use the install wizard when you are unsure what to install. It keeps local AI, portable `rg`, and copy-contract validation visible as choices instead of hidden setup.

`project-kickoff --apply` and `install-wizard --apply` both finish with `setup`, `setup --check`, then `status --fast`. The lower-level `install-harness` command copies only unless an initialization option is explicitly selected. Keep the same `--profile` on generated handoff commands.

Install profiles are operational bundles: `minimal` contains core managers/navigation/project-context guidance, `standard` adds all accepted skills plus story/bug/disciplined-change workflows, and `full` adds every tracked workflow, integration, benchmark, and reference surface. Use repeatable `--with-feature <id>` or `--without-feature <id>` for payload-declared adjustments; required core features cannot be removed. Dry-run and install reports include the resolved features, sorted source file manifest, and stable SHA-256 digest. See [Copy Into A Project](harness/copy-into-project.md) for profile inheritance, aliases, exclusions, and update behavior.

Normal agent runs should not load this beginner page unless the user asks for beginner help. Agents should start with the smaller [Agent Start](agent-start.md), routing files, and the selected skill or workflow.

## Main Commands

```shell
python -B .agents/manage.py setup --check
python -B .agents/manage.py start-here --target . --profile standard
python -B .agents/manage.py project-context-review --target .
python -B .agents/manage.py project-context-review --target . --write-review
python -B .agents/manage.py project-context-apply-review --target . --review docs/project/review/project-context-review.md
python -B .agents/manage.py status --fast
python -B .agents/manage.py next-action --summary --compact --format json
python -B .agents/manage.py sync
python -B .agents/manage.py finish
```

Run `setup` the first time only. While editing, run the focused owner check selected for the changed skill or workflow; then run `sync` and one authoritative `finish`.

## Helpful Docs

- Copy/setup: [Copy Into A Project](harness/copy-into-project.md), [Setup](harness/setup.md), [No Python Or No Admin](harness/no-python.md), [Initialize Current Project](harness/initialize-current-project.md)
- Daily use: [Daily Agent Path](operations/daily-agent-path.md), [Daily Use](operations/daily-use.md)
- Commands and search: [Commands](reference/commands.md), [Tools And Search Options](reference/tools-and-search.md)
- Customization: [Customizing The Harness](reference/customization-guide.md) for canonical edit locations, generated-file boundaries, and examples
- Workflows: [Workflow Quickstart](workflow/workflow-quickstart.md) for agents, [Using Workflows](workflow/using-workflows.md) for human prompts
- Models and delegation: [Model Compatibility And Routing](reference/model-compatibility-and-routing.md), [Delegation And Parallel Safety](reference/delegation-and-parallel-safety.md)
- Search and costs: [Repository Search](operations/repository-search.md), [Token Savings](operations/token-savings.md)
- Documentation map: [Documentation Map](reference/documentation-map.md)

## Agent Read Order

1. `AGENTS.md`
2. [Agent Start](agent-start.md)
3. `.agents/routing.md` or `automations/routing.md`
4. Selected `module.json`
5. Selected `SKILL.md` or `WORKFLOW.md`
6. Only the linked docs needed for the current task

Keep generated routing, registries, Claude, and Copilot files in sync through `python -B .agents/manage.py sync`; do not edit them by hand.
