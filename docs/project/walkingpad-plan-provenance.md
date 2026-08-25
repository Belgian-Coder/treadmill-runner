---
title: WalkingPad plan bundle provenance and regeneration
type: reference
status: current
owner: project
audience: agent-and-developer
updated: 2026-08-23
---

# WalkingPad plan bundle provenance and regeneration

The packaged 58-week 5K-to-10K plan is a deterministic, sanitized derivative of an owner-provided WalkingPad/QDomyos source snapshot. The private source files are not runtime dependencies and are not redistributed in this repository. The reviewed source layout is:

- `workout_index.csv`, containing week, session, variant, title, selection rule, source filename, and stable source ID columns;
- one indexed QDomyos v4 XML workout per source row, containing duration, Metric speed, incline, force-speed, heart-rate zone/range, and bounded speed fields.

The checked-in generated payload is [WalkingPadDistancePlanData.g.cs](../../src/TreadmillRunner.Core/Workouts/WalkingPadDistancePlanData.g.cs). Its normalized UTF-8 JSON SHA-256 is `c476161e23a94242c8172dffe3b42fc2efb559b1232753bca7fbe297529bdff3`. The payload contains 174 ordered training slots and 260 source variants. At materialization time, the catalog removes only the recognized legacy two-row low-speed stopping tail; verified Stop remains authoritative.

## Regeneration

From the repository root, point the generator at the reviewed source snapshot:

```powershell
pwsh -File eng/generate-walkingpad-plan.ps1 -SourceRoot D:\path\to\reviewed-source
```

The generator reads only the index and its explicitly referenced XML files, normalizes numeric values using invariant culture, emits deterministic compact JSON, computes SHA-256 over those normalized UTF-8 bytes, compresses with gzip, and rewrites the generated C# payload. A successful regeneration prints the slot count, variant count, and hash. Review the resulting diff and require the hash above unless a deliberate source revision has been approved and documented.

For the currently checked-in artifact, the generator script SHA-256 is `f1637593a746f16a26a3f6d21418804863a1017d9de2496804a743b9b0c57822`. Record the current script hash alongside any newly approved payload hash in the change evidence. The normalized payload hash, rather than gzip bytes, is the stable content identity because gzip implementation details may vary.
