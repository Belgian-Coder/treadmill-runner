---
title: TR-013 Connect IQ SDK workflow report
type: workflow-report
status: completed-local
owner: project
audience: developer-and-operator
updated: 2026-08-07
---

# TR-013 Connect IQ SDK workflow report

- Owning workflow: `user-story-workflow`, existing run `US-TR-013`.
- Approval: owner requested SDK installation and validation, then confirmed SDK 9.2.0 was installed.
- Execution: serial on the release workstation; no subagents and no external mutation.
- Result: all six device builds passed without warnings; both representative simulator suites passed 3/3.
- Repository gate: locked restore, formatting/analyzers, public-evidence scan, watch validation, BLE boundaries, zero-warning Release build, and every non-browser test passed.
- Release hygiene: a complete unsigned local packaging proof preserved all five source NuGet lock files byte-for-byte after migration-bundle creation.
- Connect IQ packaging: the automated local export produced one signed six-product `.iq` and a sanitized hash manifest; nothing was uploaded or published.
- Safety: watch recording still requires explicit Select; the companion contains no treadmill command route.
- Remaining external gates: interactive layouts, exact household watches, trusted HTTPS pairing, store assets/submission, and Garmin review.

The first context-audit attempt exposed an unrelated README context-budget drift. The README was shortened without raising or weakening the configured budget, after which the TR-013 workflow context refreshed successfully.
