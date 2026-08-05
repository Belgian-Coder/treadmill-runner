---
title: Planning Data and Import Flow
type: architecture
status: active
owner: project
audience: developer-and-operator
updated: 2026-08-05
---

# Planning data and import flow

TR-003 persists profiles, immutable workout revisions, recurring calendar alternatives, and confirmed import evidence in SQLite. Workout definitions remain versioned canonical JSON; calendar references always point to an exact revision.

## Data model

[![Data model diagram](diagrams/planning-data-data-model.svg)](diagrams/planning-data-data-model.svg)

Source: [Mermaid](diagrams/planning-data-data-model.mmd)

The application never updates or deletes a workout revision. Editing appends another revision; existing calendar choices and future session history keep their original revision ID.

## Movable recurring schedules

TR-010 gives every `CalendarSeries` a durable `ScheduleGroupId`. A normal recurring schedule starts as one series whose group ID equals its own ID. Moving only one occurrence creates a skip exception on the original date and an add exception on the target date. Moving “this and later” preserves earlier dates, splits the recurrence at the selected date when necessary, rotates the weekday mask by the date difference, shifts future exceptions, and keeps every continuation segment in the same logical group.

TR-011 adds three profile-owned Garmin integration tables. `GarminAccountLinks` stores the stable external subject and Data-Protection ciphertext for tokens; both profile and external subject are unique. `GarminOAuthStates` stores a hashed one-time state plus protected PKCE verifier and expiry. `GarminSyncItems` is an idempotent versioned outbox for `Workout`, `TrainingPlan`, and `Calendar` publication. Disconnecting a profile link cascades through its state and queue without changing local workouts, calendar history, or another profile.

TR-013 keeps unsupported activity delivery structurally separate. `GarminActivityUploadAccounts` stores one profile-owned protected private-session token envelope, explicit enable flag, enable watermark, provider state, and optimistic version. `GarminActivityUploadJobs` binds one completed session to one deterministic idempotency key and tracks atomic lease, attempt, response disposition, safe failure kind, and remote ID. Unknown, duplicate, and rejected outcomes are terminal. Disconnect deletes that private account aggregate, while a later enable watermark prevents historical sessions from being queued again. `GarminWatchBindings` stores one profile-owned device label and SHA-256 token hash; the raw watch token is returned once over HTTPS/loopback and never persisted.

The calendar UI exposes the scope before it writes: **Move only this session**, **Move this and later**, **Delete only this session**, or **Delete complete workout group**. Deleting one session creates a skip exception. Deleting a complete group transactionally removes every continuation segment and its saved day selections; it never deletes immutable workout revisions or completed session history.

Every mutation has a unique operation ID, expected series version, deterministic request fingerprint, and persisted receipt. Identical replays return the stored outcome; reuse of an operation ID for another request is rejected. A concurrent edit returns a conflict and the UI reloads before another attempt.

## Ordered training plans

TR-003B keeps a training plan separate from a calendar series. A calendar answers *what is scheduled on a date*; a training plan answers *which exact workout revision comes next*. A plan root owns immutable revisions, and every revision contains contiguous ordered items that reference exact immutable workout revision IDs. Categories such as `5K`, `10K`, `Base`, and `Custom` are searchable labels rather than execution rules.

Each runner can have at most one active plan run. Starting another plan or restarting the current plan abandons the previous active run without rewriting its history. Existing runs remain pinned to the plan revision that was started; editing a plan creates a new revision, and only a later restart opts the runner into that revision.

A session selected from a plan persists its selection source, plan-run ID, and plan-item ID alongside the exact workout revision. The arm endpoint validates that this is the next expected item and binds the operation ID to that selection. Only a linked session ending `Completed` advances progression. Manual, library, and calendar sessions are unbound; `Stopped`, `Interrupted`, and `Faulted` attempts remain in history but do not advance. A unique completed-item constraint makes advancement idempotent.

Run recommendation priority is deterministic:

1. one selected or unambiguous workout scheduled for today;
2. an explicit choice when today has multiple calendar alternatives;
3. the next item of the runner's active training plan;
4. Manual run when neither calendar nor plan supplies a workout.

TR-005 adds `DeviceEnrollments` as a separate local operational aggregate. It stores at most one active row per `Treadmill` or `HeartRate` role using a filtered unique index. A row contains the Windows device identifier, protocol ID, SHA-256 identity fingerprint, display/model/firmware labels, explicit treadmill telemetry mode, serialized reported capabilities, evidence level and verification time, optimistic version, and archive metadata. Forgetting archives rather than deletes the row. The raw Windows identifier stays local and must be redacted from shared evidence and diagnostic bundles.

## Preview and confirm

[![Preview and confirm diagram](diagrams/planning-data-preview-and-confirm.svg)](diagrams/planning-data-preview-and-confirm.svg)

Source: [Mermaid](diagrams/planning-data-preview-and-confirm.mmd)

Previewing writes nothing to SQLite. Confirmation reparses the bounded original bytes, so a client cannot replace the normalized definition between preview and commit.
