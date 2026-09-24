---
title: "09 — Safety and treadmill command contract"
type: specification
status: draft-for-rewrite
audience: android-team
updated: 2026-09-24
---

# 09 — Safety and treadmill command contract

This chapter is **non-negotiable**. It defines who may command the treadmill, when, how an
outcome is judged, and how the app behaves when it cannot know what happened. Every rule
comes from the previous implementation, its tests and its hardware incidents, or from an explicit
owner decision for the rewrite. The latter are marked **[NEW]**. Rules marked **[LEGACY]**
describe the previous behaviour, where the rewrite deliberately differs.

Byte-level protocol, timings and device evidence are in **08 — FTMS and treadmill**. Language-neutral
test tables are in §11. The final checklist is §12.

## 1. Authority and principles

1. The **safety key, physical Stop button, console and treadmill firmware are authoritative**.
   TreadmillRunner is neither an emergency-stop system nor a medical device.
2. **Bluetooth loss may leave the belt moving.** Disconnect is never a stop and is never presented
   as one.
3. **A successful BLE write is not success.** Success means the matching FTMS response **and**
   fresh measured telemetry (§5).
4. **Unknown is never retried.** When the physical outcome is not known, the app suspends
   automation and tells the runner to look at the belt and use the console or physical Stop.
5. **Nothing replays.** Reload, reconnect, process death, reboot, update or restore never starts
   the belt and never re-sends a command.
6. **Read-only by default.** Every control capability is disabled until the exact model, firmware
   and host stack are commissioned (08 §11). A generic FTMS device never gets control, whatever
   feature bits it reports.
7. **Only the serialized command writer writes GATT** to the control point (§4). No UI, web
   handler, automation or recovery path writes directly.
8. **Never clamp an implausible device observation into a usable value.** Treat it as a
   protocol/device fault (§8).
9. Treadmill command endpoints are never reachable from outside the home LAN. Web control is off by
   default (§6.2).

## 2. Vocabulary

| Term | Values |
|---|---|
| Command kind | `Start` (FTMS `07`, also used for Resume), `SetSpeed` (`02`), `SetIncline` (`03`), `Stop` (`08 01`). `Pause` exists in the contract for completeness, but its raw FTMS form `08 02` is **never enabled**. The app's Pause action is a `Stop` (§6.4). |
| Disposition | `Confirmed`, `Rejected`, `Unknown` |
| Origin | `Manual` (a UI action under the controller lease), `PlannedTransition`, `HeartRateAutomation`, `WorkoutCompletion` (these three are app-owned automation under the session's automation authority), `Commissioning` |
| Session state | `Idle`, `ArmedWaitingForPhysicalStart`, `Running`, `PausedWaitingForPhysicalResume`, `Completed`, `Stopped`, `Interrupted`, `Faulted`. **[NEW]** UI-only, non-persisted sub-states: `Starting`, `Pausing`, `Resuming`, `Finishing` (a command is in flight). |
| Session origin | `Hardware`, `Simulator`, `SystemTest`, `Legacy` |
| Connection generation | a positive integer that changes on every BLE (re)connection attempt (08 §9) |

### 2.1 Dispositions

| Disposition | Means | Physical effect | Follow-up |
|---|---|---|---|
| **Confirmed** | matching success response **and** fresh measured telemetry of the relevant field shows the target | known | continue |
| **Rejected** | a guard failed **before** the motion write, **or** the device answered with a non-success result code | none, or the device refused | the caller may try again with a **new** operation id after fixing the cause. Automation origins suspend automation (§4.6). |
| **Unknown** | the motion write was attempted and then something failed: no or invalid response, no confirming telemetry within the window, disconnect, generation change, or cancellation | **unknown** | never retried. Automation is suspended (`SuspendedSafety`), a persistent "Couldn't confirm" state is shown, and physical inspection is required. |

A device non-success result (`80 op 02..05`) is **Rejected**, not Unknown. The device explicitly
refused.

## 3. Command intent

Every command is an immutable intent. It is created by the session engine (or by the commissioning
runner) and executed by the writer.

| Field | Rule |
|---|---|
| `operationId` | UUID, not empty. Supplied by the caller: one per user action, one per automation decision, deterministic per commissioning step. |
| `sessionId` | UUID, not empty |
| `kind` | see §2 |
| `issuedAt`, `expiresAt` | lifetime `expiresAt − issuedAt` must be **> 0 and ≤ 5 s** (validation bound). The engine always creates **4 s** intents. |
| `expectedSessionVersion` | integer ≥ 0. It must equal the live session version at execution. |
| `expectedSessionState` | the session state at creation. It must equal the live state at execution. |
| `leaseId`, `holderId` | UUID (not empty) and text (1–128 characters after trimming). This is the controller lease (Manual) or the automation authority (automation origins). |
| `connectionGeneration` | integer > 0. It must equal the treadmill's current generation. |
| `requestedValue` | `Start`, `SetSpeed`, `SetIncline`: **required**, finite and ≥ 0 (Start carries the expected minimum start speed). `Stop`, `Pause`: **must be absent**. |
| `origin` | see §2. The default is `Manual`. |

Result record: `operationId`, `kind`, `disposition`, `requestedValue`, `acceptedValue` (after
normalization), `measuredValue` (only if fresh), `reason` (non-blank text), `connectionGeneration`,
`issuedAt`, `completedAt ≥ issuedAt`. Optional values must be finite and ≥ 0 when present.

### 3.1 Engine-side admission (before an intent is created)

These checks run under the session lock. A failure means the action is refused and **no intent**
is created.

| Kind | Required |
|---|---|
| any | No software update is being activated. For Manual origin: a current controller lease whose id and holder match (**[NEW]** exception: Stop, §6.2). For automation origins: the session's automation authority id and holder match. The expected session version equals the current version. |
| `Start` | `canStart` is verified and the minimum start speed is known. State is `ArmedWaitingForPhysicalStart` or `PausedWaitingForPhysicalResume`. `requestedValue` := the verified minimum (0.8 km/h). |
| `SetSpeed` | `canSetSpeed` is verified and the speed range is known. State is `Running` **and** measured speed > 0.05 km/h. The value is finite. The value is clamped to `[range.min, min(profileMaxSpeed, range.max)]`. |
| `SetIncline` | `canSetIncline` is verified and the incline range is known. State is `Running`. The value is finite and clamped to `[range.min, range.max]`. |
| `Stop` | `canStop` is verified. State is Armed, Running or Paused. |
| `Pause` (raw) | `canPause` is verified. It is **never** verified, so this is always refused. |

The intent is then created with `expiresAt = issuedAt + 4 s`, the current session version and
state, and the current generation.

## 4. The single serialized command writer

### 4.1 Serialization and the operation ledger

- **One writer, one gate.** Only one intent executes at a time. Others wait for the gate.
- **Ledger key = (sessionId, operationId).** Handling at submission:
  - Key has a terminal result: return that **same result** without re-running. This holds even if
    the context has since changed.
  - Key is admitted or in flight: return **Rejected** "already admitted or in flight".
  - Otherwise: record the key as *admitted*, then wait for the gate.
- If the caller cancels **while waiting for the gate** (before admission), remove the key, so the
  same operation may be submitted again. Nothing physical happened.
- Once admitted (the gate is acquired), the key is **consumed**. It becomes terminal with whatever
  result is produced, including Rejected for cancellation before the write.
- Terminal ledger entries live **15 min** in memory, with at most **4096** entries. When full,
  evict the oldest terminal entries. If the ledger is still full, reject new operations with "retry
  after an in-flight command completes".
- The same operation id under a **different** session id is a different key.
- Durable **operation receipts** are separate. They make HTTP/UI actions idempotent across restarts
  (same id + same request fingerprint → the stored response; same id + different request →
  conflict). Receipts are retained **90 days** (legacy configurable 7–365), pruned every 6 h.
  Commissioning reserves its receipt **before** any command (08 §11.2).

### 4.2 Execution algorithm

Steps run in order. The first failure decides the outcome. "Reset" means discarding the command
state (control ownership, verified control point). On Android that does **not** disconnect the
telemetry link.

| # | Step | Failure → disposition, reason |
|---|---|---|
| 1 | `now > expiresAt` | Rejected: "The command intent expired before execution." |
| 2 | Context validator: session id, version and state match; lease or automation authority is current | Rejected: "The session state, version, or control lease changed." |
| 3 | Load the treadmill enrollment | Rejected: "No treadmill is enrolled." |
| 4 | Mode is FTMS | Rejected: "FTMS control is not explicitly selected…" |
| 5 | Evidence is `HardwareVerified` and model/firmware are non-blank. Commissioning mode instead requires evidence ≥ `PassivelyObserved`, protocol `horizon-omega-z`, model/firmware equal to the approval (case-insensitive), and an observer of 1–100 characters. | Rejected: "…blocked until the exact treadmill model and firmware are hardware verified" / "…do not match this commissioning approval." |
| 6 | The capability for the kind is verified and its range exists. Normalize per §4.3. Start: `requestedValue` must equal the range minimum within 0.0001. | Rejected: "Remote X is not verified…" / "requested Start speed does not match the verified treadmill minimum." |
| 7 | Device guard: state Ready; generation equals the intent's; the **relevant field** age ≤ 5 s (incline for SetIncline, speed otherwise); for Start, speed ≤ 0.05 km/h | Rejected: "connection or connection generation changed" / "Fresh treadmill speed/incline telemetry is required." / "Remote Start requires fresh telemetry confirming that the belt is stopped." **No connect, no write.** |
| 8 | Obtain the command channel for (device, generation). Reuse it if the same device and generation and `2AD9` was verified writable + notify/indicate. Otherwise set it up (bounded 15 s). | Rejected: "FTMS command connection failed; no motion command was sent." Reset. |
| 9 | If not control-owned: write `00`, wait 300 ms. Success → owned. **Typed timeout → owned** (Omega quirk). Parsed non-success or a wrong response → Rejected ("control was not granted") and reset. Other error → Rejected ("control acquisition failed") and reset. | as stated; **no motion sent** |
| 10 | Re-check expiry, the context validator and the device guard (step 7) | Rejected (guard reason). **This is how a generation change during Request Control is caught** (test C8). |
| 11 | Write the motion payload; wait ≤ 2 s for a response with the **same opcode** (others ignored) | any exception, timeout or cancellation → **Unknown** ("The BLE command outcome is unknown; inspect the treadmill and use physical Stop if needed."). Reset. |
| 12 | Parse the response and check the opcode | unparseable or mismatched → **Unknown** ("invalid command response"). Reset. |
| 13 | Result ≠ Success | **Rejected** "The treadmill rejected X: <ResultCode>." (the connection is kept) |
| 14 | Telemetry confirmation (§5), ≤ 5 s, polled every 100 ms | not confirmed → **Unknown** ("Fresh measured telemetry did not confirm the command; physical outcome is unknown.") Reset. |
| 15 | — | **Confirmed**, with `measuredValue` = the confirming reading |

The payload by kind: Start `07`, SetSpeed `02`+u16, SetIncline `03`+s16, Stop `08 01`, Pause
`08 02` (unreachable in production). Expected response opcodes: `07`, `02`, `03`, `08`.

Rejected and Unknown results carry `measuredValue` only when the relevant field is fresh (≤ 5 s);
otherwise it is null.

### 4.3 Target normalization (never more aggressive)

`align(range, requested, current)`:

```
r     = clamp(requested, range.min, range.max)          # exact decimal arithmetic
steps = (r - range.min) / range.increment
steps = floor(steps)   if r >= current                  # increasing: round toward current
        ceil(steps)    otherwise                        # decreasing: round toward current
value = range.min + steps * range.increment
```

`current` is the latest measured value of that axis. If none exists, `current` is the requested
value itself (which rounds down).

| Range | Current | Requested | Accepted |
|---|---|---|---|
| speed 0.8–20.0 / 0.1 | 1.0 | 1.26 | 1.2 |
| speed 0.8–20.0 / 0.1 | 1.5 | 1.24 | 1.3 |
| speed 0.8–20.0 / 0.1 | 1.0 | 25 | 20.0 |
| speed 0.8–20.0 / 0.1 | 1.0 | 0.5 | 0.8 |
| incline 0–12 / 0.1 | 1.0 | 12.5 | 12.0 |
| incline 0–12 / 0.1 | 1.0 | 0.55 | 0.6 |
| incline 0–12 / 0.5 | 2.0 | 2.9 | 2.5 |

**[NEW]** Before `align`, the engine also bounds targets by the profile's maximum speed, the
workout's own limits and personal limits. A normalized target is never more aggressive than what
the user or plan requested.

### 4.4 Rate limiting and supersession **[NEW]**

- Per axis (speed, incline): at most **one intent in flight plus one latest pending target**. A
  newer target replaces the pending one. A long-press repeat coalesces into the pending slot. After
  a 3 s long-press, the final commanded target equals the last displayed value.
- Increases are limited to one per confirmation cycle. The next increase waits for the previous
  one's Confirmed.
- Stop is never coalesced away, never queued behind a pending target, and pre-empts pending
  targets: it clears them, then takes the gate next.
- After an Unknown on any axis, the pending targets are dropped and automation is suspended.

### 4.5 Channel lifecycle

- One channel per (device, generation). Control ownership lasts the channel's lifetime.
- Release the channel state on: terminal session, Disconnect, Forget, app shutdown. Release is
  idempotent and disposes once (test C14).
- A disconnect or new generation always discards ownership. The next command sends `00` again.

### 4.6 Effects of results on the session

| Result | Effect |
|---|---|
| any | stored as `lastCommandResult`, shown with requested/accepted/measured |
| **Unknown**, any origin | reset the HR dwell timers; `commandsSuspended = true`; HR mode = `SuspendedSafety`; persistent warning "A treadmill command has an unknown physical outcome; automation is suspended." If the origin is WorkoutCompletion, also show "Workout completion could not confirm that the treadmill stopped. Use the physical Stop control; this workout will remain open until stopped telemetry is confirmed." |
| **Rejected**, automation origin | reset dwell; suspend commands; `SuspendedSafety`; "Automatic treadmill commands are suspended after a rejected command." WorkoutCompletion gets its own physical-Stop warning. |
| **Rejected**, Manual | shown inline. No suspension. |
| **Confirmed** | clears the Unknown warning, plus the per-kind effects in §6 |
| Confirmed Manual SetSpeed | records a manual speed override event (expected → accepted); sets the in-segment speed override; HR mode becomes `SuspendedManualOverride` until explicitly re-enabled |
| Confirmed Manual SetIncline | records a manual incline override event; sets the in-segment incline override |
| Confirmed HR-automation SetSpeed | the accepted speed becomes the displayed requested value |
| Confirmed PlannedTransition | marks the current step's speed or incline as applied |
| Confirmed Stop (not completion) | §6.4 |
| Confirmed Stop, WorkoutCompletion, measured ≤ 0.05 | finalize as `Completed` (§6.7) |

Re-enabling HR automation (an explicit user action with lease and version) clears
`commandsSuspended` and the rejected/unknown warnings. It does **not** erase `lastCommandResult`.
Planned-controls resume is still blocked while the last result is Unknown (§7.3).

## 5. Confirmation rule

After a **success** response observed at time `tResp`, poll every 100 ms for up to **5 s**:

1. If the treadmill generation ≠ the intent's generation → stop polling, **Unknown**.
2. Pick the relevant field: incline for SetIncline, speed otherwise. It must have
   `fieldObservedAt ≥ tResp` **and** field age ≤ 5 s. A notification that refreshed only the
   *other* field does not count (tests C6, C7).
3. Predicates:

| Kind | Confirmed when |
|---|---|
| `Stop` / `Pause` | speed ≤ **0.05** km/h |
| `Start` / `SetSpeed` | speed > **0.3** km/h **and** \|speed − accepted\| ≤ **0.15** km/h |
| `SetIncline` | \|incline − accepted\| ≤ **0.15** % |

4. Timeout → **Unknown**.

The confirmation wait has its own hard ceiling of 5 s + 100 ms + 1 s, so a stuck poll ends as
Unknown. Caller cancellation after a success response also yields **Unknown** (test C10).

## 6. Sessions: start, pause, resume, stop, end, completion

### 6.1 State machine

- `Arm()`: from `Idle` only. It binds the profile, the exact workout revision and optionally the
  program item. It **never moves the belt**.
- `observeTelemetry(speed)`: the speed must be finite and ≥ 0. In `ArmedWaitingForPhysicalStart`
  or `PausedWaitingForPhysicalResume`, count consecutive samples with speed **> 0.3 km/h**. Any
  sample ≤ 0.3 resets the count to 0. At **3** consecutive samples, go to `Running`. In other
  states the counter stays 0.
- Every transition increments `version` and resets the counter. Other version bumps: a manual speed
  override event, and configuration changes such as an HR mode change, Resume planned controls, or
  Reset progress.
- Terminal: `Complete()` from Running or Paused. `Stop()`, `Interrupt()` and `Fault()` from any
  active state (Armed, Running, Paused). `Reset()` goes from terminal to `Idle`, clearing events.
- `Restore(state, version)` is allowed only for an active state with version ≥ 1. It does not replay
  events.
- An invalid transition is a programming error.
- Armed → Running records `startedAt` and a "Physical movement detected" event. Paused → Running
  records a Resumed event.

### 6.2 Who may command **[NEW]**

- Exactly one UI holds the controller lease (§6.8). By default this is the phone's Run console; a
  web client may take it. Other UIs observe. Losing the lease never stops or pauses the session.
- **Remote (web) motion control is off by default.** When the owner enables it, a web client must
  hold the lease and be paired with the Operator role.
- **Start/Resume from the web** needs a separate "Allow start from web" toggle (default off), plus
  the lease and the Operator role. The phone plays the start cue.
- **Stop from any paired Operator is always allowed, with or without the lease.** It is still an
  intent, still serialized and still confirmed. [LEGACY: Stop required the lease.]
- Every motion intent carries the session version. A stale press (SSE lag) or simultaneous
  phone + web presses are rejected. Only the first press that matches the current version is
  accepted.
- **Input lockout, 800 ms**, enforced **in the engine per session across all UIs**: after Start,
  Resume, Pause or a stepper action is accepted, further Start/Resume/Pause/stepper actions within
  800 ms are rejected. **STOP and the Stop-sheet actions are never locked out.**

### 6.3 Start

- A single press on Start sends one `07` **[NEW; LEGACY was a 3-second hold]**. Accidental starts
  are guarded by the engine lockout, the state version and fixed button positions.
- Allowed only from `ArmedWaitingForPhysicalStart` with `canStart` verified and fresh telemetry
  showing speed ≤ 0.05.
- UI sub-state `Starting` with STOP visible. The session becomes `Running` only through §6.1 (3
  fresh samples > 0.3). The Start command's own Confirmed is not what moves the state machine.
- After `Running`, the **effective target** is applied through normal SetSpeed/SetIncline intents
  (for example Start 0.8, then SetSpeed to the plan speed).
- Single use. Never queued across reconnect, never auto-retried, never replayed after restart or
  update, and never used for automatic resume.
- **Console start**: while Armed, starting the belt on the console reaches Running by the same
  3-sample rule with no app command. Before commissioning, this is the only start path.

### 6.4 Pause = Stop, progress kept **[NEW owner decision; the software path existed in LEGACY]**

- The Pause button (label "Pause (stops belt)") sends **FTMS Stop `08 01`**. Raw `08 02` is unused
  and unverified.
- While in flight: UI state `Pausing`, STOP still visible.
- **Confirmed Stop** in Armed, Running or Paused (not completion):
  1. accumulate motion up to now;
  2. state becomes `PausedWaitingForPhysicalResume` (idempotent if already Paused);
  3. `isMoving = false`; measured speed = the confirmed value;
  4. suspend automation with "The treadmill is stopped; press Start when you are ready to resume.";
  5. append a `SessionPaused(reason = TreadmillStopped)` event;
  6. save a **recovery checkpoint**.
- Paused is entered **only after stopped telemetry** (the Confirmed Stop). If the pause Stop is
  **Unknown**, the session stays Running with automation suspended and "Couldn't confirm" shown,
  and **no Resume** is offered.
- Kept while paused: the workout cursor, the frozen plan position and all recorded data. The paused
  interval is marked and is not moving time.
- **[NEW]** Optional prompt after N paused minutes (profile setting): "End and save?". Never
  auto-start.
- [LEGACY note: an older document described Pause as raw `08 02` only. That was superseded by the
  Stop-backed pause.]

### 6.5 Resume

- A single press sends a fresh `Start` (`07`) from `PausedWaitingForPhysicalResume`. UI sub-state
  `Resuming`, STOP visible.
- Running again only after 3 fresh samples > 0.3 km/h. Then re-apply the **effective target**:
  - the in-segment override if one was set, otherwise the ramp value at the frozen position;
  - plus the incline;
  - for HR segments, the last controller output capped by the segment target, with the dwell
    timers reset.
- **Console changes**: a console stop while Running becomes Paused with progress kept **[NEW;
  LEGACY stayed Running with the belt stopped]**. A console start while Paused becomes Running and
  then applies the effective target. Before commissioning, the same happens with no commands sent.

### 6.6 Stop sheet, End, Reset, Discard

- **STOP** is always visible and never locked out. It sends Stop first, then offers: **Keep
  paused**, **Reset progress**, **End and save**, **[NEW] End, save and disconnect devices**,
  **Discard**.
- **End** is accepted only when the state is `PausedWaitingForPhysicalResume` **and** the belt is
  not moving **and** measured speed ≤ 0.05 (a confirmed stop). Result: `Stopped`, summary
  persisted, device connections released. The "and disconnect" variant first makes the session
  terminal, then closes only that runner's treadmill and HR enrollments.
- **[NEW]** If there has been **no treadmill telemetry for more than 30 s**, the sheet offers "End:
  I confirm the belt is stopped". This writes the event `stop-unconfirmed-by-telemetry`, ends the
  session as `Stopped`, and **sends no command**.
- **Reset progress** has the same precondition as End. It moves the cursor to step 1; keeps
  recorded totals, samples and evidence; clears overrides and applied-step markers; sets HR mode
  to Disabled; bumps the version; writes `workout-progress-reset` plus a checkpoint; and **never
  starts motion**. Step-1 targets are reconciled only after an explicit Start and fresh motion.
  This is the only way progress is lost.
- **Discard** requires confirmation and first persists any pending H10 cleanup job.
- All of these are idempotent by operation id and need the lease and version match. Stop alone has
  the Operator exception (§6.2).

### 6.7 Natural completion

Reaching the final step is **not** physical completion. The policy is a pure function of: progression
complete, hardware mode, telemetry fresh, isMoving, measured speed, `canStop`, treadmill Ready,
generation current, stop already attempted.

```
if !progressionComplete                       -> Continue
if !hardwareMode                              -> Finalize            (simulator: immediate)
speedValid = finite(speed) && speed >= 0
if fresh && speedValid && !moving && speed <= 0.05 -> Finalize
if stopAttempted                              -> AwaitPhysicalStop
if fresh && speedValid && moving && speed > 0.05
   && canStop && ready && generationCurrent   -> RequestStop         (exactly one, origin WorkoutCompletion)
else                                          -> AwaitPhysicalStop
```

- `Completed` only after fresh stopped telemetry. Send **at most one** completion Stop. A
  rejected, stale, disconnected or unknown outcome shows "use the physical Stop control; the
  workout remains open", keeps the session live and out of History, and never retries.
- A Confirmed completion Stop with measured ≤ 0.05 while Running and progression complete
  finalizes as `Completed`. A completion Stop that did not reach stopped telemetry suspends
  automation with the physical-Stop warning.

### 6.8 Controller lease

- Time to live **15 s**. The client heartbeats every **5 s**. Expiry is measured on the
  **monotonic clock** from the last renewal. Wall-clock jumps do not affect it.
- `tryAcquire(holder)`: if there is no live lease, create one (new id; acquiredAt = now;
  expiresAt = now + 15 s). If the same holder asks, **renew the existing lease** (same id, same
  acquiredAt, new expiresAt). A different holder while the lease is live gets **null** (no
  pre-emption).
- `heartbeat(id, holder)`: renews only if both match a live lease. An expired lease cannot be
  resurrected: it returns null, and the client must acquire again.
- `release(id, holder)`: only if both match. `revokeCurrent()`: system use (for example while an
  update activates).
- A blank holder is a client error (HTTP 400, "A control lease holder is required.").
- The lease gates **manual** actions only. App-owned automation uses the session's automation
  authority. Losing the lease never stops the session. Reloading with the same holder id
  idempotently reacquires.

## 7. Recovery and reconciliation

### 7.1 BLE telemetry gap during a session

- A gap starts when the **speed** field is stale (> 5 s) or missing, or when the treadmill
  generation changes. On entry:
  - record pre-gap speed and incline;
  - phase = Reconnecting;
  - append a `DeviceDisconnected(Treadmill, reason)` event;
  - set `isMoving = false`;
  - suspend automation ("Treadmill telemetry is stale; session automation is suspended.");
  - clear any "Resume planned controls" offer.
- **Never fabricate samples.** The gap is recorded as unobserved. Elapsed time continues.
- Intents from the old generation are dead: they fail the step 7/10 generation checks.
- Reconciliation starts when fresh samples arrive. It needs **2 fresh samples on the same new
  generation**; a different generation restarts the count. Then append `DeviceReconnected`, adopt
  the new generation, and evaluate the policy below.

### 7.2 Reconciliation policy (pure function)

Inputs: session state, same enrolled treadmill, fresh stable telemetry, recovered after app
restart, measured speed/incline, pre-gap speed/incline, speed/incline increments, last command
disposition.

Evaluated in order:

1. Not the same treadmill → **Blocked**.
2. Not fresh and stable → **Blocked**.
3. Last disposition Unknown → **Blocked** ("An unknown command outcome blocks automatic recovery.").
4. State ≠ Running → **Blocked**.
5. Any non-finite input → **Blocked**.
6. Measured speed ≤ 0.3 → **Blocked** ("The belt is not moving; recovery will never issue Start.").
7. Tolerances: speed `max(0.01, speedIncrement) + 0.01`, incline `max(0.01, inclineIncrement) + 0.01`.
   A difference above tolerance on either axis → **RequireExplicitResume** with
   `possibleConsoleIntervention = true`.
8. Recovered after app restart → **RequireExplicitResume** (no console flag).
9. Otherwise → **ResumeAutomatically**.

When the increments are unknown, the engine uses 0.1 km/h and 0.5 %.

Outcome effects:

- **ResumeAutomatically**: un-suspend; phase Recovered (it returns to Ready after 5 s); HR mode
  back to the desired mode; current planned targets reconciled with **fresh** commands.
- **RequireExplicitResume**: stay suspended, phase NeedsAttention, show **Resume planned
  controls**.
- **Blocked**: stay suspended, phase NeedsAttention, no button (except via the restart path).

### 7.3 Resume planned controls (explicit user action)

This requires the lease, a matching version, a matching current generation, state Running, a
moving belt, telemetry age ≤ 5 s, and a **last result that is not Unknown** ("An unknown treadmill
command outcome must be resolved physically before controls can resume."). Effects: un-suspend;
phase Recovered; restart flags cleared; HR mode = desired; version bump. **No planned command is
sent before this tap.**

### 7.4 App/process restart

- Restore only from a durable **recovery checkpoint** of a `Running` session. The checkpoint holds:
  - session id, time, state and version, startedAt, the progression snapshot;
  - distance, measured speed and incline, overrides, desired HR mode, generation.
- The treadmill identity fingerprint in the session configuration must equal the current
  enrollment's. Otherwise the session becomes **Interrupted** ("could not confirm the same enrolled
  treadmill") and connections are released.
- The restored session has `canStart = false` (no Start in recovery). Commands are suspended and
  the HR mode is `SuspendedSafety` if HR is required. The warning reads: "restarted; tracking
  recovery never issues Start and planned controls remain paused."
- **Fresh movement must be observed within 30 s**, or the session becomes **Interrupted**
  (incomplete telemetry allowed). If movement is seen, §7.2 returns RequireExplicitResume, which
  needs **Resume planned controls**.
- Startup recovery IO retries use a backoff of `min(30 s, 2^(attempt−1) s)`, with the attempt
  capped at 8.
- **[NEW]** Process death: recover within 30 s or Interrupted. **Reboot: the session is
  Interrupted.** The run service does not auto-start; the web service does.
- Terminal persistence retries at most 3 times, with 100/200 ms delays. Matching terminal writes
  are idempotent.

## 8. Telemetry validity

| Rule | Detail |
|---|---|
| Per-field timestamps | speed and incline have independent `observedAt`. An omitted field keeps its previous value **and previous timestamp**. |
| Freshness | 5 s per field, on the monotonic clock. A future timestamp (observedAt later than the capture) means the age is unknown, which is treated as **not fresh**. |
| Relevant-field rule | command guards and confirmation check only the field the command affects |
| Stale treadmill speed | the session suspends automation and shows "Treadmill telemetry is stale". Stale data never confirms anything. |
| Stale incline | incline is not updated in the session. Incline commands are rejected. |
| Implausible | speed non-finite, < 0 or > range max (100 km/h if unknown). Incline non-finite or outside the range (\|x\| > 90 if unknown). → the telemetry is dropped and the connection state is Faulted. Never clamped. |
| Moving | speed > 0.3 km/h; stopped means ≤ 0.05 km/h for Stop confirmation, End and completion |
| Empty notification | a notification with no fields is ignored |
| Heart rate | valid only at 30–250 bpm, fresh within 5 s, with sensor contact (or contact not supported), from the profile's assigned sources. Otherwise it is stored as `null`, resets HR dwell timers, and suspends HR automation while stale. A source change bumps the HR selection generation, suspends automation and writes an event. |
| Samples | 1 per second, with measured, requested and planned values kept distinct. Gaps are preserved. Distance = measured speed × Δt while moving. |

## 9. Failure presentation

These are persistent, accessible states. They are never toasts. They appear on the phone and in
the web UI, and each has one clear action.

| State | Trigger | Action |
|---|---|---|
| Bluetooth off / adapter unavailable | adapter state | open Bluetooth settings |
| Permission revoked | runtime permission | re-grant |
| Device absent | no connection, scan miss | check power; Connect |
| Connecting | attempt in progress | wait / Disconnect |
| Telemetry stale | relevant field > 5 s | use console/physical Stop if needed |
| **Couldn't confirm** (Unknown) | Unknown result | inspect the belt; use physical Stop; re-enable automation explicitly |
| Protocol invalid | parse failure or implausible telemetry | inspect; reconnect |
| Automation suspended | any suspension reason (shown verbatim) | re-enable after checking |
| Control unavailable (read-only) | no verified capability, downgrade, Vendor mode | use the console |
| Phone too hot / battery low / storage low or backup failing | device health | as indicated |

Always shown: device identity (model, firmware, redacted fingerprint), telemetry mode, freshness,
armed state, requested vs measured values, and the last command's disposition. Do not hide faults
behind automatic reconnect.

## 10. Hardware progression rules

1. Unit tests and the simulator (08 §14).
2. BLE scanning and service feasibility on the target stack.
3. Read-only treadmill and HR telemetry.
4. Stop, unloaded.
5. Pause (= Stop) at minimum speed.
6. A small incline change while off the belt.
7. A small speed change at minimum belt speed.
8. Planned transitions and manual overrides.
9. Conservative HR-speed automation (Shadow → DecreaseOnly → Full).
10. Remote Start, as a separate model/firmware gate, with the physical-console workflow as the
    fallback.

Every gate needs:

- sanitized raw evidence;
- a golden fixture;
- observed telemetry;
- timeout/disconnect results;
- a focused automated test;
- **explicit owner approval**.

Never jump from protocol code to a running-speed human test. Capabilities are keyed by model +
firmware + host stack. On Android, every control stays disabled until the phone's own
commissioning (08 §11.4) is approved. Any other model or firmware is read-only, and a mismatch on
reconnect durably downgrades (08 §8.5).

## 11. Test translation tables (given / expected)

Codec tests are data-driven from `data/ftms/*.json` (see 08 §16). The tables below cover the
behavioural suites. A `T` in the Clock column means a controllable fake clock.

### 11.1 FTMS control point codec

Load `ftms-control-point-codec.json`: `encode`, `encodeRejects` (expect an out-of-range error),
`parseResponse` and `parseResponseRejects`.

### 11.2 FTMS treadmill data parser

Load `ftms-treadmill-data-parser.json`. Absent keys must be null, and rejects must return no data.

### 11.3 FTMS capability parser and promotion

Load `ftms-capability-parser.json`. In addition:

| # | Given | Expected |
|---|---|---|
| P1 | capabilities with ranges 0.8–20/0.1 and 0–12/0.5 (ProtocolReported); promote Start | only `canStart` true; the range objects are identical (unchanged) |
| P2 | same; promote SetSpeed | only `canSetSpeed` true |
| P3 | same; promote SetIncline | only `canSetIncline` true |
| P4 | same; promote Pause | only `canPause` true (the helper exists; production never calls it) |
| P5 | same; promote Stop | only `canStop` true |

### 11.4 Omega reassembler, status decoder, CRC

Load `omega-frame-reassembler.json` (step-wise append/reset; compare frames and the final
diagnostics list), `omega-status-decoder.json` and `crc-ccitt.json`.

### 11.5 Command intent contract

| # | Given | Expected |
|---|---|---|
| I1 | SetSpeed, value 1.0, lifetime 4 s | created; value preserved |
| I2 | SetIncline, value 2.5 | created; value preserved |
| I3 | Stop with value 1.0 | invalid argument |
| I4 | Pause with value 1.0 | invalid argument |
| I5 | Start, lifetime 3 s, lease L, generation 42, value 0.8 | every field preserved |
| I6 | lifetime 0 s | out of range |
| I7 | lifetime 5.001 s | out of range |
| I8 | Start with NaN | out of range |
| I9 | Stop with value 0 | invalid argument (Stop accepts no value at all) |
| I10 | result Unknown, measured 0, generation 9 | preserved as given |
| I11 | empty operation/session/lease id; generation ≤ 0; version < 0; blank holder or holder > 128 characters | invalid |
| I12 | result with completedAt < issuedAt, a negative value, or a blank reason | invalid |

### 11.6 Command writer (coordinator)

The fake device reports Ready. The generation is as given. Speed/incline are fresh unless stated.
The verified enrollment is ranges 0.8–20/0.1 and 0–12/0.1 with all flags true
(including Pause, which C16 exercises at writer level). The fake channel records payloads. Unless stated, `00` gets no response and is accepted
(timeout), and each motion opcode gets `80 op 01`. Tests use short windows: RC 10 ms, response
100 ms, confirmation 35 ms, poll 5 ms.

| # | Given | Expected |
|---|---|---|
| C1 | gen 7, stopped. Start (0.8). On write `07` the device reports 0.8 at the response time. Execute the same intent twice. | Confirmed; accepted 0.8; measured 0.8; payloads `00`,`07`; the second call returns the identical result with no new write |
| C2 | same as C1 with a clock T. Execute; replay with a context that is never current; the same op id under another session; advance T 16 min and replay the original session | the replay returns the identical terminal result; the other session → Rejected "session state…"; after 16 min → Rejected "session state…" (not "already"); total payloads still 2 |
| C3 | commissioning mode, PassivelyObserved enrollment `OMEGA Z`/`V10.23.17`, `00` unanswered, Start | Confirmed 0.8; payloads `00`,`07` |
| C4 | telemetry 10 s old, Start | Rejected "Fresh treadmill speed telemetry"; **zero connects**, zero writes |
| C5 | service discovery never completes; connect bound 25 ms, dispose bound 25 ms | Rejected "connection failed" within 2 s; discovery cancelled; disposed exactly once |
| C6 | speed field 10 s old, incline fresh, Start | Rejected (speed); no connect or write |
| C7 | incline field 10 s old, speed fresh 0.8, SetIncline 2.5 | Rejected (incline); no connect or write |
| C8 | speed fresh 0.8; SetSpeed 1.0; after the write the aggregate refreshes but the **speed timestamp is 10 s old** (value 1.0) | **Unknown** ("confirm"); payloads `00`,`026400` |
| C9 | incline 1; SetIncline 2.5; after the write the incline value is 2.5 but its timestamp is 10 s old | **Unknown**; payloads `00`,`031900` |
| C10 | generation changes to 8 when `00` is written; Start | **Rejected**; exactly one payload `00` |
| C11 | success response for `07`, telemetry never changes; execute twice | **Unknown**; the second call returns the identical result; payloads `00`,`07` only |
| C12 | the caller cancels at the moment `07` is written (after success) | **Unknown**; payloads `00`,`07` |
| C13 | moving 0.8; Stop; the device reports 0 after `0801` | Confirmed, measured 0; payloads `00`,`0801` |
| C14 | C13, then release the channel twice | the channel is disposed exactly once |
| C15 | moving 0.8; SetSpeed 1.0 → device 1.0 | Confirmed; accepted 1.0; measured 1.0; payloads `00`,`026400` |
| C16 | moving 0.8; SetIncline 2.5 → incline 2.5; then Pause → speed 0 | both Confirmed (2.5 and 0); payloads `00`,`031900`,`0802`; **one** channel set up (control reused) |
| C17 | commissioning Stop, PassivelyObserved exact identity, gen 11; execute twice | Confirmed 0; payloads `00`,`0801`; the replay is identical |
| C18 | commissioning approval firmware "different" | Rejected "model and firmware"; zero connects and writes |
| C19 | policy with connection timeout 0, dispose −1 ms, or either at max | construction error |
| C20 **[NEW]** | response `80 07 05` to Start | **Rejected** "rejected Start: ControlNotPermitted"; the connection is kept |
| C21 **[NEW]** | while waiting for `02`'s response, the device first sends `80 07 01` (a late Start ack), then nothing | the late ack is ignored; **Unknown** after 2 s; no retry |
| C22 **[NEW]** | Start while speed is 0.8 (moving) | Rejected "requires … belt is stopped"; no write |
| C23 **[NEW]** | Start with requested 1.0 while the verified minimum is 0.8 | Rejected; no write |
| C24 **[NEW]** | intent expired before the gate is acquired | Rejected "expired"; no write |
| C25 **[NEW]** | enrollment in Vendor mode; any kind | Rejected "FTMS control is not explicitly selected" |
| C26 **[NEW]** | `00` answered `80 00 05` | Rejected "control was not granted"; no motion write |
| C27 **[NEW]** | two different operations submitted concurrently | executed strictly one after another; no interleaved writes |
| C28 **[NEW]** | cancel while waiting for the gate (before admission), then resubmit the same op id | the resubmission is admitted and executed |
| C29 **[NEW]** | Pause/raw `08 02` requested by the production engine | refused at admission (§3.1); the writer is never reached |

### 11.7 Commissioning runners

| # | Given | Expected |
|---|---|---|
| S1 | Start/Stop pair, both Confirmed and promoted | exactly one delay of **3 s**; requests (Start, startId), (Stop, stopId); `stop` present and Confirmed; requestedMovingDuration 3 s |
| S2 | Start Rejected | no delay, no Stop, one request |
| S3 | Start Unknown | no delay, no Stop, one request |
| S4 | the same id for Start and Stop | invalid argument **before** any stage runs |
| D1 | daily sequence, all Confirmed, final telemetry 0/0 fresh | requests Start(null), SetSpeed 1.2, SetSpeed 1.5, SetIncline 1.0, SetSpeed 1.0, SetIncline 0.5, Stop(null); **7 distinct** op ids; safety Stop sent; returned to zero; final 0/0; **six** 2 s holds |
| D2 | the 3rd request (index 2, SetSpeed 1.5) is Rejected | requests Start, SetSpeed, SetSpeed, Stop; safetyStopSent; step 2 Rejected; the last step Confirmed |
| D3 | Start Rejected | a single request; no safety Stop |
| D4 **[NEW]** | step Unknown after a Confirmed Start | the reserved Stop is sent immediately (no 2 s hold first) |
| K1 **[NEW]** | stage request with a used op id | abort "already reserved"; no command |
| K2 **[NEW]** | Start stage with speed 0.4 | abort before reservation |
| K3 **[NEW]** | SetSpeed stage with speed 0.2 | abort before reservation |
| K4 **[NEW]** | no fresh telemetry within 30 s | abort, no reservation, no command |
| K5 **[NEW]** | Confirmed SetIncline | only `canSetIncline` promoted, with evidence HardwareVerified |
| K6 **[NEW]** | Stop stage request that includes a target value | invalid argument |

### 11.8 Recovery reconciliation

Base input: Running; same treadmill; fresh; not a restart; measured 6.0/1.0; pre-gap 6.0/1.0;
increments 0.1/0.5; last Confirmed.

| # | Change from base | Expected |
|---|---|---|
| R1 | none | ResumeAutomatically |
| R2 | same treadmill = false | Blocked |
| R3 | fresh = false | Blocked |
| R4 | last = Unknown | Blocked |
| R5 | measured speed 0 | Blocked, reason contains "never issue Start" |
| R6 | measured speed NaN | Blocked, reason contains "Finite treadmill telemetry" |
| R7 | measured speed 6.3 | RequireExplicitResume, possibleConsoleIntervention = true |
| R8 | restart = true | RequireExplicitResume, console = false |
| R9 **[NEW]** | measured speed 6.1 (Δ 0.1 within the 0.11 tolerance). Avoid testing exactly at the tolerance: binary floating point makes 6.11 − 6.0 > 0.11. | ResumeAutomatically |
| R10 **[NEW]** | incline 1.52 (Δ 0.52 > 0.51) | RequireExplicitResume |
| R11 **[NEW]** | state Paused | Blocked |
| R12 **[NEW]** | measured 0.3 exactly | Blocked |
| R13 **[NEW]** | scenario: 2 fresh samples on generation 2, then 1 on generation 3 | reconciliation waits for 2 samples on generation 3 |
| R14 **[NEW]** | scenario: restart with a different fingerprint | Interrupted; connections released |
| R15 **[NEW]** | scenario: restart, no movement for 30 s | Interrupted |
| R16 **[NEW]** | scenario: Resume planned controls with last Unknown | refused |

### 11.9 Controller lease

| # | Given | Expected |
|---|---|---|
| L1 | A acquires; B acquires | A gets a lease; B gets null; valid for A; invalid for (A's id, B) |
| L2 | A acquires; +10 s; A acquires again | same id and same acquiredAt; expiresAt = now + 15 s; valid |
| L3 | acquire; +15 s | invalid; B can acquire |
| L4 | acquire; +10 s; heartbeat; +14 s | valid; the renewed expiresAt = now + 1 s |
| L5 | acquire; +16 s; heartbeat | null |
| L6 | acquire; the wall clock jumps +1 day (monotonic unchanged) | still valid |
| L7 | acquire / heartbeat with a blank holder | client error "control lease holder" |
| L8 **[NEW]** | lease expires during a running session | the session continues; automation continues; manual actions are refused |

### 11.10 Session state machine and completion

| # | Given | Expected |
|---|---|---|
| M1 | Restore(Running, 7) | Running, version 7, no events |
| M2 | Arm; samples 0.31, 0.30, 0.50, 0.60 | still Armed |
| M3 | M2 + 0.70 | Running |
| M4 | running; pause; samples 0.4, 0.5 | Paused; + 0.6 → Running |
| M5 | running; stop-wait twice | Paused both times |
| M6 | running; manual override 6.0 → 7.2 | one event (6.0, 7.2, now); version + 1 |
| M7 | running → Complete, Stop, Interrupt or Fault | that terminal state |
| M8 | Idle → Complete | invalid transition |
| W1 | hardware, fresh, moving 4.5, canStop, ready, generation current, not attempted | RequestStop; with attempted → AwaitPhysicalStop |
| W2 | W1 with any of fresh, canStop, ready or generation false | AwaitPhysicalStop |
| W3 | not moving, 0, attempted | Finalize |
| W4 | not moving, speed −∞ or −0.1, attempted | AwaitPhysicalStop |
| W5 | moving, speed +∞ | AwaitPhysicalStop |
| W6 | simulator mode, everything false | Finalize |

### 11.11 Telemetry snapshot

| # | Given | Expected |
|---|---|---|
| T1 | captured at t; speed observed t − 2 s; incline timestamp null | speed age 2 s; incline age null |
| T2 | every observation at t + 1 s (future) | all ages null (not fresh) |
| T3 **[NEW]** | `2ACD` with speed only, then inclination only | each field's timestamp advances only with its own notification |
| T4 **[NEW]** | speed 25 with a range max of 20 | telemetry null; state Faulted "implausible telemetry" |

### 11.12 Reconnect policy

| # | Given | Expected |
|---|---|---|
| B1 | active, enrollment id `00112233-4455-6677-8899-aabbccddeeff`, attempts 1..7 | 1–1.5 s, 2–2.5, 4–4.5, 8–8.5, 10–10.5; attempts 5, 6, 7 identical |
| B2 | two different enrollment ids, attempt 1 | different delays |
| B3 | empty enrollment id; attempt 0 | invalid |
| B4 | idle, attempt 20 | exactly 5 min |
| B5 | idle, attempts 1..6 | 1, 2, 4, 8, 16, 32 s (each + ≤ 0.5 s) |
| B6 **[NEW]** | an attempt stable for ≥ 30 s then failing | the next delay uses attempt 1 |
| B7 **[NEW]** | an evidence/capability update during a session | no worker restart; the generation is unchanged |

### 11.13 Enrollment

| # | Given | Expected |
|---|---|---|
| E1 | a treadmill without mode or capabilities | invalid |
| E2 | an HR device with treadmill mode or capabilities | invalid |
| E3 | a treadmill with reported speed support, evidence ProtocolReported | stored; `canStart` false |
| E4 | enroll treadmill; replay the same op id | both succeed with the same result; protocol `horizon-omega-z`; mode Ftms; `canStart` false |
| E5 | a second treadmill (new op id, other address) | conflict |
| E6 | anonymous `1816`+`1826`, mode Ftms | protocol `horizon-omega-z`; evidence Unknown; every `can*` false |
| E7 | scan: anonymous complete, named other, anonymous incomplete | only the anonymous complete device is offered (role Treadmill) |
| E8 **[NEW]** | an enrollment mutation while Armed, Running or Paused | refused |
| E9 **[NEW]** | HardwareVerified; the reconnect reads firmware `V10.23.18` | evidence PassivelyObserved; all `can*` false, persisted **before** Ready |
| E10 **[NEW]** | HardwareVerified; the DIS firmware read fails | the attempt fails; no Ready |

### 11.14 Maintenance

Translate 08 §13's five-step scenario as-is.

## 12. Test checklist

Codecs and parsers:

- [ ] Every `data/ftms/*.json` file is loaded by a parameterized test (except the reference-only
      encoder, which is loaded only to prove no production encoder for `FFF3` exists).
- [ ] No production code path can write `FFF1`/`FFF3`, write `2AD9` outside the command writer, or
      emit `08 02`.

Command writer:

- [ ] All of C1–C29, including the zero-write assertions on every pre-write rejection.
- [ ] Late or unrelated `2AD9` responses are ignored (C21).
- [ ] Unknown is never retried by the writer, the session engine, automation, recovery or the UI.
      This is a scenario test with an Unknown result followed by 60 s of simulated time.
- [ ] Operation ledger replay, the in-flight duplicate, pre-admission cancel, the 15-min expiry and
      the 4096 bound. Durable receipt replay after a process restart.

Session and safety:

- [ ] Normalization table (§4.3); never more aggressive; the profile max speed is honoured.
- [ ] Rate limiting: 1 in flight + 1 pending per axis; a long-press ends at the last displayed
      value; Stop pre-empts pending targets.
- [ ] 800 ms engine lockout across phone + web; STOP is never locked out; stale-version presses
      are rejected.
- [ ] Start: one `07`; Running only after 3 samples > 0.3; console start while Armed; a Start
      press while a Start is in flight sends nothing.
- [ ] Pause = Stop: `Pausing` → Paused only after stopped telemetry; an Unknown pause shows
      "Couldn't confirm" with no Resume; the cursor and plan position are unchanged; the paused
      interval is not moving time; recovers as Paused after process death.
- [ ] Resume re-applies the effective target (override, ramp, HR cap with dwell reset).
- [ ] Console stop while Running → Paused; console start while Paused → Running.
- [ ] End only after a confirmed stop, or through the >30 s no-telemetry path (writes
      `stop-unconfirmed-by-telemetry`, no command).
- [ ] Reset progress never starts motion. Discard persists the H10 cleanup first.
- [ ] Completion: at most one Stop; Completed only after stopped telemetry; W1–W6.
- [ ] Lease: L1–L8; Stop allowed for any paired Operator without the lease **[NEW]**; web motion
      off by default; web Start needs its own toggle.

Telemetry and connection:

- [ ] Per-field timestamps (T1–T4); a stale field never confirms; implausible data is never clamped.
- [ ] Gap handling: no fabricated samples; 2-sample reconciliation on the same new generation;
      R1–R16.
- [ ] Restart recovery: fingerprint check; 30 s movement deadline; explicit Resume planned
      controls; reboot → Interrupted.
- [ ] Backoff B1–B7; no treadmill scans during a run; the scan budget is respected.
- [ ] Downgrade on identity mismatch is durable before Ready (E9, E10).
- [ ] Evidence/capability writes never restart BLE (B7). This is the regression for the Stage 3
      Unknown.
- [ ] Every §9 failure state renders persistently with its action (screenshot + accessibility test).

Maintenance:

- [ ] The maintenance scenario; Simulator, SystemTest, Legacy and running sessions are excluded.

Hardware (owner-attended, per 08 §11.4):

- [ ] Stages A–G, each with evidence, a fixture, a test and approval. Remote controls stay
      disabled until every stage is approved for `(OMEGA Z, V10.23.17, android host stack)`.
