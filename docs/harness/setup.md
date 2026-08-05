---
title: Setup
type: guide
status: active
owner: skill-manager
audience: both
updated: 2026-07-25
---

# Setup

Use before learning internals or after copying the harness. To initialize a copied target project, read [Initialize Current Project](initialize-current-project.md). To understand the value and tradeoffs first, read [Why Use This Harness](why-use-this-harness.md). To copy the harness into another project, use [Copy Into A Project](copy-into-project.md).

## Requirements

Python 3.12+, a terminal at repo root, and Codex, Copilot, or Claude Code. A consumer keeps exactly its own Git repository; the tracked `.agents/harness.lock.json` lets another clone update without a nested repository or the original harness clone. No MCP server, plugin import, hook setup, or committed machine settings are required.

If the user does not have Python and cannot use admin rights, follow [No Python Or No Admin](no-python.md). The harness cannot run `.agents/manage.py` until a Python 3.12+ runtime exists, but a user-writable, WinGet user-scope, or portable runtime is enough. For several project folders, set `AGENTS_PYTHON` to the approved shared interpreter instead of creating repo aliases.

`rg`/ripgrep is strongly recommended for fast repository search. `setup --check` reports whether a verified repo-local portable copy or a global install is available without installing anything.

Preferred unattended setup downloads the pinned portable release into the ignored repo-local tool cache and verifies its SHA256 before use:

```shell
python -B .agents/manage.py setup --install-rg-portable
```

Interactive `setup` can offer the same portable download when `rg` is missing. Package-manager installation remains available when the user explicitly wants a global tool:

```shell
python -B .agents/manage.py setup --install-rg
```

## Commands

For a new consumer project, start from the harness repository with the prepared install:

```shell
python -B .agents/manage.py project-kickoff --target D:/Projects/NewProject
python -B .agents/manage.py project-kickoff --target D:/Projects/NewProject --apply
python -B .agents/manage.py install-wizard --target D:/Projects/NewProject
python -B .agents/manage.py install-harness --target D:/Projects/NewProject --dry-run
python -B .agents/manage.py install-harness --target D:/Projects/NewProject --run-setup-check --install-rg-portable --bootstrap-local-ai
```

After the harness is already in the target project, run setup from that target:

```shell
python -B .agents/manage.py setup
python -B .agents/manage.py setup --install-rg-portable
python -B .agents/manage.py setup --check
python -B .agents/manage.py dotnet-context --target . --format json
python -B .agents/manage.py dotnet-context --target . --solution <solution.sln> --project <project.csproj> --format json
python -B .agents/manage.py dotnet-context --target . --write-evidence
python -B .agents/manage.py project-context-review --target .
python -B .agents/manage.py project-context-review --target . --write-review
python -B .agents/manage.py project-context-apply-review --target .
python -B .agents/manage.py setup --dry-run
python -B .agents/manage.py setup --deep
python -B .agents/manage.py install-harness-smoke --fast --format json
python -B .agents/manage.py install-harness-smoke --format json
```

Team-facing limits, warning actions, command budgets, and cost/context choices are visible in the complete tracked `.agents/project-policy.json` and discoverable with `python -B .agents/manage.py policy show`. Use `policy explain <path>` before changing a value, `policy set` for validated edits, and `policy refresh` after an update introduces new paths; see [Project Policy Configuration](policy-configuration.md). The file is consumer-owned and survives harness updates.

Run `install-harness` from this harness repository. Run `setup` from the copied target project, or from this repo when validating the harness itself.

`project-kickoff` is the high-level first-use path. Without `--apply`, it is read-only and reports the target state, install/update plan, one primary next action, source-vs-target command groups, context-review checklist, optional tool advisories, workflow command recommendations, and copyable chat prompts. With `--apply`, it performs only the safe sequence of install/update, target `setup`, target `setup --check`, and target `status --fast`; it does not start or resume workflow runs.

## Project Initialization

Normal `setup` is the target-project initializer. It checks generated routing, then uses `repo-navigation` to install or refresh `automations/navigation/` and its generated maps, and uses `project-context-generator` to create the target project's own `docs/project/project-context.md` package when it is missing or copied from the source harness.

`setup --check` remains read-only. It reports missing navigation maps or project context as project-initialization status, but it does not write those files.

For .NET projects, `dotnet-context --target . --format json` enriches kickoff and generated context with SDK/runtime, solution/project graph, build-policy files, CI dotnet command candidates, appsettings key names, persistence/framework feature signals, and repo-local NuGet/feed prerequisites. It does not run restore, build, test, package list/search, tool install, or user/global NuGet config inspection.

Use `--dotnet-executable <path-to-dotnet>` when a project pins an SDK outside the default `PATH`; the probe still runs only `dotnet --info`, `dotnet sln list`, and `dotnet msbuild -getProperty/-getItem`.

Use `--solution <solution.sln>` and `--project <project.csproj>` to narrow large repositories before reviewing candidates. These filters only scope the report; they do not run restore, build, test, or package/feed operations.

`dotnet-context --target . --write-evidence` writes only `docs/project/dotnet-context/dotnet-context.json` and `.md` under the target. Use those files as review evidence or a baseline for `--baseline`; canonical `docs/project/project-context.md` is still updated only through setup/generator review.

Agents and workflows should know these files exist after initialization:

- `docs/project/project-context.md`
- `docs/project/project-context.json`
- `docs/project/validation/validation-manifest.json`
- `automations/navigation/artifacts/maps/HANDOFF.md`
- `automations/navigation/artifacts/maps/NAVIGATION.md`
- `automations/navigation/artifacts/maps/staleness.json` (tool-only freshness index)

For a focused manual refresh, use:

```shell
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py update --target . --write
python -B .agents/skills/project-context-generator/scripts/generate_project_context.py --target . --write
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py project-context --target . --check
```

For a human-readable review checklist after generation, use:

```shell
python -B .agents/manage.py project-context-review --target .
python -B .agents/manage.py project-context-review --target . --write-review
python -B .agents/manage.py project-context-apply-review --target .
python -B .agents/manage.py project-context-apply-review --target . --apply
```

`--write-review` writes `docs/project/review/project-context-review.md` and `.json` as intermediate answer artifacts. It does not edit `docs/project/project-context.md`. `project-context-apply-review` is also read-only by default; add `--apply` only after the answer slots are approved to write a marker-bounded reviewed-facts section into canonical project context.

`install-harness` copies workflow definitions but leaves workflow run history and dogfood run packets behind, so consumer projects start with empty run state. Use `--download-ai-models` only when the install should download the configured local AI model bundle immediately; otherwise `--bootstrap-local-ai` writes local AI config, generated local settings, policy defaults, secrets examples, and gitignore rules without model downloads.

The prepared install command gives you verified fast search, generated routing checks, and local AI configuration without building repository indexes.

Use `install-harness-smoke --fast --format json` for a cheap local check after installer, docs, or first-time onboarding edits. It installs into a temporary target, writes `.agents/harness-smoke-target.json`, verifies clean-state copy behavior, runs `setup --check --no-link-skills`, runs `setup --no-link-skills` for project initialization, checks project context, skips local AI bootstrap and workflow start/resume, and removes the temporary target.

Use full `install-harness-smoke --format json` before release or after installer behavior changes. It adds portable ripgrep setup, local AI config, workflow start, workflow resume like a new chat, context packet checks, and consumer workflow smoke checks before removing the temporary target.

The smoke marker is local state and is excluded from reusable installs. If a kept smoke target is inspected manually, plain `setup --check` auto-skips global skill-link checks because the marker declares that the target must not claim Codex, Claude, or Copilot user-level skill folders. Real consumer projects do not get that marker, so plain `setup --check` still verifies skill links.

Windows fallback: `py -3.12 -B .agents/manage.py setup`.

Success means generated artifacts, skill links, validation, and first-agent guidance report ready/ok. A good first setup ends with status similar to:

```text
setup: ready or ok
optional tools: ready, installed, or explicitly skipped
agent start guidance: ready
```

When `--bootstrap-local-ai` is used without `--download-ai-models`, local-AI policy may report `model_download_status: not-downloaded` or `partial` plus `models_not_downloaded` for text, embedding, or vision profiles. That is not a setup failure; it means config and policy are ready, but some or all model payloads were intentionally not downloaded.

Open a new agent session if newly linked skills are not visible.

## Offline Or Restricted Networks

If the network is blocked, use `setup --check` first. Then run `setup` without install flags and record unavailable optional tools as skipped. The harness still uses direct file reads, Python fallback search, existing global `rg`, and bounded deterministic workflow evidence.

Do not use `--download-ai-models` or `--install-rg-portable` on machines that are not allowed to download external artifacts. Use an internally approved cache or package source instead.

## Troubleshooting

- Python missing or no admin rights: [No Python Or No Admin](no-python.md)
- Generated artifacts stale: `python -B .agents/manage.py sync`
- Skill links missing: `python -B .agents/manage.py link-skills --dry-run`
- `rg` missing: `python -B .agents/manage.py setup --install-rg-portable`
- Global `rg` preferred by policy: `python -B .agents/manage.py setup --install-rg`
- Need full status: `python -B .agents/manage.py status --full`
- Validation fails: keep output, then run `python -B .agents/manage.py what-now`
