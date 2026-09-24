---
title: 06 — Profiles and heart rate
type: spec
status: draft-v1
audience: agent-and-developer
updated: 2026-09-24
---

# 06 — Profiles and heart rate

This document covers:
- runner profiles and heart-rate zones (§2–§3);
- Run-screen experience preferences and audio cues (§4);
- heart-rate sensors: classification, validity, selection and fallback (§5);
- how a session consumes HR (§6);
- the HR speed controller that adjusts treadmill speed to keep HR in a target band (§7).

Legacy tests are translated in §8, and §9 is the checklist.

**How to read the rules:**
- **[current]** marks the reference behaviour of the Windows app.
- **[rewrite]** marks an owner decision from the plan ([00](00-plan.md)) that changes it.
- Entity fields are in [01](01-data-model.md) §4.5–4.9.
- The session lifecycle is in [05](05-sessions-and-recording.md).
- Command confirmation is in [09](09-safety-and-command-contract.md).
- Polar H10 specifics (SDK, recording) are in [10](10-polar-h10.md).

---

## 1. Constants
| Name | Value |
|---|---|
| HR validity range | **30–250 bpm** inclusive |
| HR freshness limit | **5 s** (an age of 5.0 s is still fresh; 5.1 s is stale) |
| HR telemetry silence (fallback trigger) | **30 s** |
| Stable connection threshold (fallback hysteresis) | **30 s** window, ≥ 2 readings, no gap > 5 s |
| Maximum concurrent HR connections | **8** |
| Controller dwell below target | **20 s** |
| Controller dwell above target | **10 s** |
| Increase step / cooldown | default **0.2 km/h / 30 s**; bounds 0.1–0.5 km/h / 15–180 s |
| Decrease step / cooldown | default **0.5 km/h / 15 s**; bounds 0.1–1.0 km/h / 5–120 s |
| Zone count | 1–10 per profile; the suggested set has 5 |
| Assignment priority | 0–99 (lower first) |
| Cue volume | 0–100 %, default 60 |

---

## 2. Profiles (runners)

### 2.1 Fields, bounds and defaults
| Field | Rule | Default / UI |
|---|---|---|
| displayName | Required, trimmed, ≤ 100 characters; **unique case-insensitively** (`trim().uppercase()`) across all profiles, including archived ones. A duplicate is a conflict: "A profile with that name already exists." | – |
| unitSystem | Always `Metric` (a legacy field; not user-editable) | Metric |
| weightKilograms | Finite, > 0 | UI 20–350, step 0.1, default **70** |
| maximumHeartRateBpm | Optional; 1–250 | UI 50–250. Entering it suggests zones (§3.2) |
| maximumSpeedKph | Optional; finite, > 0. It caps workout targets (preflight) and every automatic command. The engine uses **20 km/h** when it is null | UI 1–30, step 0.1 |
| heartRateZones | 0–10 zones (§3.1) | Suggested from the maximum HR |
| HR controller settings | increase step 0.1–0.5 km/h; increase cooldown 15–180 s; decrease step 0.1–1.0 km/h; decrease cooldown 5–120 s | 0.2 / 30 / 0.5 / 15 (UI steps 0.1 km/h and 1 s) |

**Multiple runners:**
- A household has any number of profiles.
- Each web browser (and the phone UI) keeps an **active profile** locally. Selecting it only changes the viewer's context; it never affects a running session.
- A session belongs to the profile it was armed with, and **snapshots** that profile ([05](05-sessions-and-recording.md) §4.2). Later profile edits never change old runs.

**Write rules:**
- Create returns 201 with version 1.
- Update requires `expectedVersion > 0`. A mismatch is a conflict ("The profile changed in another client. Reload and try again."). Update replaces the whole zone set and increments the version.
- Every write carries an `operationId` and is idempotent by receipt (`profile.create`, `profile.update`, `profile.archive`).

### 2.2 Archive
- Archive requires `expectedVersion`. It sets `isArchived=true`, `archivedAt=now`, and `version+1`.
- Archived profiles:
  - are hidden from the profile list, and return 404 from get and update;
  - **keep their history**;
  - keep their name reserved;
  - cannot receive new preferences or goals (404).
- There is no delete. There is no unarchive API ([current]; the store supports it, the endpoint does not).
- UI message: "Profile archived. Its local history was retained."

### 2.3 Quick start (runner suggestion)
From the HR sensors assigned to runners, suggest who is on the treadmill:
- If a session is active: **no suggestion** ("An active session owns its runner; automatic profile switching is disabled.").
- An observation is *fresh* if the assigned sensor is currently that runner's **selected** source, and its last reading is ≤ 5 s old.
- Group the fresh observations by runner:
  - exactly 1 runner → suggest it, with `requiresConfirmation = true` ("Fresh assigned sensor {label} matches one runner.");
  - 0 → none ("No fresh assigned heart-rate sensor identifies a runner.");
  - more than 1 → none ("Multiple fresh assigned sensors are present; select a runner manually.").
- It never switches automatically.

---

## 3. Heart-rate zones

### 3.1 Validation
For each zone:
- `number` is 1–10, unique within the profile;
- `name` is non-blank, trimmed, ≤ 60 characters;
- `1 ≤ minimumBpm ≤ maximumBpm ≤ 250`.

Sorted by number, zones must **not overlap**: `zone[i].minimum > zone[i−1].maximum`. Gaps are allowed.

Errors:
- a number outside 1–10 → "Zone number must be between 1 and 10.";
- bad bounds → "Heart-rate bounds must be ordered and between 1 and 250 bpm.";
- an overlap or duplicate → "Heart-rate zones must have unique numbers and must not overlap."

### 3.2 Suggested zones from the maximum HR
- Defined for a maximum HR of 10–250. The UI offers it for **50–250**.
- Given the maximum `M`:

| # | Name | Lower | Upper |
|---|---|---|---|
| 1 | Warm up | ceil(0.50·M) | ceil(0.60·M) − 1 |
| 2 | Easy | ceil(0.60·M) | ceil(0.70·M) − 1 |
| 3 | Aerobic | ceil(0.70·M) | ceil(0.80·M) − 1 |
| 4 | Threshold | ceil(0.80·M) | ceil(0.90·M) − 1 |
| 5 | Maximum | ceil(0.90·M) | **M** |

Golden:

| M | Z1 | Z2 | Z3 | Z4 | Z5 |
|---|---|---|---|---|---|
| 150 | 75–89 | 90–104 | 105–119 | 120–134 | 135–150 |
| 185 | 93–110 | 111–129 | 130–147 | 148–166 | 167–185 |
| 190 | 95–113 | 114–132 | 133–151 | 152–170 | 171–190 |
| 200 | 100–119 | 120–139 | 140–159 | 160–179 | 180–200 |

Use exact decimal arithmetic, or compute `ceil(M × p / 100)` with integers, so float error never shifts a boundary.

**Editor behaviour:**
- **Suggested mode**: while the zones equal the suggestion for the current maximum (same count, numbers, names and bounds), changing the maximum regenerates them. The helper text reads "Z1–Z5 update automatically."
- Clearing the maximum in suggested mode clears the zones; the helper text reads "Enter your max HR to create Z1–Z5."
- Any manual edit, add or remove switches to custom mode ("Custom zones — reset anytime."). "Reset to suggested zones" regenerates the zones (enabled for a maximum of 50–250).
- "Add heart-rate zone" appends `{number: count+1, name: "Zone {count+1}", 100–120}`.
- A legacy placeholder set (exactly one zone `{1, "Zone 1", 100–120}`), or an empty set, with a valid maximum is replaced by the suggestion when the form opens.

### 3.3 How zones are used
- The session snapshot keeps the zones at arm time. Analytics, exports (FIT zone fields only when there are exactly zones 1–5), and HR-zone workout targets use the snapshot ([05](05-sessions-and-recording.md) §7.5, [07](07-exports-and-backup.md)).
- Premade plans with zone targets require the profile to contain those zone numbers ([04](04-calendar-and-plans.md)).

---

## 4. Experience preferences and audio cues

### 4.1 Options
| Option | Values | Default |
|---|---|---|
| displayStyle | `Balanced`, `LargeText`, `HighContrast` | Balanced |
| primaryMetrics | **2 or 3 distinct** of `Speed`, `Incline`, `HeartRate`, `ElapsedTime`, `Distance`, `Calories` (ordered as chosen) | Speed, HeartRate, ElapsedTime |
| cues.stepChanges | bool | true |
| cues.heartRateDeparture | bool | true |
| cues.halfway | bool | true |
| cues.connectionProblems | bool | true |
| cues.completion | bool | true |
| cues.volumePercent | 0–100 | 60 |

**API semantics:**
- Values are parsed by name, case-insensitively. An unknown style gives "DisplayStyle must be Balanced, LargeText, or HighContrast."; an unknown metric gives "Unsupported live metric '{x}'." (400).
- Fewer than 2, more than 3, or duplicate metrics: "Choose two or three distinct primary metrics." (400).
- A volume outside 0–100: 400.
- Get on a profile without saved preferences returns the defaults with `version 0` and `updatedAt` = the Unix epoch.
- Save with `expectedVersion` null or 0 creates version 1. Afterwards the version must match, and each save increments it.
- The profile must exist and be non-archived.

### 4.2 Cue triggers (the live Run screen)
When the live session snapshot changes (previous → incoming), play one cue for each of these that holds and is enabled:

| Cue | Fires when |
|---|---|
| stepChanges | `previous.currentStep.index` exists and differs from `incoming.currentStep.index` |
| heartRateDeparture | The previous snapshot had an HR reading and an HR target, the HR was **within** the target (`min ≤ hr ≤ max`, a null bound counts as satisfied), and the incoming HR is **outside** it |
| halfway | Once per screen session: `incoming.remaining` is known and `elapsed ≥ remaining` (the elapsed time has reached the remaining planned time) |
| connectionProblems | The UI loses its live connection to the engine. **[rewrite]** Also: treadmill telemetry gap start, and HR source loss |
| completion | The previous state was non-terminal and the incoming one is terminal |

**The cue sound [current]:**
- One 660 Hz sine tone of 0.2 s.
- Gain envelope: from 0.0001, exponential ramp to `0.2 × volume%/100` over 15 ms, then exponential ramp to 0.0001 by 180 ms; stop at 200 ms.
- A volume of 0 is silent.

**[rewrite]:** the phone plays cues through the media stream (the same tone). Start only ever comes from the phone's Run console or the treadmill console ([00](00-plan.md) §5.4); the web never starts the belt. Cues never gate safety actions.

---

## 5. Heart-rate sensors

### 5.1 Classification (from the advertised name and service UUIDs)
- Heart Rate Service `0000180d-0000-1000-8000-00805f9b34fb`.
- Polar service `0000feee-0000-1000-8000-00805f9b34fb`.
- `n` = the name, trimmed and lower-cased.

| Output | Rule (the first match wins) |
|---|---|
| **Kind** | `ChestStrap` if the Polar service is advertised, or `n` contains any of: `polar h10`, `polar h9`, `chest`, `strap`, `belt`.<br>Else `Watch` if `n` contains any of: `watch`, `garmin watch`, `forerunner`, `fenix`, `fēnix`, `epix`, `vivoactive`, `vívoactive`, `venu`, `instinct`, `enduro`, `tactix`, `quatix`, `marq`, `lily`, `descent`, `approach`, `apple`, `pixel`, `galaxy`, `coros`, `suunto`.<br>Else `Sensor` |
| **Family** | `Polar` if `n` contains `polar h10`, or the Polar service is advertised, or `n` contains `polar`.<br>Else `Garmin` if `n` contains any of: `garmin`, `forerunner`, `fenix`, `fēnix`, `epix`, `vivoactive`, `vívoactive`, `venu`, `instinct`, `enduro`, `tactix`, `quatix`, `marq`, `lily`, `descent`, `approach`.<br>Else `Other` |
| **Preferred Polar** | `n` contains `polar h10`, or the Polar service is advertised |
| **Enrollment priority hint** | 0 = preferred Polar, 1 = chest strap, 2 = watch, 3 = other |

- The stored kind and family can be set at enrollment. When the stored value is `Sensor`/`Other` or null, the **effective** value is re-classified from the display name.
- Golden: "Garmin Fenix 8", "Garmin fēnix 8", "Garmin Vivoactive 5" and "Garmin vívoactive 6" → Watch / Garmin. "Polar H10 ABCD1234" → ChestStrap / Polar.

### 5.2 Measurement parsing and validity
Heart Rate Measurement (`2A37`); byte layouts and golden vectors are in [10](10-polar-h10.md) and `data/polar`.

The flags byte:

| Bit | Meaning |
|---|---|
| 0 | UINT16 heart rate |
| 1 | contact detected |
| 2 | contact supported |
| 3 | energy expended present |
| 4 | RR present (1/1024 s units) |

Contact state:
- `NotSupported` when bit 2 = 0;
- else `Detected` when bit 1 = 1;
- else `NotDetected`.

**Signal quality per reading:**
- `ContactLost` if the contact state is `NotDetected`;
- else `Valid` if the bpm is 30–250;
- else `Invalid`.

**A source is fresh at `now`** if and only if all hold:
- its connection is `Ready`;
- the quality is `Valid`;
- the bpm is 30–250;
- `observedAt` exists;
- `0 ≤ now − observedAt ≤ 5 s`.

Only a fresh source's bpm is published (otherwise null). Invalid, stale or contact-lost HR is **stored as null** in samples and **resets the controller dwell** (§7).

### 5.3 Assignments
Each (profile, sensor) pair may have an assignment: `priority` (0–99), `autoConnect`, and `isPreferred` (at most one per profile).

- Setting a new preferred sensor clears the previous preferred assignment of that profile on other sensors (their version is incremented).
- Assignments are configured per sensor as a whole list (replace semantics). The profiles in the list must be distinct.
- Forgetting (archiving) a sensor deletes its assignments.
- **Polar-family sensors are always eligible** for automatic connection, even with `autoConnect=false`.

### 5.4 Source selection (`HeartRateSourceSelector`)
Inputs: the source snapshots, all assignments, the profile (or none), `now`, and the 5 s freshness limit. The output is at most one source. **Samples are never averaged across sources.**

**Eligibility:**
- **With a profile P:**
  - `A` = P's assignments where `autoConnect`, or where the source's family is Polar.
  - If `A` is non-empty, the eligible set is the sources joined with `A`: **only this runner's sensors, never another runner's private sensor**.
  - If `A` is empty, the eligible set is the sources that are **not assigned to anyone** (legacy shared sensors), with no assignment.
- **Without a profile** (idle, no runner): every source, each paired with its lowest-priority eligible assignment (autoConnect or Polar), if any.

**Ordering** among the eligible **fresh** sources:

| With a profile | Without a profile |
|---|---|
| 1. Preferred first | 1. Family tier |
| 2. Priority ascending (a missing assignment counts as 99) | 2. Preferred first |
| 3. Family tier | 3. Priority ascending |
| 4. Enrollment ID ascending | 4. Enrollment ID ascending |

- **Family tier:** Polar = 0; ChestStrap (non-Polar) = 1; Garmin = 2; Watch (non-Garmin) = 3; other = 4.
- The enrollment-ID tie-break is ascending lexicographic order of the lower-case canonical UUID. This equals the legacy .NET GUID order.

The first source in that order is **selected**.

**Displayed source** (when none is selected, for status only):
- Among the eligible (the profile's autoConnect assignments, or unassigned sensors when the profile has none), order: `Ready` first, then Polar, then chest strap, then other, then ID.
- Its HR is not used.

**Selection reason text:**
- "Selected the highest-priority fresh sensor assigned to this runner."
- "Sensor is enrolled but a fresh pulse sample is not available yet."
- "No automatic heart-rate sensor is assigned to this runner."

**Selection generation:**
- Per profile (and one key for "no profile"), keep `(selectedEnrollmentId, generation)`.
- Whenever the selected ID changes, including to or from none, `generation += 1`, and a diagnostic entry is recorded (the previous source's state, quality and age).
- Sessions use the generation to detect a source change (§6).

### 5.5 Connection demand, fallback and hysteresis
While a run holds its devices, HR connections are demanded **progressively**:
1. Order the profile's eligible assignments (autoConnect or Polar family): preferred first, then priority. Keep at most 8.
2. Walk the list:
   - Always demand the current entry.
   - Continue to the next entry **only if the current one "needs a fallback"**.
   - Stop at an entry that is temporarily suspended: an H10 memory transfer holds its connection, which is not evidence that a lower-priority sensor is preferable.
3. **Needs a fallback** if any of these hold:
   - it has an open BLE reliability incident;
   - it is flagged `requiresStableRecovery`;
   - its connection is `Faulted` or `Reconnecting`;
   - it is `Ready` but its latest reading is not `Valid`, the bpm is outside 30–250, there is no reading, the age is negative, or the age is **> 30 s**.

   A source that is still `Connecting` or discovering does **not** trigger a fallback.
   - The 5 s freshness limit decides whether a pulse can be *published*. It is not proof the sensor is gone; that is the 30 s silence boundary.
4. **Hysteresis:**
   - When a fallback is activated below a source, flag that source `requiresStableRecovery`, so the fallback stays connected.
   - Clear the flag only when the source becomes **durably stable** on its current connection: at least 2 readings, spanning at least **30 s**, with no inter-reading gap > 5 s (a larger gap restarts the window).
   - A preferred source must be stable for a while before a connected fallback is dropped.
   - The flag is also cleared when the source leaves the progressive list.
5. **Without a run**, the idle policy applies:
   - manually demanded sensors connect;
   - if the profile has no assignments, up to 8 unassigned HR sensors connect.
6. Explicitly disconnected and temporarily suspended sensors are never demanded.

**Reconnect backoff** (per device):
- `n` = the number of consecutive failures (≥ 1).
- Active run: `base = min(10 s, 2^min(n−1, 4) s)`, giving 1, 2, 4, 8, 10, 10 … s.
- Idle: `base = min(300 s, 2^min(n−1, 9) s)`.
- Jitter:
  - `bucket = ((idByte0 << 8) | idByte1) mod (limit + 1)` ms, where `idByte0` and `idByte1` are the first two bytes of the enrollment ID in .NET GUID byte order: the first 32-bit group in little-endian;
  - `limit` = 500 when active, else `min(500, max(0, (300 − base) × 1000))`.
- delay = base + bucket.
- **[rewrite]** Keep the active schedule 1, 2, 4, 8, 10 s (cap 10 s) with jitter, and idle up to 5 min (plan §6.1). Deterministic per-device jitter is fine.

**Reconnect identity** (after an address change, for example H10 address rotation):
- An enrolled HR sensor is re-found by, in order:
  1. the exact device ID;
  2. if allowed, the unique advertiser whose name equals the enrolled display name (case-insensitive) and that advertises HRS or the Polar service;
  3. if allowed and the effective family is not Other, the unique advertiser of the same effective family **and** kind that advertises HRS or the Polar service.
- An ambiguous result (0 or more than 1 candidates) resolves to nothing.
- Split advertisement packets are merged per device ID (the last non-blank name wins; service UUIDs are united).
- **[rewrite]** The H10 uses a filtered scan within the scan budget ([10](10-polar-h10.md)).

---

## 6. Heart rate inside a session
Evaluated every engine tick for hardware runs ([05](05-sessions-and-recording.md) §4.4):
1. **Source change:**
   - If the device snapshot's selection generation ≠ the session's, adopt the new generation, enrollment and source type, and **reset the controller completely**: dwell and cooldowns.
   - If the session had a previous selection (its generation was > 0) **and** the workout needs HR:
     - suspend automation with the reason "The heart-rate source changed; explicitly re-enable automation after confirming the new sensor.";
     - add the warning "Heart-rate source changed; automatic speed control is paused.";
     - append `session-warning {code: "heart-rate-source-changed", message: "Heart-rate source changed to {name or 'no fresh sensor'}."}`.
2. **The workout does not need HR:** `sessionHr = published bpm` (null if not fresh) and `hrAge = device age`. HR is recorded for analytics only.
3. **The workout needs HR:**
   - If the published bpm exists and the age is ≤ 5 s, use it and remove the stale warning.
   - Otherwise:
     - `sessionHr = null`;
     - add the warning "Heart-rate telemetry is stale; heart-rate automation is suspended.";
     - if Running and not already suspended, **suspend automation** (`SuspendedSafety`) with that reason.
4. The heart-rate source type shown in the UI:
   - `PolarH10` (family Polar);
   - `GarminBleBroadcast` (family Garmin);
   - `BluetoothHeartRate` (any other selected sensor);
   - `None`;
   - `Simulated` (simulator).

**Simulator HR:**
- It starts at 132 bpm.
- Setting a value requires 30–250 or null (null simulates stale telemetry, age 6 s).
- A reading older than 5 s becomes null.

---

## 7. HR speed controller

### 7.1 Modes
| Mode | Selectable | Behaviour |
|---|---|---|
| `Disabled` | yes | No decisions; dwell reset |
| `Shadow` | yes (the default at arm for HR workouts) | Computes increase/decrease decisions **without executing** them. The reason text says "Shadow-mode increase recorded without a write." (or decrease) |
| `DecreaseOnly` | yes | Executes decreases only. Increases are reported as "Increases are disabled in decrease-only mode." |
| `Full` | yes | Executes increases and decreases |
| `SuspendedManualOverride` | **no** (system) | Entered by a manual speed change. No decisions |
| `SuspendedSafety` | **no** (system) | Entered by stale HR or treadmill data, a telemetry gap, a source change, pause/stop, a rejected or unknown automated command, or restart recovery. No decisions |

**Selecting a mode** (on the phone's Run console, with version + operation ID; [current]: also the controller lease):
- The two suspended modes are rejected ("Suspended modes are system states, not selectable modes.").
- A non-HR workout accepts only `Disabled`.
- On hardware, `DecreaseOnly` and `Full` require `canSetSpeedRemotely` ("Remote speed control is not hardware verified.").

Effect:
- set the mode and the **desired mode**;
- reset the dwell;
- version+1;
- **clear the command suspension** and remove the rejected/unknown-command warnings.

Reason texts:
- Disabled: "Heart-rate automation is disabled."
- Shadow: "Shadow mode records decisions without sending speed commands."
- DecreaseOnly: "Only above-target speed decreases are enabled."
- Full: "Below-target increases and above-target decreases are enabled."

**Suspension bookkeeping:**
- Entering a suspension remembers the current selectable mode as the **desired mode**.
- A successful reconnect (ResumeAutomatically), "Resume planned controls", or a re-selection restores the desired mode.
- A manual speed override sets `SuspendedManualOverride`, with the reason "A manual speed override suspends heart-rate automation until explicitly re-enabled."

### 7.2 Controller algorithm
State: `belowSince`, `aboveSince`, `lastIncreaseAt`, `lastDecreaseAt` (all initially null).

Input:
- `now`, `mode`;
- `hr`;
- `targetMin`, `targetMax` (bpm);
- `hrAge`, `treadmillAge`;
- `currentSpeed`, `minSpeed`, `maxSpeed`, `increment`;
- `safetyReady`.

```
validate: speeds and increment finite; minSpeed >= 0; maxSpeed >= minSpeed; increment > 0; targetMin <= targetMax  (else error)
if mode in {Disabled, SuspendedManualOverride, SuspendedSafety}:
    resetDwell(); return None("Automation is {mode}.")
if not safetyReady or hr not in 30..250 or targetMin/targetMax null
   or hrAge null/negative/>5 s or treadmillAge null/negative/>5 s:
    resetDwell(); return None("Fresh heart-rate and treadmill telemetry plus a safe command context are required.")

if hr < targetMin:                                   # below target -> speed up
    aboveSince = null; belowSince ??= now
    if now - belowSince < 20 s: return None("Heart rate has not remained below target for 20 seconds.")
    if lastIncreaseAt and now - lastIncreaseAt < increaseCooldown: return None("Increase cooldown is active.")
    if mode == DecreaseOnly: return None("Increases are disabled in decrease-only mode.")
    target = align(currentSpeed + increaseStep, currentSpeed, minSpeed, maxSpeed, increment)
    if target <= currentSpeed + 0.0001: return None("The maximum permitted speed has been reached.")
    lastIncreaseAt = now; belowSince = now
    return Increase(target, execute = (mode == Full))

if hr > targetMax:                                   # above target -> slow down
    belowSince = null; aboveSince ??= now
    if now - aboveSince < 10 s: return None("Heart rate has not remained above target for 10 seconds.")
    if lastDecreaseAt and now - lastDecreaseAt < decreaseCooldown: return None("Decrease cooldown is active.")
    target = align(currentSpeed - decreaseStep, currentSpeed, minSpeed, maxSpeed, increment)
    if target >= currentSpeed - 0.0001: return None("The minimum permitted speed has been reached.")
    lastDecreaseAt = now; aboveSince = now
    return Decrease(target, execute = (mode in {Full, DecreaseOnly}))

resetDwell(); return None("Heart rate is within target.")
```
The reason strings for executed decisions are "Below-target dwell and increase cooldown passed." and "Above-target dwell and decrease cooldown passed."

**Alignment to the machine increment (never more aggressive):**
```
align(requested, current, min, max, inc):            # exact decimal arithmetic
  bounded = clamp(requested, min, max)
  steps   = (bounded - min) / inc
  steps   = requested >= current ? floor(steps) : ceil(steps)
  return min + steps × inc
```
- Increases round **down** and decreases round **up** to the grid anchored at the treadmill minimum.
- Examples (min 0.8, inc 0.1):
  - 1.05 + 0.2 = 1.25 → **1.2**;
  - 2.05 − 0.5 = 1.55 → **1.6**.

**Resets:**
- `resetDwell()` clears `belowSince` and `aboveSince` and keeps the cooldowns.
- `reset()` also clears `lastIncreaseAt` and `lastDecreaseAt`. It is used when the HR source changes.
- The session calls `resetDwell()` on:
  - a mode change;
  - a manual override;
  - a pause or stop;
  - an Unknown or rejected command;
  - a progress reset;
  - any automation suspension.

### 7.3 Session integration
Each tick, for a Running session whose current step has an HR speed directive ([05](05-sessions-and-recording.md) §4.8 order):
- Skip if:
  - commands are suspended;
  - the treadmill age is > 5 s;
  - the generation doesn't match;
  - the treadmill speed range is unknown.
- If HR is needed and (the HR is null, the age is null, or the age is > 5 s): suspend with `SuspendedSafety` and skip.
- **The target band:**
  - `heartRate` directive: `[minimumBpm, maximumBpm]` from the step.
  - `heartRateZone` directive: **[current]** not resolved to bpm, so the controller always returns "Fresh heart-rate and treadmill telemetry plus a safe command context are required." and zone steps run at their initial speed with no automation. **[rewrite, recommended]** Resolve the zone number against the session's zone snapshot: `[zone.minimumBpm, zone.maximumBpm]`. If the zone is missing, stay passive and show "Zone {n} is not defined for this runner." This is an open owner question ([00](00-plan.md) §16).
- **The speed band:**
  - `min = max(step.minimumKph, range.minimum)`;
  - `max = min(step.maximumKph, profile.maxSpeed (20 if null), range.maximum)`;
  - `increment = range.increment`.
- `safetyReady = canSetSpeedRemotely AND (simulator OR the treadmill connection is Ready)`.
- The decision reason is shown as the automation reason.
- If `execute`, issue SetSpeed(target) with origin `HeartRateAutomation`. On confirmation, the accepted value becomes the speed override, so the requested speed shows the new target.
- A rejected or unknown automated command suspends automation (`SuspendedSafety`).
- **[rewrite]** On Resume after a pause, the HR segment target is `min(last controller output, segment max)`, with the dwell reset ([05](05-sessions-and-recording.md) §4.5).

---

## 8. Legacy tests as given/expected tables

### 8.1 Profiles and zones
| # | Given | Expected |
|---|---|---|
| PR-1 | Zones {1 Easy 100–130}, {2 Tempo 131–160} | accepted; 2 zones |
| PR-2 | Zones {1 100–140}, {2 140–160} | rejected (overlap: 140 is not > 140) |
| PR-3 | Weight NaN / +∞ / 0 | rejected |
| PR-4 | Suggested zones for 190 | 95–113, 114–132, 133–151, 152–170, 171–190 with the names Warm up, Easy, Aerobic, Threshold, Maximum |
| PR-5 | Controller settings (0.09, 30, 0.5, 15), (0.2, 14, 0.5, 15), (0.2, 30, 1.01, 15), (0.2, 30, 0.5, 121) | each rejected |
| PR-6 | Profile controller (0.3, 45, 0.7, 20), then arm | the session snapshot holds 0.3 / 45 / 0.7 / 20 |

### 8.2 Preferences
| # | Given | Expected |
|---|---|---|
| PF-1 | A new profile, get the preferences | version 0; Balanced; 3 metrics |
| PF-2 | Save {LargeText, [Distance, Incline], cues step off / hr on / halfway off / conn on / completion on, 75} with expected 0 | version 1; the values round-trip exactly |
| PF-3 | Save metrics [Speed] | 400 |
| PF-4 | Save volume 101 | 400 |
| PF-5 | Metrics [Speed, Speed] | rejected ("two or three distinct") |
| PF-6 | HighContrast [HeartRate, ElapsedTime, Speed], 65 | accepted |

### 8.3 Source selection (freshness 5 s; every source Ready, Valid, 132 bpm, observed now, unless stated; runners A and B are two profiles)
| # | Sources | Assignments (profile, priority, preferred, autoConnect) | Profile | Selected |
|---|---|---|---|---|
| HS-1 | Polar H10 (Polar), fēnix 8 (Garmin watch) | A→Polar (1, preferred), A→fēnix (0) | A | **Polar** (preferred beats priority) |
| HS-2 | Polar (age 5.1 s), fēnix | A→Polar (0, preferred), A→fēnix (1) | A | **fēnix** (fallback) |
| HS-3 | vívoactive (Garmin) | B→vívoactive (0, preferred) | A | **none** (never another runner's sensor) |
| HS-4 | Polar | none | A | **Polar** (unassigned shared sensor) |
| HS-5 | Polar (age exactly 5.0 s) | A→Polar (0, preferred) and, separately, autoConnect=false | A | **Polar** in both cases (the boundary is fresh; Polar is always eligible) |
| HS-6 | Polar with quality ContactLost / Invalid / Unavailable | A→Polar | A | none |
| HS-7 | Polar with 29 / 251 bpm | A→Polar | A | none |
| HS-8 | Polar (Polar family), Garmin watch (131 bpm) | P→Polar (20, not preferred, autoConnect false), P→Garmin (0, preferred, autoConnect true) | P | **Garmin** (an explicit preference outranks the family) |

### 8.4 HR speed controller (target 130–150 bpm, speed band 0.8–10, increment 0.1, ages 0, safetyReady, default settings 0.2/30/0.5/15)
| # | Sequence | Expected |
|---|---|---|
| HC-1 | t0: HR 120, Full, speed 1.0 → None. t0+20 s: HR 120, speed 1.05 | Increase to **1.2**, execute |
| HC-2 | t0: HR 160, DecreaseOnly, speed 2.05 → None. t0+10 s: same | Decrease to **1.6**, execute |
| HC-3 | Shadow: t0 HR 120; t0+20 HR 120 | Increase, **execute=false** |
| HC-4 | Then t0+21: HR age 6 s (Full) → None (dwell reset); t0+40: HR 120, Full | None (a new dwell started at 40) |
| HC-5 | Realistic: 4.5 km/h, HR 120 every second 0..20 s (Full) | Increase to **4.7** at 20 s |
| HC-6 | t=40, HR 120, speed 4.7 | None, the reason contains "cooldown" |
| HC-7 | t=50, HR 120, 4.7 | Increase to **4.9** (cooldown 30 s from 20; the dwell started at 20) |
| HC-8 | t=51, HR 140 | None (within target; dwell reset) |
| HC-9 | HR 160 every second from 52..62 at 4.9 | Decrease to **4.4** at 62 s |
| HC-10 | resetDwell; t=63 HR 120 → None; t=82 → None; t=83 → | Increase (20 s dwell from 63, and the cooldown since 50 has passed) |
| HC-11 | After an increase at 20 s: resetDwell; t=21 HR 120 at 4.7 → None; t=41 | None, reason "cooldown" (resetDwell keeps the cooldown) |
| HC-12 | HR 0 at t0 and t0+20 | None, not executed |
| HC-13 | HR age −1 s, or treadmill age −1 s, at t0 and t0+20 | None |
| HC-14 | Speed already at the maximum (10.0), below target for 20 s | None ("The maximum permitted speed has been reached.") |

### 8.5 Classification
| Name | Kind | Family |
|---|---|---|
| Polar H10 ABCD1234 | ChestStrap | Polar |
| Garmin Fenix 8 / Garmin fēnix 8 / Garmin Vivoactive 5 / Garmin vívoactive 6 | Watch | Garmin |
| Apple Watch | Watch | Other |
| Wahoo TICKR (no Polar service) | Sensor | Other |
| HRM-Pro chest strap | ChestStrap | Other |

---

## 9. Test checklist
- [ ] **PHR-01** Profile validation and defaults (§2.1); unique names including archived; update replaces the zones; version and receipt semantics.
- [ ] **PHR-02** Archive hides the profile, keeps its history, reserves its name, and blocks new preferences and goals.
- [ ] **PHR-03** Zone validation and the suggested-zone generator match every golden row in §3.2 exactly (no float drift).
- [ ] **PHR-04** The zone editor's suggested/custom mode, add-zone defaults and placeholder replacement.
- [ ] **PHR-05** Preferences: defaults (version 0), 2–3 distinct metrics, volume 0–100, version checks, names case-insensitive on input.
- [ ] **PHR-06** Cue triggers fire exactly as in §4.2, once per trigger; halfway once per session; the tone matches the envelope.
- [ ] **PHR-07** Classification matches §5.1 and §8.5.
- [ ] **PHR-08** HR validity: contact lost, out-of-range and stale readings are never published or recorded; a 5.0 s age is fresh, 5.1 s is stale.
- [ ] **PHR-09** The selector passes §8.3; there is no averaging; the tie-break is deterministic.
- [ ] **PHR-10** Fallback demand and hysteresis: a fallback connects only on the §5.5 conditions; the preferred sensor must be stable for 30 s (≥ 2 readings, no gap > 5 s) before the fallback is released; an H10 memory suspension stops the progression.
- [ ] **PHR-11** A generation change resets the controller, suspends automation, and writes the `heart-rate-source-changed` event exactly once per change.
- [ ] **PHR-12** The controller passes §8.4, including alignment, cooldown preservation over resetDwell, and the shadow and decrease-only rules.
- [ ] **PHR-13** Mode selection rules (suspended modes not selectable; a non-HR workout allows only Disabled; hardware verification); suspension and desired-mode restoration.
- [ ] **PHR-14** Session integration: the speed band is clamped to the profile and treadmill; a manual override gives SuspendedManualOverride; stale HR gives SuspendedSafety; a confirmed automated speed becomes the requested speed.
- [ ] **PHR-15** [rewrite, recommended] HR-zone steps resolve against the snapshot zones; a missing zone stays passive.
- [ ] **PHR-16** Reconnect backoff schedule and identity resolution (§5.5).
