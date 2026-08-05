---
title: Initialize Current Project
type: guide
status: active
owner: skill-manager
audience: both
updated: 2026-07-05
---

# Initialize Current Project

Use this after copying the harness into a target project, or when a project has the harness but no current navigation maps or project context.

Run from the target project root:

```shell
python -B .agents/manage.py setup
python -B .agents/manage.py setup --check
python -B .agents/manage.py dotnet-context --target . --format json
python -B .agents/manage.py dotnet-context --target . --solution <solution.sln> --project <project.csproj> --format json
python -B .agents/manage.py project-context-review --target .
python -B .agents/manage.py project-context-review --target . --write-review
python -B .agents/manage.py project-context-apply-review --target .
```

`setup` is the write-mode initializer. It keeps generated routing in sync, then uses `repo-navigation` to install or refresh the navigation workflow and maps, and uses `project-context-generator` to create the workflow-ready project context package when it is missing or clearly copied from the harness source project.

`setup --check` is read-only. It reports whether initialization is still needed, but it does not write maps, context files, skill links, local AI config, or tool caches.

For .NET business applications, `dotnet-context --target . --format json` is a read-only inspection path. It reports SDK/runtime, solution and project graph, `Directory.Build.*` policy, CI dotnet command candidates, appsettings key names, persistence signals, and repo-local NuGet/feed prerequisites. It does not run restore, build, test, package-list/search, tool install, or read user/global NuGet config.

If the required SDK is not first on `PATH`, pass a trusted local executable explicitly: `python -B .agents/manage.py dotnet-context --target . --dotnet-executable D:/dotnet/dotnet.exe --format json`.

For monorepos or large solutions, use `python -B .agents/manage.py dotnet-context --target . --solution <solution.sln> --project <project.csproj> --format json` to narrow the report before reviewing validation candidates. The filters do not run restore, build, test, or package/feed commands.

Use `python -B .agents/manage.py dotnet-context --target . --write-evidence` only when you want durable project-local evidence under `docs/project/dotnet-context/`. Those artifacts are intermediate context evidence, not canonical project context.

`project-context-review --target .` is also read-only. It lists missing or draft facts in `docs/project/project-context.md`, including stack/runtime, validation commands, generated-file boundaries, external systems, persistence, CI, and secrets/configuration expectations.

`project-context-review --target . --write-review` writes `docs/project/review/project-context-review.md` and `.json` as project-local review artifacts with answer slots. It does not edit canonical project context.

After the answer slots are approved, run `python -B .agents/manage.py project-context-apply-review --target .` to preview the canonical change. Add `--apply` to write a marker-bounded reviewed-facts section into `docs/project/project-context.md`; other context sections are not rewritten.

## Generated Project Map

`repo-navigation` owns the mapping workflow and generated navigation files:

- `automations/navigation/WORKFLOW.md`
- `automations/navigation/module.json`
- `automations/navigation/scripts/`
- `automations/navigation/artifacts/maps/HANDOFF.md`
- `automations/navigation/artifacts/maps/NAVIGATION.md`
- `automations/navigation/artifacts/maps/staleness.json` (tool-only freshness index)

Agents should read `automations/navigation/artifacts/maps/HANDOFF.md` when it exists. It gives the compact read order, map freshness state, and the command to detect stale source changes.

Manual refresh:

```shell
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py install --target . --write
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py update --target . --write
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py check --target .
```

## Project Context

`project-context-generator` owns the generated project context package:

- `docs/project/project-context.md`
- `docs/project/project-context.json`
- `docs/project/diagrams/*.mmd`
- `docs/project/diagrams/*.svg`
- `docs/project/validation/validation-manifest.json`
- `docs/project/validation/run_project_validation.py`

The source harness repository has its own `docs/project/project-context.md`, but install-harness excludes that project-specific file from consumer copies. The target project gets its own context during `setup`.

When an existing reviewed context is present, setup does not overwrite it. If the existing context is incomplete, setup writes a generated sidecar such as `docs/project/project-context.generated.md` and reports that review is still needed.

Manual refresh:

```shell
python -B .agents/skills/project-context-generator/scripts/generate_project_context.py --target . --write
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py project-context --target . --check
```

Use `--overwrite` only when the project owner wants to replace the current context:

```shell
python -B .agents/skills/project-context-generator/scripts/generate_project_context.py --target . --write --overwrite
```

## Agent Read Path

For implementation work, agents should load these after `AGENTS.md` and `docs/agent-start.md`:

1. `docs/project/project-context.md`
2. `automations/navigation/artifacts/maps/HANDOFF.md` when present
3. The selected routing file, selected `module.json`, and selected skill or workflow entry file

If the context is missing, draft-like, or reports stale assumptions, run `python -B .agents/manage.py setup` and stop before implementation until the missing project facts are explicit.
