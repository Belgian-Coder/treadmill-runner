# 04 — Calendar, training plans and the premade catalog

This spec defines everything about **what a runner trains and when**:

- recurring calendar series;
- ordered training plans (programs) and their runs;
- schedule adjustments;
- the Today recommendation;
- the premade catalog and its installation;
- goals and progression advice;
- the idempotency and versioning rules that protect every write.

It describes the behaviour of the current app exactly. Where the Kotlin app must deliberately differ, the text says **Decision** and gives the reason.

Related specs: workout definitions, canonical JSON and hashing are in [02-workouts.md](02-workouts.md). Session states and origins are in [05-sessions-and-recording.md](05-sessions-and-recording.md). Profiles, HR zones and the HR speed controller are in [06-profiles-and-heart-rate.md](06-profiles-and-heart-rate.md). The catalog data is in [data/premade/](data/premade/README.md).

**Contents**

1. Concepts and ownership
2. Dates, weekday masks and time zones
3. Calendar series
4. Programs (training plans)
5. Program runs, projection and schedule adjustments
6. Advancement and progress
7. Premade catalog and installation
8. Today recommendation
9. Goals and progression recommendations
10. Operation receipts, expected versions and idempotency
11. Reference: legacy HTTP surface
12. Test tables (given → expected)
13. Test checklist for the Kotlin implementation

---

## 1. Concepts and ownership

| Concept | Answers | Stored as |
|---|---|---|
| **Calendar series** | *What is scheduled on a date?* A weekly recurrence of one or more alternative workouts | `CalendarSeries` + options + exceptions + exception options |
| **Training day selection** | *Which alternative did the runner pick for that date?* | `TrainingDaySelections`, one per runner and date |
| **Program** (training plan) | *Which exact workout comes next?* An ordered list of items | `WorkoutPrograms` → immutable `WorkoutProgramRevisions` → `WorkoutProgramItems` → `WorkoutProgramItemAlternatives` |
| **Program run** | *Where is this runner in the plan, and on which dates?* | `WorkoutProgramRuns`, plus sparse `WorkoutProgramScheduleOverrides` and `WorkoutProgramExtraOccurrences` |
| **Premade installation** | *Which catalog template did this runner add?* | `PremadePlanInstallations` |
| **Goal** | Weekly or monthly targets | `LocalGoals` |
| **Progression recommendation** | Deterministic advice after one session, and the runner's decision | `ProgressionRecommendations` |

### 1.1 Ownership rules (all mandatory)

- A calendar series belongs to exactly one runner (`userProfileId`). It can never be moved to another runner: an update with a different profile is rejected.
- A program revision is either **personal** (`ownerProfileId` set) or **household** (`ownerProfileId` null).
  - A runner sees household programs and their own personal programs, never another runner's.
  - Starting a personal program for another runner is refused with **403**.
- **Premade installations are always personal**: owner = the runner who installed them.
  - Their generated workouts are **plan-internal** and never appear in the workout library, the manual-workout selector, the calendar-series editor or the "reuse a recent run" list.
- There is **at most one active run per runner**. Starting or restarting any program abandons the runner's current active run; its history stays.
- Program runs, overrides, extra occurrences, selections, goals and recommendations are all runner-scoped.
  - Every read and write takes the runner ID.
  - A run ID paired with another runner's ID behaves as **not found (404)**. It is never a permission leak.
- Calendar is a **view-and-manage** surface.
  - Creating a recurring schedule or starting or scheduling a plan happens from the **Plan/Workouts** area.
  - Calendar only moves, skips, repeats, restores, deletes and changes training days.
- No planning action ever prepares a session, acquires treadmill control, sends a Bluetooth command or starts the belt.

---

## 2. Dates, weekday masks and time zones

- **Dates are local calendar dates** (`LocalDate`, ISO `yyyy-MM-dd`). Recurrence is evaluated on local dates only.
  - A weekly Sunday series stays on Sundays across DST changes, because no clock time is involved.
- **Weekday mask** (7 bits):

| Day | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|---|---|---|---|---|---|---|---|
| Bit value | 1 | 2 | 4 | 8 | 16 | 32 | 64 |

  - Valid masks are 1–127.
  - Common values: `37` = Mon+Wed+Sat, `69` = Mon+Wed+Sun, `44` = Wed+Thu+Sat, `21` = Mon+Wed+Fri.
  - Weekday index for a date: `(isoDayOfWeek − 1)`, so Monday = 0. The flag is `1 << index`.
- **Weekday rotation** is used when a series shifts by `offset` days:
  ```
  n = ((offset mod 7) + 7) mod 7
  rotated = ((mask << n) | (mask >> (7 − n))) & 127
  ```
  - The input mask must be 1–127, otherwise it is invalid.
  - Examples: rotate(Mon, +1) = Tue; rotate(Sun, +1) = Mon; rotate(Mon, −1) = Sun; rotate(Mon+Wed, +2) = Wed+Fri; rotate(all, +12) = all.
- **Time zones:**
  - A calendar series stores a `timeZoneId` label (non-blank, ≤ 100 chars). It is **not** used for evaluation and is not validated against a tz database.
  - A program run schedule stores a `timeZoneId` that **must** be a known IANA ID (validated when the run is created).
  - "Today" for a scheduled run is the current instant converted to that zone. For example, 2026-08-09T22:30Z in Europe/Brussels is 2026-08-10, and 2026-08-10T02:00Z in America/New_York is 2026-08-09.
- **Date range limits:** the maximum supported date is 9999-12-31. Every projection must stop at the maximum date without overflowing, and a one-day read at 9999-12-31 must succeed and return nothing.
- **Kotlin:** use `java.time.LocalDate`, `ZoneId.of(...)` and `DayOfWeek`. A series time zone that `ZoneId` does not know is still accepted as a label.

---

## 3. Calendar series

### 3.1 Data

| Field | Type | Rules |
|---|---|---|
| `id` | UUID | Non-empty |
| `scheduleGroupId` | UUID | Non-empty. **Defaults to `id`** when a series is created. All segments produced by "move this and later" share it. |
| `userProfileId` | UUID | Non-empty. The runner must exist and not be archived. Immutable after creation. |
| `name` | string | Trimmed, non-blank, ≤ 160 |
| `timeZoneId` | string | Trimmed, non-blank, ≤ 100 (a label) |
| `startDate` | date | – |
| `endDate` | date or null | ≥ `startDate` |
| `intervalWeeks` | int | 1–52 |
| `weekdayMask` | int | 1–127 |
| `alternatives` | list of `{workoutRevisionId, displayOrder}` | At least one. Revision IDs unique, display orders unique, `displayOrder ≥ 0`. Every revision must exist. |
| `exceptions` | list of `{date, kind, alternatives}` | At most one per date. `Skip` has no alternatives. `Add` and `Replace` have at least one, with the same uniqueness rules. |
| `version` | int | Starts at 1. +1 on every change to this row or its children. Optimistic concurrency token. |
| `createdAtUtc` | instant | – |

- Exception `kind` is parsed case-insensitively from `Skip | Replace | Add`. Numeric strings are rejected.
- A stored kind that fails to parse makes reading the series fail. That is a data error, not a silent skip.
- Exceptions also have an optional `note` (≤ 500) in storage. It is unused by the logic and must be preserved.

### 3.2 Recurrence: `occursOn(date)`

```
occursOn(date):
  if date < startDate                       → false
  if endDate != null and date > endDate     → false
  if weekday(date) not in weekdayMask       → false
  anchorWeek    = startDate − daysSinceMonday(startDate)
  candidateWeek = date − daysSinceMonday(date)
  weeks = (candidateWeek − anchorWeek) / 7          // exact integer
  return weeks mod intervalWeeks == 0
```

The interval is anchored to the **Monday of the start date's week**. Example: start Mon 2026-01-05, interval 2, Mon+Wed → 01-05 ✓, 01-07 ✓, 01-12 ✗, 01-19 ✓.

### 3.3 Resolving a day: `resolveDay(allSeries, profileId, date)`

```
options = []
for series in allSeries where series.userProfileId == profileId:
  ex = series.exceptions.find(date)          // at most one
  base = series.recurrence.occursOn(date)
  if ex?.kind == Skip: continue
  if base and ex?.kind != Replace:
      add series.alternatives                 // tagged with series.id
  if ex?.kind == Add or (base and ex?.kind == Replace):
      add ex.alternatives
dedupe by (seriesId, workoutRevisionId), keeping the lowest displayOrder
sort by displayOrder, then seriesId, then workoutRevisionId
```

- **Replace only applies on a base occurrence.** A Replace on an off-day produces nothing.
- **Add** works on any date, including off-days and days after the end date.
- **Add on a base day** yields base alternatives + added alternatives, deduplicated.
- UUID ordering is the order of their canonical lower-case string form. That is equivalent to the legacy ordering.
- `resolveRange(from, through)` requires `through ≥ from`. It returns only the days that have at least one option, in date order, and stops at `through` without computing `through + 1` (safe at the maximum date).

### 3.4 Training day selection

- One row per `(userProfileId, date)`: `{calendarSeriesId, workoutRevisionId, selectedAtUtc}`. It is upserted.
- Saving is valid only if `(seriesId, workoutRevisionId)` is one of `resolveDay(runner's series, date)`'s options. Otherwise the request is rejected (400 "Choose an effective workout option for that profile and date.").
- Selections are deleted:
  - when an occurrence of that series on that date is moved or deleted (3.6);
  - for dates ≥ source date across the whole group on "move this and later";
  - for all group segments on "delete group".
- A series update does **not** delete selections. A stale selection simply stops matching; the range read shows `isSelected = false`.

### 3.5 Create and update

- **Create:** new ID, `scheduleGroupId = id`, version 1. Validate references first: the runner must exist and not be archived (404) and every revision must exist (404). Success is 201.
- **Update (full replace):**
  - `expectedVersion > 0` is required (400).
  - The series must exist (404).
  - The runner must equal the stored owner (400 "A calendar schedule cannot be transferred to another profile.").
  - References are validated as for create.
  - A stored version different from `expectedVersion` gives 409.
  - The update replaces name, time zone, recurrence, alternatives and exceptions. It keeps `scheduleGroupId`. Version +1.

### 3.6 Occurrence scopes

The UI always asks for the scope before writing. There are four:

| Scope | Operation | Effect |
|---|---|---|
| **Move only this session** | `move(seriesId, sourceDate, targetDate, moveFollowing=false, expectedVersion)` | Upsert a `Skip` exception at source and an `Add` exception at target. The Add carries the **effective alternatives of the source date** with their display orders. Version +1. Delete this series' selections on source and target. |
| **Move this and later** | `move(…, moveFollowing=true, expectedVersion, expectedSegments[])` | Split or shift the recurrence (algorithm below). All resulting segments share `scheduleGroupId`. Delete the group's selections with date ≥ source. |
| **Delete only this session** | `deleteOccurrence(seriesId, date, expectedVersion)` | Upsert a `Skip` exception at date. Version +1. Delete this series' selection on that date. |
| **Delete complete workout group** | `deleteGroup(seriesId, expectedSegments[])` | Delete every segment in the group, with their options, exceptions and the group's selections. It never deletes workout revisions or session history. |

**Upsert exception** means: if the date already has an exception, replace its kind and options; otherwise create one.

#### Move validation, in this order

1. `sourceDate ≠ targetDate` (400 "Choose a different date for the moved session."), and `|target − source| ≤ 365` days (400).
2. The operation-receipt check (section 10).
3. The series exists (404). Its version equals `expectedVersion` (409).
4. Load all of the runner's series. The **group** is the series with the same `scheduleGroupId`.
5. For moveFollowing: `expectedSegments` must list **exactly** every group segment with its current version: same count, every ID present, every version equal. Otherwise 409 ("The workout group changed after it was loaded.").
6. The series must have at least one effective option on `sourceDate` (400 "The selected schedule has no session on the source date.").
7. For moveFollowing: the source must be a **base** occurrence (`occursOn(source)`). Otherwise 400 "An individually added session can only be moved by itself; it cannot shift the recurring workout group."
8. Target collisions (occupied = `resolveDay` has any option):
   - Move only: if the target is occupied by **any** series of the runner (own group or another) → 400 "The target date already contains a session from this workout group."
   - Move this and later: if the target is occupied by **another group** → 400 (same message). If the move is **backward** (target < source) and the target is occupied by the **own group** → 400. A forward target occupied by the own group is allowed, because the group itself shifts.
9. For a backward moveFollowing, the **overlap guard**:
   - `preserved` = the group's other segments that start before the source, plus the selected segment truncated to end at `source − 1`, keeping only exceptions < source.
   - `shifted` = the continuation (defined below), plus every later group segment shifted by the offset.
   - If any date in `[target, source − 1]` is an occurrence in both `preserved` and `shifted` → 400 "Moving this and later sessions to that earlier date would overlap existing sessions in the workout group. Move only this session or choose a later date."
10. For moveFollowing, the **cross-group guard**: if any shifted segment has any date on which both it and some series of another group have an option → 400 "Moving this and later sessions would overlap another workout group. Choose dates that remain empty for the entire shifted sequence." (overlap algorithm below).

#### Move this and later: algorithm

```
offset = target − source (days)
shiftedEx = selected.exceptions where date ≥ source, each date + offset
newMask   = rotate(selected.weekdayMask, offset)
newEnd    = selected.endDate + offset (null stays null)
if selected.startDate < source:                     // split
    remove the selected exceptions with date ≥ source
    selected.endDate = source − 1 ; selected.version += 1
    create continuation: id = new UUID (client-independent), same profile, name, timeZone, group,
        start = target, end = newEnd, interval = same, mask = newMask,
        alternatives = selected.alternatives, exceptions = shiftedEx, version = 1
else:                                               // the selected segment starts at the source
    shift the selected segment wholesale: start += offset, end += offset, mask = rotate,
        every exception date += offset, version += 1
for every other group segment with startDate ≥ source:
    shift it wholesale the same way, version += 1
delete the TrainingDaySelections of (group IDs + continuation ID) with date ≥ source
```

Worked example: a Monday series starting 2026-08-10. After a single move of 08-10 to 08-11 the version is 2. Then "move this and later" from 08-17 to 08-19:
- the original segment ends 08-16 (version 3);
- a new continuation starts Wed 08-19 with mask Wed (version 1), in the same group;
- 08-17 is empty, 08-19 and 08-26 have sessions.

#### Overlap between two recurring series (cross-group guard)

- `from` = the later of the two start dates; `through` = the earlier of the two end dates (unbounded → max date). If `through < from`, there is no overlap.
- `period = lcm(intervalA, intervalB) × 7` days.
- For **every** date `d` in `[from, min(from + period − 1, through)]` where both recurrences occur: step `d, d + period, …` up to `through`. If on any stepped date both series have an option (after exceptions), they overlap.
- Also check every exception date of either series inside `[from, through]`.
- **Decision:** the legacy code only stepped from the *first* common date in the window. The Kotlin app checks every common residue, which is stricter and never less safe.

#### Delete group

- Seed series not found → store a receipt with status 404 and return 404.
- `expectedSegments` must match every group segment and version exactly → else 409.
- Delete selections, then all segments (cascade), in one transaction.

### 3.7 The merged calendar read (range)

`range(profileId, from, to)` requires `to ≥ from` and at most **62 days inclusive**; otherwise 400.

For each date, the options are:
1. **Program occurrences** of the runner's active *scheduled* runs (section 5). They come in projection order: date, then position, then non-repeat before repeat. Each item emits its primary (`displayOrder 0`) and then its alternatives in display order.
2. **Calendar series options** from `resolveDay`.

Days with no options are omitted. Options whose workout revision cannot be found are skipped silently.

Each option carries these fields:

| Field | Calendar series option | Program option |
|---|---|---|
| `seriesId` | series ID | run ID |
| `scheduleGroupId` | group ID | run ID |
| `scheduleName` | series name | program name |
| `workoutRevisionId`, `workoutName` (definition title), `revisionNumber`, `displayOrder` | ✓ | ✓ (primary 0) |
| `isSelected` | matches the stored day selection | `true` only when it is not a repeat **and** the item has no alternatives |
| `source` | `"Calendar"` | `"Program"` |
| `programRunId`, `programItemId`, `programPosition`, `programTotal` (item count), `weekNumber`, `phase`, `programRunVersion`, `programWeekdayMask` | – | ✓ |
| `isRepeat`, `extraOccurrenceId` | – | ✓ (repeats only) |
| `originalDate` | – | the generated date for base items; null for repeats |
| `isCompleted` | – | `true` for the base (non-repeat) occurrence of an item that has a Completed linked session. **Repeat occurrences never inherit it.** |

The calendar UI labels program entries with the plan name, "workout N of TOTAL", the phase and the week number.

---

## 4. Programs (training plans)

### 4.1 Data and limits

| Entity | Field | Rules |
|---|---|---|
| Program | `id`, `isArchived`, `createdAtUtc` | – |
| Revision | `programId`, `revisionId`, `revisionNumber` | ≥ 1 and contiguous per program (the next is always max + 1) |
| | `name` | Trimmed, non-blank, ≤ 160 |
| | `description` | Trimmed. Blank → null. ≤ 2,000 |
| | `category` | Trimmed, non-blank, ≤ 40. A searchable label such as `5K`, `10K`, `Base`, `Custom`, `General fitness`, `Walking`. It is never an execution rule. |
| | `templateId` | Blank → null. ≤ 100. Set only for premade installations. |
| | `templateVersion` | Blank → null. ≤ 40 |
| | `ownerProfileId` | Null (household) or a non-empty runner ID |
| | `contentSha256` | See 4.3. Unique per program: an identical revision cannot be appended twice. |
| | `items` | 1–**1,000**. Item IDs are unique. Positions are exactly `1..n` in any input order; they are stored and returned sorted by position. |
| Item | `id`, `workoutRevisionId` | Non-empty |
| | `position` | ≥ 1 |
| | `weekNumber`, `sessionNumber` | Null or ≥ 1 |
| | `phase` | Trimmed. Blank → null. ≤ 80 |
| | `alternatives` | 0–**20**. Sorted by `displayOrder`. |
| Alternative | `workoutRevisionId` | Non-empty. Unique within the item and **≠ the item's primary revision** |
| | `displayOrder` | ≥ 1 and unique within the item. The primary is implicitly 0. |
| | `variant` | Trimmed, non-blank, ≤ 40. Examples: `hr-alternative`, `fixed-fallback`. |

- `item.allowsWorkoutRevision(r)` is true when `r` is the primary or one of the alternatives.
- **Referenced workout revisions** must exist, and their workout kind must be `Structured` or `PlanInternal`. `ManualTemplate` is refused.

### 4.2 Revisions, immutability, visibility

- **Revisions are immutable.** Editing a custom program appends revision `current + 1`. Any other number → 409.
- Custom create/edit requests carry only an ordered list of workout revision IDs. Items get new IDs and positions 1..n, with no week, session, phase or alternatives.
- **Premade installations are immutable.** Appending a revision to a program whose current revision has a `templateId` → 409 "Premade training plans are immutable. Choose another template from the catalog instead."
- **Runs are pinned** to the revision they started on.
  - Editing a program never changes an active run.
  - Only a later start or restart opts the runner into the newest revision.
  - The next-item validation for the pinned run keeps using the pinned items.
- **Archive** sets `isArchived = true`. It is idempotent per operation, and a missing program gives 404. Archived programs are hidden from lists and cannot be started (404).
- **List for a runner** (`programs?profileId=`):
  - Take each non-archived program whose latest revision's owner is null or equals the runner.
  - Show the revision the runner's active run is pinned to if there is one, otherwise the latest.
  - Custom programs are all listed.
  - Template programs are grouped by `(ownerProfileId, templateId)`, and only one **canonical** entry per group is shown. The canonical entry is the one with an active run first, then the highest template version (parsed as a dotted version; unparseable = 0.0), then the newest `createdAtUtc`.
- **Display name** of a template program is the catalog template's `name` for `(templateId, templateVersion)` when the catalog still knows it; otherwise the stored name. This hides legacy suffixes such as "· Copy 2".
- **Required training days**, shown on summaries: the maximum number of items that share one `weekNumber`. If no item has a week number, it defaults to **3**.

### 4.3 Program revision content hash

```
value = join("\n",
  name, description ?? "", category, templateId ?? "", templateVersion ?? "",
  ownerProfileId as lower-case "D" UUID ?? "",
  join(",", items by position → "{workoutRevisionId}:{weekNumber}:{sessionNumber}:{phase}:{alts}")
)
alts = join(";", alternatives → "{workoutRevisionId}:{displayOrder}:{variant}")
null ints and null phase render as empty strings; UUIDs are lower-case with dashes
contentSha256 = UPPER-case hex SHA-256(UTF-8(value))
```

This hash is upper-case, unlike the workout and template hashes, which are lower-case. The difference must be preserved for data compatibility.

---

## 5. Program runs, projection and schedule adjustments

### 5.1 Run data

| Field | Rules |
|---|---|
| `id`, `userProfileId`, `programRevisionId` | – |
| `status` | `Active`, `Completed` or `Abandoned`. **At most one `Active` per runner** (a unique partial index). |
| `startedAtUtc`, `endedAtUtc` | `endedAtUtc` is set when the run leaves Active |
| `version` | Starts at 1. +1 on every state or schedule change. The optimistic token for all schedule operations. |
| `scheduledStartDate`, `scheduledWeekdayMask`, `scheduleTimeZoneId` | Either all absent (mask 0) or all present (mask 1–127, zone non-empty and valid, start date on a selected weekday: "The first training date must be one of the selected training days.") |

- A run without a schedule still drives the Today recommendation. It does not appear on the calendar.
- **Override** (`WorkoutProgramScheduleOverrides`): unique per `(runId, itemId)`. It is either `{isSkipped: true, targetDate: null}` or `{isSkipped: false, targetDate: date}`, plus `updatedAtUtc`.
- **Extra occurrence** (`WorkoutProgramExtraOccurrences`): `{id, runId, itemId, date, createdAtUtc}`. There can be several per item, and several per date.

### 5.2 Start and restart

The request has these fields:
- `operationId`, `profileId`, `expectedProgramRevisionId`;
- `expectedActiveRunId` and `expectedActiveRunVersion` (both or neither);
- optional `scheduledStartDate`, `scheduledWeekdayMask`, `scheduleTimeZoneId` (all three or none).

Start and restart behave identically. They have different receipt types: `program.start` and `program.restart`.

1. Validate IDs (400) and the pairing rules above (400).
2. The receipt check.
3. The program exists (404). Its **current** (latest) revision ID equals `expectedProgramRevisionId` (409 "The training plan revision changed after confirmation was shown.").
4. If the revision has an owner and it is not this runner → **403**.
5. Build the schedule (time zone, weekday and start-date validation → 400).
6. For a **template** program with a schedule: the number of selected weekdays must equal the maximum number of items in one week (default 1 when no week numbers) → 400 "Select exactly N training days for this plan." Custom programs have no day-count rule.
7. In one transaction:
   - The runner exists and is not archived (404).
   - The revision exists, its program is not archived, and its owner is null or this runner (404).
   - Load the runner's active runs. There must be ≤ 1, and they must match the expectation exactly: none when no expected ID was given; otherwise exactly that ID and version. Otherwise 409 "The active training plan changed after confirmation was shown."
   - Abandon the active run (`status = Abandoned`, `endedAtUtc = now`, version +1).
   - Insert the new run (`Active`, version 1, with the schedule).
8. Return the run (200). The receipt stores the run DTO.

The UI must warn explicitly when a start will abandon a current plan ("Start X for RUNNER?" / "Restart X for RUNNER?").

### 5.3 Projection

**Base projection** (`project(revision, run, from, to)`), for calendar ranges without adjustments:
```
if run has no schedule → []
cursor = schedule.startDate
for item in items by position:
    while weekday(cursor) not in mask: cursor += 1
    if cursor > to: break
    if cursor ≥ from: emit (cursor, item)
    if cursor == MAX_DATE: break
    cursor += 1
```
Positions are never rebased by the query window. For example, with start Mon 2026-08-10 and Mon+Wed, a window of 08-17 alone yields position 3.

**Full projection** (`projectAll(revision, run, overrides, extras)`):
```
cursor = schedule.startDate
for item in items by position:
    while weekday(cursor) not in mask: cursor += 1
    original = cursor ; atMax = (cursor == MAX_DATE) ; if not atMax: cursor += 1
    ov = overrides[item.id]
    if ov == null:          emit {date: original, item, originalDate: original}
    elif not ov.isSkipped:  emit {date: ov.targetDate ?? original, item, originalDate: original}
    // skipped items are not emitted
    if atMax: break
for extra in extras whose item exists in this revision:
    emit {date: extra.date, item, originalDate: null, isRepeat: true, extraOccurrenceId: extra.id}
sort by date, then item position, then isRepeat (false first)
```

Worked example (the core test):
- 3 items, start Mon 2026-08-10, mask Mon+Wed+Sat.
- Overrides: item1 → 08-11; item2 skipped.
- Extra: item1 on 08-13.
- Result: 08-11 (item1), 08-13 (item1, repeat), 08-15 (item3).

### 5.4 Schedule actions: preview and apply

Every action is **previewed first**. The preview is side-effect free. **Apply** recomputes the same preview inside a transaction, so the preview is only advisory.

| Action | UI label | Needs target | Allowed when |
|---|---|---|---|
| `MoveOne` | Move only this session | yes | The item is not currently skipped. It may be completed: a completed item moves only its calendar date and keeps its linked session and progress. |
| `MoveFollowing` | Move this and following | yes | The item is not skipped. It may be completed (a late completion). A **later completed** item blocks. |
| `Skip` | Skip | no | The item is not completed |
| `Restore` | Restore | no | The item is not completed and has an override |
| `Repeat` | Repeat workout · keep dates | yes | The item **is completed** |
| `RepeatAndShift` | Repeat workout · shift the rest | yes | The item **is completed** |

The action is parsed case-insensitively from these names. Numeric strings are rejected (400 "Action must be MoveOne, MoveFollowing, Skip, Restore, Repeat, or RepeatAndShift.").

#### Preview algorithm

```
run = run(runId, profileId)                              else 404
if run.status != Active or run has no schedule → blocked "Only an active scheduled training plan can be adjusted."
item = revision.items[itemId]                            else 404
effective = projectAll(revision, run, overrides, extras)
original  = projectAll(revision, run)                    // no adjustments
completed = { itemId of sessions linked to this run with state Completed }
current   = effective non-repeat occurrence of item (null when skipped)
origDate  = original occurrence date of item

if action ∈ {MoveOne, MoveFollowing, Repeat, RepeatAndShift} and target == null → blocked "Choose a target date."
if target != null and |target − (current?.date ?? origDate)| > 365 → blocked "Choose a date within one year of the currently scheduled session."
if action ∈ {Skip, Restore} and item ∈ completed → blocked "Completed plan sessions cannot be skipped or restored. Move the completed session to its actual date, with or without shifting the later incomplete plan, or use Repeat workout for another attempt."
if action ∈ {MoveOne, MoveFollowing} and current == null → blocked "This session is currently skipped. Restore it before moving it."
if action ∈ {Repeat, RepeatAndShift} and item ∉ completed → blocked "An incomplete session should be rescheduled, not repeated."

impacts = []
MoveOne:        if target == current.date → blocked "Choose a different date for the moved session."
                impacts += (item, pos, current.date → target)
MoveFollowing:  if target == current.date → blocked "Choose a different date for the moved sessions."
                offset = target − current.date
                for occ in effective non-repeat with position ≥ item.position (in projection order):
                    if occ.item != item and occ.item ∈ completed → blocked "A completed later session prevents shifting this part of the plan."
                    impacts += (occ.item, pos, occ.date → occ.date + offset)
Skip:           impacts += (item, pos, (current?.date ?? origDate) → null)
Restore:        if no override for item → blocked "This session already uses its original schedule."
                impacts += (item, pos, current?.date → origDate)
Repeat:         impacts += (item, pos, null → target, isRepeat)
RepeatAndShift: impacts += (item, pos, null → target, isRepeat)
                next = first date > target whose weekday is in the run mask
                shift = next − target
                for occ in effective non-repeat with position > item.position and occ.item ∉ completed:
                    impacts += (occ.item, pos, occ.date → occ.date + shift)

chronology = []
if action ∈ {MoveOne, MoveFollowing}:
    latestPrior = max date of effective non-repeat occurrences with position < item.position
    if latestPrior != null and target ≤ latestPrior: chronology = [target]

affected = { non-repeat impact item IDs }
occupied = { occ.date for occ in effective where occ.isRepeat or occ.item ∉ affected }
external = { new dates of impacts on which the runner's calendar series have any option (resolveDay) }
collisions = sorted distinct ({impact.newDate ∈ occupied} ∪ external ∪ chronology)

message by action:
  Skip:           "This plan step will be skipped and progression will continue to the next step."
  Restore:        "This session will return to its original planned date."
  Repeat:         "An extra attempt will be added without changing later sessions."
  RepeatAndShift: "An extra attempt will be inserted and the remaining incomplete plan will move forward."
  MoveFollowing:  "{impacts.count} session(s) will move by the same number of days."
  MoveOne:        "Only this session will move; later sessions keep their dates."
if collisions ≠ ∅ and action ∈ {MoveOne, MoveFollowing, Restore}:
    canApply = false
    message = chronology ≠ ∅ ? "This session would move before an earlier plan session. Choose a date after the prior session."
                             : "That change would place two plan sessions on {dates as 'd MMM yyyy', comma-separated}. Choose an empty date instead."
elif collisions ≠ ∅:            // Repeat or RepeatAndShift
    canApply = true ; message += " Warning: {n} date(s) will contain more than one session."
else canApply = true
return {runId, itemId, action, runVersion: run.version, canApply, message, impacts, collisionDates: collisions}
```

A **blocked** preview has `canApply = false`, the reason as its message, and empty impacts and collisions. It still reports the current `runVersion`.

**Collision rules summary:**
- Ordinary moves, shifts, restores and training-day changes **fail closed** on any occupied date. Occupied means another plan occurrence, a repeat, or any calendar-series session of the runner.
- An explicit **repeat may share** an occupied date. The preview names every such date, and neither session is replaced.

#### Apply

1. Serialize all schedule changes process-wide (one gate).
2. The receipt check.
3. Recompute the preview in the transaction. If `!canApply` → 400 with the preview message.
4. If `run.version ≠ expectedRunVersion` (or the preview's version ≠ it) → 409 "The training plan schedule changed after the preview was shown."
5. Mutate:
   - `Restore`: delete the item's override.
   - `Skip`: upsert the override `{skipped, target null}`.
   - Otherwise: for each **non-repeat** impact, upsert the override `{target = newDate}`. For `Repeat` and `RepeatAndShift`, also insert an extra occurrence `{new id, item, date = target}`.
6. `run.version += 1`. The outcome is the preview with the new `runVersion`. Store the receipt with the outcome JSON (enums as strings). Commit.

Stopped, interrupted and faulted attempts are **not** completions, so their item stays incomplete: it is rescheduled (moved), not repeated. A completed but unsatisfactory attempt stays completed and may get a separate repeat.

**Repeats** are calendar choices, not program positions. Running a repeat is an **unlinked** calendar session (selection source `Calendar`, no run or item). It never rewinds or double-advances the plan.

### 5.5 Change training days (default days)

The request carries `profileId`, `weekdayMask`, `effectiveDate`, and for apply also `operationId`, `expectedRunVersion` and `expectedRevision`. **Today** is the run's local date (2). **Preview:**

```
run = run(runId, profileId)                         else 404
blocked unless run is Active and scheduled:  "Only an active scheduled training plan can change its default days."
blocked if effectiveDate < today:            "Choose today or a future effective date."
blocked if mask ∉ 1..127:                    "Select valid training weekdays."
blocked if popcount(mask) ≠ popcount(run.mask): "Select exactly {n} training day(s)."
blocked if mask == run.mask:                 "Choose a different weekly training rhythm."
effective = projectAll(revision, run, overrides, extras)
completed = completed item IDs
generated = projectAll(revision, run) dates by item
explicit  = { ov.item : ov.isSkipped or ov.targetDate == null or item not generated or ov.targetDate ≠ generated[item] }
eligible  = effective non-repeat, date ≥ effectiveDate, item ∉ completed, item ∉ explicit, ordered by position
blocked if eligible empty: "No future generated sessions are eligible; completed runs and explicit exceptions are preserved."
cursor = nextSelected(max(eligible[0].date, effectiveDate), mask)     // nextSelected(d) = first date ≥ d on the mask
previous = null
for occ in effective non-repeat ordered by position:
    if occ ∈ eligible:
        if previous != null and cursor ≤ previous: cursor = nextSelected(previous + 1)
        impacts += (occ.item, pos, occ.date → cursor)
        previous = cursor ; cursor = nextSelected(cursor + 1)
    else:
        if previous != null and occ.date ≤ previous → blocked "The new training days would change the workout order around a preserved session. Move that session first or choose a later effective date."
        previous = occ.date
        if cursor ≤ occ.date: cursor = nextSelected(occ.date + 1)
affected = impact item IDs
occupied = effective dates where isRepeat or item ∉ affected
collisions = sorted distinct(impact.newDate ∈ occupied ∪ calendar-series collisions on impact dates)
preserved = count(effective where isRepeat or item ∈ completed or item ∈ explicit or date < effectiveDate)
          + count(skipped overrides)
revision = lower-hex SHA-256(UTF-8(join("|",
    runId as 32 lower-hex digits without dashes, run.version, currentMask, newMask, effectiveDate yyyy-MM-dd,
    join(";", impacts → "{itemId 32-hex}:{position}:{currentDate yyyy-MM-dd}:{newDate yyyy-MM-dd}"),
    join(";", collisions yyyy-MM-dd)))))
canApply = collisions empty
message = canApply ? "{k} future session(s) will move to the new weekly rhythm. {preserved} completed, earlier, repeated, or explicitly adjusted occurrence(s) stay unchanged."
                   : "The new training days would place two sessions on {dates d MMM yyyy}. Choose different days or move the existing session first."
```

A blocked preview has an empty `revision`, no impacts or collisions, and `preservedExceptionCount = 0`.

**What stays fixed:** completed items, skipped items, repeat attempts, individually moved items, and generated items before the effective date. Plan order and progression identity never change.

**Apply (atomic):**
1. The gate and the receipt check.
2. Recompute the preview. If it is blocked → 400.
3. `preview.runVersion ≠ expected` or `preview.revision ≠ expectedRevision` → 409 "The training-day preview is no longer current. Review it again." `run.version ≠ expected` → 409.
4. **Pin** every current non-repeat occurrence that is neither impacted nor already overridden: upsert an override at its current date. Then upsert an override at the new date for every impact.
5. `run.scheduledStartDate = nextSelected(old start, newMask)`, `run.mask = newMask`, version +1. The receipt stores the outcome.

Pinned overrides that equal the regenerated date under the new mask count as **non-explicit** in later previews. That is why a later reverse change can move them again.

### 5.6 Clear upcoming

- **Preview:**
  - The run is not found for this runner → 404 ("The selected runner's training plan was not found.").
  - The run is not active, or not scheduled → `canApply false`, "Only an active scheduled training plan has upcoming sessions to clear."
  - Otherwise: `upcoming = projectAll(…).where(date ≥ today)`, repeats included. The result is `{upcomingSessionCount, firstDate, lastDate, canApply: true}`.
  - Message when zero: "This active plan has no dated sessions from today onward; clearing it will still end the active plan." Otherwise: "Clear N upcoming session(s) from d MMM yyyy through d MMM yyyy?".
- **Apply:** the gate and receipt check; recompute; the version must match (409). Then `status = Abandoned`, `endedAtUtc = now`, version +1. The outcome message is "Removed N upcoming training session(s). Completed history was preserved." (`canApply false`).
- Other runners' runs, profiles, workout definitions and completed history are untouched. Overrides and extras stay in storage with the abandoned run.
- "Today" is always computed on the phone from the run's zone. A client-supplied date is ignored.

---

## 6. Advancement and progress

### 6.1 Linking a session to a plan item

- A session has a selection source: `Manual | Library | Calendar | Program` (`Legacy` exists only on migrated rows and cannot be requested).
- `Program` requires both `programRunId` and `programItemId`. Any other source must carry neither.
- When a session is prepared with `Program`, validate all of the following. Otherwise refuse with 409 "The selected workout is not the next item in this runner's active training plan.":
  - the run is **Active** and owned by the runner;
  - the item is **exactly the next item** of the run's progress (6.2);
  - `item.allowsWorkoutRevision(workoutRevisionId)` (primary or any alternative).
- Consequence: a calendar entry for a later plan item cannot be run as that item before its predecessors are completed or skipped.

### 6.2 Progress calculation

```
completed = { itemId of linked sessions with state Completed }   // terminal Stopped/Interrupted/Faulted don't count
skipped   = { itemId of overrides with isSkipped }
k = 0
for item in items by position:
    if item ∉ completed and item ∉ skipped: break
    k += 1
next = k < items.count ? items[k] : null
completedItemCount = k − |skipped ∩ items[0..k)|
skippedItemCount   = |skipped ∩ items[0..k)|
isComplete = next == null
```

Only the **contiguous prefix** counts. A completion out of order does not advance past an earlier incomplete item.

### 6.3 Advancement rules

- **Only a session that ends `Completed` and is linked to the run and item advances the plan.** Manual, library and calendar sessions, and repeat occurrences, are unlinked and never advance it.
- **Decision (Kotlin):** the linked session must also have **origin `Hardware`** (PLN-02). Simulator and SystemTest sessions never advance plans; migrated `Legacy` rows that are already Completed and linked keep counting. The legacy app also advanced on simulator sessions.
- **Idempotent:** a unique constraint on `(runId, itemId)` over sessions with state Completed means an item can be completed at most once per run. The next-item validation normally prevents a second attempt; the constraint is the last line of defence.
- **Run completion:** when a linked session is finalized as Completed and the run is Active, count the distinct completed item IDs of the run. If the count equals the revision's item count, set `status = Completed`, `endedAtUtc = session end`, version +1.
  - Skipped items do not count toward this total, so a run with any skipped item stays Active even when progress `isComplete` is true. Keep this legacy behaviour.
- Moving a completed item's date (MoveOne or MoveFollowing) never rewrites the linked session.

---

## 7. Premade catalog and installation

### 7.1 Catalog

There are 16 templates, in this display order. All are version `1.0.0` except the WalkingPad plan (`2.0.0`).

| # | id | Name | Goal | Experience | Weeks | /wk | Max min (D) | Max km/h (V) | Repeatable | HR | Extra tags |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | getting-started | Getting Started | General fitness | Beginner | 4 | 3 | 22 | 5.5 | no | no | general-fitness, walking |
| 2 | first-5k | First 5K | 5K | Beginner | 6 | 3 | 30 | 7.0 | no | no | 5k |
| 3 | beginner-5k-standard | Beginner 5K Standard | 5K | Beginner | 9 | 3 | 34 | 7.5 | no | no | 5k |
| 4 | beginner-5k-progressive | Beginner 5K Progressive | 5K | Beginner | 10 | 3 | 36 | 8.0 | no | no | 5k |
| 5 | beginner-5k-gentle | Beginner 5K Gentle | 5K | Beginner | 14 | 3 | 38 | 7.0 | no | no | 5k, gentle |
| 6 | heart-rate-5k-gentle | Heart-rate 5K Gentle | 5K | Beginner | 12 | 3 | 36 | 7.5 | no | **yes** | 5k, heart-rate, gentle |
| 7 | 5k-performance | 5K Performance | 5K | Experienced | 8 | 4 | 45 | 11.0 | no | no | 5k, performance |
| 8 | first-10k | First 10K | 10K | Intermediate | 6 | 3 | 48 | 8.0 | no | no | 10k |
| 9 | 10k-builder-gentle | 10K Builder Gentle | 10K | Intermediate | 14 | 3 | 55 | 8.0 | no | no | 10k, gentle |
| 10 | heart-rate-10k-gentle | Heart-rate 10K Gentle | 10K | Intermediate | 14 | 3 | 55 | 8.5 | no | **yes** | 10k, heart-rate, gentle |
| 11 | 10k-performance | 10K Performance | 10K | Experienced | 8 | 4 | 65 | 12.0 | no | no | 10k, performance |
| 12 | 5k-to-10k-distance-first-58 | 5K to 10K Distance First | 10K | Beginner | 58 | 3 | – | – | no | no | 5k, 10k, long-plan |
| 13 | general-treadmill-fitness | General Treadmill Fitness | General fitness | All levels | 6 | 3 | 35 | 8.0 | no | no | general-fitness |
| 14 | 5k-maintenance | 5K Maintenance | 5K | Intermediate | 4 | 3 | 38 | 8.5 | **yes** | no | 5k, maintenance |
| 15 | 10k-maintenance | 10K Maintenance | 10K | Intermediate | 4 | 3 | 55 | 9.0 | **yes** | no | 10k, maintenance |
| 16 | walking-and-recovery | Walking and Recovery | Walking | All levels | 4 | 3 | 30 | 5.5 | **yes** | no | walking, recovery |

- Every template's tag set also includes the goal slug: lower-case, spaces → `-`.
- Descriptions are in `data/premade/catalog.json`. No description may mention rehabilitation.
- Catalog filters: goal, experience, text search, HR requirement, duration, sessions per week.
- `data/premade/` is the **source of truth**: all definitions, keys, phases, hashes and counts. Section 7.2–7.4 specify how the data was produced, so the Kotlin tests can regenerate it and compare.

### 7.2 Generator for templates 1–11 and 13–16

These are the parameterised templates. The code uses banker's rounding (`HALF_EVEN`), which matches .NET `Math.Round`, and `.` as the decimal separator.

```
for week in 1..W:
  phase = phaseOf(id, week, W)
  p = (W == 1) ? 1 : (week − 1) / (W − 1)
  for session in 1..S:
    type  = session == S ? "Long" : session == 2 ? "Quality" : "Easy"
    dur   = max(15, round(D × (0.58 + p × 0.42) − (session == 1 ? 4 : 0)))
    f     = session == 2 ? 1.0 : session == S ? 0.85 : 0.78
    speed = round(clamp(V × f + p × V × (1 − f), 0.8, V) × 2) / 2
    incl  = (session == 2 and goal ∈ {"General fitness", "Walking"}) ? 2 : 1
    zone  = HR ? (session == 2 ? 3 : 2) : null
    key   = "{lower(type)}-{dur}-{speed:0.0}-{incl:0.0}-{zone ?? "fixed"}"      // e.g. easy-15-5.5-1.0-fixed
    name  = zone == null ? "{type} {dur} min" : "{type} Z{zone} · {dur} min"
    position = running counter from 1

phaseOf(id, week, W):
  if id contains "maintenance": "Maintenance cycle"
  r = week / W : r ≤ 0.3 "Foundation" ; r ≤ 0.75 "Build" ; r ≤ 0.9 "Peak" ; else "Consolidate"
```

**Building one workout** (schema v1, title = name, description = `"Premade plan workout · {phase}"`):

```
warm = clamp(dur div 6, 4, 8) ; cool = clamp(dur div 8, 4, 7) ; main = max(5, dur − warm − cool)   // integer division
ws = min(4.5, speed)
mainSpeed = zone ? heartRateZone(zone, initial = speed, min = max(0.8, speed − 1.5), max = speed) : fixed(speed)
steps:
  1. time warm min,  fixed ws,  incline 0.5,  cue "Warm up"
  2. time main min,  mainSpeed, incline incl, cue zone ? "Stay in Z{zone}" : "Steady effort"
  3. time cool min,  fixed ws,  incline 0.5,  cue "Cool down"
```

Worked examples:

| Template | Pos | Key | Steps (min) | Speeds |
|---|---|---|---|---|
| first-5k | 1 | `easy-15-5.5-1.0-fixed` | 4 / 7 / 4 | 4.5 / 5.5 / 4.5 |
| first-5k | 2 | `quality-17-7.0-1.0-fixed` | 4 / 9 / 4 | 4.5 / 7 / 4.5 |
| first-5k | 18 | `long-30-7.0-1.0-fixed` | 5 / 21 / 4 | 4.5 / 7 / 4.5 |
| heart-rate-5k-gentle | 2 | `quality-21-7.5-1.0-3` | 4 / 13 / 4 | 4.5 / Z3 (7.5, 6.0–7.5) / 4.5 |

Identical keys within one template share one workout. `10k-performance` has 32 positions but only 30 unique workouts: position 3 = position 5, and position 19 = position 21.

### 7.3 The 58-week WalkingPad plan (`5k-to-10k-distance-first-58`, v2.0.0)

- **Source:** `data/premade/walkingpad-5k-to-10k-source.json`. It has 174 slots `W01D1…W58D3`, and each slot has a `primary` variant plus 0 or 1 alternative.
- **Counts:**
  - 174 positions, 58 weeks, 3 per week, 260 variants;
  - 88 slots have no alternative and 86 have one;
  - 65 alternatives are `hr-alternative` (slots W11D1…W57D3);
  - 21 are `fixed-fallback` (W23, W28, W33, W38, W43, W48, W53 D1–D3, where the primary is HR-guided).
- **Phases:** week ≤ 12 "Foundation" (36 positions), ≤ 26 "5K base" (42), ≤ 44 "10K build" (54), else "Distance consolidation" (42).
- **Materialisation per variant:**
  1. Drop the **legacy stop tail**: if the last two rows are (60 s at 1.0 km/h) and (any duration at 0 km/h), remove both. Every variant has it. Normal treadmill Stop is authoritative.
  2. Each remaining row becomes one step: `time(durationSeconds)` and `fixed incline(incline)`. The speed is:
     - `heartRateZone(zone, initial = speed, min = minimumSpeed, max = maximumSpeed)` if `!forceSpeed && zone > 0 && minimumSpeed > 0 && maximumSpeed > 0`;
     - else `heartRate(bpmMin, bpmMax, speed, min, max)` if `!forceSpeed && heartRateMinimum > 0 && heartRateMaximum > 0 && min > 0 && max > 0` (this never occurs in the data);
     - else `fixed(speed)`.
     - No cue and no notes.
  3. The title is `"{slot} · {variant.title}"`. The description is `"WalkingPad source variant {variant.id} ({variant.variant}); legacy low-speed stopping tail removed. {variant.selectionRule}"`.
- **Session fields:**
  - `workoutKey` = primary `id` and `workoutName` = primary `title`;
  - `durationMinutes` = ⌈Σ primary step minutes⌉ (tail removed);
  - `targetSpeedKph` and `targetInclinePercent` = max over the primary's raw rows;
  - `heartRateZoneNumber` = null.
  - Alternatives keep source order: `workoutKey` = variant `id` (e.g. `W11D1H`, `W23D1F`), `variant` label, name = title, `displayOrder` 1.
- **Checks:**
  - The first session is `W01D1 · Long easy: 10 x 1 min at 8.0 km/h`. It has 21 steps; the first and last are 5 min at 4.5 km/h. No step has fixed speed 0.
  - W11D1's alternative, step 2, is `heartRateZone(2, 7.5, 4.0, 10.0)`.
- **HR zones referenced:** 1, 2 and 3. `requiresHeartRate` is **false**, so the plan installs without zones. The HR steps need the runner's zones only when such a session is prepared (6-profiles).

### 7.4 Hashes

- **Workout definition hash:** the lower-case hex SHA-256 of the canonical JSON (02-workouts). Every definition in `data/premade` carries `definitionSha256`.
- **Template content hash** (stored on the installation):
  ```
  lower-hex SHA-256(UTF-8(join("|", id, version, name, weeks, sessionsPerWeek, sourceContentSha256 ?? "",
     join(";", sessions → "{week}:{session}:{phase}:{workoutKey}:{join(",", alternative workoutKeys)}"))))
  ```
- **WalkingPad source hash:** the SHA-256 of the exact bytes of `walkingpad-5k-to-10k-source.json` = `c476161e23a94242c8172dffe3b42fc2efb559b1232753bca7fbe297529bdff3`.

### 7.5 Preview and installation (materialisation)

**Preview** (`templateId`, `profileId`, optional `version`) is read-only.

1. The template exists (404). The runner exists and is not archived (404).
2. **HR readiness:** if `requiresHeartRate`, every zone number referenced by a session must exist in the runner's zones. Otherwise the plan is not compatible: "This plan needs runner heart-rate zones Z1–Z5 before it can be added."
3. **Capability normalisation** runs for every distinct workout key: the primary built per 7.2/7.3, and each alternative. The policy is 02-workouts' capability policy.
   - It uses the enrolled treadmill's verified speed and incline ranges, if any, and the runner's maximum speed.
   - Targets are **aligned down** to the treadmill increment and are never made more aggressive.
   - A target outside the range, or above the runner's maximum, is **rejected**.
   - HR-zone directives normalise their initial, minimum and maximum speed. If the result would be inconsistent, the directive is left unchanged.
4. The response has these fields:
   - the catalog entry, with `alreadyAdded` and `copyCount` for this runner;
   - `compatible`, `compatibilityMessage`, `heartRateZonesReady`;
   - the normalised max speed and incline (the template maxima when no normalised targets exist);
   - the normalised and rejected target counts, and the unique workout count;
   - phases (`name`, first and last week, session count);
   - the workouts by key (key, name, description, expanded step count, duration, blocks);
   - the sessions (position, week, session, phase, key, name, alternatives).

   Messages:
   - "One or more targets cannot fit the selected runner and verified treadmill limits."
   - "Runner limits are compatible; verified treadmill ranges are not enrolled yet."
   - "Targets fit the selected runner and verified treadmill ranges."

**Install** (`operationId`, `profileId`, `templateId`, `templateVersion`), receipt type `premade-plan.materialize`:

1. The fingerprint scope is `{profileId, templateId, templateVersion, contentSha256}`. The receipt check replays with `replayed: true`.
2. Prepare as in the preview. If it is not compatible → 400 with the message. **Nothing is persisted.**
3. Serialize installs process-wide.
4. **Idempotency:** if the runner already has an installation of the same `templateId` and `templateVersion` whose program is **not archived**:
   - return it with `alreadyAdded: true`, status **200** and its `copyNumber`;
   - store a receipt with 200;
   - create nothing.
   There is no "fresh copy" path. A second independent copy is only possible after archiving the first.
5. Otherwise, in one transaction:
   - `copyNumber` = the max over **all** prior installations of this runner, template and version (archived ones included) + 1, or 1.
   - For each distinct normalised definition, in ordinal key order and **deduplicated by canonical hash**: create a workout with kind **`PlanInternal`**, name = title, and revision 1 (canonical JSON + hash).
   - Create a program with revision 1: name = template name, description, category = template **goal**, `templateId`, `templateVersion`, `ownerProfileId` = runner, and the content hash.
   - Create one item per session: position, week, session, phase, the primary revision, and alternatives with `displayOrder` 1..n and the `variant` label.
   - Insert the installation: `{id, profileId, templateId, templateVersion, templateContentSha256, copyNumber, programId, createdAtUtc}`. It is unique on (profile, template, version, copy) and on programId.
   - Return 201: `{installationId, programId, programRevisionId, templateId, templateVersion, copyNumber, positionCount, uniqueWorkoutCount, alreadyAdded: false, replayed: false}`.
6. Installing **never starts a run**. The UI then offers **Start plan**: pick the first date and exactly `sessionsPerWeek` weekdays, then start (5.2). **Keep for later** leaves the plan inactive.

**Catalog list for a runner** marks `alreadyAdded` and `copyCount` using only installations whose program is not archived.

**Plan-internal hiding:** a workout is plan-internal if its kind is `PlanInternal`, **or** any of its revisions is referenced by an item of a program revision with a `templateId`. The second rule covers legacy rows stored as `Structured`. Plan-internal workouts are excluded from the library, the manual selector, calendar-series editors and the recent-run reuse list. Only their owning plan exposes them, as immutable detail views.

**HR zone references:** definitions store `zoneNumber` only, never BPM. BPM bounds are resolved from the **selected runner's** zones each time a session is prepared (06-profiles). A second runner installs and schedules their own independent copy, with their own progress and calendar.

### 7.6 Provenance and licensing (WalkingPad plan)

- The plan is a deterministic, sanitised derivative of an **owner-provided WalkingPad/QDomyos 58-week 5K-to-10K source snapshot**. The reviewed source layout was:
  - `workout_index.csv`: week, session, variant, title, selection rule, source file name and stable source ID;
  - one indexed **QDomyos v4 XML** workout per row: duration, metric speed, incline, force-speed flag, HR zone or range, and bounded speed fields.
- The legacy generator read only the index and the XML files it explicitly references.
  - It normalised numbers with the invariant culture and emitted deterministic compact JSON, which is the file in `data/premade/`.
  - It hashed those UTF-8 bytes with SHA-256, then gzip-compressed and embedded them.
  - The **normalised payload hash**, not the gzip bytes, is the content identity: `c476161e…bdff3`.
  - The legacy generator script hash was `f1637593a746f16a26a3f6d21418804863a1017d9de2496804a743b9b0c57822`. It is recorded for provenance only.
- The private source files are **not** runtime dependencies and are not redistributed. Personal weight, BPM values, sensor IDs, gait preferences, machine paths and account data were not copied. Rows carry HR **zones**, never BPM.
- The other 15 templates were authored independently from read-only owner-provided examples and configuration patterns. They are parameterised (7.2).
- No plan is an official export of Horizon, Garmin, QDomyos, WalkingPad or any other provider. No plan makes medical or rehabilitation claims.
- **Changing the source:** a revised payload is a new template **version**, for example `2.1.0`, with a new source hash that is recorded in change evidence. Installed copies never change (immutability, 4.2).

---

## 8. Today recommendation

The core resolver, applied to today's local date:

1. **Exactly one** distinct calendar workout today → recommend it (`Calendar`).
2. **More than one** → `CalendarChoiceRequired`. The runner must choose explicitly, and nothing is preselected.
3. Otherwise, if the runner has an **Active** run with a next item → recommend `Program(nextItem.primaryRevision, runId, itemId)`.
4. Otherwise → `Manual`.

The UI applies these refinements. They are mandatory:

- "Today's options" is the merged calendar day (3.7), with program occurrences first.
  - A stored day selection (`isSelected`) wins and is recommended directly.
  - A single option is recommended.
  - Several options without a selection → the explicit choice. A program item with alternatives therefore always asks.
- A recommended **program option that is not a repeat** is run with selection source `Program` and its run and item IDs. A **repeat** option is run as `Calendar` with no run or item.
- When no calendar option exists and no choice is pending, the active plan's next item is recommended ("Program").
- Before falling back to Manual, the most recent reusable completed **Structured** run may be offered ("Recent"). Plan-internal workouts are never offered this way.

---

## 9. Goals and progression recommendations

### 9.1 Local goals

| Field | Rules |
|---|---|
| `id`, `profileId` | The runner must exist and not be archived (404) |
| `kind` | `Sessions`, `Minutes`, `Distance` or `PlanCompletion` (exact case) |
| `period` | `Weekly`, `Monthly` or `Plan` |
| `targetValue` | Finite and > 0 |
| `enabled` | bool |
| `version`, `createdAtUtc`, `updatedAtUtc` | – |

- Unique per `(profileId, kind, period)`.
- **Save = upsert.** Look the goal up by `id` if one is given (scoped to the runner), otherwise by `(kind, period)`.
  - When it does not exist: `expectedVersion` must be null (else 409). Insert with version 1.
  - When it exists: `expectedVersion` must equal the stored version (else 409). Version +1.
- Errors: invalid values → 400; a missing runner → 404.
- **Progress** is derived, never stored, from the runner's **completed** sessions, excluding the Simulator and SystemTest origins:
  - `Weekly` = the rolling last 7 days from now; `Monthly` = the rolling last 30 days;
  - `Sessions` = count, `Minutes` = the sum of durations, `Distance` = the sum of km.
  - Worked example: sessions 1 day ago (30 min, 4.25 km) and 14 days ago (40 min, 6.5 km) → weekly 1 / 30 min / 4.25 km, monthly 2 / 70 min / 10.75 km.
- `PlanCompletion` / `Plan` is stored and round-tripped, but the legacy app derives no progress for it. **Decision:** show it as the active run's `(completed + skipped) / total`.
- Goal edits never change session history.

### 9.2 Trends (inputs to goals and History)

- Include only the facts of this runner with completion `Completed` and origin not Simulator or SystemTest.
- The results are: count, total duration, total distance, the number with incomplete telemetry, the longest distance, the longest duration, and the highest average HR (null if none).
- Distance and duration must be finite and non-negative; otherwise the input is invalid.

### 9.3 Progression adviser (`local-progression-v1`)

The input has these fields:
- `profileId` and `sessionId` (non-empty);
- `completion` (`Completed | Interrupted | Missed`);
- `adherencePercentage` (0–100, finite);
- `perceivedExertion` (null or 1–10);
- `heartRateCoveragePercentage` (0–100);
- `telemetryComplete`, `wasInterrupted`, `missedScheduledSessions`.

The first matching rule wins:

| # | Condition | Action | Reason |
|---|---|---|---|
| 1 | completion = Missed or missedScheduledSessions > 0 | `Reschedule` | "A scheduled session was missed; choose a new date before changing training load." |
| 2 | !telemetryComplete or wasInterrupted or completion = Interrupted | `Repeat` | "The session evidence is incomplete or interrupted, so progression is not inferred." |
| 3 | RPE ≥ 9 or adherence < 70 | `Reduce` | "Very high effort or low adherence suggests reducing the next session after confirmation." |
| 4 | RPE ≤ 6 **and** adherence ≥ 90 **and** HR coverage ≥ 80 | `Advance` | "Comfortable effort, strong adherence, and sufficient heart-rate coverage support advancing after confirmation." |
| 5 | otherwise (including a null RPE that misses rule 4) | `Maintain` | "The completed session supports keeping the current progression." |

Every result has `algorithmVersion = "local-progression-v1"` and `requiresConfirmation = true`.

**Evidence from a session** (built when the runner asks for a suggestion for a terminal session they own):
- `adherence` is the session analytics adherence (05). `RPE` comes from the debrief.
- `hrCoverage` = the share of samples with an HR value × 100.
- `telemetryComplete` = more than one sample and no device-disconnected event.
- `wasInterrupted` = the state is Interrupted or Faulted.
- `missedScheduledSessions` = 0.
- Completion mapping: Completed → Completed; Interrupted or Faulted → Interrupted; **Stopped → Completed** in the legacy app.
  - **Decision:** the Kotlin app maps Stopped → Interrupted, which gives the conservative `Repeat`.

**Persistence:** `ProgressionRecommendations` has these fields:
- `id`, `operationId`, `profileId`, `sessionId` (unique per runner + session);
- `action`, `reason` (≤ 500), `algorithmVersion`, `evidenceJson`;
- `status` = `Pending | Accepted | Rejected`;
- `createdAtUtc`, `decidedAtUtc` (null exactly when Pending), `version`.

Behaviour:
- Asking again for the same session updates a **Pending** recommendation in place (version +1) and returns an already-decided one unchanged.
- **Decide** (`accepted`, `expectedVersion`): the version must match (409), and the status must be Pending (409 "Recommendation already has a decision receipt."). Then set Accepted or Rejected with `decidedAtUtc`, version +1.
- The list is the newest 50 per runner.
- A recommendation **never mutates** a program, a run or a workout revision. Acting on it is a separate, explicit user action.
- A debrief change refreshes the recommendation state.

---

## 10. Operation receipts, expected versions and idempotency

### 10.1 Receipts

Every planning write carries a client-generated **operation ID** (UUID, non-empty; an empty one → 400). The server persists one receipt per operation ID:

| Field | Meaning |
|---|---|
| `clientOperationId` | Unique |
| `operationType` | e.g. `calendar.series.create` (≤ 100 chars) |
| `statusCode` | The HTTP-equivalent status of the stored outcome |
| `outcomeJson` | The exact response body to replay |
| `createdAtUtc` | Used for retention |
| `requestFingerprint` | 64 lower-case hex chars = SHA-256 of the canonical request scope |

**Rules:**

1. **Before** doing work, look up the receipt.
   - Same type and same fingerprint → **replay**: return the stored status and body, and do nothing.
   - Different type or fingerprint → **409** "That operation ID was already used for another action or request."
2. The receipt is inserted **in the same transaction** as the mutation.
   - If the insert loses a race (unique violation), re-read the winner and treat it as a replay or conflict as in rule 1.
   - Two concurrent identical requests therefore both succeed with the same result. Two concurrent different requests with one ID give one success and one 409.
3. A not-found outcome inside the store is also receipted, with 404 and `{}`.
4. **Retention:** prune receipts older than **90 days** (configurable 7–365) every 6 hours. The replay window is therefore 90 days.
5. Receipts are local operational data. Receipts younger than 90 days are migrated from the legacy backup.

**Fingerprint:** the lower-hex SHA-256 of the UTF-8 JSON of the scope object, serialised as follows:
- camelCase property names in the listed order, no whitespace;
- UUIDs as lower-case `D` strings, dates as `yyyy-MM-dd`, nulls written as `null`;
- lists of segments ordered by `seriesId` string.

Byte-compatibility with legacy fingerprints matters only when a migrated receipt is replayed.

### 10.2 Operation catalogue

| Operation | Type string | Fingerprint scope (in order) | Success |
|---|---|---|---|
| Create series | `calendar.series.create` | `targetSeriesId(null), profileId, name, timeZoneId, startDate, endDate, intervalWeeks, weekdayMask, alternatives[{workoutRevisionId, displayOrder}], exceptions[{date, kind, alternatives}], expectedVersion` | 201 + series |
| Update series | `calendar.series.update` | Same, with `targetSeriesId = id` | 200 + series |
| Move occurrence | `calendar.occurrence.move` | `seriesId, sourceDate, targetDate, moveFollowing, expectedVersion, expectedSegments[{seriesId, version}]` | 204 |
| Delete occurrence | `calendar.occurrence.delete` | `seriesId, date, expectedVersion` | 204 |
| Delete group | `calendar.group.delete` | `seriesId, expectedSegments` | 204 (404 receipted) |
| Save day selection | `calendar.day.select` | `profileId, date, seriesId, workoutRevisionId` | 204 |
| Program schedule change | `calendar.program.schedule.change` | `runId, profileId, programItemId, action, targetDate, expectedRunVersion` | 200 + outcome |
| Change training days | `calendar.program.default-days.change` | `runId, profileId, weekdayMask, effectiveDate, expectedRunVersion, expectedRevision` | 200 + outcome |
| Create program | `program.create` | `name, description, category, ownerProfileId, items[workoutRevisionId]` | 201 + program |
| Append revision | `program.revision.create` | `programId, name, description, category, ownerProfileId, items` | 201 + program |
| Archive program | `program.archive` | `programId` | 204 |
| Start plan | `program.start` | `programId, profileId, expectedProgramRevisionId, expectedActiveRunId, expectedActiveRunVersion, scheduledStartDate, scheduledWeekdayMask, scheduleTimeZoneId` | 200 + run |
| Restart plan | `program.restart` | Same | 200 + run |
| Clear upcoming | `program.run.clear-upcoming` | `runId, profileId, expectedRunVersion, today (the run's local date)` | 200 + outcome |
| Install premade | `premade-plan.materialize` | `profileId, templateId, templateVersion, contentSha256` | 201, or 200 if already added |

Goals and recommendation decisions are protected by **expected versions only**, without receipts. Recommendation creation is keyed by `(runner, session)`.

### 10.3 Expected versions

| Aggregate | Token | Violations |
|---|---|---|
| Calendar series | `version` per segment | Update, delete occurrence and move-only need the segment version. Move-following and delete-group need **every group segment's version**. Mismatch → 409 "The schedule changed in another client. Reload and try again." |
| Program | Next revision number | Stale → 409 |
| Program run | `version` | Start and restart need the expected active run ID and version, or none. Schedule change, training days and clear need `expectedRunVersion > 0`. Training days also needs the preview `revision`. Stale → 409. |
| Goal | `version` | 9.1 |
| Recommendation | `version` | 9.3 |

**Serialisation:** schedule changes, training-day changes and clear-upcoming share one process-wide gate. Premade installs have their own gate. Every multi-row change is a single database transaction: the change and its receipt commit or roll back together.

**UI contract:** on 409 the client reloads the affected data before another attempt. It never retries a write automatically with the same operation ID and a different body.

---

## 11. Reference: legacy HTTP surface

This table is for parity only. The Kotlin web interface may shape its routes differently, but it must keep the same semantics.

| Method and path | Purpose |
|---|---|
| `GET /api/planning/calendar/series?profileId` | List a runner's series (name order) |
| `POST /api/planning/calendar/series` · `PUT …/series/{id}` | Create / update |
| `POST …/series/{id}/occurrences/{date}/move` · `…/delete` | Move (one or following) / delete one |
| `POST …/series/{id}/delete-group` | Delete a group |
| `GET /api/planning/calendar/{profileId}?from&to` | Merged range (≤ 62 days) |
| `POST /api/planning/calendar/{profileId}/days/{date}/selection` | Save a day selection |
| `POST /api/planning/calendar/program-runs/{runId}/schedule/preview` · `/apply` | Program schedule actions |
| `POST …/program-runs/{runId}/default-days/preview` · `/apply` | Change training days |
| `GET /api/planning/programs?profileId` · `GET …/programs/{id}?profileId` | Summaries (no items) / detail (items) |
| `POST /api/planning/programs` · `…/{id}/revisions` · `…/{id}/archive` | Create / append / archive |
| `POST …/programs/{id}/start` · `…/restart` | Start / restart |
| `GET …/programs/runs/{runId}/clear-upcoming/preview?profileId` · `POST …/clear-upcoming` | Clear upcoming |
| `GET /api/planning/premade-plans?profileId` | Catalog with `alreadyAdded` and `copyCount` |
| `GET /api/planning/premade-plans/{templateId}/preview?profileId&version` | Preview |
| `POST /api/planning/premade-plans/materialize` | Install |
| `GET/PUT /api/local-first/profiles/{id}/goals` · `GET …/insights` · `GET/POST …/recommendations` · `POST …/recommendations/{rid}/decision` | Goals, trends, advice |

Program summaries expose these fields:
- `id`, `revisionId`, `revisionNumber`, `name`, `description`, `category`, `itemCount`;
- `run`, `completedItemCount`, `skippedItemCount`, `nextItemId`, `nextWorkoutRevisionId`, the next workout name, revision and duration;
- `isComplete`, `requiredTrainingDays`, `templateId`, `templateVersion`, `ownerProfileId`.

The detail view adds the items: position, week, session, phase, workout name, revision number, duration, and alternatives.

---

## 12. Test tables (given → expected)

These tables translate the legacy unit and integration tests. Dates are 2026 unless stated. "Mask 37" means Mon+Wed+Sat. For the store and endpoint tests, "runner" is a fresh non-archived profile and "W(x)" a fresh workout revision.

### 12.1 Weekday rotation and recurrence

| # | Given | Expected |
|---|---|---|
| R1 | rotate(1, +1) | 2 |
| R2 | rotate(64, +1) | 1 |
| R3 | rotate(1, −1) | 64 |
| R4 | rotate(5, +2) | 20 |
| R5 | rotate(127, +12) | 127 |
| R6 | Series created without a group ID | `scheduleGroupId == id` |
| R7 | Weekly Sunday from 03-22, range 03-22…04-05 (Brussels DST start 03-29) | Days 03-22, 03-29, 04-05, each with one option |
| R8 | Weekly Sunday from 10-18, range 10-18…11-01 (DST end 10-25) | Days 10-18, 10-25, 11-01 |
| R9 | Start Mon 01-05, interval 2, Mon+Wed | 01-05 ✓, 01-07 ✓, 01-12 ✗, 01-19 ✓ |
| R10 | Interval 0 or 53; mask 0 or 128; end < start | Rejected |

### 12.2 Day resolution

| # | Given | Expected |
|---|---|---|
| D1 | Weekly Sunday from 03-01 with base B. Exceptions: Skip 03-08, Replace(R) 03-15, Add(A) 03-22 | 03-08 → none; 03-15 → only R; 03-22 → 2 options (B, A); 03-29 → only B |
| D2 | Two series on the same Thursday, one for another runner. Own alternatives with orders 20 and 10 | Resolving for the own runner returns orders [10, 20] only |
| D3 | Weekly Sunday series. Replace on Monday 03-02 (off-day) | 03-02 → no options |
| D4 | Same, but Add on Monday 03-02 | 03-02 → exactly the added option |
| D5 | Tuesday series from 08-01, base B. Skip 08-04; Replace(R) 08-05 (Wed, off-day); Replace(R) 08-11; Add(A, order 1) 08-18 | Selecting: Thu 08-06 B → invalid; 08-05 R → invalid; 08-04 B → invalid; 08-11 B → invalid; 08-11 R → ok; 08-18 B → ok; 08-18 A → ok and stored (the last write wins) |
| D6 | Stored exception kind corrupted to "99" | Reading the series fails with a data error |

### 12.3 Calendar series operations

| # | Given | Expected |
|---|---|---|
| C1 | Create a series (op X), then send op X again with a different name | First 201; second **409** |
| C2 | Update with expectedVersion 1 (op Y); then op Y on another series ID | 200 (version 2); then **409** (op reuse) |
| C3 | Save a selection (op Z) twice; op Z for another date; op Z for another runner; op Z with another revision | 204, 204; then 409, 409, 409. The range shows `isSelected = true` |
| C4 | Store: create v1, select 08-04, rename → v2 | Versions 1 → 2. The selection round-trips. Selecting a non-option revision → invalid |
| C5 | Monday series (no end) from M. Move-only M → M+1 (op A); replay A; A with target M+2 | 204, 204, 409. M is empty. M+1 has an option with the original group ID and name |
| C6 | Continue C5: move-following M+7 → M+9 with expectedVersion 2 and segments [(orig, 2)] | 204. M+7 empty. M+9 option from a **new series ID** in the **same group**. M+16 has a session. The group has 2 segments; the continuation version is 1 |
| C7 | Move-following on the continuation at M+16 with a stale segment version (orig +1) | 409. M+16 is still scheduled |
| C8 | Delete-one on the continuation at M+9 (v1), twice with the same op | 204, 204 (replay). M+9 is empty |
| C9 | Delete-group with one stale version; then with correct versions (op G) twice | 409 (both segments remain); then 204, 204. No series of the group remain |
| C10 | Two single-day series: Monday (Mon M) and Tuesday (M+1). Move-only M → M+1 | **400** (target occupied by another group) |
| C11 | Monday series 08-10…08-24, and a Tuesday series on 08-18 only. Move-following 08-10 → 08-11 | **400**, message contains "another workout group" (08-18 would collide after the shift) |
| C12 | Mon+Wed+Fri series from Monday M. Move-following from Friday M+11 to Sunday M+6 | **400** "…overlap…". Wednesday M+9 is still scheduled. There is still exactly one series |
| C13 | Monday series M…M+28 with an Add exception on Tuesday M+1. Move-following M+1 → M+3 | **400** "…only be moved by itself…". M+1 and M+7 are unchanged |
| C14 | Update with another runner's ID | **400**. The series still belongs to the owner; the other runner has none |
| C15 | Create with an unknown runner, or an unknown revision | **404** each, and nothing is persisted |
| C16 | Create with a null exception entry | **400** |
| C17 | Range 9999-12-31…9999-12-31 | 200, `from = to = 9999-12-31`, no days |
| C18 | Range longer than 62 days, or to < from | 400 |

### 12.4 Programs and core progress

| # | Given | Expected |
|---|---|---|
| P1 | Revision with name "␣␣First 5K␣␣", description "␣␣Build safely.␣␣", category "␣␣5K␣␣"; items given as [pos 2, pos 1] | "First 5K", "Build safely.", "5K"; items ordered [pos 1, pos 2] |
| P2 | Duplicate item IDs; positions {1, 3} | Both rejected |
| P3 | An item with primary P and alternative H (`hr-alternative`, order 1) | Allows P ✓, H ✓, random ✗. An alternative equal to P → rejected |
| P4 | 21 alternatives on one item | Rejected (max 20) |
| P5 | The WalkingPad template as items (174) | Accepted. 1,001 items → rejected (max 1,000) |
| P6 | Items 1, 2, 3. Completed: 1 and 3 | completed 1, next = item 2, not complete |
| P7 | Items 1, 2. Item 1 Stopped / Interrupted / Faulted | completed 0, next = item 1 |
| P8 | Items 1, 2, both Completed | completed 2, next null, complete |
| P9 | Items 1, 2, 3. Item 1 Completed, item 2 skipped | completed 1, skipped 1, next = item 3 |
| P10 | Calendar [c] + an active run at item 1 | `Calendar(c)`, with no run or item |
| P11 | Calendar [a, b] | `CalendarChoiceRequired`, no revision |
| P12 | No calendar; active run with item 1 completed | `Program(item2.revision, run, item2)` |
| P13 | Nothing | `Manual` |
| P14 | 4 items, start 08-10, mask Mon+Wed+Sat, project 08-10…08-17 | 08-10, 08-12, 08-15, 08-17 in item order |
| P15 | 4 items, start 08-10, Mon+Wed, project 08-17…08-17 | A single item with **position 3** |
| P16 | 3 items, start 08-10, mask 37. Override item1 → 08-11, item2 skipped. Extra item1 on 08-13 | Dates [08-11, 08-13, 08-15]. Item 2 absent. One repeat carrying its extra ID |
| P17 | Schedule start 08-06 (Thursday) with mask 37 | Rejected: "first training date" |
| P18 | Schedule time zone "Definitely/Not-A-TimeZone" | Rejected: "time zone" |
| P19 | 1 item, start 9999-12-31 (Friday), Friday, UTC; projectAll | One occurrence on 9999-12-31, no overflow |

### 12.5 Program runs: store and endpoint scenarios

| # | Given | Expected |
|---|---|---|
| S1 | Runner in Europe/Brussels, local date at 2026-08-09T22:30Z | 2026-08-10 |
| S2 | Runner in America/New_York, local date at 2026-08-10T02:00Z | 2026-08-09 |
| S3 | Two runners each start a 2-item program. Runner 1 has Stopped, Interrupted and Faulted linked sessions on item 1; runner 2 has a Completed **Manual** session | Both: completed 0, next = item 1 |
| S4 | S3 + runner 1 Completes linked item 1 | Runner 1: completed 1, next item 2, and item-2 validation passes. Runner 2 unchanged |
| S5 | S4 + runner 1 Completes linked item 2 | The run becomes **Completed** with `endedAt`. Validating item 2 now → rejected |
| S6 | Program rev1 (workout v1) started; rev2 (workout v2) appended | Validation: (rev1 item, v1) ✓; (rev2 item, v2) ✗ |
| S7 | S6 + restart | The new run is on rev2. Old run validation ✗. New run (rev2 item, v2) ✓ and (rev2 item, v1) ✗. The old run is **Abandoned** with `endedAt` = the restart time |
| S8 | Item with primary P and alternative H, run started | The alternative is persisted. Validation with P ✓ and with H ✓ |
| S9 | Program (2 items) started with mask Mon+Wed from 08-10 for runner A and runner B. Clear preview for A as of 08-07 | `canApply`, 2 upcoming. Preview with B's ID on A's run → **404** |
| S10 | S9 apply | Outcome `canApply false`, count 2. A has no active run; B's run is unchanged. A's profile still exists. A's run is Abandoned |
| S11 | 3 items, mask 37 from 08-10. Preview MoveOne as another runner | **404** |
| S12 | S11: preview MoveFollowing item1 → 08-11 | `canApply`, 3 impacts, new dates [08-11, 08-13, 08-16] |
| S13 | Apply S12 with version 1 | runVersion 2. A Skip with stale version 1 → **409**. A Skip with version 2 → version 3, completed 0, skipped 1, next = item 2, item 1 not projected |
| S14 | S13: preview MoveFollowing on the skipped item1 → 08-12 | `canApply false`, message contains "skipped" |
| S15 | 2 items, Mon+Wed from 08-10. MoveOne or MoveFollowing item2 → 08-09 | Both `canApply false`, message contains "before" |
| S16 | 3 items, mask 37, item1 Completed. Preview Repeat item1 → 08-12 | `canApply` **true**, collisions [08-12], one repeat impact |
| S17 | Apply S16 | completed 1, next item 2. One repeat of item 1 on 08-12 |
| S18 | S17: preview RepeatAndShift item1 → 08-15 | `canApply`, 3 impacts (1 repeat + 2 shifted). runVersion = the version after S17 |
| S19 | 3 items, mask 37, item1 Completed. Preview MoveOne item1 → Tue 08-11 | `canApply`, a single impact 08-10 → 08-11. Skip / Restore of item 1 → `canApply false`, message contains "Completed" |
| S20 | Apply S19 | completed 1, next item 2, base item 1 on 08-11. The linked session keeps its original start and end times and its Completed state |
| S21 | 3 items, mask 37, item1 Completed. MoveFollowing item1 → 08-12 | `canApply`, new dates [08-12, 08-14, 08-17]. After apply the canonical dates are the same. Progress and the linked session are unchanged |
| S22 | 6 items, mask 37 from 08-10, today 08-04. Item1 Completed; item2 MoveOne → 08-16; item3 Skip; Repeat item1 → 08-14. Then preview training days Tue+Thu+Sun, effective 08-10 | `canApply`, runVersion = after the repeat, 3 impacts with new dates [08-18, 08-20, 08-23], no collisions, `preservedExceptionCount` 4, revision length 64 |
| S23 | Apply S22 | Version +1, mask = Tue+Thu+Sun. Item1 base on 08-10, repeat on 08-14, item2 on 08-16, item3 absent, item6 on 08-23 |
| S24 | S23: preview back to mask 37, effective 08-17 | `canApply`. No impact for item 2 or item 3. After apply: the repeat is on 08-14, item 2 on 08-16, item 3 absent |
| S25 | 4 items, mask 37 from 08-10. MoveOne item1 → 08-12 | `canApply false`, collisions [08-12], message contains "empty date". Apply → **400** |
| S26 | S25: MoveFollowing item2 → 08-10 | `canApply false`, collisions [08-10] |
| S27 | S25: MoveOne item1 → 08-18 (applied). Then preview training days Tue+Thu+Sun, effective 08-10 | `canApply`, no collisions, every new date after 08-18. Apply succeeds |
| S28 | 5 items, mask 37 from 08-10. MoveOne item2 → 08-16. Then training days Mon+Wed+Sun, effective 08-10 | `canApply`. Item 3's new date is after 08-16. After apply the base dates are strictly increasing by position and distinct |
| S29 | 4 items, mask 37 from 08-10, plus a calendar series (Tuesdays 08-11…08-18). MoveOne item1 → 08-18 | `canApply false`, collisions [08-18] |
| S30 | S29: training days Tue+Thu+Sun, effective 08-10 | `canApply false`, collisions contain 08-11 |
| S31 | 1 item, Monday from 08-10. MoveOne → 2027-08-10 (applied). Then preview MoveOne → 2028-08-09 | `canApply` (the 365-day limit is measured from the **current** date) |
| S32 | 3 items, mask 37. Training days Tue only | `canApply false`, "exactly 3" |
| S33 | Training days Tue+Thu+Sun with effective 08-03 and today 08-04 | `canApply false` |
| S34 | Apply training days with revision "000…0" (64 zeros) | **409** |
| S35 | Personal program (owner A) and household program | A lists both. B lists only the household program. Owner fields: A / null |
| S36 | Create (op C) twice | Same program ID and revision ID, 201 both times, 2 items |
| S37 | Start (op S) twice; then a new op with no expected active run | 200 with the same run ID both times; then **409** |
| S38 | After S37, list for the runner | The run is present, completed 0, next = item 1 and its revision |
| S39 | Program [W, W]. Start with mask 37 from next Monday M. Complete a linked session of item 1. Repeat item1 → M+1 | The M option: not a repeat, `isCompleted`, position 1. The M+1 option: `isRepeat`, not completed, same run and item, a non-empty `extraOccurrenceId`, `originalDate` null |
| S40 | Program [W, W], mask 37 from Monday M, item1 Completed. MoveFollowing item1 → M+1 | Impacts: item1 M → M+1, item2 M+2 → M+3. Apply: version +1. Calendar M…M+3 shows only M+1 (item1: completed, `originalDate` M) and M+3 (item2: not completed, `originalDate` M+2). The session history selection still points to the run and item 1. Summary: completed 1, next = item 2, run Active |

### 12.6 Premade catalog and installation

| # | Given | Expected |
|---|---|---|
| T1 | Catalog | 16 templates with unique IDs. Every version parses. `sessionCount = weeks × sessionsPerWeek`. Positions 1..n. No description contains "rehab" |
| T2 | `5k-to-10k-distance-first-58` | 58 weeks, 174 sessions, 58 distinct weeks, 4 phases, version 2.0.0, 260 variants, 86 sessions with one alternative, 65 `hr-alternative`, 21 `fixed-fallback` |
| T3 | Build its session 1 | Title "W01D1 · Long easy: 10 x 1 min at 8.0 km/h". 21 steps. First and last are 5 min at fixed 4.5. No fixed-0 step |
| T4 | W11 S1's alternative, step 2 | `heartRateZone(2, 7.5, 4, 10)` |
| T5 | HR templates | Exactly 2. Every session's step 2 is `heartRateZone` with a zone in 1–5 |
| T6 | `first-5k` hash computed twice | Equal, 64 characters |
| T7 | Every template file in `data/premade` | Rebuilding with the Kotlin generator (7.2 / 7.3) and canonical writer reproduces every `definitionSha256`, `contentSha256`, key, name, phase and count |
| T8 | The WalkingPad source file | SHA-256 = `c476161e…bdff3` |
| T9 | Install the WalkingPad plan (op I) for runner A (all zones) | 201. positionCount 174. uniqueWorkoutCount between 174 and 260 (260 with no normalisation) |
| T10 | Replay op I | 201 again, same program ID, `replayed: true` |
| T11 | Same request with a new op | **200**, `alreadyAdded: true`, same program ID |
| T12 | A's program list | Contains it: `templateId` set, owner A, itemCount 174, no `items` field. Detail: 174 items; item 1 phase "Foundation", week 1 |
| T13 | Runner B's program list | Does not contain it |
| T14 | Force the generated workouts' kind to `Structured`, then list library workouts | No `PlanInternal` kind, and no description starting "Premade plan workout" (template provenance hides them) |
| T15 | Start A's copy as B | **403** |
| T16 | Start as A: start 08-10, mask 37, Europe/Brussels | 200. The run has start 08-10 and mask 37. Calendar 08-10…08-16: days 08-10, 08-12, 08-15; the first option `Program` with positions 1, 2, 3 and total 174 |
| T17 | Preview MoveFollowing item1 → 08-11 | `canApply`, **174 impacts**. Apply → version +1. Calendar 08-10…08-17: the first three days are 08-11, 08-13, 08-16 |
| T18 | Skip item1 | Summary: `skippedItemCount` 1, next = item 2 |
| T19 | Install again (a `freshCopy` flag in the request is ignored) | 200, same program, copyNumber 1, `alreadyAdded` |
| T20 | Append a revision to the installed program | **409** (immutable) |
| T21 | Install `getting-started` (201), archive it (204) | Catalog `alreadyAdded` false. Install again → 201, `alreadyAdded` false, **new program ID, copyNumber 2**, stored name "Getting Started". After renaming the stored revision to "Getting Started · Copy 2", the list still shows "Getting Started" |
| T22 | HR 5K preview for a runner with Z1–Z5 | `compatible` and `heartRateZonesReady` true, template ID echoed |
| T23 | HR 5K preview for a runner with only Z1 | Both false. The message contains "heart-rate zones" |
| T24 | Install `getting-started`, start mask 37 from 08-10 (fixed now 2026-08-04T10:00Z). Training-days preview as another runner | **404** |
| T25 | T24: preview mask 69, effective 08-10 | `canApply`, current mask 37, new mask 69, impacts non-empty. The calendar before apply shows positions 1–12 |
| T26 | Apply (op D); replay op D; op D with mask 44 | 200 (version +1); 200 with the same version (and the calendar is identical); **409** |
| T27 | Positions after T26 (08-10…09-06) | 1: 08-10, 2: 08-12, 3: 08-16, 4: 08-17, 5: 08-19, 6: 08-23, 7: 08-24, 8: 08-26, 9: 08-30, 10: 08-31, 11: 09-02, 12: 09-06 |
| T28 | Reverse: preview mask 37, effective 08-12, then apply | Preview `canApply`, current 69, new 37. Positions: 1: 08-10, 2: 08-12, 3: 08-15, 4: 08-17, 5: 08-19, 6: 08-22, 7: 08-24, 8: 08-26, 9: 08-29, 10: 08-31, 11: 09-02, 12: 09-05 |

### 12.7 Goals, trends, adviser, receipts

| # | Given | Expected |
|---|---|---|
| G1 | Adviser: adherence 95, RPE 5, telemetry ok, HR coverage 90 | `Advance` |
| G2 | Adherence 62, RPE 8 | `Reduce` |
| G3 | Adherence 95, RPE 9 | `Reduce` |
| G4 | Adherence 90, RPE 6, telemetry incomplete | `Repeat` |
| G5 | Any input with missed = 1 | `Reschedule` |
| G6 | Adherence 85, RPE 7, telemetry ok | `Maintain` |
| G7 | Every result | algorithm `local-progression-v1`, non-empty reason, requires confirmation |
| G8 | Trend facts: hardware (5 km, complete), hardware (4 km, incomplete), simulator 99, system test 99 | 2 sessions, 9 km, 1 incomplete, longest 5 km |
| G9 | Save 6 goals (Distance, Minutes, Sessions × Weekly, Monthly with targets 15, 120, 3, 60, 480, 12) | Each version 1. The list has 6, all enabled |
| G10 | Completed hardware sessions: 1 day ago (30 min, 4.25 km) and 14 days ago (40 min, 6.5 km) | Weekly: 1 run, 30 min, 4.25 km. Monthly: 2 runs, 70 min, 10.75 km. Insights include 6 goals |
| G11 | Save an existing goal without an expected version, or with a stale one | 409 |
| G12 | Decide a recommendation twice | The second → 409 |
| G13 | Receipt (op R) added, then added again with another row ID | The first insert succeeds; the second is refused. `find(R)` returns the same type, status, outcome and fingerprint |
| G14 | Receipts created 91 days and 89 days before now; prune at now − 90 days | 1 removed; the 91-day one is gone, the 89-day one kept |
| G15 | Two concurrent creates with the same op ID but different bodies | One 201, one 409. Replaying the winner → 201 |
| G16 | Two concurrent identical creates | Both 201 with the same entity ID |

---

## 13. Test checklist for the Kotlin implementation

**Calendar**
- [ ] Weekday rotation R1–R5; recurrence validation R10; interval anchoring R9; DST stability R7–R8.
- [ ] `resolveDay` / `resolveRange` D1–D4 (Skip, Replace-on-base-only, Add anywhere, profile filter, ordering, dedupe); a property test comparing against a brute-force evaluation.
- [ ] Day selections D5, C3–C4; selection cleanup on move, delete and delete-group.
- [ ] Series create, update, transfer refusal, missing references: C1–C2, C14–C16.
- [ ] Move only / move this and later / delete one / delete group, including the split, rotation, shared group ID and continuation version: C5–C9.
- [ ] Collision guards: C10 (another group), C11 (later shifted date), C12 (backward overlap), C13 (added-only exception), with the exact messages.
- [ ] Merged range: the ≤ 62-day limit, the max-date read C17–C18, option ordering, and the program-option fields including `isCompleted` and repeat identity (S39–S40).

**Programs and runs**
- [ ] Revision validation and limits P1–P5, 4.1 (1,000 items, 20 alternatives, alternative ≠ primary).
- [ ] Revision content hash (upper-case) and uniqueness; custom revision append; premade immutability T20.
- [ ] Visibility and canonical listing (S35, T12–T13, T21 display name); archive hides and allows re-install.
- [ ] Start/restart: expected revision, expected active run, 403 for another owner, day count for templates, abandoning the previous run, pinned revisions S6–S7, S36–S38.
- [ ] Projection P14–P16, P19; schedule validation P17–P18; run-zone "today" S1–S2.
- [ ] Progress P6–P9; recommendation P10–P13 plus the UI refinements in section 8.
- [ ] Advancement: only Completed + linked (+ **Hardware** origin) advances S3–S5; the next-item validation and alternatives S8; the unique completed-item constraint; run completion.
- [ ] Schedule actions: every blocked reason and message in 5.4; S11–S21, S25–S26, S29, S31; apply recomputes the preview, 400 on blocked, 409 on stale; repeats never advance progress.
- [ ] Change training days S22–S24, S27–S28, S30, S32–S34, T24–T28, including the revision hash, pinning, and replay/conflict.
- [ ] Clear upcoming S9–S10: preview, apply and runner isolation.
- [ ] All schedule writes are serialised and transactional with their receipt.

**Premade**
- [ ] Load `data/premade`; counts and hashes T1–T2, T6, T8.
- [ ] The generator (7.2) and the WalkingPad materialiser (7.3) reproduce every definition hash T3–T5, T7.
- [ ] Preview: HR readiness T22–T23, capability normalisation, messages.
- [ ] Install: idempotency, replay, alreadyAdded, copy numbers after archive, dedupe by hash, `PlanInternal` kind, profile scope, legacy `Structured` hiding T9–T14, T19, T21.
- [ ] Scheduling an installed plan and adjusting a 174-item plan T15–T18.
- [ ] Provenance text shown in the catalog detail (7.6); no medical claims.

**Goals, advice, receipts**
- [ ] Goal upsert, uniqueness, versioning, rolling-window progress G9–G11.
- [ ] Trends G8; adviser table G1–G7 (plus the Stopped → Interrupted decision).
- [ ] Recommendation persistence: one per runner + session, pending refresh, the decision receipt G12, never mutating plans.
- [ ] Receipts: replay, conflict, the concurrency race, 404 receipting, 90-day pruning G13–G16; the fingerprint scopes in 10.2.
