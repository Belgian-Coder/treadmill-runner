---
title: TR-024 — Premade training-plan catalog and public evidence
type: user-story
status: implemented
owner: project
audience: agent-and-developer
updated: 2026-08-06
---

# TR-024 — Premade training-plan catalog and public evidence

## Outcome

As one of the two household runners, I can preview a neutral premade plan against my profile and the enrolled treadmill, add it to my training without activating it, and browse long plans by phase and week.

## Acceptance boundaries

- The read-only catalog contains the approved 16 templates, including the 58-week, 174-session distance-first plan.
- Catalog filters cover goal, experience, search, heart-rate requirement, duration, and sessions per week through template data and the touch UI.
- Preview resolves heart-rate zone references from the selected runner and reports runner/treadmill target normalization before persistence.
- Materialization is profile-scoped and idempotent. **Add fresh copy** is the only deliberate duplication path.
- Identical definitions inside one plan are stored once while all scheduled positions remain present.
- Installed template revisions preserve template/version, week, session, phase, and owner provenance and are immutable.
- Adding a plan never activates a program and never starts or commands the treadmill.
- Generic QDomyos XML retains conservative fixed-speed behavior. Only an explicit v4 bundle with positive HR targets and bounded speed fields becomes an HR-controlled workout.
- Public evidence contains demo data only and is checked for secrets, private addresses, production identities, and source-machine paths.

## Deliberate exclusions

No deployment, GitHub tag, public release, treadmill motion, BLE commissioning, long soak, or repeated power cycle is part of this story.

## Validation

The deterministic catalog, importer, API/persistence, migration, and Playwright suites cover the acceptance rules. The checked-in evidence packet is under `docs/project/evidence/tr-024/`; representative images are under `screenshots/showcase/`.
