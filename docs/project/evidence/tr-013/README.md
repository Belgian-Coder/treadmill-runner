---
title: TR-013 Connect IQ SDK acceptance evidence
type: validation-evidence
status: accepted-local
owner: project
audience: developer-and-operator
updated: 2026-08-07
---

# TR-013 Connect IQ SDK acceptance evidence

This sanitized packet closes the compiler and automated simulator portion of TR-013. It contains no developer-key material, account data, watch identifiers, private addresses, or machine-specific paths.

- [Accepted follow-up plan](accepted-plan.md)
- [Workflow report](workflow-report.md)
- [Validation manifest](validation-manifest.json)
- [Resolved failures](failure-summary.md)
- [Interactive simulator report](interactive-simulator-report.md)

The evidence proves warning-free SDK 9.2.0 builds for every declared target, passing Run No Evil tests and interactive layout/input acceptance on representative Fenix 8 and Vivoactive devices, and an automated local signed `.iq` export. It does not claim all-target interactive layout, physical-watch, trusted-HTTPS, IQ Store submission, or Garmin review acceptance.

Interactive simulator automation on 2026-08-07 found and resolved clipped hints, timer alignment, and header-spacing issues. The rebuilt Ready and Recording layouts passed final readability review on both representative devices.
