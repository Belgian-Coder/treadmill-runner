# Connect IQ acceptance matrix

| Device | Build | Ready layout | Explicit start | Recording layout | Stop + save | Back protected | Standalone | Paired HTTPS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Fenix 8 43 mm | SDK 9.2.0 pass | pending | pending | pending | pending | pending | pending | pending |
| Fenix 8 47 mm | SDK 9.2.0 pass; 3/3 unit tests | pending | pending | pending | pending | pending | pending | pending |
| Fenix 8 Solar 47 mm | SDK 9.2.0 pass | pending | pending | pending | pending | pending | pending | pending |
| Fenix 8 Solar 51 mm | SDK 9.2.0 pass | pending | pending | pending | pending | pending | pending | pending |
| Vivoactive 5 | SDK 9.2.0 pass; 3/3 unit tests | pending | pending | pending | pending | pending | pending | pending |
| Vivoactive 6 | SDK 9.2.0 pass | pending | pending | pending | pending | pending | pending | pending |

Automated acceptance on 2026-08-07 used SDK 9.2.0. Every declared target compiled without warnings; Fenix 8 47 mm and Vivoactive 5 each passed the three Run No Evil settings/formatting tests. PRG hashes are recorded in `docs/project/evidence/tr-013/connectiq-sdk-validation.json`.

For each remaining row, record simulator/device version, tester, date, interactive result, and screenshot paths. Physical-watch acceptance is required for the two household watches before treating pairing as daily-use ready.
