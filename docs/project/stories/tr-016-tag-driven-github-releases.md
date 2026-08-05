---
title: TR-016 Tag-driven GitHub releases
type: user-story
status: implemented
owner: project
audience: agent-and-developer
updated: 2026-08-05
---

# TR-016: Tag-driven GitHub releases

## Story

As the TreadmillRunner maintainer, I want releases to use immutable semantic-version tags without building every commit so that GitHub Actions usage stays deliberate and signed releases remain reproducible.

## Acceptance criteria

- Ordinary branch pushes and pull requests do not start GitHub Actions.
- `vMAJOR.MINOR.PATCH` tag pushes start the combined release-validation job.
- Maintainers can deliberately dispatch the same validation from the Actions UI or GitHub CLI.
- `eng/create-github-release.ps1` remains the only supported tag/release publisher and keeps the non-exportable signing key local.
- An interrupted matching tag/draft can be resumed, while conflicting or published tags/releases are never overwritten.
- The exact procedure and recovery rules remain available in the repository without relying on task history.

## Design boundary

GitHub checks out and validates tagged source. It does not sign or publish update assets. The release workstation validates again, builds deterministic assets, signs the manifest, creates the annotated tag, uploads to a draft, verifies the full asset set, and publishes. This deliberately favors a local trust boundary over an exportable CI signing credential.

## Operator reference

Follow [TreadmillRunner release operations](../release-operations.md). Do not hand-create or force-move a version tag.
