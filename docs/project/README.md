---
title: Project Documentation
type: index
status: active
owner: project-context-generator
audience: both
updated: 2026-08-06
---

# Project Documentation

Use this folder in consumer projects for project-specific architecture, domain, operations, and decision records.

Start with `project-context.md`. It is the human-owned project profile that story, bug, migration, and upgrade workflows should read before planning implementation. In a copied target project, run `python -B .agents/manage.py setup` first; setup uses `repo-navigation` to create navigation maps and `project-context-generator` to create this project's context package when missing. Generate or refresh it directly with `python -B .agents/skills/project-context-generator/scripts/generate_project_context.py --target <project-root> --write` when it is stale or still a draft. Keep baseline technologies, commands, folder rules, generated-file boundaries, external systems, persistence ownership, validation expectations, and planning inputs there. Change its context status from `draft` to `reviewed` only after those facts are confirmed. Keep story- or bug-specific impacted entities and ERDs in the generated workflow plan.

The [installation guide](../installation.md) is the user/operator starting point for Windows setup, first run, online and offline updates, repair, and household-network safety.

The [story backlog](backlog.md) owns delivery order. Safety- or extension-sensitive acceptance details live in the [story index](stories/README.md).

The project-owned [user-story plan override](workflow-overrides/user-story-workflow/plan.md) adds TreadmillRunner-specific mock-first constraints for sanitized prototype data, safety and disconnected states, responsive evidence, the Blazor production boundary, and separate design and implementation approval.

Operators publishing or installing application updates should use the [release operations runbook](release-operations.md). It documents signing trust, UI activation, rollback, and recovery.

Garmin's supported Training API path, unsupported per-profile completed-activity upload, credential/retry boundaries, duplicate prevention, and removal are documented in [Garmin integrations](garmin-connect.md). The watch source, pairing protocol, SDK validation, physical acceptance, and complete IQ Store submission package are documented in [Connect IQ companion setup and store release](connect-iq-companion.md).

One-tap reuse, Screen Wake Lock behavior, local QR access, generated workout-set import, BLE reliability/battery reporting, and database integrity maintenance are documented in [Local reliability, access, and generated workout sets](local-daily-use-reliability.md).

Use `python -B .agents/manage.py project-context-review --target . --write-review` to create `docs/project/review/project-context-review.md` and `.json` when facts need answers. Those files are intermediate review artifacts; they help collect answers but do not replace the canonical `project-context.md`. After answers are approved in the JSON artifact, run `python -B .agents/manage.py project-context-apply-review --target .` to preview the managed canonical section, then add `--apply` to write it.

For .NET projects, `python -B .agents/manage.py dotnet-context --target . --write-evidence` can write `docs/project/dotnet-context/dotnet-context.json` and `.md` with read-only SDK/runtime, build-policy, CI, config-key, persistence, and NuGet/feed facts. Treat those files as project-local evidence or baselines for drift review; they are not promotable harness source by default.
Use `--dotnet-executable <path-to-dotnet>` when the repo requires a trusted local SDK that is not first on `PATH`; this still does not run restore, build, test, package, or tool commands.
Use `--solution <solution.sln>` and `--project <project.csproj>` to narrow the report in large .NET repositories without changing the read-only/no-restore policy.

The reusable harness copy contract excludes the source repository's project-specific `docs/project/project-context.md`, generated diagrams, review artifacts, and validation evidence. Consumer projects should initialize their own context with setup instead of inheriting this repository's context.

Keep reusable harness documentation in the other `docs/` folders. Project docs may reference harness docs, but harness docs should not depend on project-specific files.
