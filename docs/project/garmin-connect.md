---
title: Garmin integrations
type: integration-runbook
status: implemented-setup-required
owner: project
audience: runner-operator-and-developer
updated: 2026-08-22
---

# Garmin integrations

TreadmillRunner deliberately separates three Garmin paths because they have different trust, support, and duplicate risks.

| Path | Purpose | Support level | Account/developer requirement | Default |
|---|---|---|---|---|
| Connect IQ companion | Explicitly record a native treadmill activity on the runner's watch; optionally display the local session | Garmin public Connect IQ APIs; source prepared, SDK/store acceptance pending | Garmin developer account/key for IQ Store publishing; ordinary Garmin account for watch sync | Standalone recording available after installation; gateway pairing optional |
| Completed-activity upload | Reconcile a completed TreadmillRunner run with a watch activity, or upload the local FIT when no single match exists | **Unsupported private Garmin consumer interface** through pinned `garminconnect` adapter | Runner's own Garmin credentials and possibly MFA; no Connect Developer Program approval | Disabled per profile; duplicate mode defaults to `PreferWatch` |
| Official training sync (TR-011) | Publish workouts/plans/calendar content to Garmin | Supported Garmin Connect Developer Program Training API | Approved program credentials and Garmin-supplied contract | Disabled until approved/configured |

When enabled, TreadmillRunner waits five minutes after local completion and looks for a Garmin treadmill activity with a start within ten minutes, close duration and distance, and corroborating heart-rate summary/curve. No match uploads the local FIT. Multiple plausible matches also upload the local FIT and leave manual duplicate cleanup to the runner; ambiguity never authorizes deletion.

## Recommended household choices

- If a watch is worn: use `PreferWatch` to retain the native activity and its Garmin/watch-derived fields, or explicitly choose `MergeAndReplace` to overlay TreadmillRunner telemetry into the original watch FIT.
- If no watch is worn: enable completed-activity upload for that runner before the run. The NUC queues the completed local FIT automatically.
- If only workouts/plans need to appear on Garmin devices: use the official Training API path after Garmin approval. It does not upload completed activities.

## Experimental completed-activity upload

### Profile setup

1. Install a current signed TreadmillRunner release. The pinned adapter runtime is included and checked offline during packaging.
2. Open TreadmillRunner on the NUC, over trusted HTTPS, or from a device whose direct peer address is on the trusted household LAN. Private-LAN HTTP is allowed for convenience but is not encrypted.
3. Open **Profiles**, edit the runner, and find **Garmin activity upload — Experimental**.
4. Enter that runner's Garmin email and password. The password is streamed once to an isolated Python process and is never persisted.
5. If Garmin requests MFA, enter the current verification code. Challenges expire after five minutes and are bound to the selected profile.
6. Choose duplicate handling for this runner's connection. `PreferWatch` is the safe default. `MergeAndReplace` is explicit opt-in and uses Garmin's unsupported download/import/delete consumer endpoints.
7. Explicitly enable automatic upload when ready.

Each household profile has an independent protected account envelope, enable switch, jobs, failures, and disconnect action. An MFA challenge is cryptographically bound to its selected profile. TreadmillRunner intentionally has no household login: any trusted-LAN operator can administer either profile, so the application must not be exposed to a guest or public network.

After the one-time login completes, unattended operation does not require the password, the browser, or an interactive Windows session. The NUC stores only the resulting Garmin session token in the existing DPAPI-backed encrypted envelope. The background worker resumes after service or machine restart and uploads eligible completed sessions for profiles that remain enabled. If Garmin expires the session or requires MFA again, reconnect that profile from the NUC, trusted HTTPS, or the private household LAN.

### One-time upload acceptance

The operator may explicitly call `POST /api/integrations/garmin/activity-upload/profiles/{profileId}/test-activity` with a fresh `operationId` and the connected account's `expectedVersion`. The route is idle-only and limited to the same local/private/HTTPS transport policy. It creates a clearly labelled one-minute synthetic completed session with 60 samples, then uses the normal `SessionFitActivityExporter`, durable queue, encrypted account token, and worker. It never issues a treadmill command.

Treat the operation ID as single-use. A repeated ID is rejected. Poll the profile job list until it is `Confirmed`, `Failed`, or `Unknown`; an `Unknown` test must be reviewed in Garmin Connect and must not be blindly resent. The synthetic session remains visible in local History so the exact FIT source is auditable.

The pinned library can return `{"status":"uploaded"}` after a successful import HTTP response without exposing Garmin's activity ID. TreadmillRunner treats that documented library-success shape as Confirmed with an empty remote ID. Empty, malformed, interrupted, or otherwise unrecognized responses remain Unknown and are never automatically retried.

### Queue behavior and duplicate handling

- Enabling records a UTC watermark. Only sessions ending after that explicit enable are eligible, so connecting, disconnecting, or reconnecting cannot upload old history unexpectedly.
- The worker reconciles completed/stopped sessions at least once per minute, but a normal job is not eligible until five minutes after the local session ended. Synthetic acceptance tests bypass the watch search and remain immediate.
- Exactly one strong watch match follows the connection's selected behavior. `PreferWatch` records the matched remote ID/evidence and skips local upload. `MergeAndReplace` downloads the original FIT, preserves its watch/proprietary messages, overlays local distance/speed/incline/heart-rate summaries and samples, then imports the merged FIT.
- In merge mode, the original watch activity is deleted only after Garmin confirms the merged import with a distinct activity ID. A missing ID, duplicate rejection, timeout, interrupted response, or other uncertainty retains the original. Once replacement upload is durable, deletion is a separate auditable phase so service restart cannot lose its IDs.
- No or multiple plausible matches cause the normal local upload. Multiple matches are deliberately left for the runner to resolve manually.
- A job is unique by local session and has a deterministic SHA-256 idempotency key over exporter version, profile, and session. Leasing uses an atomic status/attempt compare-and-set so two workers cannot upload the same pending job.
- The gateway exports FIT from authoritative local session history into a temporary file, invokes the adapter, and deletes the temporary file.
- Confirmed uploads are terminal. Refreshed Garmin tokens replace the prior encrypted token envelope.
- Known authentication or provider failures use bounded attempts/backoff and are visible with the matching workout, start, duration, and History link. Only a known retryable provider failure can be retried explicitly. Authentication requires reconnect; provider-declared duplicate/rejection is terminal and dismiss-only.
- A timeout, interrupted in-flight lease, or ambiguous response is **Unknown**. Unknown is never retried automatically or by the normal retry action, because doing so can create a duplicate. Review Garmin Connect, then dismiss the local warning; upload manually only if absence is certain.
- A started upload cannot be cancelled safely. Disconnect is rejected while a request is in flight so its Confirmed/Failed/Unknown audit outcome cannot be erased. Wait for that outcome and disconnect again; a successful disconnect then deletes the profile's local protected token and queue. It does not delete activities already present in Garmin Connect.

### Credential and process security

- ASP.NET Core Data Protection encrypts Garmin session tokens at rest. Production keys use the release-independent service key directory and Windows DPAPI described in the release runbook.
- Garmin passwords and MFA codes are never written to SQLite, configuration, logs, screenshots, diagnostics, or API responses.
- The adapter communicates with the gateway as bounded JSON Lines over redirected standard input/output. Secret-bearing input is not used as a command-line argument.
- The browser receives account labels, state, safe errors, counts, and job dispositions—never protected token material.
- Upload account changes are idle-only. Credential requests are accepted via HTTPS or from a direct loopback/private/link-local peer. Private-LAN HTTP carries the one-time password and MFA without transport encryption; use it only on the trusted household network.
- Never expose or port-forward TreadmillRunner. The installer firewall rule remains limited to the Windows Private profile/local subnet. Public/non-local HTTP peers are rejected with HTTP 426, but NAT or a reverse proxy can hide the original peer address and must not be used to bypass this boundary.
- Provider behavior is not guaranteed. Garmin may change or block the private consumer interface at any time.

### Runtime, installation, and readiness

The external reference is pinned to `garminconnect==0.3.8` and reviewed against repository commit `091cad8f8caeb1dbaa0b7d62679c725c12dee458`. Provenance is recorded in `automations/reference-refresh/artifacts/references/cards/python-garminconnect.md`. `requirements.lock.txt` fixes every transitive Windows x64/CPython 3.12 wheel and SHA-256. Release creation downloads the pinned official CPython 3.12.10 embeddable archive, verifies its fixed SHA-256, installs only hash-locked binary dependencies into the immutable release, retains license metadata, and runs a credential-free `probe` using that exact bundled runtime.

Normal installation performs no Python, `pip`, or package download step. The service runs `tools\garmin\runtime\python.exe` and `tools\garmin\garmin_activity_adapter.py` from the current immutable release. Every update package and end-user installer is rejected during creation if the offline probe does not report `ready`.

The profile status reports one of these safe states:

| Adapter state | Meaning | Operator action |
|---|---|---|
| `Ready` | Bundled runtime, adapter, and dependency import passed | The local/HTTPS sign-in form is available |
| `RuntimeMissing` | Python executable is absent or cannot start | Install or repair the current signed release |
| `DependencyMissing` | The pinned adapter dependency cannot import | Install or repair the current signed release |
| `AdapterInvalid` | Script/configuration or probe response is invalid | Restore signed defaults or repair the release |
| `Unavailable` | A bounded local probe failed or timed out | Retry once, then repair the signed release |

Readiness never contacts Garmin and never includes credentials, protected tokens, internal command lines, or sensitive paths. Connection returns HTTP 503 with only the safe state/message when readiness is not `Ready`.

Developers may still override `GarminActivityUpload__PythonExecutable`, `GarminActivityUpload__AdapterScriptPath`, and `GarminActivityUpload__PythonPath` with absolute paths. This is a local-development fallback only. A relative configured path is constrained to the application content root; do not use a per-user `python` for the Windows service. The legacy `eng/install-garmin-adapter.ps1` remains a developer aid and is not part of normal installation.

### Status and recovery

| State | Meaning | Action |
|---|---|---|
| Disconnected | No protected token envelope exists | Connect locally/over HTTPS if this runner wants unsupported upload |
| Connected, disabled | Account is ready but no completed sessions are queued | Enable explicitly only for runs not recorded on the watch |
| Pending | Job is waiting for the five-minute watch check, upload, or confirmed-replacement cleanup phase | Wait for the next worker interval |
| Confirmed | Garmin returned a successful import result | Verify activity in that runner's Garmin Connect history |
| FoundInGarmin | One strong watch match was retained under `PreferWatch` | No upload is required; review the stored match evidence if needed |
| Failed | Known provider/authentication error | Correct network/authentication; reconnect for auth errors or select retry for a known provider failure |
| Unknown | Request may have reached Garmin, but confirmation was lost | Check Garmin Connect; do not retry blindly; dismiss after review |
| Adapter setup required | Bundled runtime/script/dependency missing, invalid, or unavailable | Install or repair the current signed release, select **Check again**, and leave upload disabled until `Ready` |

Backups contain encrypted token ciphertext and queue state. A restore under a different Windows DPAPI identity may make the token unreadable; disconnect/reconnect that runner. Never copy a database or Data Protection key ring to an untrusted host.

## Connect IQ companion

The companion uses Garmin's public ActivityRecording API with running/treadmill sport metadata. The watch—not the NUC—records and saves the activity. Pairing only calls the read-only `/api/watch/status` route and never exposes treadmill commands. See [Connect IQ companion setup, testing, and IQ Store release](connect-iq-companion.md).

## Official Training API path

TR-011 remains separate. It uses OAuth/PKCE, profile-owned encrypted tokens, and a durable publication outbox for supported structured workouts, plans, and calendar items. Garmin's detailed Training API contract is available after program approval, so production publication remains intentionally unavailable until approved endpoint/payload documentation and credentials exist. Do not point it at private consumer routes or reuse completed-activity tokens.

Service configuration is documented by `GarminConnect__*` settings. `Provider=Mock` is development/test only; `Provider=Configured` additionally requires an independently implemented and fixture-tested approved contract adapter.

Official references:

- [Garmin Connect Developer Program](https://developer.garmin.com/gc-developer-program/overview/)
- [Garmin Training API](https://developer.garmin.com/gc-developer-program/training-api/)
- [Garmin Activity API](https://developer.garmin.com/gc-developer-program/activity-api/)
- [Garmin Connect IQ overview](https://developer.garmin.com/connect-iq/overview/)
- [python-garminconnect upstream](https://github.com/cyberjunky/python-garminconnect)

## Removal

1. Disable completed-activity upload on the runner profile.
2. Disconnect the unsupported account to delete its local protected token/job aggregate.
3. Revoke the Connect IQ watch binding from the same profile.
4. Remove the app from the watch through Garmin Connect Mobile/IQ Store if no longer wanted.
5. Optionally remove `C:\ProgramData\TreadmillRunner\garmin-python` during a maintenance window after resolving its absolute path and confirming no profile uses the adapter.
6. Revoke any applicable Garmin sessions from the runner's Garmin account. Activities already uploaded remain until deleted in Garmin Connect.

No removal action sends a treadmill command or deletes local TreadmillRunner session history.
