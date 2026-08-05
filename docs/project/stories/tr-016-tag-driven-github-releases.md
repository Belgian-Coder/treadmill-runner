---
title: TR-016 Local-only tagged GitHub releases
type: user-story
status: implemented
owner: project
audience: agent-and-developer
updated: 2026-08-05
---

# TR-016: Local-only tagged GitHub releases

## Story

As the TreadmillRunner maintainer, I want releases to use immutable semantic-version tags while all build work stays on the release workstation so that GitHub hosted minutes are never consumed and signed releases remain reproducible.

## Acceptance criteria

- Ordinary branch pushes, pull requests, and `vMAJOR.MINOR.PATCH` tags do not start GitHub Actions.
- No workflow is installed under `.github/workflows`; local scripts are the only supported build and package path.
- `eng/create-github-release.ps1` remains the only supported tag/release publisher and keeps the non-exportable signing key local.
- An interrupted matching tag/draft can be resumed, while conflicting or published tags/releases are never overwritten.
- The exact procedure and recovery rules remain available in the repository without relying on task history.

## Design boundary

The release workstation validates, builds deterministic assets, signs the manifest, creates the annotated tag, uploads to a draft, verifies the full asset set, and publishes. GitHub stores source, tags, and finished release assets only; it performs no build. This deliberately favors a local trust boundary and zero hosted-runner usage.

## Operator reference

Follow [TreadmillRunner release operations](../release-operations.md). Do not hand-create or force-move a version tag.
