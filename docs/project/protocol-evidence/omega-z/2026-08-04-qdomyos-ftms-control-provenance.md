# Omega Z FTMS control reference provenance — 2026-08-04

## Scope

This record captures independently authored protocol observations used to prepare TreadmillRunner's
offline command fixtures. It does not record a successful physical command from TreadmillRunner.
The owner manually started and manually stopped the treadmill during the preceding session.

## Reference pin

- Repository: a separately cloned `../qdomyos-zwift` research checkout
- Upstream: `https://github.com/cagnulein/qdomyos-zwift.git`
- Commit: `99f27b2cb5360ce925c19c40d5a4ddff29ef4057`
- Review mode: local, read-only; no source copied into TreadmillRunner
- License boundary: declarative protocol facts and independently authored fixtures only

## Independently observed FTMS control facts

| Operation | FTMS control-point payload |
|---|---:|
| Request Control | `00` |
| Set Target Speed 1.0 km/h | `02 64 00` |
| Set Target Inclination 2.5% | `03 19 00` |
| Start / Resume | `07` |
| Stop | `08 01` |
| Pause | `08 02` |
| Response Code prefix | `80` |

Speed is an unsigned little-endian integer in 0.01 km/h units. Inclination is a signed
little-endian integer in 0.1 percent units.

QDomyos was observed requesting control and issuing Start before some speed/incline targets.
TreadmillRunner intentionally does **not** reproduce that sequence: a target change never emits
Start and therefore cannot restart a stopped belt as a side effect.

QDomyos also contains name-based Horizon/Paragon selection and an explicit Force FTMS setting.
The enrolled anonymous Omega Z cannot safely be selected by name alone, so TreadmillRunner binds
control to its persisted Windows device ID, redacted identity fingerprint, explicit FTMS mode,
exact model/firmware, connection generation, and individually confirmed capabilities.

## Prepared validation order

The one-shot commissioning runner accepts exactly one of `Stop`, `Pause`, `SetIncline`,
`SetSpeed`, or `Start`. A caller-provided operation ID is persisted before a command attempt and
cannot be replayed after process restart. Only the single confirmed capability is promoted.

No commissioning command was executed while preparing this record.
