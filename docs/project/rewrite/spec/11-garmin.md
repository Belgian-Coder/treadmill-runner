---
title: Garmin — activity upload, watch matching, FIT merge, Connect IQ companion
type: spec
status: draft-v1
owner: project
audience: agent-and-developer
updated: 2026-09-24
---

# 11 — Garmin

This spec covers everything Garmin-related that the Kotlin phone app must re-implement:

- the **unofficial completed-activity upload**: login with MFA, token handling, watch-activity search and matching, upload, and the `MergeAndReplace` FIT merge with guarded delete;
- the **job state machine**, worker scheduling and every recovery action (Merge into one, Undo merge, latest-two repair);
- the **Connect IQ companion watch app**;
- the **official Training API** path, which stays **parked**.

The current system runs upload through an isolated Python process that wraps the `garminconnect` library. The Android app does it **in Kotlin on the phone** (stories GAR-00, GAR-01 and GAR-02 in [00-plan.md](00-plan.md)). This document describes the Python adapter's contract in full because it is the **behavioural reference**: the Kotlin client must produce the same dispositions for the same Garmin responses.

Supporting data lives in `data/garmin/`:

| File | Content |
|---|---|
| `adapter-protocol.jsonl` | Every adapter request and response line, with notes |
| `adapter-disposition-vectors.json` | Import-result interpretation and error classification vectors |
| `adapter-search-vectors.json` | Watch-search paging, filtering and bounds vectors |
| `matcher-vectors.json` | Watch-activity matcher and canonical-copy vectors |
| `latest-two-planner-vectors.json` | Latest-two reconciliation planner vectors |
| `idempotency-and-identity-vectors.json` | Idempotency key hashes, FIT serial numbers, token scheme |
| `job-state-machine.json` | Statuses, phases, lease classes, timers, transitions |
| `upload-worker-scenarios.json` | Worker, job-store and login scenarios translated from tests |
| `fit-merge-vectors.json` | Merge fixtures and before/after field summaries |
| `fit-semantics-vectors.json` | Strict FIT-equivalence rules and vectors |
| `transport-policy-vectors.json` | Which peers may send credentials over HTTP |
| `connectiq-contract.json` | Watch app settings, pairing token, status endpoint |

Cross-references: the FIT export itself (records, laps, calories, elevation, zones) is in [07-exports-and-backup.md](07-exports-and-backup.md). Session states, origins and deletion are in [05-sessions-and-recording.md](05-sessions-and-recording.md). The H10 recording lifecycle that gates Garmin export is in [10-polar-h10.md](10-polar-h10.md). Profiles are in [06-profiles-and-heart-rate.md](06-profiles-and-heart-rate.md).

---

## 1. The three Garmin paths

| Path | Purpose | Support level | Default |
|---|---|---|---|
| **Connect IQ companion** (watch app) | The runner records a native Garmin treadmill activity on the watch. Optionally the watch shows the TreadmillRunner runner and session. | Public Connect IQ APIs | Standalone recording works once installed; pairing is optional |
| **Completed-activity upload** (unofficial) | After a run, find the matching watch activity, or upload the app's FIT when no watch recorded the run. | **Unsupported private Garmin consumer interface.** Garmin may change or block it at any time. | Disabled per profile. Duplicate handling defaults to `PreferWatch` |
| **Official Training API** | Publish workouts, plans and calendar items to Garmin | Supported Garmin Connect Developer Program | **Parked** (needs program approval) |

**FIT share is the always-available fallback** (GAR-06). Every session can be shared as a FIT file through the Android share sheet, or downloaded from the web UI and imported by hand at connect.garmin.com. It never depends on the unofficial client.

**Recommended household choices** (shown to users in this spirit):

- Watch worn: keep `PreferWatch`. The native watch activity stays, with its Garmin-derived fields. `MergeAndReplace` is an explicit opt-in that overlays TreadmillRunner telemetry on the watch timeline.
- No watch worn: enable completed-activity upload before the run.
- Only structured workouts on the watch: that is the Training API path (parked), or a FIT workout file (see [03-import-export-formats.md](03-import-export-formats.md)).

---

## 2. Account model (one per profile)

Each runner profile has **at most one** upload account and **at most one** watch binding. They are fully independent between profiles.

### 2.1 Upload account fields

| Field | Type | Rules |
|---|---|---|
| `id` | UUID | |
| `userProfileId` | UUID | Unique; cascade-deleted with the profile |
| `accountLabel` | string 1..160 | Garmin `displayName` from the social profile, else the email, trimmed |
| `protectedTokenStore` | string ≤ 32 768 | The encrypted token JSON (§5.3). Never sent to a browser |
| `enabled` | bool | Automatic upload switch |
| `watchActivityHandling` | `PreferWatch` \| `MergeAndReplace` | Default `PreferWatch` |
| `state` | `Connected` \| `NeedsAuthentication` \| `ProviderUnavailable` | See §7.8 |
| `connectedAtUtc` | instant | |
| `uploadFromUtc` | instant? | **Enable watermark** (§2.2) |
| `lastUploadSuccessAtUtc` | instant? | Set on Confirmed, on delete-original success and on undo completion |
| `lastError` | string ≤ 1000? | Safe text only |
| `version` | int > 0 | Optimistic concurrency; every settings call passes `expectedVersion` |
| `updatedAtUtc` | instant | |

### 2.2 Enable watermark (`uploadFromUtc`)

- On first connect with `enabled = true`: `uploadFromUtc = now`.
- On reconnect of an existing account: it is set to `now` only if it was null and `enabled` is true. Otherwise it is kept.
- On settings change from disabled to enabled: `uploadFromUtc = now`.
- Only sessions with `endedAt >= uploadFromUtc` are ever queued. Connecting, disconnecting, reconnecting or re-enabling therefore **never uploads old history**.
- Disconnect deletes the account (and its jobs), so a later connect starts a fresh watermark.

### 2.3 Watch activity handling

| Mode | On exactly one strong watch match |
|---|---|
| `PreferWatch` (default, "Keep the watch activity") | Record the match (`FoundInGarmin`). Upload nothing. Garmin keeps the watch's training metrics, running dynamics and other watch-derived fields. |
| `MergeAndReplace` ("Merge and replace the watch activity") | Download the original watch FIT, build a merged FIT (§9), upload it, then delete the original only after the merged copy is **proven** in Garmin. If anything is uncertain, both activities are kept for manual review. |

With no match, both modes upload the app's own FIT. With more than one plausible match, both modes stop in `ReviewRequired`.

### 2.4 Token storage (phone)

- Only the session **tokens** are stored, never the password or MFA code.
- On Android the token JSON is encrypted with an **Android Keystore** AES-GCM key (non-exportable). This is acceptance criterion AC2 of GAR-01.
- Tokens are **not** migrated from the old app and cannot be restored on another phone. After such a restore, the account shows "reconnect" (see [07-exports-and-backup.md](07-exports-and-backup.md) and 00-plan §7).
- Every Garmin call that returns a (possibly refreshed) token store replaces the stored encrypted blob in the same transaction as the job update.
- Passwords and MFA codes are never written to the database, settings, logs, screenshots, diagnostics or API responses.

### 2.5 Credential transport (web UI)

Credential-bearing and account-mutating web requests are accepted over **HTTPS from any peer**, or over **plain HTTP only from loopback, private or link-local peers** (IPv4 10/8, 172.16/12, 192.168/16, 169.254/16, 127/8; IPv6 ::1, fc00::/7, fe80::/10; IPv4-mapped addresses are unwrapped). Other peers get **HTTP 426**. This applies to connect, MFA, test activity, historical recovery and latest-two repair. Vectors: `transport-policy-vectors.json`.

Watch-token creation is stricter: HTTPS, or loopback only.

All account changes (connect, MFA, settings, disconnect, test activity, recovery, repair) require an **idle runner**: no session in `ArmedWaitingForPhysicalStart`, `Running` or `PausedWaitingForPhysicalResume`. Otherwise the answer is 409.

### 2.6 Disconnect

- Requires `expectedVersion`.
- **Rejected while any job of the account is `InFlight`**: "An activity upload has already started and cannot be cancelled safely. Wait for its outcome, then disconnect."
- On success it deletes the account and its job rows (cascade). It does **not** delete anything in Garmin Connect.

---

## 3. Login and MFA

### 3.1 User flow

1. The profile's Garmin section shows **Garmin activity upload — Experimental**, disabled by default.
2. The user enters the Garmin email (≤ 254 characters) and password (≤ 512 characters), chooses duplicate handling, and optionally ticks "Enable automatic upload after connecting".
3. If Garmin asks for MFA, a **Garmin verification code** field appears (4..16 characters, numeric keyboard, `one-time-code` autocomplete).
4. Result: `Connected` (with enabled/disabled message) or `Failed`.

### 3.2 Pending-login (MFA challenge) rules

These are the server-side rules. The Kotlin app keeps the half-finished login in memory:

- A challenge has a random `challengeId` and is **bound to the profile**. Completing it with a different profile answers "The Garmin verification request does not belong to this runner." (404).
- It **expires after 5 minutes**: "The Garmin verification request expired; start again." (404).
- Starting a new login for the same profile **discards** that profile's pending challenge.
- At most **4** pending challenges exist globally: "Too many Garmin verification requests are already pending; complete or wait for one to expire."
- A challenge is single-use. It is removed before the code is sent.
- **Never retry a login automatically.** Logins are rate-limited by Garmin, and an account can be locked. "Needs login" is a persistent state until the user reconnects.

### 3.3 Result messages

| Result | Message |
|---|---|
| MFA required | "Enter the current Garmin verification code. The password is held only by the isolated login process and is never persisted." |
| Connected, enabled | "Connected. Completed-activity upload is enabled for this runner." |
| Connected, disabled | "Connected. Completed-activity upload remains disabled until explicitly enabled." |
| Failed | the safe adapter message, else "Garmin authentication failed." |
| Connect threw | 409 "The unsupported Garmin provider could not authenticate. Verify the adapter installation and account details." |
| MFA threw | 409 "Garmin verification failed. Start the connection again." |

---

## 4. Reference contract: the current adapter (JSON Lines)

The current gateway runs the adapter as a child process per operation. **The Kotlin app has no child process**, but its `garmin-client` module must expose the same operations and return the same dispositions, so the contract tests port one-to-one.

### 4.1 Framing

- One JSON object per line, UTF-8, `\n`-terminated. Stdout carries protocol lines only; library logging is disabled.
- Secrets travel **only on stdin**, never as command-line arguments.
- The host writes one request line. For `connect` it keeps stdin open for the MFA line; for everything else it closes stdin.
- The host reads **one** response line per step. It is bounded to **65 536 characters**, and a `tokenStore` in it to **32 768 characters**.
- Timeout per read is `TimeoutSeconds`, default 45, clamped to 10..90.
- Anything oversized, empty, non-JSON or missing is **ambiguous**. Ambiguity after a mutation may have started means Unknown (§7).
- Exit code is 0 on a handled operation, 2 when the top-level handler caught an exception (the error line is still emitted).

### 4.2 Operations

| Operation | Request fields | Success response | Failure responses |
|---|---|---|---|
| `probe` | — | `{state:"ready"}` | `failed/provider-unavailable` |
| `connect` | `email`, `password`; later line `{mfaCode}` | `{state:"mfa-required"}` then `{state:"connected", accountLabel, tokenStore}` | `failed/authentication`, `failed/rate-limit`, `failed/provider` |
| `upload` | `tokenStore` (≥ 128 chars), `activityPath` (`.fit`, readable) | `{state:"confirmed", remoteId?, tokenStore}` | `failed/duplicate`, `failed/rejected`, `failed/authentication`, `failed/rate-limit`, `unknown/response`, `unknown/transport` |
| `search` | `tokenStore`, `startedAtUtc` | `{state:"confirmed", tokenStore, candidates[]}` | `failed/response` (malformed, with tokenStore), `failed/search-window-truncated` (with tokenStore), `failed/*` |
| `download` | `tokenStore`, `remoteId` (digits), `outputPath` (`.fit`) | `{state:"confirmed", tokenStore, remoteId}`; FIT written to `outputPath` | `failed/*` |
| `delete` | `tokenStore`, `remoteId` (digits) | `{state:"confirmed", tokenStore, remoteId}` | `failed/*`, `unknown/transport` |

A **search candidate** has these fields: `remoteId` (string), `activityType` (always `treadmill_running`), `startedAtUtc` (ISO-8601 UTC), `durationSeconds`, `distanceKilometers`, `averageHeartRate` (decimal or null), `maximumHeartRate` (integer or null), and `heartRateSamples[] {elapsedSeconds, bpm}` (≤ 200).

**Response kinds:** `provider-unavailable`, `authentication`, `rate-limit`, `duplicate`, `rejected`, `provider`, `response`, `transport`, `search-window-truncated`.

The full line-by-line examples are in `adapter-protocol.jsonl`. The pure functions `interpretImportResult` and `classifyError` are specified with vectors in `adapter-disposition-vectors.json`. Two rules matter most:

- **Upload confirmation:** an import result with `successes[0].activityId` (or `internalId`) is `confirmed` with that `remoteId`. A library-success shape `{status: uploaded|success|succeeded|completed}` is `confirmed` **without** a remote ID. Failures containing "duplicate" or "already exists" are `failed/duplicate`; other failures are `failed/rejected`. Anything else is `unknown`.
- **Error classification** runs in this order: dependency missing, then authentication (type name contains `Authentication` or `MFA`, or the message contains `401` or `403`), then rate limit (`TooManyRequests` or `429`), then duplicate (`409` or "duplicate"). After that, **`unknown/transport` if the upload or delete call had started**, else `failed/provider`. Messages are fixed safe strings. Provider text, tokens and URLs never leak into them.

### 4.3 Search algorithm (read-only)

The full algorithm and vectors are in `adapter-search-vectors.json`. In short:

- Page through the activity list newest-first, **20 per page, at most 10 pages**. Garmin's list endpoint does not reliably filter by `treadmill_running`, so filtering happens client-side.
- Keep activities of type `treadmill_running` whose start is within **±600 s** of the local session start. Garmin sends `startTimeGMT` as a naive UTC string, usually with a space separator; accept both space and `T`.
- For each kept activity, fetch the chart details (`maxChartSize=1000`, `maxPolylineSize=0`). Down-sample the heart-rate curve to at most 200 points. If the detail call fails, keep the candidate without samples.
- Stop after **6** candidates. The sixth is an overflow marker, and the host rejects more than 5 as ambiguous. Also stop on a short page, or when a page's oldest start is before the window.
- If 10 full pages pass without reaching the window, answer `failed/search-window-truncated`. A non-array page answers `failed/response`. Both messages say "no upload or deletion was attempted".

### 4.4 Readiness (current app only)

The Python runtime reports `Ready`, `RuntimeMissing`, `DependencyMissing`, `AdapterInvalid` or `Unavailable`. Connect answers 503 with the safe state when not `Ready`.

In the Kotlin app, the equivalent is the **feature flag** plus a module self-check:

- The flag can be turned off remotely.
- Flag off or module broken means `provider-unavailable`: the account goes to `ProviderUnavailable` and connect answers 503 "Garmin upload is turned off in this build."
- The status response keeps the fields `adapterState`, `adapterMessage` and `canConnect` so the UI logic ports unchanged.

---

## 5. What the pinned library does (garminconnect 0.3.8)

The current adapter pins **`garminconnect==0.3.8`**, reviewed at upstream commit `091cad8f8caeb1dbaa0b7d62679c725c12dee458`. Its transitive dependencies are `curl_cffi 0.16.0` (TLS impersonation), `requests 2.34.2` and `ua-generator 2.1.3`. The adapter constructs `Garmin(..., retry_attempts=0)`, so **the library itself never retries** 5xx or transport errors. This section is what a Kotlin port must reproduce. Re-verify every URL and header in the GAR-00 spike (§6), because Garmin changes them.

### 5.1 Library calls used by the adapter

| Adapter step | Library call | HTTP (host `https://connectapi.garmin.com` unless stated) |
|---|---|---|
| connect | `Garmin(email, password, prompt_mfa=…, retry_attempts=0).login()` | Login chain (§5.2), then `GET /userprofile-service/socialProfile` (`displayName`) and `GET /userprofile-service/userprofile/user-settings` |
| connect result | `client.display_name`, `client.client.dumps()` | — |
| any token op | `Garmin(retry_attempts=0).login(tokenStore)` | Loads tokens; proactive refresh (§5.3); then the two profile calls above |
| search | `get_activities(start, limit)` | `GET /activitylist-service/activities/search/activities?start={n}&limit=20` |
| search details | `get_activity_details(id, maxchart=1000, maxpoly=0)` + `parse_activity_detail_metrics` | `GET /activity-service/activity/{id}/details?maxChartSize=1000&maxPolylineSize=0`. Positional `activityDetailMetrics[].metrics` are mapped through `metricDescriptors[].{key, metricsIndex}`; the keys used are `sumElapsedDuration` / `sumDuration` and `directHeartRate` / `heartRate` |
| download | `download_activity(id, ActivityDownloadFormat.ORIGINAL)` | `GET /download-service/files/activity/{id}` with `Accept: */*`. Returns a **ZIP**, which must contain exactly one `.fit` (≤ 16 MiB); the ZIP itself must be ≤ 32 MiB |
| upload | `import_activity(path)` | `POST /upload-service/upload/fit`, multipart field `file`, filename sent quoted (`"name.fit"`), part type `application/octet-stream`. Extra headers: `NK: NT`, `origin: https://sso.garmin.com`, `User-Agent: GCM-iOS-5.7.2.1`. HTTP 409 means duplicate |
| delete | `delete_activity(id)` | `DELETE /activity-service/activity/{id}`. A 204 counts as success |

List items used: `activityId`, `startTimeGMT`, `activityType.typeKey`, `duration` (s), `distance` (m), `averageHR`, `maxHR`.

**Important quirk:** in 0.3.8, `import_activity` calls the low-level `post(..., api=True)`, which already returns parsed JSON. The follow-up `hasattr(response, "json")` check is therefore always false, so the method **always returns `{"status":"uploaded","fileName":…}` after any 2xx response**. The current system has therefore *never* received a Garmin activity ID from an upload. That is why the whole no-ID resolution machinery (§8.3) exists and is exercised daily.

The Kotlin client **should parse the real response**: `detailedImportResult.successes[].internalId` is normally the activity ID. It must then apply the §4.2 rules:

- a success ID means `confirmed` with that `remoteId`;
- a 2xx with no failures and no usable ID means `confirmed` without ID (the pinned-library parity path);
- failures mean `duplicate` or `rejected`.

Whether Garmin answers 201 with an ID or 202 (asynchronous processing) without one must be recorded in the spike.

### 5.2 Login chain (DI OAuth, "mobile SSO")

The login chain tries up to five strategies in order. It stops immediately on bad credentials or MFA. 429s, Cloudflare challenges and transport errors fall through to the next strategy. After each strategy, the token is verified with `GET /userprofile-service/socialProfile`; a 401 or 403 discards it and moves to the next strategy.

1. **mobile+cffi**: iOS mobile login with TLS impersonation (`safari_ios`, `safari`, `chrome120`).
2. **mobile+requests**: the same with a plain HTTP stack.
3. **widget+cffi**: SSO embed HTML form (`/sso/embed`, `/sso/signin`, CSRF scraped), with a 3–8 s anti-WAF delay between GET and POST.
4. **portal+cffi** and 5. **portal+requests**: `GET https://sso.garmin.com/portal/sso/en-US/sign-in`, a **10–20 s** anti-WAF delay, then `POST https://sso.garmin.com/portal/api/login` with `clientId=GarminConnect` and `service=https://connect.garmin.com/app`.

The **mobile login** (the primary path):

```
POST https://sso.garmin.com/mobile/api/login
  ?clientId=GCM_IOS_DARK&locale=en-US&service=https://mobile.integration.garmin.com/gcm/ios
Headers: User-Agent: <iOS Safari UA>, Accept: application/json, text/plain, */*,
         Content-Type: application/json, Origin: https://sso.garmin.com
Body:    {"username": email, "password": password, "rememberMe": true, "captchaToken": ""}
Response responseStatus.type:
  SUCCESSFUL                 -> serviceTicketId
  MFA_REQUIRED               -> customerMfaInfo.mfaLastMethodUsed (default "email")
  INVALID_USERNAME_PASSWORD  -> authentication error (stop)
  CAPTCHA_REQUIRED           -> next strategy
HTTP 429 -> rate limited (next strategy); HTTP 403 -> Cloudflare challenge (next strategy)
```

**MFA verification** reuses the same HTTP session (cookies):

```
POST https://sso.garmin.com/mobile/api/mfa/verifyCode   (same query params as login)
Body: {"mfaMethod": <method>, "mfaVerificationCode": code, "rememberMyBrowser": true,
       "reconsentList": [], "mfaSetup": false}
fallback: POST https://sso.garmin.com/portal/api/mfa/verifyCode
          ?clientId=GarminConnect&locale=en-US&service=https://connect.garmin.com/app
SUCCESSFUL -> serviceTicketId
```

The widget flow has its own form-based MFA page.

**Service ticket to DI token:**

```
POST https://diauth.garmin.com/di-oauth2-service/oauth/token
Headers: Authorization: Basic base64("<client_id>:"), Content-Type: application/x-www-form-urlencoded,
         + native headers (below)
Form: client_id, service_ticket, grant_type=https://connectapi.garmin.com/di-oauth2-service/oauth/grant/service_ticket,
      service_url=<the service URL used at login>
client_id tried in order: GARMIN_CONNECT_MOBILE_ANDROID_DI_2025Q2, GARMIN_CONNECT_MOBILE_ANDROID_DI_2024Q4,
                          GARMIN_CONNECT_MOBILE_ANDROID_DI, GARMIN_CONNECT_MOBILE_IOS_DI
-> {access_token (JWT), refresh_token}; the effective client id is the JWT claim client_id, else the one tried.
```

If every DI exchange fails, the library falls back to a **JWT_WEB cookie** obtained by consuming the ticket at the service URL. **The Kotlin port should not implement the JWT_WEB fallback** at first. Treat a DI exchange failure as a login failure and record it in the spike.

**Native API headers** on every `connectapi` call:

```
Authorization: Bearer <di_token>
Accept: application/json
User-Agent: GCM-Android-5.23
X-Garmin-User-Agent: com.garmin.android.apps.connectmobile/5.23; ; Google/sdk_gphone64_arm64/google; Android/33; Dalvik/2.1.0
X-Garmin-Paired-App-Version: 10861
X-Garmin-Client-Platform: Android
X-App-Ver: 10861
X-Lang: en
X-GCExperience: GC5
Accept-Language: en-US,en;q=0.9
```

### 5.3 Token store and refresh

- The token store is `{"di_token": <JWT>, "di_refresh_token": <opaque>, "di_client_id": <string>}`. Persist exactly this JSON (encrypted), so a future token-format change is a single migration.
- **Proactive refresh:** before any call, if the JWT `exp` is within **900 s**, refresh:

  ```
  POST https://diauth.garmin.com/di-oauth2-service/oauth/token
  Basic auth with di_client_id; form grant_type=refresh_token, client_id, refresh_token
  -> new access_token; refresh_token rotates if one is returned
  ```

- **Reactive refresh:** on HTTP 401, refresh once and repeat the call once.
- A refresh failure leaves the old token. The next call then fails with authentication, and the account moves to `NeedsAuthentication`.
- **Return the current token store with every result**, success or known failure, so the caller always persists the rotated refresh token.
- Library gotcha (not needed in Kotlin): `login(tokenstore)` treats a string of 512 characters or fewer as a **file path**. Real DI token JSON is longer, but a Kotlin port must never guess based on length.

### 5.4 Error mapping inside the library

- Non-2xx responses raise `GarminConnectConnectionError("API Error <status> - <message>")`. A 404 raises `NotFound`.
- A 401 after refresh raises `Authentication`; a 429 raises `TooManyRequests`.
- The adapter's `classifyError` (§4.2) then maps these by type name and message text.

The Kotlin client should use **typed errors** (HTTP status, `SocketTimeout`, `SSLException`, etc.) that map to the same kinds. The string matching was only a Python convenience.

---

## 6. Risk: the March 2026 Garmin auth change

**What changed.** In March 2026 Garmin changed its authentication flow and the `garth` library (the previous OAuth1 to OAuth2 token source used by older `garminconnect` versions) was **deprecated**. `garminconnect` 0.3.x replaced it with the native **mobile SSO + DI OAuth2** flow in §5.2. That flow uses **TLS impersonation (`curl_cffi`)** to get past Cloudflare bot detection, plus multiple fallback strategies and randomized anti-WAF delays. Upstream moves fast: 0.3.0 appeared in 2026 and 0.3.16 is already published. Every Garmin change can break unattended upload.

**Guidance for the Kotlin re-implementation:**

1. **Spike first (GAR-00), before any GAR-01 work.** From the actual phone, using the Ktor client on the **OkHttp engine** (Android's Conscrypt TLS, a real Android fingerprint), prove the following against a **test Garmin account**:
   - login without MFA;
   - login with MFA;
   - proactive and reactive token refresh;
   - one search;
   - one original download;
   - one upload (record the HTTP status and body: ID or no ID);
   - one delete.

   Record request and response shapes in sanitized evidence, with no tokens.
2. **Port from a pinned upstream version**, and record the upstream commit. Re-check upstream **monthly** (00-plan risk register). Port only the mobile strategy first. Add the portal strategy only if the spike shows the mobile one gets blocked from the phone. Do **not** port TLS impersonation. If OkHttp's genuine Android fingerprint is blocked, that is a spike **failure** and the FIT share fallback stays the product answer.
3. **Never auto-retry a login.** Rate-limit manual attempts too: at least 60 s between attempts per profile, and show the lockout risk.
4. **Isolate the client** in the `garmin-client` module behind a small interface (§14). Put it behind a **remotely switchable feature flag**. Keep all Garmin URLs, client IDs and headers in one versioned constants file with a "last verified" date.
5. **Fail soft:** when the flag is off or the client breaks, jobs go to `Failed/provider-unavailable` (retryable). FIT share keeps working. Nothing is deleted.
6. Keep the adapter **contract tests** (`adapter-*.json`) as the Kotlin client's unit tests, with a fake HTTP engine replaying recorded Garmin responses.

---

## 7. Upload queue, worker and job state machine

### 7.1 Job fields

| Field | Notes |
|---|---|
| `id`, `userProfileId`, `accountId`, `workoutSessionId` | `workoutSessionId` is **unique**: one job per session, ever |
| `idempotencyKey` | 64-hex, unique (§7.3) |
| `status` | `Pending`, `InFlight`, `Confirmed`, `Failed`, `Unknown`, `Dismissed`, `FoundInGarmin`, `ReviewRequired` |
| `attemptCount` | 0..3 |
| `availableAtUtc` | not leasable before this instant |
| `leaseExpiresAtUtc` | set while `InFlight` |
| `operationPhase` | ≤ 30 chars; see `job-state-machine.json` |
| `remoteId` | the Garmin activity now representing the run (plain upload, merged copy, or the local copy after undo) |
| `matchedRemoteId` | the watch original (or restored original) |
| `replacementRemoteId` | the merged copy |
| `matchEvidence` | ≤ 1000 chars, user-visible |
| `failureKind` | `authentication`, `provider`, `provider-unavailable`, `duplicate`, `rejected`, `merge-source`, `transport`, `review-required`, `watch-match` |
| `lastError` | ≤ 1000 chars, user-visible |
| `createdAtUtc`, `updatedAtUtc`, `acknowledgedAtUtc` | |

Remote IDs are bounded to 256 characters. Derived fields:

- **`canRetry`** is true when `status == Failed` and one of these holds:
  - `failureKind` is `provider`, `provider-unavailable` or `duplicate`; or
  - `failureKind == merge-source`, the phase is `WatchSearch`, and all three remote IDs are null.
- **`retryAtUtc`** is `availableAtUtc` when the job is Pending or can be retried, else null.

### 7.2 Eligibility and queueing (the reconciler)

Each worker pass first queues new jobs. A session is queued when **all** of these hold:

- its profile's account is `enabled`, `state == Connected` and `uploadFromUtc` is set;
- the session origin is `Hardware` or `Legacy`. **Simulator and SystemTest sessions are never auto-queued.** The current code excludes only SystemTest; the new app must also exclude Simulator, as 00-plan requires;
- `startedAt` and `endedAt` are set, `endedAt >= uploadFromUtc`, and the state is `Completed` or `Stopped`;
- no job exists for the session yet;
- **no H10 recording for the session is unsettled.** Settled means `Merged`, `RemovalPending`, `Completed`, `Skipped` or `NotStarted`. So the Garmin export waits until the recording is merged, confirmed never started, or explicitly skipped (see [10-polar-h10.md](10-polar-h10.md)).

At most **100** sessions are queued per pass, ordered by `endedAt`. A new job is created with:

- `status = Pending`, `operationPhase = WatchSearch`, `attemptCount = 0`;
- **`availableAtUtc = endedAt + 5 minutes`**, which gives the watch time to sync.

A unique-constraint race when two passes insert the same session is swallowed (0 inserted).

### 7.3 Idempotency key

```
key = lowercase_hex( SHA-256( UTF-8( "garmin-fit-v1|" + profileId + "|" + sessionId ) ) )
synthetic test jobs: prefix "garmin-fit-test-v1"
```

UUIDs are lower-case and hyphenated. Vectors are in `idempotency-and-identity-vectors.json`.

The key is **local only**, because Garmin's import has no idempotency header. Exactly-once is enforced by:

- one job per session, since both the session ID and the key are unique;
- the atomic lease;
- the persisted mutation boundary (§7.5);
- the rule that Unknown is never retried automatically.

Migrated jobs keep their stored key.

### 7.4 Worker loop and leasing

- The worker runs **every 1 minute**, or at once on a wake signal. Enqueueing a test, retrying and starting a recovery all send a wake. At most one wake is pending.
- The worker **skips the pass while a maintenance mutation is running**: backup, restore or data recovery (see [07-exports-and-backup.md](07-exports-and-backup.md)).
- Each pass:
  1. reconcile, which queues new sessions (§7.2);
  2. resume incomplete replacement uploads (§7.6);
  3. lease and process jobs one at a time until none is leasable.
- **Lease** = 2 minutes. A job is leasable when all of these hold:
  - `status == Pending`;
  - `attemptCount < 3`;
  - `availableAtUtc <= now`;
  - its account is enabled and `Connected`;
  - its session has no unsettled H10 recording.

  Order: `availableAtUtc`, then `createdAtUtc`, then `id`. The lease is an atomic compare-and-set: `UPDATE … SET status='InFlight', attemptCount=n+1, leaseExpiresAt=now+2min WHERE id=? AND status='Pending' AND attemptCount=n AND <account and H10 guards>`, retried up to 3 times on contention. With two workers, **exactly one wins**.
- **Expired leases**, checked at the start of every lease call:
  - In a **read-only phase** (`WatchSearch`, `VerifyResync`, `ResolveReplacement`, `EnsureReplacement`, `DeleteReplacementDuplicates`, `ResolveOriginal`, `ResolveRestoredOriginal`, `ResolveLocalSource`, `ResolveRestoredLocal`, `DeleteGeneratedCopies`) the job goes back to `Pending` with `attemptCount - 1` (floor 0), `availableAt = now`, and error and lease cleared.
  - In any **other phase** (a mutation may have started) the job becomes **`Unknown`**: "The service restarted or timed out after a Garmin mutation may have begun; the outcome is unknown and will not be retried automatically."
- **Every state write from the worker is conditioned on the lease it holds** (`status == InFlight AND leaseExpiresAt == <the value it leased>`). A stale worker whose lease was taken over cannot write. See the "stale lease" scenarios in `upload-worker-scenarios.json`.
- On Android the loop runs inside the app's foreground/keeper process. It must survive process death: all state is in the DB, and an expired lease is handled as above. Network loss shows up as a timeout or transport error and is classified normally.

### 7.5 The at-most-once mutation boundary

Before **any** Garmin mutation (upload, restore upload, delete), the worker persists a switch to the matching **mutation phase** under its lease. Phases: `Upload`, `ReplacementUpload`, `RestoreOriginal`, `RestoreLocal`, `DeleteReplacementDuplicate`, `DeleteGeneratedCopy`, `DeleteOriginal`, `DeleteResyncedOriginal`. The mutation is sent only after that write commits. From then on, a crash, a timeout or an unreadable response can never lead to a blind resend:

| Outcome after the boundary | Result |
|---|---|
| timeout or ambiguous, plain upload/delete | `Unknown` ("…no automatic retry will occur.") |
| timeout or ambiguous, merged upload | `Pending/ResolveReplacement` in +2 min (read-only resolution) |
| timeout or ambiguous, original restore | `Pending/ResolveRestoredOriginal` in +2 min |
| timeout or ambiguous, local restore | `Pending/ResolveRestoredLocal` in +2 min |
| adapter `unknown` result | the same as the row above for its phase, or `Unknown` for a plain upload |

Before the boundary (search or download only):

- timeout or ambiguous means `Pending` retry in +2 min: "The Garmin watch search timed out before any account mutation; it can be retried safely." Once attempts are exhausted (3), the job becomes `Failed/provider`;
- a merge or validation failure means **`Failed/merge-source`**: "The matched watch FIT could not be validated or merged; the original watch activity was retained." This is retryable.

### 7.6 Result handling

**Read failures** (search or download not `confirmed`):

- `provider-unavailable`: `Failed/provider-unavailable`, and the account goes to `ProviderUnavailable`.
- `authentication`: **`Failed/authentication`**, and the account goes to `NeedsAuthentication`. Not retryable; the user reconnects, then dismisses or waits for new runs.
- Anything else: `Pending` at +2 min (+15 min for `rate-limit`). It becomes `Failed/provider` when `attemptCount >= 3`.

**Mutation results:**

| Result | Job |
|---|---|
| `confirmed` + token | `Confirmed` (`remoteId` may be null), token replaced, `lastUploadSuccessAt = now` |
| `unknown` | `Unknown` (or the resolution phase, per §7.5) |
| `provider-unavailable` | `Failed/provider-unavailable` |
| `duplicate` or `rejected` | `Failed/<kind>` (terminal; duplicate is retryable after the user resolves it) |
| `authentication` | `Failed/authentication`, account `NeedsAuthentication` |
| other known failure | `Pending` at +2^(attempt−1) min (1, 2, 4), +15 min for rate-limit; `Failed/provider` at 3 attempts |

**Automatic resume of an interrupted merged upload.** At each pass, up to 25 jobs are resumed. A job qualifies when all of these hold:

- `Unknown/ReplacementUpload` with `matchedRemoteId` set, and `remoteId` and `replacementRemoteId` null;
- the account is enabled, `Connected` and `MergeAndReplace`;
- the retained backup pair (§10) exists for exactly that `matchedRemoteId`.

Such a job moves to `Pending/ResolveReplacement`: read-only resolution, **never** a second upload. This is the only automatic exit from `Unknown`.

**No automatic retry of `Unknown` or `ReviewRequired`**, ever. The normal "Retry known failure" action is refused for them too.

### 7.7 User actions on a job

| Action | Allowed when | Effect |
|---|---|---|
| **Retry known failure** | `canRetry` | `Pending`, attempts 0, `availableAt = now`. The phase is mapped: `ReplacementUpload` goes to `ResolveReplacement` if duplicate, else `EnsureReplacement`; `RestoreOriginal` goes to `ResolveRestoredOriginal` / `ResolveOriginal`; `RestoreLocal` goes to `ResolveRestoredLocal` / `ResolveLocalSource`; `DeleteReplacementDuplicate` to `…Duplicates`; `DeleteGeneratedCopy` to `…Copies`; `DeleteResyncedOriginal` to `VerifyResync`. On an enabled `Connected` `MergeAndReplace` account with no matched and no replacement ID, the phase is `WatchSearch`. A `ProviderUnavailable` account returns to `Connected` |
| **Dismiss** | `Failed`, `Unknown` or `ReviewRequired` | `Dismissed` |
| **Found in Garmin** (acknowledge) | `Unknown` | `FoundInGarmin`, `acknowledgedAt = now`, `remoteId` unchanged. Needs an `operationId`. The UI says: "Marked as found in Garmin. This is your acknowledgment, not provider confirmation, and it will not be retried." |
| **Not found — retry** (confirm absent) | `Unknown`, phase `Upload`, **no** remote, matched or replacement ID, account enabled and `Connected` | Needs `operationId` and the confirmation text **`NOT FOUND`**. The job goes back to `Pending` at `WatchSearch` (`MergeAndReplace`) or `Upload` (`PreferWatch`), with IDs, evidence, errors and acknowledgment cleared. **Never available for a replacement or restore attempt.** Guidance: "Confirm only after waiting at least five minutes and checking Garmin Connect for a matching activity." |
| **Re-run merge check** | legacy `Confirmed`, phase `WatchSearch`, no IDs, account `MergeAndReplace` | `Pending/WatchSearch` (idempotent by `operationId`) |
| **Merge into one** / **Undo merge** | §11 | |
| **Reconcile latest two** | §12 | |

All `operationId` actions store a receipt keyed by the ID, with a request fingerprint (see `idempotency-and-identity-vectors.json`). A replay returns the stored outcome. A different request with the same ID gets 409.

### 7.8 Account state changes caused by jobs

- An authentication failure moves the account to `NeedsAuthentication` and sets `lastError`. Leasing then stops for that account until reconnect. Reconnect sets `Connected` and clears the error.
- `provider-unavailable` moves it to `ProviderUnavailable`. A retry of such a job resets the account to `Connected`.
- Any success sets `Connected`, clears `lastError` and replaces the token.

---

## 8. Matching the watch activity and the MergeAndReplace flow

### 8.1 The match reference

The match reference is built from the completed local session:

- `startedAtUtc` = session start;
- `durationSeconds` = **timer duration** (excludes pauses);
- `distanceKilometers` = session distance;
- average and maximum HR (from samples, else from the session summary);
- the HR sample curve.

The search window is centred on `startedAtUtc`.

### 8.2 Matcher algorithm (exact thresholds)

**Plausible shape.** A candidate is plausible when **all** of these hold:

1. `lower(activityType) == "treadmill_running"`;
2. `|candidate.start − local.start| ≤ 600 s` (±10 minutes, inclusive);
3. `|candidate.duration − local.duration| ≤ max(180 s, 0.15 × local.duration)` (inclusive).

**Distance is evidence only and never vetoes.** A watch that started late can estimate indoor distance very differently, and treadmill distance is authoritative. **Heart rate is not used by the current matcher.** The candidate still carries summary HR and the HR curve for evidence and future use, and the evidence text states that "local Polar heart rate remains authoritative".

**Disposition:**

| Plausible count | Disposition | Worker action |
|---|---|---|
| 0 | `None`: "No Garmin treadmill activity has a close start, duration, and distance." | Upload the local FIT (phase `Upload`) |
| 1 | `Single`: "One watch activity matched by treadmill shape: start {Δs:F0}s, duration {Δd:F0}s, distance {Δkm:F2}km; local Polar heart rate remains authoritative" | `PreferWatch`: `FoundInGarmin`. `MergeAndReplace`: §8.3 |
| > 1 | `Multiple`: "{n} Garmin treadmill activities are plausible; normal upload remains enabled." | **`ReviewRequired`** (phase `Review`, `failureKind = review-required`, `lastError` "A possible Garmin activity match requires manual review before upload."). Nothing is uploaded or deleted. The evidence is kept for the UI |

**Canonical local copy.** This test identifies *our own* uploaded copies (plain or merged). A candidate is canonical when `treadmill_running` AND `|Δstart| ≤ 45 s` AND `|Δduration| ≤ 45 s` AND `|Δdistance| ≤ 0.08 km`.

**Synthetic test jobs** (phase `Upload`, origin SystemTest) **skip the watch search** and upload at once.

**Superseded wording.** Older runbook text says a match needs "corroborating heart-rate summary/curve" and that a "no-HR possible match" becomes ReviewRequired. The current code and tests replaced that with the shape-only rule above: a unique shape match is `Single` even without local or watch HR, or with very different watch HR. **Port the code behaviour.** An unused `ReviewRequired` value remains in the matcher's result type. It may be dropped.

Vectors: `matcher-vectors.json`.

### 8.3 MergeAndReplace, end to end

The `WatchSearch` pass, with exactly one plausible candidate:

1. **Download** the candidate's original FIT (read-only).
2. **Back up** the original FIT, then export the local FIT and **back it up**, then build the **merged FIT** (§9) and **back it up**. Backups are atomic writes (§10). If the merge fails, the job is `Failed/merge-source` and nothing was mutated.
3. Persist phase `ReplacementUpload` with `matchedRemoteId` and evidence. This is the boundary.
4. **Upload** the merged FIT:
   - `confirmed` with a **distinct** ID: `replacementRemoteId` is set, and the job goes to `Pending/DeleteOriginal` (now);
   - `confirmed` with the **original's** ID: `Pending/ResolveReplacement` +2 min. "Garmin returned the original activity ID for the merged import. The app will identify the accepted replacement before deleting or uploading anything else.";
   - `confirmed` without ID: `Pending/ResolveReplacement` +2 min. "Garmin accepted the merged activity without returning its ID. The app will find it read-only before removing the original.";
   - `unknown`, timeout or ambiguous: `Pending/ResolveReplacement` +2 min. "…The app will check read-only and will not upload it again.";
   - known failure: §7.6. The original is kept.

**ResolveReplacement / EnsureReplacement** (read-only unless it is `EnsureReplacement` and nothing is found):

1. Require `matchedRemoteId` and the retained original+merged pair. Otherwise `Unknown`: "The accepted merged activity cannot be resolved because its original and replacement backups are incomplete."
2. Search. The candidates are every result except the matched original that is either the durable `replacementRemoteId` or a canonical local copy, sorted by ID.
3. Download each candidate and compare it with:
   - the retained merged FIT (semantic, and exact SHA-256);
   - the retained original;
   - the retained local FIT (or a fresh deterministic export if none is retained).
4. Pick the **keeper**, in this order:
   1. the durable replacement ID, if it matches the merged backup;
   2. any exact byte match of the merged backup;
   3. a semantic merged match that matches **only** the merged category.
5. Stop with **ReviewRequired**, deleting and uploading nothing, when:
   - a semantic merged match also matches another category: "…matches multiple retained FIT categories by strict semantic content without a durable or exact merged-replacement proof…";
   - or any non-keeper candidate is not proven to be a copy of one of the three retained FITs: "{n} canonical Garmin activities exist, but at least one lacks strict FIT content proof…".
6. Keeper found: `replacementRemoteId = keeper`, and the job goes to `Pending/DeleteReplacementDuplicates` (now). Evidence: "Resolved accepted merged activity {id} from {n} canonical candidate(s) using strict FIT content proof; no second upload was sent."
7. No keeper:
   - In `ResolveReplacement`, or with a durable replacement: a **resolution check**. It reschedules at +5 min, then +10 min. After the 3rd unsuccessful check the job is `Unknown` ("…No second upload was sent; use Check Garmin again later."). While waiting, the error reads "Garmin accepted the merged activity; waiting for it to appear before removing any duplicate."
   - In `EnsureReplacement` (only reached by the user's Merge into one): **upload the retained merged FIT once** through the same boundary. A distinct ID resolves it. A missing ID or unknown result goes back to `ResolveReplacement`.

**DeleteReplacementDuplicates** (repeats until clean):

1. Search. The retained replacement must be listed, else ReviewRequired: "The retained merged Garmin activity {id} was not visible, so no duplicate or original activity was deleted."
2. Download it. It must match the retained merged FIT, else ReviewRequired: "…no longer matches the saved merged FIT. Nothing was deleted."
3. Every other canonical copy (not the matched original, not the replacement) must be FIT-proven to be the merged, original or local FIT. Otherwise ReviewRequired: "Garmin activity {id} has the local session shape but does not match either retained TreadmillRunner FIT. Nothing else was deleted."
4. Delete **one** proven copy per pass, in phase `DeleteReplacementDuplicate`, then return in +2 s.
5. When no duplicates remain: go to `DeleteOriginal` (now) if the original is still listed, else to `VerifyResync` (+2 min, with `remoteId = replacementRemoteId`).

**DeleteOriginal:**

1. Require a matched ID, a replacement ID and a retained original. Otherwise `Unknown` with "no Garmin activity was deleted".
2. Download the replacement. It must match the retained merged FIT, else ReviewRequired: "The retained merged Garmin activity no longer matches the saved merged FIT. The original activity was not deleted."
3. Download the matched original. It must match the retained original, else ReviewRequired: "The Garmin activity at the matched watch ID no longer matches the backed-up original FIT. No deletion was attempted."
4. Delete it. Confirmed: `Pending/VerifyResync` (now), `remoteId = replacementRemoteId`. Otherwise the result is handled per §7.6 with "The merged activity exists, but Garmin did not confirm removal of the original; review both activities manually."

**VerifyResync** (a watch may re-upload the original after it was deleted):

1. Search. The replacement must be visible and FIT-verified; otherwise ReviewRequired.
2. Download every other plausible candidate. Each one that matches the retained **original** is deleted (phase `DeleteResyncedOriginal`).
3. Then the job is **`Confirmed`**, phase `Upload`, `remoteId = replacementRemoteId`. This is a one-shot check: a later watch re-upload is not reopened.

---

## 9. The MergeAndReplace FIT merge algorithm

**Principle.** The merged file is the **app's own FIT export**: the local timeline, samples, events, lap, session and activity. It is **decorated** with provenance and measurements from the watch that the app cannot produce. Everything derived from the watch's own (different) timeline is dropped. The output is one internally consistent activity that Garmin re-analyses. It uses the FIT SDK encoder, protocol 2.0.

### 9.1 Inputs and preconditions

- Watch FIT: 1 B..16 MiB, valid header and CRC, decodes, **exactly one** FileId, of type `activity`. Otherwise the error is "The watch FIT must contain exactly one Activity File Id message." There must be at least one timestamped record: "The watch FIT contains no timestamped record messages."
- Local session: `startedAt`, `endedAt` and at least one sample. Samples are normalized first (timeline normalization; see [05-sessions-and-recording.md](05-sessions-and-recording.md)).
- The local FIT is produced by the normal app-only exporter, then decoded and used as the skeleton.

### 9.2 Output message order and sources

| # | Message | Source and rule |
|---|---|---|
| 1 | `file_id` | **Watch** FileId, standard fields only. `serial_number = watchSerial XOR sessionSerial XOR 0x4D455247` (XOR 1 more if it equals `watchSerial`). `time_created = local start`. Manufacturer, product and product name stay the watch's |
| 2 | `file_creator`, `device_settings`, `user_profile`, `training_settings`, `sport` | **Watch**, first instance of each type, standard fields only. In `user_profile`, `default_max_heart_rate` and `resting_heart_rate` are **removed** when outside [30, 250] |
| 3 | `zones_target` + `hr_zone`s | **Watch**, only when the zone model validates (§9.4); else the whole model is omitted |
| 4 | `device_info` | **Watch**, one per `device_index` (first occurrence; a missing index counts as 255), standard fields only, `timestamp = local start` |
| 5 | `event` | **Local** timer events: start at local start, a stop at each pause, a start at each resume (normalized history, redundant and out-of-window events dropped), `stop_all` at the effective end |
| 6 | `record` × N | **Local** records, decorated (§9.3) |
| 7 | `lap` (exactly one) | **Local** lap. HR summary recomputed (§9.5). `time_in_hr_zone` removed |
| 8 | `session` (exactly one) | **Local** session. `sport_profile_name` copied from the watch session if present. HR summary recomputed. `time_in_hr_zone` removed |
| 9 | `activity` | **Local** activity. `local_timestamp = FIT(localEffectiveEnd) + (watch.activity.local_timestamp − watch.activity.timestamp)` when the watch activity has both; otherwise absent |

**Dropped entirely:** every other watch message. That includes watch records, laps (multiple watch laps are **not** preserved), `time_in_zone`, `split`, `split_summary`, HRV, proprietary or unknown messages (for example message 534), `developer_data_id`, `field_description`, and **all developer fields** on every message.

### 9.3 Record decoration

**Pairing watch records with local records** (by FIT timestamp in whole seconds):

1. **Exact first.** Every watch record whose timestamp equals a local record's timestamp claims that record, first come first served among equal timestamps.
2. **Nearby next.** Each unmatched watch record, in time order, takes the **nearest unassigned** local record within **±5 s**, subject to these constraints:
   - it must come after the previously assigned local index;
   - it must come before the next exact-matched local index;
   - ties go to the earliest local record.
3. A watch sample never takes the slot of a later exact match. Unmatched records stay undecorated.

**For each local record:**

- Copy these **watch-only fields** from the matched watch record when present: `cadence`, `power`, `cycle_length`, `temperature`, `cycles`, `total_cycles`, `left_right_balance`, `vertical_oscillation`, `stance_time_percent`, `stance_time`, `cadence256`, `fractional_cadence`, `left_pco`, `right_pco`, `left_power_phase`, `left_power_phase_peak`, `right_power_phase`, `right_power_phase_peak`, `vertical_ratio`, `stance_time_balance`, `step_length`, `respiration_rate`, `enhanced_respiration_rate`, `core_temperature`.
- **HR gap fill:** if the local HR is missing or invalid (255) **and** the matched watch HR is in **[30, 250]**, use the watch HR. Otherwise keep the local value, which may stay absent.
- **Elevation baseline:** the local trace is zero-based relative elevation from incline × distance (see [07-exports-and-backup.md](07-exports-and-backup.md)). Add `baseline` to `altitude` and `enhanced_altitude`. `baseline` is the first watch record, in time order, with `enhanced_altitude`, else `altitude`; if none, 0.
- **Remove `zone`** from every record. The derived zone belongs to the app-only file, not to the merged file.
- **Stale compressed speed and distance:** the records are rebuilt from local data and `compressed_speed_distance` is not in the copy list, so the watch's rolling compressed speed/distance **never** reaches a merged record. The local `speed`, `enhanced_speed` and `distance` are authoritative.

### 9.4 Zone model validation (all-or-nothing)

Keep `zones_target` and `hr_zone`s only when **all** of these hold:

- exactly one `zones_target` and at least one `hr_zone`;
- `zones_target.max_heart_rate` and `threshold_heart_rate` are each absent or in [30, 250];
- every `hr_zone` has `message_index` and `high_bpm`, and `high_bpm` is in [30, 250];
- sorted by index, the indexes are exactly 0..n−1 and `high_bpm` is strictly increasing.

Any violation omits the **whole** zone model (conflicting indexes, a value of 29 or 251, and so on).

### 9.5 Heart-rate summaries (lap = session)

Computed from the final record HR series (after gap fill), treating 255 as absent:

- **avg** = time-weighted mean. For i ≥ 1 with a valid HR, weight = `elapsed[i] − elapsed[i−1]`, using sample *elapsed* (timer) deltas. If the total weight is 0, use the plain mean of valid values. Round half away from zero and clamp to ≤ 254.
- **min** / **max** over all valid values, including sample 0.
- If there are no valid values, avg, min and max are all omitted.

Lap and session values are identical. Example: local HR [135, —, 120] with watch HR 90 gives records [135, 90, 120], avg 105, min 90, max 135.

### 9.6 Never-written fields

- **Merged file:** `total_training_effect`, `total_anaerobic_training_effect`, `training_stress_score`, `intensity_factor`, `training_load_peak`, `time_in_hr_zone` (lap and session), record `zone`, developer fields, and lap/session cadence, power and normalized-power aggregates. They are absent because the lap and session come from the local exporter, which never writes them. Garmin recomputes what it can. **Firstbeat-style values** (Training Effect, load, recovery, VO2 max, stamina, primary benefit) **are therefore not kept in a merged activity.** Users who want them choose `PreferWatch`, which the UI recommends.
- **App-only file** (no watch): never cadence, power, running dynamics, GPS, temperature, respiration, HRV, or any Firstbeat field. `file_id` and `device_info` are `manufacturer = development`, `product = 1`, product name "TreadmillRunner", `serial = sessionSerial`. Full rules are in [07-exports-and-backup.md](07-exports-and-backup.md).

### 9.7 Post-merge coherence validation

The merged bytes are decoded again and **all** of these must hold, else invalid data (the job becomes `Failed/merge-source`, retryable, nothing mutated):

- header and CRC are valid;
- only these message types: `file_id`, `file_creator`, `device_settings`, `user_profile`, `zones_target`, `hr_zone`, `training_settings`, `sport`, `device_info`, `event`, `record`, `lap`, `session`, `activity`;
- exactly one each of `file_id`, `lap`, `session` and `activity`; `file_id.time_created` = local start;
- record count = sample count; each record timestamp = its sample `capturedAt`;
- each record HR equals the canonical selection (absent = 255 = absent); record `zone` is absent **or 255** (the FIT SDK decodes an omitted field as the invalid sentinel 255, so both must be accepted, while a real 1..5 is rejected); no developer fields on records;
- events = normalized timer history + 2. The first is `timer start` at local start, the middle ones are `stop` for a pause and `start` for a resume at their times, and the last is `timer stop_all` at the effective end;
- `lap.message_index = 0`, `session.first_lap_index = 0`, `session.num_laps = 1`; lap and session start = local start and timestamp = effective end; activity timestamp = effective end, `num_sessions = 1`, and `local_timestamp` equals the expected shifted value (or both are absent);
- elapsed = max(timer duration, effectiveEnd − start) and timer = session duration, each within 0.01 s. Lap and session agree on elapsed, timer, distance, calories and avg/min/max HR, and `activity.total_timer_time` = session timer;
- `num_time_in_hr_zone` = 0 on lap and session; TE, anaerobic TE, TSS, IF and training-load-peak are all absent;
- every `device_info.timestamp` = local start.

**Performance:** a 4-hour, 1 Hz activity (14 400 records) must merge in **< 30 s**. Re-measure on the reference phone.

Before/after vectors: `fit-merge-vectors.json`.

**Superseded wording.** Older runbook text says the merge "preserves its watch/proprietary/developer messages", keeps "multiple native watch laps", carries "compatible five-zone HR durations" and leaves Training Effect "watch-owned". The merger was rewritten in September 2026 to the canonical rebuild described here, and the tests assert the new behaviour. **Port this section, not the older wording.**

---

## 10. Strict FIT equivalence and retained source FITs

### 10.1 Equivalence

`matches(expected, candidate)` is true when the SHA-256 values are equal **or** the files are semantically equivalent:

- Both files are valid Activity FITs, 1 B..16 MiB, and each contains ≥ 1 record, ≥ 1 session and ≥ 1 activity.
- Drop `device_info`, `developer_data_id` and `field_description`, and all developer fields.
- Ignore record field 8 (`compressed_speed_distance`) and every `file_id` field except `type`.
- Compare the remaining messages **in order**, each field by number with its value count and every decoded raw value.
- Headers, definitions and CRC bytes are not compared.

It never throws. Any decode problem means "not equivalent". This is what lets a Garmin-rewritten copy (new FileId and DeviceInfo) be recognized as ours, while any change to records, summaries or timing is not. Vectors: `fit-semantics-vectors.json`.

"Exact" comparisons (`matches…Exactly`) are SHA-256 only. The comparison must be constant-time.

### 10.2 Retained source FITs

For each MergeAndReplace job the app keeps **three plaintext FITs**. They contain activity and health data and live in app-private storage (phone: `filesDir/garmin-source/`):

```
{originalRemoteId}_{jobId:N}_original.fit      the downloaded watch original
{originalRemoteId}_{jobId:N}_replacement.fit   the merged FIT that was uploaded
{jobId:N}_local.fit                             the deterministic plain app export
```

- `{jobId:N}` is the UUID as 32 hex digits without hyphens. `originalRemoteId` is sanitized to `[A-Za-z0-9_-]`.
- **Atomic write:** copy to `<dest>.<random>.tmp` with write-through, then rename over the destination.
- **Recovery pair:** the recovery original ID for a job exists only when **exactly one** non-empty `*_{jobId}_original.fit` exists **and** its sibling `_replacement.fit` exists and is non-empty. Recovery actions are unavailable without that pair.
- **Pruning:** after every backup write, any `*.fit` in the folder whose last-write time is older than **7 days** is deleted. Pruning happens only on a later write, so an eligible file can stay longer when no later merge runs.
- On undo or older jobs where the local backup is missing, the local FIT is re-exported deterministically and backed up before resolution.
- Temporary working files (`{jobId}-local.fit`, `-watch.fit`, `-merged.fit`) live in the cache directory and are deleted after every job step.

---

## 11. Per-session "Merge into one" and "Undo merge"

These actions appear on a History session that has a job **and** a retained recovery pair **and** an enabled, `Connected` account. Status (`GET …/sessions/{id}/historical-recovery`) returns `{available, busy, canMergeIntoOne, canUndoMerge, state (the job phase or "Unavailable"), message}`:

| Condition | Message |
|---|---|
| no job | "This historical item has no Garmin upload record." |
| no pair | "Retained original and merged Garmin FITs are not available for this historical item." |
| account not enabled or not `Connected` | "Reconnect and enable Garmin activity upload before recovering this historical item." |
| busy, phase VerifyResync | "The merged activity is in Garmin. TreadmillRunner is performing one immediate read-only verification for any watch-recreated duplicate; unresolved or ambiguous results remain bounded and fail closed. Refresh status for the latest result." |
| busy, other phase | "Garmin recovery is running. Refresh this historical item for the latest phase." |
| available | "Choose one guarded outcome: keep one merged Garmin activity, or restore separate watch-original and TreadmillRunner activities. Local History remains unchanged." |

**Start (POST)** needs:

- `operationId`, `action` and an exact confirmation: `MergeIntoOne` + **`MERGE INTO ONE`**, or `UndoMerge` + **`UNDO GARMIN MERGE`**;
- an idle runner, the transport policy, and the retained pair;
- a job that is not `Pending` or `InFlight` ("This Garmin recovery is already running…");
- an enabled, `Connected` account.

It is serialized process-wide and idempotent by `operationId`, and it answers 202 with the job.

### 11.1 Merge into one ("Keep one Garmin activity")

**Outcome:** exactly one FIT-verified merged activity stays in Garmin. Only the backed-up original and FIT-proven generated duplicates are deleted.

**Phase selection:**

- `ResolveReplacement` when:
  - a durable replacement ID exists; or
  - the phase is `ReplacementUpload` / `ResolveReplacement`; or
  - a matched ID exists and the phase is not `UndoComplete`.
- Otherwise `EnsureReplacement`, which uploads the retained merged FIT once if none is found.

**IDs kept:** a durable replacement ID is kept. After an undo, the restored original ID is kept as `matchedRemoteId`.

The job then runs §8.3.

### 11.2 Undo merge ("Restore two Garmin activities")

**Outcome:** exactly **two** separate Garmin activities: one exact backed-up watch original and one exact plain TreadmillRunner export. Only FIT-proven merged copies or duplicate source copies are deleted.

**Phase selection:**

- `DeleteGeneratedCopies` when both sources are already identified: a matched ID ≠ remote ID, and no replacement ID;
- otherwise resume `ResolveRestoredOriginal`, `ResolveRestoredLocal` or `ResolveLocalSource` from a matching earlier phase;
- else `ResolveOriginal`.

Durable IDs are kept.

**ResolveOriginal / ResolveRestoredOriginal:**

1. Search plausible candidates plus the matched ID. Download each and keep those matching the retained original. Prefer the matched ID, then an exact match, then the first.
2. Found: `matchedRemoteId = found` and go to `ResolveLocalSource`.
3. Not found in `ResolveOriginal`: **upload the retained original once** (phase `RestoreOriginal`). An ID resolves it; no ID or unknown goes to `ResolveRestoredOriginal` with the +2, +5, +10 min checks, then `Unknown`.

**ResolveLocalSource / ResolveRestoredLocal:**

1. Search canonical copies plus the current remote ID, excluding the matched original. Download each.
2. A candidate that matches the retained local FIT is a local copy.
3. A candidate that matches **none** of local, original or merged means **ReviewRequired**: "…overlaps this session but does not match the retained local, original, or merged FIT evidence. Undo stopped without deleting or uploading anything."
4. Found: `remoteId = local copy` and go to `DeleteGeneratedCopies`.
5. Not found: **upload the retained local FIT once** (`RestoreLocal`), with the same resolution rules.

**DeleteGeneratedCopies:**

1. Search. Download the matched ID, the remote ID and every plausible candidate.
2. The kept watch original must match the retained original, and the kept local copy must match the retained local FIT. Otherwise ReviewRequired: "Undo stopped without deleting anything."
3. Delete **one** other copy per pass (phase `DeleteGeneratedCopy`, +2 s) when it is FIT-proven to be original, merged or local.
4. A canonical-shaped candidate with no proof means ReviewRequired. A non-canonical unproven candidate is ignored (someone else's run).
5. At the end, both kept sources must have been **freshly verified in this pass**, else ReviewRequired ("Undo could not freshly FIT-verify both retained source activities…").
6. Then `Confirmed/UndoComplete`: "Undo complete: watch-original {m} and plain local {r} are retained; only FIT-content-proven original, merged-replacement, and local duplicates were removed. The local TreadmillRunner session was unchanged."

**Both actions leave the local History session unchanged.** Merge into one after an undo is supported. The worker test runs merge, undo, merge and ends with one activity (4 uploads in total).

---

## 12. "Reconcile latest two" (bounded manual repair)

This is a one-shot, synchronous repair for the **latest** local session of a profile. It fixes the case where the app's complete upload and a **late-started partial watch recording** both ended up in Garmin. It needs:

- confirmation **`RECONCILE LATEST TWO`**, an `operationId`, an idle runner and the transport policy;
- a `Completed` or `Stopped` session with start and end;
- a job, and an enabled `MergeAndReplace` account.

Flow:

1. Search.
2. If exactly **one** canonical copy is the only candidate, record it (`remoteId = matchedRemoteId = it`) and answer "The canonical activity {id} is already the only Garmin activity in the bounded session window." Nothing is deleted.
3. Otherwise run the planner (`latest-two-planner-vectors.json`):
   - exactly two distinct candidates, exactly one canonical;
   - the other is a late partial: start delay in (45, 600] s, missing duration in (45, 600] s, the two agree within 120 s, and the distance delta is ≤ 0.75 km.
4. The partial's downloaded FIT must **match the retained original backup** of this job: "The late partial activity did not match the retained original FIT exactly; nothing was deleted."
5. Delete the partial and record the keeper.

Any adapter problem answers 409: "Historical Garmin reconciliation stopped before a safely confirmed result."

---

## 13. Deletion constraints

- **The app deletes a Garmin activity only when its downloaded FIT is proven** (SHA-256 or strict semantic equality) to be one of this job's retained files. Deletion is also limited to the specific roles in §8.3, §11 and §12. Ambiguity **never** authorizes a delete or a blind replacement.
- **One delete per leased pass**, each behind its own persisted mutation phase.
- **Local session delete** (see [05-sessions-and-recording.md](05-sessions-and-recording.md)) is allowed only when the Garmin job is settled:
  - no job, or `Confirmed`, `FoundInGarmin`, `Dismissed`, `Failed`, `ReviewRequired`; or
  - `Pending` in `WatchSearch`: the read-only search is cancelled. "This local session can be deleted. Its pending read-only Garmin watch search will be canceled; no remote activity is deleted."

  It is refused for `Unknown`, `InFlight` or any other `Pending` phase: "Wait for the Garmin upload to finish, or acknowledge its unknown outcome, before deleting it."

  Settled with a possible remote copy: "This local session and its settled Garmin upload record can be deleted. The remote Garmin activity is not deleted." The Garmin job state is part of the deletion-preview revision hash, so a change after the preview makes the delete fail ("…Review it again.").
- **Disconnect** is refused while a job is `InFlight` (§2.6) and never deletes Garmin activities.
- **Profile archive or delete** cascades the account, jobs and watch binding locally only.

---

## 14. Kotlin module design (guidance)

```
garmin-client/        (JVM, Ktor client + OkHttp engine)
  GarminAuth          login(email, pw) -> Connected | MfaRequired(handle) | Failed(kind)
                      completeMfa(handle, code); refresh(); tokens as opaque JSON
  GarminActivities    search(tokens, startedAtUtc) -> SearchResult(tokens, candidates) | Failed
                      downloadOriginal(tokens, id) -> bytes (validated ZIP -> single FIT)
                      import(tokens, fitBytes, fileName) -> Confirmed(id?) | Failed(kind) | Unknown(kind)
                      delete(tokens, id) -> Confirmed | Failed | Unknown
  Dispositions        interpretImportResult / classify   (ported vectors)
domain-garmin/        (pure Kotlin) matcher, planner, job state machine, idempotency key,
                      retry/backoff policy, message texts
protocol-fit/         merge, semantic equivalence (Garmin FIT Java SDK)
app/                  worker (coroutine loop, 1-min tick + wake), Room DAO with lease CAS,
                      Keystore token cipher, backups folder, web + native UI
```

- Every result carries the **current token JSON**, and the caller persists it in the same DB transaction as the job update.
- A Kotlin **per-call timeout** (default 45 s, 10..90) and **cancellation after the mutation boundary** are classified exactly like the adapter's timeout and ambiguous paths (§7.5).
- The worker runs only while the app process is alive. That is fine because the keeper keeps it resident, and the DB state makes restarts safe.

---

## 15. User-facing wording

Keep the plain-language style. The key strings follow; the others appear inline above.

**Profile panel "Garmin activity upload" (Experimental):**

- "Optional and disabled by default."
- "This uses Garmin's private consumer interface, which Garmin may change without notice. Known failures can be retried; an unknown upload outcome is never retried automatically."
- "Use this only on your trusted household network and never expose or port-forward TreadmillRunner. The password is sent once to the isolated login process and is never stored." (Phone app: "…sent once to Garmin by this phone and is never stored.")
- Summary: "Automatic upload enabled|disabled · {n} confirmed · {n} matched on watch · {n} pending", plus "{n} unknown outcome(s)—review before dismissing.", "{n} possible watch match(es) require review before any upload.", "{n} known failure(s) can be retried."
- Duplicate handling, "When the same run is found on the Garmin watch":
  - "Keep the watch activity": "Recommended. Garmin keeps the Fenix training metrics, running dynamics, and other watch-derived fields."
  - "Merge and replace the watch activity": "The original is removed only after Garmin confirms a distinct merged upload. If confirmation is uncertain, both activities are kept for manual review."
- Buttons: "Enable automatic upload" / "Disable automatic upload", "Save duplicate handling", "Disconnect upload account" then "Confirm disconnect" / "Keep connected", "Retry known failure", "Found in Garmin", "Not found—retry", "Dismiss", "Re-run merge check", "Open history".
- Job row: "{title or 'Completed treadmill run'} · {status}" / "{local start} · {duration} · attempt {n}", then `lastError`, then for ReviewRequired "Possible match — review required: {evidence}".

**Status meanings** (help text):

| State | Meaning | Action |
|---|---|---|
| Disconnected | No stored tokens | Connect if this runner wants upload |
| Connected, disabled | Ready, nothing queued | Enable only for runs not recorded on the watch |
| Pending | Waiting for the five-minute watch check, upload, read-only identity resolution, or a guarded cleanup step | Wait; History shows the phase |
| Confirmed | Garmin accepted the activity | Check the runner's Garmin Connect history |
| FoundInGarmin | One strong watch match kept (PreferWatch), or your acknowledgment | Nothing to do |
| ReviewRequired | Possible or ambiguous match, not enough proof for an automatic decision | Review the evidence in History, then acknowledge or dismiss; nothing is uploaded or deleted automatically |
| Failed | Known provider, authentication or pre-upload merge error | Fix the cause; reconnect for auth; retry when offered |
| Unknown | The request may have reached Garmin, but confirmation was lost | Check Garmin Connect; never blind-retry. Use Merge into one / Undo merge if offered, else Found / Not found / Dismiss |

**History session "Garmin activity" card:**

- Title:
  - review required: "Choose what Garmin should keep";
  - Confirmed: "One activity kept in Garmin";
  - queued or running: "Updating Garmin…";
  - Unknown: "Check this activity in Garmin";
  - Failed: "Garmin needs attention";
  - else "Garmin activity status".
- Recovery chooser: "Choose how this run appears in Garmin" / "Your run stays in TreadmillRunner either way. Only the Garmin activities are changed."
  - **"Keep one Garmin activity"**: "Keep the verified combined activity and remove proven duplicates."
  - **"Restore two Garmin activities"**: "Restore the original watch activity beside the verified TreadmillRunner activity."
- Confirmations:
  - "Keep one Garmin activity? The app will keep the verified combined activity in Garmin and remove only the backed-up original and proven duplicates. Your local History remains unchanged."
  - "Restore two Garmin activities? The app will restore the original watch activity and one verified TreadmillRunner activity, then remove only proven merged or duplicate copies. Your local History remains unchanged."
- Unknown without recovery: "**Can you see this run in Garmin?** Mark it as found to settle the local status. This does not change Garmin." with the button "I found it in Garmin".
- Technical details show the status, operation phase, failure phase, and copyable job, Garmin activity, matched and replacement IDs.

---

## 16. Web API surface (for the Ktor port)

The paths are kept so the UI logic ports directly. `{p}` = profile ID, `{j}` = job ID, `{s}` = session ID.

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/integrations/garmin/activity-upload/profiles/{p}/status` | — | account fields, counts (pending includes InFlight), `adapterState`, `adapterMessage`, `canConnect` |
| GET | `…/profiles/{p}/jobs` | — | newest 25 jobs, with workout title, start and duration |
| POST | `…/profiles/{p}/connect` | `{email, password, enabled=false, watchActivityHandling="PreferWatch"}` | 426 / 409 idle / 503 not ready / 400 bounds |
| POST | `…/profiles/{p}/mfa` | `{challengeId, code}` | code 4..16 |
| POST | `…/profiles/{p}/settings` | `{enabled, watchActivityHandling, expectedVersion}` | idle only; 409 on version |
| POST | `…/profiles/{p}/disconnect` | `{expectedVersion}` | 204 / 404 / 409 |
| POST | `…/profiles/{p}/test-activity` | `{operationId, expectedVersion}` | §17 |
| POST | `…/profiles/{p}/jobs/{j}/retry` | — | 202 / 404 |
| POST | `…/profiles/{p}/jobs/{j}/dismiss` | — | 204 / 404 |
| POST | `…/profiles/{p}/jobs/{j}/acknowledge-found` | `{operationId}` | |
| POST | `…/profiles/{p}/jobs/{j}/confirm-absent-retry` | `{operationId, confirmation:"NOT FOUND"}` | |
| POST | `…/profiles/{p}/jobs/{j}/reprocess-merge` | `{operationId}` | |
| POST | `…/profiles/{p}/sessions/{s}/reconcile-latest-two` | `{operationId, confirmation:"RECONCILE LATEST TWO"}` | synchronous |
| GET/POST | `…/profiles/{p}/sessions/{s}/historical-recovery` | `{operationId, action, confirmation}` | §11 |
| GET/POST | `/api/integrations/garmin/watch/profiles/{p}` | `{deviceLabel}` | watch binding |
| POST | `/api/integrations/garmin/watch/profiles/{p}/revoke` | `{expectedVersion}` | |
| GET | `/api/watch/status` | Bearer token | §18 |

---

## 17. Synthetic upload acceptance test

`POST …/test-activity` with a fresh `operationId` and the account's `expectedVersion`. Conditions: idle, transport policy, and account connected, enabled and at that version.

It creates a clearly labelled session:

- **SystemTest** origin, title "TreadmillRunner Garmin upload test";
- session ID = `operationId`;
- 60 s long: 60 samples at 1 Hz, 4.5 km/h, 0.5 %, HR 120;
- calories algorithm v1; completed now.

It enqueues a `Pending/Upload` job at once, keyed `garmin-fit-test-v1`, skipping the watch search, and wakes the worker. It **never issues a treadmill command**. The response is 202: "A one-minute synthetic TreadmillRunner FIT activity was queued for Garmin. Review its job before any retry."

A reused `operationId` replays "This synthetic Garmin test operation already completed and will not be queued again." An existing session with that ID gets "That test operation was already created. Review its existing Garmin job instead of sending it again."

The synthetic session stays in History (excluded from totals) so the exact FIT source can be audited. This is the GAR-00 and GAR-01 hardware acceptance hook.

---

## 18. Connect IQ companion watch app

### 18.1 Behaviour (Monkey C, SDK 9.2.0)

- A **watch app** (not a data field). App ID `4F3634D9B7CA4B1A84BDF84EC89B72A1`, minimum API 3.2.0, permissions `Fit`, `Communications` and `Sensor`, English only.
- **Supported devices:** `fenix843mm`, `fenix847mm` (Fenix 8 AMOLED 43/47 mm), `fenix8solar47mm`, `fenix8solar51mm`, `vivoactive5`, `vivoactive6`. They build warning-free. Run No Evil unit tests pass on `fenix847mm` and `vivoactive5` simulators. Add a device only after a build and simulator pass.
- **Select**, when ready, first enables the `SENSOR_HEARTRATE` and `SENSOR_ONBOARD_HEARTRATE` sensors. It then creates and starts an `ActivityRecording` session: name "TreadmillRunner", `SPORT_RUNNING` / `SUB_SPORT_TREADMILL`. A failed start disables the sensors and returns to ready.
- **Select** while recording: stop, disable sensors, **save**. On a save failure the screen shows "SAVE FAILED" / "Select: retry", and Select retries the save (the stopped session is kept). A stop failure shows "Stop failed".
- **Back** is consumed while a session exists, so an accidental Back cannot end or discard a recording. When the app stops, the active recording is neither saved nor discarded.
- **Pages** while recording:
  - page 1: runner name, session title, elapsed `MM:SS` (minutes are not capped: 3661 s is "61:01"), "Select: stop/save", "↓ metrics · 1/2";
  - page 2 "LIVE METRICS": the watch's own heart rate (bpm), calories (kcal), distance (km, 2 decimals), speed (km/h, 1 decimal), and "↑ timer · 2/2". A missing value shows "--"; values are never invented.
- Ready screen: "READY" / "Select: start". A footer line shows the gateway state.
- Opening, pairing or reconnecting **never** starts a recording. The app has **no treadmill command** of any kind.
- The saved activity reaches Garmin Connect through the watch's normal sync. That is the preferred route when a watch is worn, and it pairs with `PreferWatch` upload (the app then records `FoundInGarmin`).

### 18.2 Pairing token scheme and status contract

- There is one binding per profile: `{id, userProfileId, deviceLabel (1..100), tokenSha256 (unique), createdAtUtc, lastSeenAtUtc, version}`.
- Creating a binding makes a token from **32 random bytes, base64url without padding** (43 characters). It is shown **once** ("Copy this token now. TreadmillRunner stores only its SHA-256 hash and cannot show it again."). Only `sha256_hex(token)` is stored. Creating again replaces the binding.
- The watch settings (entered in Garmin Connect Mobile) are: "Runner name" (fallback, ≤ 32), "Gateway HTTPS URL" (no trailing slash) and "Watch pairing token" (≤ 128).
- **Paired mode** requires the URL to start with `https://` **and** a token of ≥ 20 characters. Otherwise the watch shows "Standalone" and makes no request.
- The watch polls `GET {url}/api/watch/status` with `Authorization: Bearer {token}`: on show, then every **30 s**, with at most one request in flight.
- The server answers **401** for:
  - a missing header or one that is not `Bearer `;
  - a token outside 20..128 characters;
  - an unknown or revoked hash.

  A valid token gets 200 `{runnerName, sessionTitle, state, sessionId}`. The session fields are filled only when the bound profile owns the current session and it is not Completed, Stopped, Interrupted or Faulted; otherwise the answer is `{…, "Manual treadmill", "Ready", null}`. A successful lookup updates `lastSeenAtUtc`. It never takes a treadmill lease.
- The watch then overwrites its `runnerName` setting, shows the session title, and sets the footer to `state`. HTTP code 0 shows "Phone offline"; anything else shows "Gateway unavailable".

The full contract and unit-test vectors are in `connectiq-contract.json`.

### 18.3 Moving to the phone app

Connect IQ `makeWebRequest` requires **HTTPS with a certificate the phone's Garmin Connect Mobile trusts**. The phone app's web server uses a **local CA** (00-plan), which Garmin Connect Mobile will not trust by default. So:

- **v1:** the companion runs **Standalone** (recording only), which is fully functional. Paired status against the phone's embedded server is best-effort. It works only if a publicly trusted certificate for a LAN host name is installed (for example DNS-01 ACME). The endpoint contract above stays unchanged so existing watch builds keep working.
- **Future (GAR-03, P2): the Connect IQ Mobile SDK for Android.** The TreadmillRunner app talks to the watch app directly over Garmin Connect Mobile's Bluetooth channel: `ConnectIQ` instance, `IQDevice`/`IQApp`, `sendMessage` and `registerForAppEvents`. There is then no HTTPS and no token. It needs:
  - the watch paired with **the treadmill phone's** Garmin Connect Mobile. Connect IQ messaging goes through the phone the watch is paired with, which is usually the runner's own phone. That is the main practical constraint;
  - a watch-app build that uses `Communications.registerForPhoneAppMessages` / `transmit` instead of polling;
  - a message schema mirroring the status body `{runnerName, sessionTitle, state, sessionId}`. It stays read-only, with no command messages.
- **IQ Store publishing (GAR-04):** it needs a Garmin developer account and the same RSA-4096 developer key for every update, plus interactive testing on every declared device (text not clipped, one Select = one recording, Back protection, save exactly once, Running/Treadmill in Garmin Connect, standalone and revoked-token behaviour). Increment the app version on every submission and keep the application ID. A store binary cannot be rolled back by the app's own update mechanism.
- **Privacy disclosure** (store listing): the watch app needs no account and receives no Garmin password. Pairing sends only a bearer token to the household endpoint over HTTPS. It collects no advertising IDs and contacts no TreadmillRunner cloud. Revocation removes server-side access.

---

## 19. Official Training API (parked)

This is kept for completeness. **Do not build it in the rewrite** (GAR-05, P2). The current design, if it is ever resumed:

- A separate, **supported** Garmin Connect Developer Program integration. It needs program approval and a Garmin-supplied contract. It **never** reuses the unofficial upload tokens or consumer endpoints, and it does not upload completed activities.
- **OAuth 2.0 authorization code with PKCE (S256):**
  - `state` = 32 random bytes base64url. Only its hash is stored, with a **10-minute** lifetime, single-use;
  - the code verifier is stored encrypted;
  - query `response_type=code, client_id, redirect_uri, scope ("training"), state, code_challenge, code_challenge_method=S256`;
  - the callback exchanges the code; tokens are stored encrypted per profile and refreshed when within 2 minutes of expiry.
- A **durable publication outbox** per profile, holding documents with `schemaVersion 1` of type `workout` or `calendar`, content-addressed by revision. The worker runs every 1 minute with a 2-minute lease and **max 5 attempts**; backoff is `min(60, 2^(attempt−1))` minutes. A "reconnect required" condition is terminal until reconnect. Calendar publication covers **180 days** ahead.
- Provider modes are `Disabled` (default), `Mock` (tests only) and `Configured`. `Configured` additionally requires an independently implemented, fixture-tested **approved contract adapter**. Without it, profiles show **"Training API setup required"** and no proprietary payload is guessed.

---

## 20. Translated tests (summary tables)

The full given/expected data is in `data/garmin/`. The key tables follow.

### 20.1 Adapter contract (from the Python contract tests)

| Given | Expected |
|---|---|
| import `{detailedImportResult:{successes:[{activityId:12345}],failures:[]}}` | `confirmed`, `remoteId "12345"` |
| import failures `[{messages:["invalid FIT"]}]` | `failed/rejected` "Garmin rejected the imported activity." |
| error "API Error 409 - duplicate", upload started | `failed/duplicate`, message without "API Error" |
| import `{status:"uploaded"}` | `confirmed`, no remoteId |
| import `{}`, `null`, `{detailedImportResult:{successes:[{}]}}` | `unknown` |
| transport error "socket failed token=secret", upload started | `unknown/transport`, message without "secret" |
| missing `garminconnect` or `curl_cffi` module | `failed/provider-unavailable` |
| probe with the dependency importable | exactly one line `{state:"ready"}` |
| search: 1 treadmill activity +20 s, HR 130.25/155.0, 20 chart samples | confirmed; remoteId "123"; avg 130.25; max 155 (integer); 20 samples |
| search: page 0 = 20 cycling, page 1 = treadmill 999 | candidate "999"; list calls (0,20), (20,20) |
| search: 7 matching treadmill activities | exactly 6 candidates (overflow marker) |
| search: page is an object, not an array | `failed/response`, no candidates, "no upload or deletion" |
| search: 10 full pages never reach the window | `failed/search-window-truncated`; calls (0..180 step 20, 20) |

### 20.2 Matcher (all 16 match and 5 canonical-copy vectors are in `matcher-vectors.json`)

| Local (start, dur s, km) | Candidate(s) | Expected |
|---|---|---|
| 18:43:15, 2010, 3.10 | +120 s, 2091, 3.10 | Single; "start 120s, duration 81s, distance 0.00km" |
| same | +60 s 2050 3.08 **and** +180 s 2100 3.12 | Multiple (worker: ReviewRequired) |
| same | +660 s, 2091, 3.10 | None |
| same, no local HR | +120 s, 2091, 3.10 | Single |
| same | no watch HR, exact shape | Single |
| same | +180 s, 1830, 3.08, HR 95/110 | Single |
| 18:13:10, 1712, 2.6706 | +144 s, 1576.743, 3.03929 | Single; "start 144s, duration 135s, distance 0.37km" |

### 20.3 Latest-two planner

| Candidates (vs local 18:13:10, 1712 s, 2.67 km) | Expected |
|---|---|
| only "complete" +2 s, 1710, 2.67 | canonical-only = complete |
| partial +135 s 1577 3.04; complete +2 s 1710 2.67 | keep complete, delete partial |
| three activities | rejected "Exactly two" |
| complete + unrelated +120 s 900 s 1.1 km | rejected "not a bounded late-start partial" |

### 20.4 Upload worker (outcome mapping)

| Client upload result | Job | canRetry | Account |
|---|---|---|---|
| confirmed (ID) | Confirmed | no | Connected |
| failed/duplicate | Failed | yes | Connected |
| ambiguous response | Unknown | no | Connected |
| timeout | Unknown | no | Connected |
| client unavailable | Failed | yes | ProviderUnavailable |

In every row: the uploaded file was a readable FIT, and nothing is leasable one hour later.

| Scenario | Expected calls and result |
|---|---|
| PreferWatch, one match | `search` → FoundInGarmin (matched `watch-123`) |
| MergeAndReplace, distinct replacement ID | `search, download, upload` → DeleteOriginal: `download, download, delete` → VerifyResync: `search, download, download, delete` → Confirmed/Upload, remoteId `replacement-456`, 3 backup files |
| MergeAndReplace, no ID returned | `search, download, upload` → Pending/ResolveReplacement; 3 checks → Unknown; Merge into one → ResolveReplacement; total uploads stays **1** |
| two identical merged copies appear | keep `corrected-1`; delete `corrected-2` then `watch-original`; 1 upload |
| duplicate copy with rewritten metadata only | treated as a semantic duplicate and deleted |
| duplicate copy with changed record content | ReviewRequired "strict FIT content"; nothing deleted |
| two plausible watch matches | `search` only → ReviewRequired "2 Garmin treadmill activities are plausible" |
| Undo merge, then Merge into one | undo: remaining `local-1` + `restored-original`, 3 uploads; merge again: remaining `corrected-3`, 4 uploads |

### 20.5 FIT merge and semantics

See `fit-merge-vectors.json` (20 cases) and `fit-semantics-vectors.json` (17 cases). The central before/after summary follows.

| Field | Watch input | Local input | Merged output |
|---|---|---|---|
| file_id manufacturer / product / name | garmin / 4242 / "fenix 8" | development / 1 / "TreadmillRunner" | garmin / 4242 / "fenix 8" |
| file_id serial | 123456 | 0x11111111 | 123456 ^ 0x11111111 ^ 0x4D455247 = 1549115670 |
| user_profile friendly name / max HR / resting HR | "Watch runner" / 190 / 60 | — | kept (0 or 255 become absent) |
| zones_target + hr_zone | 190, custom, [119…199] | — | kept only when valid |
| record count and timestamps | 3 at watch times | 3 at local times | 3 at local times |
| record HR | 90 | 135, 150, 120 | 135, 150, 120 (watch fills only local gaps, if 30..250) |
| record cadence / power / temperature / core temperature / power phase | 88 / 220 / 21 / 37.2 / 45 | — | copied from the paired watch record |
| record altitude | 100 | 0, 10, … (relative) | 100, 110, … |
| record compressed_speed_distance | [1,2,3] | — | absent |
| record zone | — | derived zone | absent (or 255 after decoding) |
| developer fields and messages | present | — | absent |
| laps | 2 watch laps (optional fixture) | 1 | 1 (local) |
| session TE / anaerobic TE / TSS | 3.4 / 2.1 / 44 | — | absent |
| session avg cadence / power / normalized power | 88 / 220 / 230 | — | absent |
| session sport_profile_name | "Watch treadmill profile" | "TreadmillRunner" | "Watch treadmill profile" |
| session avg / max HR | 90 / — | from samples | 135 / 150 |
| session ascent / descent | — | 9.95 / 4.99 m | 9 + 0.95 / 4 + 0.99 |
| time_in_zone, split, split_summary, message 534 | present | — | absent |
| activity local_timestamp | end + 7200 | — | local end + 7200 |

---

## 21. Test checklist

The Kotlin implementation must pass all of these. *[auto]* means an automated test; *[hw]* means a manual check against a real account or device.

**Client and contract**

- [ ] *[auto]* Every vector in `adapter-disposition-vectors.json` (import interpretation and error classification, including redaction).
- [ ] *[auto]* Every vector in `adapter-search-vectors.json`, with a fake HTTP engine (paging calls, ±600 s window, type filter, overflow, malformed, truncation, HR normalization, space- and `T`-separated times).
- [ ] *[auto]* Host bounds: more than 5 candidates, more than 200 HR samples, token JSON over 32 768 characters, or a response over 65 536 characters is an ambiguous read. A timeout before a mutation is a retryable failure; after one it is Unknown.
- [ ] *[auto]* ZIP handling for the original download: ≤ 32 MiB, exactly one `.fit` of ≤ 16 MiB; anything else fails with no upload or delete.
- [ ] *[auto]* Token refresh: proactive at exp − 900 s, reactive once on 401, rotated refresh token persisted, a returned token always saved in the same transaction.
- [ ] *[auto]* Login never auto-retries. MFA challenge is profile-bound, expires at 5 min, is replaced per profile, and at most 4 exist globally.
- [ ] *[auto]* Passwords and MFA codes never appear in the DB, logs, diagnostics or API responses. Token blob encrypted with the Keystore key.
- [ ] *[hw]* GAR-00 spike: login, MFA, refresh, search, download, upload (ID or no-ID recorded), delete against a test account from the phone (OkHttp engine).

**Queue, worker, state machine**

- [ ] *[auto]* Idempotency key vectors; one job per session; unique key.
- [ ] *[auto]* Eligibility: watermark, Completed/Stopped only, Hardware/Legacy only (Simulator and SystemTest excluded), H10 settled, at most 100 per pass, 5-minute delay.
- [ ] *[auto]* Atomic lease has exactly one winner. `attemptCount < 3`. Lease 2 min. An expired read lease goes back to Pending with the attempt decremented; an expired mutation lease becomes Unknown.
- [ ] *[auto]* Stale-lease writes are rejected at the mutation boundary and at resolution completion.
- [ ] *[auto]* Worker outcome mapping table (§20.4) and every scenario in `upload-worker-scenarios.json`.
- [ ] *[auto]* Backoff: read failure +2 min (+15 for rate limit); mutation failure 1/2/4 min; Failed at 3 attempts; auth goes to NeedsAuthentication and is not retryable.
- [ ] *[auto]* No automatic retry of Unknown or ReviewRequired; the normal retry action is refused for them. The only automatic exit is the ReplacementUpload resume into read-only resolution.
- [ ] *[auto]* User actions: retry phase mapping, dismiss, acknowledge found, confirm absent (`NOT FOUND`, phase Upload only, never for a replacement or restore), reprocess legacy merge, all idempotent by `operationId`.
- [ ] *[auto]* Disconnect refused while InFlight; succeeds afterwards and deletes local jobs only.
- [ ] *[auto]* The worker pauses while backup, restore or data recovery runs.

**Matching and merge**

- [ ] *[auto]* All `matcher-vectors.json` vectors: ±600 s inclusive, duration ≤ max(180 s, 15 %) inclusive, distance never vetoes, HR not used, case-insensitive type, the exact evidence strings.
- [ ] *[auto]* Canonical-copy vectors (45 s / 45 s / 0.08 km) and all latest-two planner vectors.
- [ ] *[auto]* PreferWatch strong match: FoundInGarmin with only a search call. Multiple: ReviewRequired with only a search call.
- [ ] *[auto]* All `fit-merge-vectors.json` cases, including zone-model validation, HR gap fill in 30..250, time-weighted HR, elevation baseline, one lap, pause/resume events, record pairing (exact first, ±5 s), no developer data, no compressed speed/distance, zone absent or 255, TE/TSS absent, local_timestamp offset kept.
- [ ] *[auto]* Post-merge coherence validator rejects each violated rule. A merge failure becomes Failed/merge-source (retryable) with no remote mutation.
- [ ] *[auto]* 4-hour 1 Hz merge in < 30 s (re-measure on the reference phone).
- [ ] *[auto]* All `fit-semantics-vectors.json` vectors; comparison never throws; exact comparison is constant-time.
- [ ] *[auto]* Backups: file naming, atomic write, recovery pair detection (exactly one original with its replacement), 7-day pruning on the next write.

**Recovery and deletion**

- [ ] *[auto]* The no-ID merged upload never re-uploads (checks at +2/+5/+10 min, then Unknown). Merge into one resumes read-only.
- [ ] *[auto]* Duplicate cleanup deletes only FIT-proven copies, one per pass; the original is deleted only after replacement and original are both verified; VerifyResync deletes watch-recreated originals and completes terminally.
- [ ] *[auto]* Undo merge ends with exactly one original and one plain local copy; both are freshly verified before UndoComplete; Merge into one works after undo.
- [ ] *[auto]* Recovery availability needs the pair plus an enabled Connected account; busy and disabled requests are rejected without changing the job; exact confirmation strings; replay by `operationId`.
- [ ] *[auto]* Local session delete follows the Garmin-settled rules and the preview revision includes the job state.
- [ ] *[hw]* One MergeAndReplace run against a test account with a real Fenix/Vivoactive recording; Garmin shows exactly one activity with local distance and HR, and watch cadence.

**Connect IQ**

- [ ] *[auto]* Pairing token: 43-character base64url, only the SHA-256 stored, shown once, replace and revoke with version, created only over HTTPS or loopback.
- [ ] *[auto]* Status endpoint: 401 cases, the Ready fallback for terminal or foreign sessions, `lastSeenAtUtc` updated, no treadmill lease.
- [ ] *[auto]* Watch unit tests (Run No Evil): settings validation, elapsed formatting, metric formatting.
- [ ] *[hw]* On each declared device: Select starts exactly one recording, Back does not discard, Select stops and saves once, save-failure retry, Running/Treadmill in Garmin Connect, Standalone when unpaired, revoked token becomes unavailable on the next refresh, no treadmill control.

**Fallback and flags**

- [ ] *[auto]* Feature flag off: jobs become Failed/provider-unavailable (retryable), connect answers 503, FIT share and download still work.
- [ ] *[auto]* Transport policy vectors (HTTP allowed only from loopback, private or link-local peers; HTTPS from any peer).
