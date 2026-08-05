---
title: Omega Z Stage 3 FTMS Start and Stop Commissioning
type: protocol-evidence
status: observed-with-follow-ups
owner: project
audience: agent-and-developer
updated: 2026-08-04
---

# Omega Z Stage 3 FTMS Start and Stop commissioning

## Authorization and physical conditions

- Owner authorization: “I can confirm, please test,” followed by an explicit request for a Start,
  three-second wait, and Stop without restarting the gateway.
- Observer: project owner, present at the treadmill.
- Conditions confirmed by the owner: belt empty, safety key fitted, physical Stop reachable, and
  treadmill positioned so an unloaded trial could not contact a person or object.
- Exact device: model `OMEGA Z`, console software `S3.02`, Bluetooth firmware `V10.23.17`, FTMS mode.
- Version provenance: `V10.23.17` was read from the Bluetooth Device Information service; the
  owner separately observed `S3.02` on the console during power-on on 2026-08-04. These are distinct
  software components.
- No operation ID was reused. Each failed or completed attempt remained durably consumed.

## Pre-command failures and fixes

| Observation | Motion write reached? | Cause | Durable correction |
|---|---:|---|---|
| FTMS `1826` characteristic discovery returned `AccessDenied` | no | Command discovery created redundant WinRT device/service handles while telemetry already held FTMS | Command discovery now targets only `1826/2AD9` and reuses its persistent handle; discovery yields once after service discovery |
| Explicit shared command open returned `SharingViolation` | no | Telemetry still held the service using Windows' default incompatible sharing mode | Both telemetry and command handles explicitly use `GattSharingMode.SharedReadAndWrite` |
| Request Control completed its GATT write but no FTMS response notification arrived | no Start/Stop write | This firmware does not publish a Request Control result although it accepts later command responses | A typed post-write response timeout is accepted only for Request Control; motion commands still require their own matching response and fresh telemetry |

The first two failures occurred before Request Control. The third sent only Request Control `00`,
which cannot move the belt. No Start was sent in any failed attempt.

## Confirmed command evidence

The first successful sequence used two gateway processes. Start was confirmed at `0.8 km/h`, and
Stop was confirmed at `0.0 km/h`. The owner visually observed approximately 13 seconds of belt
movement. That duration was longer than the requested three seconds because the test shut down the
Start process, waited three seconds, and started a second process before issuing Stop. The protocol
commands worked, but that wrapper timing was rejected as unsuitable for future paired trials.

The replacement single-process trial kept one gateway running and reported:

| Measurement | Observed |
|---|---:|
| Start intent to confirmed `0.8 km/h` | `6.234 s` |
| Confirmed Start to Stop intent | `3.043 s` |
| Stop intent to confirmed `0.0 km/h` | `3.956 s` |
| Confirmed Start to confirmed Stop | `6.998 s` |
| Pair end-to-end | `13.232 s` |

The paired runner waited the intended three seconds, but Stop still reacquired FTMS control on a
new command connection and incurred a two-second missing-response timeout. The coordinator was then
changed to retain one serialized command connection and control ownership across commands. Request
Control now has a separate `300 ms` response window; motion responses retain `2 s`. This optimization
is software-tested but has not yet received another owner-attended motion trial.

## Accepted facts

- The verified identity baseline is `OMEGA Z` / console `S3.02` / Bluetooth `V10.23.17`.
- FTMS Start/Resume `07` remotely starts this exact unit at its reported minimum `0.8 km/h`.
- FTMS Stop `08 01` remotely stops this exact unit and fresh telemetry reaches `0.0 km/h`.
- Both motion commands returned matching FTMS responses and were confirmed independently by fresh
  measured telemetry on the same connection generation.
- `CanStartRemotely` and `CanStopRemotely` were promoted for this model/firmware. Speed, incline, and
  Pause remain separate commissioning stages.
- The unit accepts Request Control but does not return its optional response notification in the
  observed windows.

## Follow-ups

- Validate the retained-connection latency optimization in one later owner-attended empty-belt
  trial; do not infer its physical latency from unit tests.
- Record the owner's visual duration for that optimized run alongside structured timestamps.
- Continue with target speed, incline, and Pause only under their already prepared stage commands.
- Retain the physical console, safety key, and physical Stop as authoritative.

## Daily-control sequence trial

The owner next authorized this exact sequence: start toward `1.2 km/h`, set `1.5 km/h`, set incline
to `1.0%`, slow to `1.0 km/h`, set incline to `0.5%`, Stop, then observe whether speed and incline
returned to zero. Because FTMS Start/Resume has no target field, the independently implemented
runner used the verified sequence `Start 0.8` then `SetSpeed 1.2`.

Observed result:

| Step | Disposition | End-to-end latency | Measured value |
|---|---|---:|---:|
| Start at `0.8 km/h` | Confirmed | `4.644 s` | `0.8 km/h` |
| Set speed `1.2 km/h` | Confirmed | `1.443 s` | `1.2 km/h` |
| Set speed `1.5 km/h` | Unknown after successful FTMS response | `1.032 s` | unavailable after reconnect |
| Incline `1.0%`, speed `1.0 km/h`, incline `0.5%` | Not sent | n/a | n/a |
| Reserved safety Stop | Confirmed | `2.307 s` | `0.0 km/h` |
| Post-Stop observation | Fresh | n/a | speed `0.0 km/h`, incline `0.0%` |

The runner correctly stopped advancing after the unknown result and sent the separately reserved
Stop. The unknown result was traced to an application defect: every capability-promotion database
update incremented the enrollment version, and `ReadOnlyDeviceCoordinator` treated that metadata
version change as a new physical enrollment. It cancelled generation 1 and created generation 2
while `1.5 km/h` awaited telemetry confirmation. The coordinator now restarts a worker only when the
enrollment identity changes. A focused regression test updates evidence/capabilities, waits through
the enrollment refresh interval, and proves the connection generation remains stable.

No `1.5 km/h` retry was sent after the unknown result. A later repetition requires a new sequence
operation ID and explicit owner approval.

## Corrected complete daily-control rerun

The owner explicitly approved a fresh rerun after the enrollment-version restart defect was fixed.
Sequence operation `e341adc1-9ce2-4ec4-991f-29c2fd5c0e22` completed in one gateway process. Every
command used its own deterministically derived, previously unused operation ID; no operation from
the partial trial was replayed.

| Step | Disposition | End-to-end latency | Fresh measured value | Generation |
|---|---|---:|---:|---:|
| Start at verified minimum `0.8 km/h` | Confirmed | `4.448 s` | `0.8 km/h` | 1 |
| Set speed `1.2 km/h` | Confirmed | `1.433 s` | `1.2 km/h` | 1 |
| Set speed `1.5 km/h` | Confirmed | `1.434 s` | `1.5 km/h` | 1 |
| Set incline `1.0%` | Confirmed | `1.445 s` | `1.0%` | 1 |
| Set speed `1.0 km/h` | Confirmed | `1.440 s` | `1.0 km/h` | 1 |
| Set incline `0.5%` | Confirmed | `1.446 s` | `0.5%` | 1 |
| Stop | Confirmed | `1.776 s` | `0.0 km/h` | 1 |
| Post-Stop observation | Fresh | n/a | speed `0.0 km/h`, incline `0.0%` | 1 |

The telemetry generation remained stable for the entire sequence. The final structured outcome set
`SpeedAndInclineReturnedToZero=true`, and the gateway shut down normally after observation. This
trial verifies FTMS Set Target Speed and Set Target Inclination for the listed targets on exact model
`OMEGA Z`, firmware `V10.23.17`, in addition to the previously verified Start and Stop.

The owner requested more time to visually inspect later sequences. The commissioning runner now
waits at least two seconds after every confirmed Start, speed, or incline action before issuing the
next planned action. A rejected or unknown action still skips the remaining plan and sends the
separately reserved safety Stop immediately; the observation hold never delays that safety Stop.
