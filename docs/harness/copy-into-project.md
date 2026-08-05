---
title: Copy Into A Project
type: guide
status: active
owner: skill-manager
audience: both
updated: 2026-07-25
---

# Copy Into A Project

Use this when a project should consume the reusable AI harness from this repository. Read [Why Use This Harness](why-use-this-harness.md) when deciding whether the extra structure is worth it for a project.

## Command

Run the command from this harness repository. The install target can be a new folder or an existing project. Its existing `.git` directory is never copied, traversed, or modified. `--force` is available only for an intentional first install; versioned updates have no force-overwrite path, and safety or lock-integrity failures cannot be overridden.

First-use kickoff:

```shell
python -B .agents/manage.py project-kickoff --target D:/Projects/NewProject
python -B .agents/manage.py project-kickoff --target D:/Projects/NewProject --apply
```

The first command is read-only. It validates the copy contract, previews the install/update, checks project-context status, prints one primary next action, separates commands by source harness vs target project, and prints copyable chat prompts. The `--apply` command installs or updates the harness, then runs `setup`, `setup --check`, and `status --fast` in the target. It does not start or resume workflow runs; it prints exact workflow commands for a later explicit step.

Guided install:

```shell
python -B .agents/manage.py install-wizard --target D:/Projects/NewProject
```

The wizard asks for an install profile and optional setup choices, then prints the recommended command. Use `--apply` when the wizard should run the install immediately, or `--no-input --format json` for automation.

Prepared install:

```shell
python -B .agents/manage.py install-harness --target D:/Projects/NewProject --run-setup-check --install-rg-portable --bootstrap-local-ai
```

Replace `D:/Projects/NewProject` with the target path:

| Platform | Example |
|---|---|
| Windows | `D:/Projects/NewProject` |
| Linux | `/home/alex/projects/new-project` |
| macOS | `/Users/alex/projects/new-project` |

`--bootstrap-local-ai` prepares `.agents/local-ai.json`, local AI policy defaults, secrets examples, and gitignore rules without copying or downloading model/runtime payloads.
`--install-rg-portable` downloads pinned ripgrep into the ignored repo-local tool cache. The tracked ripgrep manifest pins the release URL and SHA256; setup refuses to execute the downloaded binary if verification fails.
Repository search uses installed or system `rg` plus direct file reads. Setup, sync, workflow use, and smoke validation do not build an index or embedding cache.
`model_download_status: not-downloaded` or `partial` plus `models_not_downloaded` in policy output is expected unless `--download-ai-models` is also used.

Profiles:

- `minimal`: core CLI and routing generators, navigation/project-context owners, manager skills, and first-use/daily guidance.
- `standard`: `minimal` plus every accepted reusable skill and the story, bug, and disciplined-change workflows.
- `full`: `standard` plus every tracked workflow, integration/agent descriptor, benchmark definition, local-AI guidance, and reference guide.

Profiles are feature bundles declared by `.agents/harness-payload.json`; `minimal` is a strict subset of `standard`, which is a strict subset of `full`. Payload-defined aliases and custom profiles are accepted without changing the Python parser. Add or remove optional bundles with repeatable flags:

```shell
python -B .agents/manage.py install-harness --target D:/Projects/NewProject --profile minimal --with-feature story-workflow --with-feature bug-workflow
python -B .agents/manage.py install-harness --target D:/Projects/NewProject --profile full --without-feature benchmarking --dry-run
```

Required core features cannot be removed. Unknown profiles/features, profile or dependency cycles, contradictory overrides, and exclusions still required by selected features block before writes. The selected profile and every `--with-feature`/`--without-feature` flag are preserved in kickoff, wizard, start-here, copy-contract, promote, and export handoffs.

To also download the configured local AI models during install, make that explicit:

```shell
python -B .agents/manage.py install-harness --target D:/Projects/NewProject --run-setup-check --download-ai-models --max-download-gb 20
```

Embedding models are benchmark-only and are never prepared during harness installation.

Dry-run first when copying into an existing project:

```shell
python -B .agents/manage.py install-harness --target D:/Projects/NewProject --dry-run
```

After a minimal copy or a manual update, open the target project and run:

```shell
python -B .agents/manage.py setup
python -B .agents/manage.py setup --check
python -B .agents/manage.py status --fast
```

Expected success:

```text
setup: generated routing, navigation maps, and project context are initialized
setup --check: generated artifacts, skill links, repository layout, and validation readiness are ready/ok
status --fast: next_command is present and no blocking setup issue is reported
```

## Updating An Existing Target

Run updates inside the consumer project. `.agents/harness.lock.json` is committed with that project and records the upstream repository, stable semantic tag, immutable commit, install profile/features, normalized payload digest, and base hash/size for every managed file. A fresh clone therefore has the same ownership baseline without the original harness clone or ignored installation state.

The updater resolves annotated `vMAJOR.MINOR.PATCH` tags, excludes prereleases from `latest`, downloads the resolved commit archive over HTTPS without cloning the harness repository, and blocks a tag whose commit moved. It never runs `git pull`, `push`, `checkout`, `add`, or `commit`.

Harness maintainers create annotated tags only after the release commit is clean, the focused updater/copy-contract suites and repository check pass, and `harness-release-check --tag <tag>` validates the final tag locally. The GitHub repository must protect `v*.*.*` tags from updates and deletion. Consumer products may also run repository-specific GitHub Actions; local deterministic release gates remain authoritative for signing and publishing. The first supported tag is `v1.0.0`.

Check and preview:

```shell
python -B .agents/manage.py harness-status --check-upstream
python -B .agents/manage.py harness-update --to latest
```

Apply only after reviewing the complete preview:

```shell
python -B .agents/manage.py harness-update --to latest --apply
```

The preview classifies every path against the tracked base:

- A managed file is updated or deleted only while its current hash still equals the lock.
- A new upstream-managed path is added only when the destination is absent.
- Project settings, local settings, workflow runs, caches, project context, secrets, overlays, `.git`, and other consumer-owned files are outside the managed payload.
- A direct managed-file edit or unknown file at a new managed destination blocks the entire update; no partial apply occurs.
- A project that must own a formerly managed path adds it to tracked `.agents/harness.overlay.json` before updating. The file uses `{"schema_version": 1, "tool": "harness-project-overlay", "paths": ["path/from/project/root"]}`. Every declared path must be an existing regular file managed by either the current or target harness; the updater preserves it and removes it from the new lock. Unknown, duplicate, unsafe, missing, and harness-state paths block the update.
- Replacements/deletions and the old lock are backed up under ignored `.agents/harness-update/transactions/<id>/`; the lock is replaced last.
- An I/O failure automatically restores the old payload. An applied transaction remains available for explicit rollback:

```shell
python -B .agents/manage.py harness-rollback --transaction <id>
```

Interactive write-mode `setup` checks stable tags only when attached to a terminal, prints the update preview, and defaults its apply prompt to No. `setup --check`, `setup --dry-run`, `setup --offline`, CI, and other non-interactive runs do not fetch or apply updates. After confirmation, the updated manager is restarted in a new process for offline setup verification.

Legacy consumers convert once with `harness-adopt --tag v1.0.0`. Adoption verifies the old ignored `.agents/harness-install.json` against the selected archive, writes the tracked lock, and moves the legacy evidence into ignored update state. Normal update code has no legacy fallback.

## Promoting Consumer Harness Edits Back

If you intentionally improve copied harness files inside a consumer project, preview the reverse flow from this source harness repository:

```shell
python -B .agents/manage.py harness-promote --target D:/Projects/NewProject --dry-run
```

Run `harness-promote` from a sibling clone of the harness repository. The report compares that source payload, the consumer `.agents/harness.lock.json` baseline, and current consumer files. Only explicitly selected harness-owned files can be copied into the sibling clone:

```shell
python -B .agents/manage.py harness-promote --target D:/Projects/NewProject --apply --paths docs/agent-start.md
```

`harness-promote` never promotes workflow run history, local caches, secrets, model payloads, portable tools, generated install plans, or target `docs/project/**` context, review artifacts, diagrams, or evidence. Its `--profile` and feature overrides constrain both the report and `--apply`; a file owned by the still-active larger-profile baseline is classified `outside-selected-profile` and cannot be copied back under a smaller selection. After apply, run the validation commands printed in the report before committing the source repo change.

Project-specific changes belong in explicit project-owned settings/overlays and are never promoted. A reusable improvement is promoted through the sibling source clone and reviewed on a normal harness branch.

## Restricted Or Offline Targets

When the target cannot download tools or models, omit download flags:

```shell
python -B .agents/manage.py install-harness --target D:/Projects/NewProject --run-setup-check --bootstrap-local-ai
```

The harness remains usable with Python fallback search, direct `rg` if it already exists, bounded deterministic workflow evidence, and deterministic validation. Record unavailable optional setup as skipped evidence.

For a versioned update, provide an explicit commit archive and JSON metadata containing the same `repository`, `tag`, `commit`, and `payload_digest`:

```shell
python -B .agents/manage.py harness-update --to v1.2.3 --archive D:/mirror/skills-v1.2.3.zip --archive-metadata D:/mirror/skills-v1.2.3.json
```

The updater safely extracts the archive into ignored cache state and rejects traversal, absolute paths, links, unsupported entry types, duplicate paths, corrupt archives, and digest mismatches.

## Verify The Installer

After docs or first-time onboarding changes, run the fast prepared install smoke from this harness repository:

```shell
python -B .agents/manage.py install-harness-smoke --fast --format json
```

It installs into a temporary target, confirms clean-state exclusions, runs setup check, checks project context, skips local AI bootstrap and workflow start/resume, and removes the target.
The setup checks use `--no-link-skills`, and kept smoke targets contain `.agents/harness-smoke-target.json` so manual plain `setup --check` does not report false missing global skill links.

Before release or after installer behavior changes, run the full prepared install smoke:

```shell
python -B .agents/manage.py install-harness-smoke --format json
```

It installs into a temporary target, writes `.agents/harness-smoke-target.json`, confirms clean-state exclusions, runs setup and local-AI config steps, checks project context, starts and resumes a workflow run, checks the context packet, runs consumer workflow smoke checks, and removes the target unless `--keep` is used.

For test authors, `.agents/skills/skill-manager/assets/fixtures/sample-consumer/` provides a tiny consumer project shape with project context, a `src/` layout, central NuGet package management, and repo-local `NuGet.config`.

## Public Export

Use this only when preparing a clean shareable copy outside the active repository:

```shell
python -B .agents/manage.py public-export --target temp/public-export --dry-run
python -B .agents/manage.py public-export --target temp/public-export
```

The export uses the same payload feature resolver and exclusions as install, including `--profile`, repeatable `--with-feature`, and repeatable `--without-feature`. It does not copy workflow run history, local AI caches, model payloads, secrets, portable tool downloads, or generated install evidence. An existing export directory may be reused when every existing path belongs to the resolved selection: unchanged files are preserved, changed selected files are reported as collisions unless `--force` is supplied, and forced updates replace only those selected files. Any out-of-selection path returns `export-target-not-empty` in both dry-run and write mode, reports `existing_target_paths` and `out_of_selection_existing_paths`, and blocks all writes.

## Copy Contract

Candidate roots are declared in `.agents/harness-payload.json`, then filtered by the resolved profile/features:

- `AGENTS.md`, `README.md` when absent, `.editorconfig`, `.gitattributes`, `.gitignore`
- `docs/**`
- `.agents/**`
- `automations/**`
- `.github/**`
- `.claude/**`

Excluded by default:

- `.git/**`
- Python bytecode, logs, editor state, and local cache/build folders, including nested `__pycache__/**`, `.cache/**`, `.pytest_cache/**`, `.idea/**`, `.vscode/**`, `tmp/**`, `dist/**`, `build/**`, `coverage/**`, `bin/**`, `obj/**`, and `*.pyc`
- ignored local settings such as `.claude/settings.local.json`, `.github/copilot/settings.local.json`, `*.local`, and repo-local `.env` files
- `.agents/.deps/**`, `automations/**/Scripts/output/**`, and `automations/reference-refresh/References/repositories/**` runtime/retrieval payloads
- `.agents/local-ai/cache/**`
- `.agents/local-ai/bundle/**`
- `.agents/local-ai/downloads/**`
- `.agents/local-ai/runtime/**`
- `.agents/tools/cache/**`
- `.agents/local-ai/secrets.json`
- `.agents/local-ai/secrets.local.json`
- `.agents/local-ai/local.settings.json`
- `.agents/local-ai/project.settings.json` (created and owned by the consumer project)
- `.agents/project-policy.json` (tracked complete consumer limits, warning actions, command budgets, and portable cost/context policy)
- `.agents/harness.overlay.json` (optional tracked declaration of paths transferred to project ownership)
- `.agents/harness.lock.json` (created by install/update in the consumer; not copied from the source)
- `.agents/harness-install.json`
- `.agents/harness-install-plan.json`
- `.agents/harness-install-plan.md`
- `.agents/harness-smoke-target.json`
- `automations/*/runs/**`
- `docs/project/project-context.md`, `docs/project/project-context.json`, `docs/project/diagrams/**`, `docs/project/review/**`, and `docs/project/validation/**`

Workflow run history and dogfood run packets are never copied by `install-harness`; consumer projects start with the selected workflow definitions and an empty run state. Required safety exclusions are enforced by install, export, and promote even if a copied or edited payload omits them; copy-contract validation separately reports the incomplete declaration. Source enumeration, hashing, install/export destinations, promotion reads and writes, retained manifest paths, and install evidence all reject absolute or traversal paths and every symbolic-link, junction, or reparse component. Traversal is deterministic and never follows those indirections. Source-maintainer routing/registry files and navigation maps are omitted from feature selections so partial installs do not receive indexes for absent modules; write-mode `setup` regenerates those outputs from the modules actually installed.

`install-harness` writes tracked `.agents/harness.lock.json` in the target. Every owned row has one unique safe path, a 64-character lowercase SHA-256, and a non-negative byte count. The lock deliberately excludes absolute paths and timestamps, so cloning the consumer preserves a deterministic ownership baseline. On first install into a normal consumer repo, an existing root `README.md` is preserved as consumer-owned and an existing `.gitignore` receives merged harness ignore entries instead of becoming a managed file. Versioned updates use `harness-update`; they never force-overwrite collisions.

## After Copy

`setup --check` is the consumer smoke test. It verifies generated routing/adapters, repository layout, validation readiness, and user-level skill links without writing project files. Use `setup --check --no-link-skills` when validating a temporary copy that should not claim the active Codex, Claude, or Copilot skill links.
The low-level installer is copy-only by default. Selecting `install-harness --run-setup-check` explicitly initializes the target with `setup --no-link-skills`, then runs `setup --check --no-link-skills`; this makes partial-profile generated routing consistent without depending on or changing the user's global agent profile.
`install-harness-smoke` also writes `.agents/harness-smoke-target.json` into kept temporary targets. In that marked state, plain `setup --check` auto-skips global skill-link checks so manual dogfood inspection does not report a false missing-link issue. Real consumer installs do not receive that marker.

Review `docs/project/project-context.md` before using story, bug, migration, or upgrade workflows. It must define the target project's technologies, local run commands, validation commands, folder structure, generated-file boundaries, external systems, baseline persistence/data-store ownership, planning inputs, and validation expectations. Change the context status from `draft` to `reviewed` only after those facts are confirmed; implementation planning should stop while critical facts are missing. Change-specific impacted entities and ERDs belong in the generated workflow plan, not in this baseline context.

Use the read-only context review after setup:

```shell
python -B .agents/manage.py project-context-review --target D:/Projects/NewProject
python -B .agents/manage.py project-context-review --target D:/Projects/NewProject --write-review
python -B .agents/manage.py project-context-apply-review --target D:/Projects/NewProject
python -B .agents/manage.py project-context-apply-review --target D:/Projects/NewProject --apply
```

The `--write-review` mode writes `docs/project/review/project-context-review.md` and `.json` in the target project. Treat those as answer sheets for missing facts. `project-context-apply-review` previews the marker-bounded reviewed-facts section for `docs/project/project-context.md`; add `--apply` only after the answers are approved.

The source harness project context is excluded from consumer copies. To initialize the target project's own maps and context package, run:

```shell
python -B .agents/manage.py setup
python -B .agents/manage.py setup --check
```

Focused lower-level commands are still available when setup reports project-initialization work:

```shell
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py update --target . --write
python -B .agents/skills/project-context-generator/scripts/generate_project_context.py --target . --write
python -B .agents/skills/repo-navigation/scripts/repo_navigation.py project-context --target . --check
```

If it reports generated files are missing or stale, run:

```shell
python -B .agents/manage.py setup
python -B .agents/manage.py check
```
