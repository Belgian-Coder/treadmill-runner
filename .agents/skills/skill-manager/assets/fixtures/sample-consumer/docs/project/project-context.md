# Project Context

## Context Status

- Status: reviewed-fixture
- Owner: harness-tests
- Updated: 2026-05-30

## Technologies

| Area | Value | Evidence |
|---|---|---|
| Runtime | .NET 8 fixture | `src/SampleConsumer/SampleConsumer.csproj` |
| Package management | Central package management | `Directory.Packages.props` |
| Package feeds | Repo-local NuGet.config | `NuGet.config` |

## Commands

| Purpose | Command | Notes |
|---|---|---|
| Build | `dotnet build src/SampleConsumer/SampleConsumer.csproj` | Requires local .NET SDK; fixture tests may inspect without executing. |

## Folder Structure

| Path | Purpose |
|---|---|
| `src/SampleConsumer/` | Minimal app project. |
| `docs/project/project-context.md` | Reviewed project facts for workflow planning. |
