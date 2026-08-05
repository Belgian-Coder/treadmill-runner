---
title: Project Context Generator Dogfood
type: reference
status: active
owner: project-context-generator
audience: agent
updated: 2026-06-13
---

# Project Context Generator Dogfood

Compact evidence from dogfooding `project-context-generator` on this repository without overwriting the reviewed project context.

## Scope

- Target: repository root.
- Output: ignored local cache under `.agents/local-ai/cache/project-context-dogfood/`.
- Reviewed baseline: `docs/project/project-context.md`.
- Durable source changes: generator detection logic and validation command discovery only.

## Result

| Check | Before Fix | After Fix | Expected |
|---|---|---|---|
| Technologies | `.NET`, GitHub Actions | GitHub Actions, Python | Python plus integration surfaces |
| Fixture `.csproj` handling | Counted as active .NET project | Ignored as fixture payload | Ignore fixture/sample technology signals |
| .NET validation commands | `dotnet build`, `dotnet test` | none | no .NET commands for this repo |
| Harness validation commands | none | `check-additions`, `sync --check`, `validate-agent-compatibility`, `check` | generated manifest can guide workflow validation |
| Generated runner | not checked | list mode loaded harness commands and repo test signals | runner is usable without executing full validation |

## Evidence

Commands:

```shell
python -B .agents/skills/project-context-generator/scripts/generate_project_context.py --target . --output-dir .agents/local-ai/cache/project-context-dogfood --write --format markdown
python -B .agents/skills/project-context-generator/scripts/run_self_tests.py
python -B .agents/skills/project-context-generator/scripts/generate_project_context.py --target . --output-dir .agents/local-ai/cache/project-context-dogfood --write --overwrite --format markdown
python -B .agents/local-ai/cache/project-context-dogfood/validation/run_project_validation.py --target . --evidence-dir .agents/local-ai/cache/project-context-dogfood/validation/evidence --list --format json
```

Observed after-fix generated facts:

- `technologies`: GitHub Actions, Python.
- `dotnet_projects`: none.
- `validation_commands`: harness commands plus discovered repo test signals, such as `python-unittest` when present.
- `status`: generated runner returned `listed`; command execution intentionally skipped by `--list`.

## Follow-Up

- Keep the generated cache output untracked.
- Refresh this report only when generator heuristics or validation command discovery changes.
- If a consumer repo has real .NET sources, `.sln` or active `.csproj` files outside fixtures still produce .NET context and validation commands.
