---
title: TR-013 Connect IQ SDK follow-up plan
type: accepted-plan
status: completed-local
owner: project
audience: developer-and-operator
updated: 2026-08-07
---

# TR-013 Connect IQ SDK follow-up plan

The owner authorized installing Connect IQ SDK 9.2.0 and continuing TR-013 validation. The accepted local scope was:

1. Compile every device declared in the companion manifest with the real SDK and treat warnings as failures.
2. Fix only source, manifest, settings, and launcher-resource incompatibilities exposed by the compiler.
3. Add pure Run No Evil tests for gateway-setting validation and elapsed-time formatting.
4. Run those tests on representative Fenix 8 47 mm and Vivoactive 5 simulators.
5. Make the repository validator discover SDK Manager, Java, and an external protected developer key without committing machine-specific configuration.
6. Update TR-013 documentation and sanitized evidence.

No treadmill command, BLE operation, Garmin credential, physical-watch action, store upload, release, or deployment was authorized by this follow-up.
