---
title: Project Documentation
type: index
status: active
owner: project-context-generator
audience: both
updated: 2026-08-23
---

# Project Documentation

Use this folder in consumer projects for project-specific architecture, domain, operations, and decision records.

Start with `project-context.md`. It is the human-owned project profile for implementation and review. Keep baseline technologies, commands, folder rules, generated-file boundaries, external systems, persistence ownership, validation expectations, and planning inputs there. The previous repository-local AI harness was retired on 2026-09-13; its generator commands in older reference documents are no longer available.

The [installation guide](../installation.md) is the user/operator starting point for Windows setup, first run, online and offline updates, repair, and household-network safety.

The [story backlog](backlog.md) owns delivery order. Safety- or extension-sensitive acceptance details live in the [story index](stories/README.md).

Operators publishing or installing application updates should use the [release operations runbook](release-operations.md). It documents signing trust, UI activation, rollback, and recovery.

Garmin's supported Training API path, unsupported per-profile completed-activity upload, credential/retry boundaries, duplicate prevention, and removal are documented in [Garmin integrations](garmin-connect.md). The watch source, pairing protocol, SDK validation, physical acceptance, and complete IQ Store submission package are documented in [Connect IQ companion setup and store release](connect-iq-companion.md).

Metric session/workout export routes and their immutable-source guarantees are documented in [Session and workout exports](exports.md). The generated WalkingPad catalog's sanitized source boundary, content hash, generator hash, and regeneration command are documented in [WalkingPad plan provenance](walkingpad-plan-provenance.md).

The [Polar H10 memory architecture](polar-h10-memory.md) documents lifecycle, recovery, persistence, Garmin ordering, and the manual archive. Its independently authored wire-contract boundary and unsupported physical claims are recorded in [Polar H10 PFTP memory protocol provenance](protocol-evidence/polar-h10/2026-09-10-pftp-memory-provenance.md).

One-tap reuse, Screen Wake Lock behavior, local QR access, generated workout-set import, BLE reliability/battery reporting, and database integrity maintenance are documented in [Local reliability, access, and generated workout sets](local-daily-use-reliability.md).

The [Bluetooth, reliability, and mobile usability audit](bluetooth-mobile-reliability-audit.md) records the recovery review, iPhone/iPad refinements, regression coverage, and physical-device evidence boundary.

Files under `docs/project/review/` are intermediate review artifacts; they can help collect answers but do not replace the canonical `project-context.md`.

Files under `docs/project/dotnet-context/` are retained project-local evidence or baselines for drift review; they are not current generated instructions.
Use `--dotnet-executable <path-to-dotnet>` when the repo requires a trusted local SDK that is not first on `PATH`; this still does not run restore, build, test, package, or tool commands.
Use `--solution <solution.sln>` and `--project <project.csproj>` to narrow the report in large .NET repositories without changing the read-only/no-restore policy.

The reusable harness copy contract excludes the source repository's project-specific `docs/project/project-context.md`, generated diagrams, review artifacts, and validation evidence. Consumer projects should initialize their own context with setup instead of inheriting this repository's context.

Keep reusable harness documentation in the other `docs/` folders. Project docs may reference harness docs, but harness docs should not depend on project-specific files.
