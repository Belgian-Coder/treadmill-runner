# TreadmillRunner-Android: specification pack

This folder is the complete, self-contained input for building **TreadmillRunner-Android** from scratch in a new repository. Copy the whole folder, including `data/`, into that repository as `docs/spec/`. Then move `stories/` to the repository root and `harness/` to `harness/templates/`, as plan section 12.1 describes.

It describes everything the current Windows/.NET TreadmillRunner does:
- rules, data structures and algorithms;
- device protocols and golden test vectors.

Together these let the Kotlin app re-implement the behaviour and stay data-compatible: it can import the current app's backups, and its exports can be imported back. Nothing here depends on the old repository.

| File | Content |
|---|---|
| [00-plan.md](00-plan.md) | Architecture, technology, delivery (remote updates and debugging), design system, screens, validation, user stories, phases |
| [01-data-model.md](01-data-model.md) | Every entity and field; reading the legacy `.trb` backup |
| [02-workouts.md](02-workouts.md) | Workout schema v1, revisions, canonical JSON and hashing, summaries, preflight |
| [03-import-export-formats.md](03-import-export-formats.md) | Native JSON, QDomyos XML, FIT workout, v4 bundle |
| [04-calendar-and-plans.md](04-calendar-and-plans.md) | Calendar series, programs, program runs, premade catalog, goals and progression |
| [05-sessions-and-recording.md](05-sessions-and-recording.md) | Session state machine, samples, events, metrics, recovery, deletion, maintenance |
| [06-profiles-and-heart-rate.md](06-profiles-and-heart-rate.md) | Profiles, zones, HR source selection, HR speed controller, cues |
| [07-exports-and-backup.md](07-exports-and-backup.md) | Session export formats (JSON, CSV, TCX, FIT) and backup semantics |
| [08-ftms-and-treadmill.md](08-ftms-and-treadmill.md) | BLE/FTMS byte layouts, Omega Z profile and evidence, enrollment, reconnect |
| [09-safety-and-command-contract.md](09-safety-and-command-contract.md) | Command confirmation, intents, lease, start/pause/stop rules, failure states |
| [10-polar-h10.md](10-polar-h10.md) | HRS parsing, PFTP protocol, recording lifecycle and merge, firmware 4.x, SDK mapping |
| [11-garmin.md](11-garmin.md) | Activity upload and matching, FIT merge, job states, Connect IQ companion |
| `data/` | Golden vectors and datasets: FTMS, Polar, workouts, premade plans, exports, Garmin |
| [harness/](harness/) | Templates for `ticket.md`, `plan.md`, packets, `execution-log.md` and `validation.md` (plan section 12) |
| [stories/](stories/) | One folder per user story, numbered in build order, each with a filled `ticket.md` |

**Conventions:**
- Units are metric.
- Times are UTC ISO-8601 unless a time zone is stated.
- IDs are UUID strings. Migrated IDs are kept verbatim.
- Each spec ends with a test checklist that the Kotlin implementation must pass.
