# Connect IQ acceptance matrix

| Device | Build | Ready layout | Explicit start | Recording layout | Stop + save | Back protected | Standalone | Paired HTTPS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Fenix 8 43 mm | SDK 9.2.0 pass | pending | pending | pending | pending | pending | pending | pending |
| Fenix 8 47 mm | SDK 9.2.0 pass; 3/3 unit tests | pass (simulator) | pass (simulator) | pass (simulator) | pass (simulator) | pass (simulator) | pass (simulator) | pending |
| Fenix 8 Solar 47 mm | SDK 9.2.0 pass | pending | pending | pending | pending | pending | pending | pending |
| Fenix 8 Solar 51 mm | SDK 9.2.0 pass | pending | pending | pending | pending | pending | pending | pending |
| Vivoactive 5 | SDK 9.2.0 pass; 3/3 unit tests | pass (simulator) | pass (simulator) | pass (simulator) | pass (simulator) | pass (simulator) | pass (simulator) | pending |
| Vivoactive 6 | SDK 9.2.0 pass | pending | pending | pending | pending | pending | pending | pending |

Automated acceptance on 2026-08-07 used SDK 9.2.0. Every declared target compiled without warnings; Fenix 8 47 mm and Vivoactive 5 each passed the three Run No Evil settings/formatting tests. PRG hashes are recorded in `docs/project/evidence/tr-013/validation-manifest.json`.

For each remaining row, record simulator/device version, tester, date, interactive result, and screenshot paths. Physical-watch acceptance is required for the two household watches before treating pairing as daily-use ready.

## Interactive simulator evidence

On 2026-08-07, Codex Computer Use exercised the committed PRGs in Connect IQ SDK 9.2.0 on Fenix 8 47 mm simulator 6.0.2 and Vivoactive 5 simulator 5.2.0. On both devices, an explicit Select started recording, Back left the recording active, and a second Select stopped/saved and returned to Ready. Standalone state was visible throughout. No paired HTTPS route was configured or tested.

The first layout pass exposed clipped action hints and a vertically misaligned timer. The corrected build uses concise hints, optically aligns the large timer with Ready, and moves the session title into a separate header row. Measured glyph centers differ by only 2 px on Fenix and 1.5 px on Vivoactive. Final Ready and Recording captures passed visual review on both representative devices. Source captures are listed in [`screenshots/README.md`](screenshots/README.md).
