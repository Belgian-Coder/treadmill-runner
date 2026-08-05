---
title: TR-019 Local-only release publishing
type: user-story
status: completed
owner: project
audience: operator-and-developer
updated: 2026-08-05
---

# TR-019: Local-only release publishing

## Story

As the maintainer, I want GitHub Actions disabled completely so commits and release tags never spend hosted minutes, while locally validated and signed packages can still be pushed to GitHub Releases.

## Acceptance criteria

- No workflow remains under `.github/workflows`, so commits, pull requests, tags, and manual dispatches cannot start a hosted build.
- `eng/create-github-release.ps1` remains the supported publisher and performs validation, build, signing, packaging, tag creation, asset upload, checksum verification, and publication on the release workstation.
- The non-exportable signing key and all Garmin runtime assembly remain local.
- Release documentation and repository instructions explicitly prohibit GitHub-hosted builds.
- The active hosted run is cancelled and the policy change is committed and pushed without creating another run.

## Boundaries

GitHub remains the public source, tag, and finished-asset host. It is not a build or signing environment. Published tags remain immutable; a release correction uses a higher version.

## Evidence

The serial workflow run is `automations/user-story-workflow/runs/US-TR-019`. The active release-validation run was cancelled, `.github/workflows/ci.yml` was removed, and repository documentation now defines local-only release publication.
