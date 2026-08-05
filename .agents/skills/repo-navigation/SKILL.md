---
name: repo-navigation
description: Use when orienting in a repository with compact briefs, dependency impact queries, navigation maps, responsibility summaries, staleness checks, and token-budgeted read order.
---

# Repo Navigation

## Goal

Provide compact, deterministic orientation and maintained local maps before deeper work.

## Workflow

1. For read-only/offline dogfood, use docs, help, and these strict commands against a narrow root. `brief`/`changed` may invoke read-only git status/history. The full profile is local-write; strict dogfood is only the no-output/no-write subset.

```shell
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py --help
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py brief --target <project-root> --format markdown --budget short
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py focus --target <project-root> --query "<task>" --format markdown
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py deps --target <project-root> --path <repo-relative-file> --format markdown
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py rdeps --target <project-root> --path <repo-relative-file> --format markdown
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py impact --target <project-root> --path <repo-relative-file> --depth 3 --format markdown
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py check --target <project-root> --format markdown
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py project-context --target <project-root> --check --format markdown
```

Source-review modes before widening: `repo_navigation.py`, `brief_repo.py`, `source_focus.py`, `update_navigation.py`, `project_context.py`, and `install_navigation_workflow.py`; prove no-write/no-temp/no-profile. Run `eval-skill` or self-tests only when temporary fixture writes are allowed.
2. Use brief/lite/changed/focus for orientation. For a known file, use `deps`, `rdeps`, or bounded `impact` before widening reads.
3. For durable maps, `install`/`update` without `--write` build generated payloads in memory; `--write` refreshes owned files:

```shell
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py install --target <project-root> --write
```

4. Before planning, prefer `docs/project/project-context.md` and `HANDOFF.md`. Keep raw navigation JSON tool-only.
5. Keep setup and project-context integration intact. Prefer guidance, manifests, commands, dependencies, git state, ownership, symbols, and staleness evidence over broad reads.
6. Commit generated maps only when expected; briefs are disposable evidence.

## Rules

- Do not scan ignored output, caches, secrets, or generated registries. Do not install tools or change settings.
- Strict read-only/offline excludes `--output`, `--write`, `--overwrite`, setup/install/update writes, generated map/project-context files, workflow smoke commands that create temporary lifecycle evidence, eval/self-test temp fixtures, browser/MCP/network commands, and unreviewed management commands.
- Use `rg` first; use ast-grep only when it reduces context, with `rg`/Python `ast` fallback.
- Write only with explicit `--write` inside the selected root. Use `brief --output` only for an approved destination.
- Keep output compact. Prefer `focus`, wrapper `check`, `project-context --check`, or source reads over raw map JSON.
- Treat reported `write-mode-only` next commands as skipped under strict dogfood; follow only when writes are explicitly allowed.
- Reopen focused source before edits or claims.
- Treat dependency queries as conservative static evidence. They resolve unambiguous Python and JavaScript/TypeScript paths, over-approximate C# namespace matches as inferred, and can miss reflection, dependency injection, generated code, configured aliases, and unsupported languages; verify with exact search and direct source reads.
- Dependency relationship extraction performs a bounded read of at most 1 MiB per source and stays fail-closed: any larger supported source, the enforced 5,000-file ceiling, or the 6,000-relationship ceiling produces explicit partial-scan evidence. A larger `--max-files` request is clamped and reported; do not remove these bounds or add an unbounded override.
- Report stale maps, skips, caps, and missing project-context facts. Generated context is evidence, not truth.
- If optional setup/map installation is skipped or fails, report the skipped/failed status and continue non-blocking with disposable briefs when core navigation remains sufficient; otherwise report the blocker.

## Validation

```shell
python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/repo-navigation
python -B .agents/skills/repo-navigation/scripts/run_self_tests.py
python -B .agents/manage.py eval-skill --skill .agents/skills/repo-navigation --suite .agents/skills/repo-navigation/suites/repo-navigation-evals.json
```

## Stop Rules

Stop before writing outside the selected project root, committing generated maps without project approval, reading suspected secret files, or claiming maps are fresh when staleness checks fail or were skipped.

## Completion Contract

Report low-context files used, target root, mode, generated/checked map paths, skipped files, stale findings, output paths, validation, optional setup skipped/failed, blocked checks, failed commands, and remaining risk.

Report `Skill used: repo-navigation - <reason>` when this skill materially affected the work.
