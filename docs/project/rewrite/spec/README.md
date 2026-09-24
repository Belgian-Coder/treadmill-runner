# TreadmillRunner-Android: specification pack

This folder is the complete, self-contained input for building **TreadmillRunner-Android** from scratch in a new repository. Copy the whole folder, including `data/`, into that repository as `docs/spec/`. Then move `stories/` to the repository root and `harness/` to `harness/templates/`, as plan section 12.1 describes.

It describes everything the current Windows/.NET TreadmillRunner does:
- rules, data structures and algorithms;
- device protocols and golden test vectors.

Together these let the Kotlin app re-implement the behaviour. The only compatibility contract is the **run (session) data structure**: the new app imports the current app's runs (session JSON exports, or the runs extracted from its `.trb` backup), and its own run exports use the same structure and can be imported back. Everything else is recreated, not migrated. Nothing here depends on the old repository.

| File | Content |
|---|---|
| [00-plan.md](00-plan.md) | Architecture, technology, delivery (remote updates and debugging), design system, screens, validation, AI harness, user stories, phases, owner decisions and open questions (§16) |
| [01-data-model.md](01-data-model.md) | Every entity and field; extracting runs from the legacy `.trb` backup |
| [02-workouts.md](02-workouts.md) | Workout schema v1, revisions, canonical JSON and hashing, summaries, preflight |
| [03-import-export-formats.md](03-import-export-formats.md) | Native JSON (the primary format), QDomyos XML, FIT workout, v4 bundle; conversion and loss rules |
| [04-calendar-and-plans.md](04-calendar-and-plans.md) | Calendar series, programs, program runs, premade catalog, goals and progression |
| [05-sessions-and-recording.md](05-sessions-and-recording.md) | Session state machine, samples, events, metrics, recovery, deletion, maintenance |
| [06-profiles-and-heart-rate.md](06-profiles-and-heart-rate.md) | Profiles, zones, HR source selection, HR speed controller, cues |
| [07-exports-and-backup.md](07-exports-and-backup.md) | Session export formats (JSON, the run compatibility contract; CSV, TCX, FIT) and backup semantics |
| [08-ftms-and-treadmill.md](08-ftms-and-treadmill.md) | BLE/FTMS byte layouts, Omega Z profile and evidence, enrollment, reconnect |
| [09-safety-and-command-contract.md](09-safety-and-command-contract.md) | Who may command (phone Run console and treadmill console only), command confirmation, intents, start/pause/stop rules, failure states |
| [10-polar-h10.md](10-polar-h10.md) | HRS parsing, PFTP protocol, recording lifecycle and merge, firmware 4.x, SDK mapping |
| [11-garmin.md](11-garmin.md) | Activity upload and matching, FIT merge, job states, Connect IQ companion (standalone in v1) |
| `data/` | Golden vectors and datasets: FTMS, Polar, workouts, premade plans ([data/premade/README.md](data/premade/README.md)), exports, Garmin. All device addresses, names and accounts are synthetic |
| [harness/](harness/) | Templates for `ticket.md`, `plan.md`, packets, `execution-log.md` and `validation.md` (plan section 12) |
| [stories/](stories/README.md) | One folder per user story, numbered in build order, each with a filled `ticket.md`; the index is `stories/README.md` |

**Conventions:**
- Units are metric.
- Times are UTC ISO-8601 unless a time zone is stated.
- IDs are UUID strings. Imported run IDs are kept verbatim.
- Each spec ends with a test checklist that the Kotlin implementation must pass.
