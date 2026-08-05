# Delivery

Use for modern .NET CI, packaging, deployment, and release evidence.

## Build, CI, Packages

- Prefer documented restore, build, test, format, analyzer, and CI commands.
- Keep SDK versions pinned through existing `global.json`, containers, or pipeline images.
- Do not weaken warnings, NuGet audit, analyzers, package sources, or credential policy for a green build.
- Keep build, test, package, staged release, and final promotion separate when the repo owns release flow.
- Treat environment gates, approvals, rollback, and artifact provenance as required evidence for release changes.
- Keep NuGet metadata, versions, public API tracking, and package validation consistent for libraries.
- Avoid final runtime images that use SDK images; floating major/minor tags need an accepted moving-base policy.
- Do not embed secrets in Dockerfiles, pipeline variables, package sources, or environment defaults.
- For containers, check Dockerfiles, SDK container publish, Compose, Kubernetes, Azure Container Apps, and AKS manifests before recommending changes.

## Cloud And Aspire

- Detect `.NET Aspire` AppHost and ServiceDefaults before changing service wiring, discovery, configuration, or observability.
- Treat AppHost as local orchestration/manifest generation, not the production host.
- Keep ServiceDefaults shared; avoid duplicate OpenTelemetry or service-discovery registration in individual apps.
- Use Aspire `WaitFor` only for true startup-readiness dependencies.
- Keep `Aspire.Hosting.*` in AppHost projects and client/component packages in service projects.
- Keep local orchestration separate from hosted release work; `azd`, Azure Container Apps, AKS, Bicep, Terraform, registry writes, and production writes need explicit approval.
- Prefer managed identity or platform secret stores over checked-in connection strings or pipeline variables.
- Record environment-specific config sources; do not assume development secrets exist in CI or production.

## Evidence And Observability

- Record test result, coverage, mutation, benchmark, snapshot, static analysis, and deployment checks in workflow-owned validation folders when handoff evidence is needed.
- Compare benchmark/routing evidence only to explicit baselines.
- Keep generated artifacts bounded and named by run or case ID.
- Confirm logs, metrics, tracing, health checks, and readiness probes match deployment expectations.
- For distributed systems, verify OpenTelemetry traces, metrics, logs, and W3C trace-context propagation across service boundaries.
- Keep payload, header, and body logging safe by default.
- Report missing observability as risk when changing background work, queues, auth, or external calls.

## Review Checklist

- CI command order mirrors local commands.
- Package, container, and deployment metadata match target runtime.
- No credential, upload, or production-write behavior was added implicitly.
- Validation evidence is deterministic enough for later comparison.
