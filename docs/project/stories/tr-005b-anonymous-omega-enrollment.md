---
title: TR-005B Anonymous Omega Z Enrollment
type: user-story
status: implemented
owner: project
audience: agent-and-developer
updated: 2026-08-03
---

# TR-005B — Anonymous Omega Z enrollment

As the owner of an Omega Z that sometimes advertises FTMS services without a local name, I want it to appear as a bounded read-only enrollment candidate so that TreadmillRunner can establish its identity from Device Information and receive telemetry without inferring control capability.

## Acceptance

- An unnamed `1816` plus `1826` advertisement may enter the Omega enrollment path.
- A named non-Omega device is not matched from the service signature alone.
- The raw advertised name is distinct from the operator-facing display label in the enrollment request.
- Exact model and firmware still come from `2A24` and `2A26`.
- Advertisement matching never enables speed, incline, pause, Stop, or Start.

Hardware provenance is recorded in [Stage 2 read-only FTMS evidence](../protocol-evidence/omega-z/2026-08-03-stage2-read-only-ftms.md). Implementation evidence is owned by `automations/user-story-workflow/runs/US-TR-005B`.

## Implementation

- `OmegaZCompatibilityProfile` retains its exact `JFTMOmega Z` prefix and additionally accepts only an unnamed advertisement containing both `1816` and `1826`.
- `EnrollDeviceRequest` carries `AdvertisedName` separately from `DisplayName`, so a synthesized operator label cannot become protocol identity evidence.
- The Devices page forwards the raw advertised name. Enrollment remains `Unknown` until characteristic reads advance its evidence, and the default Omega capabilities keep every control flag false. Passive first-telemetry evidence is persisted asynchronously as `PassivelyObserved`; matching model/firmware never promotes controls before explicit verification/commissioning.
- Focused protocol and hosted endpoint tests cover the positive signature, named and incomplete negatives, request identity separation, and all control flags.
