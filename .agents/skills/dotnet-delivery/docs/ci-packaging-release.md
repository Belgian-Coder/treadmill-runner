# CI Packaging Release

Use this file for .NET pipeline, packaging, container, release, and operational-readiness changes.

Read-only/offline review is inspection-only. Do not run build/test/format/pack/publish, Docker build, cloud CLIs/APIs, package pushes, image pushes, signing, release creation, or evidence-output commands unless the task explicitly allows writes, network, or temp-only validation.

## CI Shape

- Prefer the repository's existing CI provider and command order. Mirror local restore, build, test, format, analyzer, and pack commands before inventing pipeline-only behavior.
- In GitHub Actions, keep `setup-dotnet`, SDK version selection, cache keys, artifacts, summaries, and permissions explicit. Cache NuGet packages by lock files, project files, and central package files rather than broad branch-only keys.
- In Azure DevOps, keep `DotNetCoreCLI` tasks, templates, variables, test-result publication, artifacts, environments, service connections, and approvals visible in YAML or documented pipeline settings.
- Keep matrix builds limited to meaningful target frameworks, OSes, or workloads. Large matrices raise cost and flake surface.
- In CI, publish test results and coverage as pipeline artifacts even when tests fail, so failures are diagnosable. In local read-only review, inspect existing evidence only; do not create evidence files unless approved or temp-scoped.

## Quality Evidence

- Use `dotnet-quality-gates` to normalize test results, coverage, mutation, BenchmarkDotNet, snapshot, package, routing, and scanner evidence when workflow handoff or comparison matters. In strict dogfood, only parse existing evidence without output flags; skip commands that run project tools or write reports. For implementation, write only to an approved/temp evidence folder.
- Keep benchmark comparisons tied to explicit baselines. Do not accept moving thresholds without a reviewable rationale.
- Keep generated artifacts bounded by run ID, case ID, or pipeline attempt. Avoid unbounded logs and large binary evidence unless explicitly requested.

## Packages And Releases

- NuGet authoring needs package metadata, license, README, symbols/source link, API compatibility, deterministic versioning, and package validation aligned with project policy.
- Use SemVer deliberately. Major version for breaking public contracts, minor for additive public API, patch for compatible fixes.
- If the repo uses NBGV or another versioning tool, preserve its branch/tag assumptions.
- SBOM, provenance, signing, and release notes are release-system concerns; add them only when the project owns the tooling.
- Package dry-runs still write `.nupkg`, metadata, or temp artifacts; keep them out of strict read-only passes.
- MSIX changes need explicit signing, certificate, Store, sideload, and auto-update decisions.

## Containers

- Prefer multi-stage Dockerfiles with SDK images only in build stages and runtime/aspnet images in final stages.
- Prefer rootless or non-root final containers when the base image and app permissions support it.
- Do not bake secrets into images through `ENV`, copied config, package sources, or build args.
- Keep base-image pinning policy explicit: floating tags ease patching; digest or full-version pins improve reproducibility.
- Health checks, readiness probes, ports, volume writes, and user permissions should match the deployment target.
- Docker builds and container dry-runs can write image/cache layers; run them only with explicit approval or an isolated temp/cache boundary.

## Operations

- OpenTelemetry, health checks, metrics, traces, logs, and W3C trace-context propagation need to match the runtime topology.
- Structured logging should preserve correlation IDs and avoid PII, secrets, raw request bodies, tokens, and large payloads by default.
- Environment approvals, service connections, workload identity, managed identity, and package feed credentials are explicit boundaries, not defaults. Inspect YAML/documented names locally; do not query or edit live settings with cloud CLIs/APIs without approval.
