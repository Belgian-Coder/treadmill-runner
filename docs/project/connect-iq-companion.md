---
title: Connect IQ companion setup and store release
type: developer-and-operator-runbook
status: source-prepared-sdk-acceptance-required
owner: project
audience: runner-operator-and-developer
updated: 2026-08-05
---

# Connect IQ companion setup and store release

The source project is `connectiq/TreadmillRunnerCompanion`. It is a watch app, not a data field. The runner presses Select to start a native Garmin treadmill recording and presses Select again to stop and save. Back is consumed while recording to reduce accidental loss. Closing, pairing, or reconnecting never silently starts a recording, and the app contains no treadmill command route.

Garmin Connect receives the saved activity through the watch's normal sync. This is the preferred route when the runner wears a watch because it preserves native watch heart rate and sensor details. The optional NUC pairing adds runner/session labels only.

## What is implemented

- ActivityRecording session with Running / Treadmill metadata.
- Explicit watch/onboard heart-rate sensor enablement before recording so enabled sensor data is written to FIT.
- Ready, recording, elapsed-time, stop/save, and connection-state UI.
- Standalone behavior when gateway URL/token are absent or unavailable.
- Optional HTTPS status polling every 30 seconds.
- One revocable watch binding per TreadmillRunner profile; raw token shown once, SHA-256 only at rest.
- Fenix 8 43/47 mm AMOLED, Fenix 8 Solar 47/51 mm, Vivoactive 5, and Vivoactive 6 manifest targets.
- Store copy, privacy disclosure, test matrix, screenshot folder, and submission checklist.
- Static validation plus representative SDK compilation entry point in `eng/validate-connectiq.ps1`.
- Save failure remains visible and retains the stopped session for an explicit Select-to-retry action.

## External work still required before IQ Store availability

This workstation currently has no Connect IQ SDK, `monkeyc`, or Java toolchain, so no `.prg`/`.iq`, simulator screenshots, physical-watch proof, developer signature, or store submission is claimed. These are required release actions, not missing watch-app features.

1. Sign in to Garmin's developer portal and install the current SDK with SDK Manager. The Connect IQ overview reported SDK 9.2.0 on 2026-08-05; verify the current version at execution time.
2. Install the Monkey C extension for Visual Studio Code.
3. Generate a developer key and store it in a protected, backed-up location outside this repository.
4. Run:

   ```powershell
   ./eng/validate-connectiq.ps1 -DeveloperKey C:\secure\garmin-developer-key.der -RequireSdk
   ```

5. In the Garmin simulator, run all manifest products and complete `connectiq/TreadmillRunnerCompanion/store/test-matrix.md`.
6. Test on the owner's exact Fenix 8 and identify/test the second household Vivoactive model. A generic family name is not enough for final compatibility acceptance.
7. Export the signed `.iq` package with the Monkey C extension's **Export Project** command. Record its SHA-256 and developer key identity in release evidence.
8. Capture real simulator screenshots, prepare the current required icon dimensions, and complete the submission checklist.
9. Upload through Garmin's Connect IQ developer dashboard and wait for Garmin review. Record the final listing URL/version only after acceptance.

A Garmin developer account is required to publish to IQ Store. This does not require approval for the Garmin Connect Training API; they are separate programs/surfaces.

## Pair and operate

1. Configure a trusted HTTPS origin for the NUC that the watch/phone can reach by following **NUC HTTPS preparation** below. Garmin web requests require HTTPS; an IP-address HTTP URL is not a valid paired mode. Until that is complete, the companion is Standalone-only.
2. Open **Profiles**, edit the runner, create a watch pairing, and copy the one-time token.
3. In Garmin Connect Mobile, open the installed app's settings and enter the HTTPS gateway URL and token. Runner name is an optional standalone fallback.
4. Open the watch app. Select starts recording. Select stops and saves. The NUC need not be online for recording.
5. Revoke a lost/replaced watch from the runner profile. An invalid token returns no profile/session information.

The status endpoint response is intentionally small: runner name, current session title, session state, and optional local session ID. It accepts only a Bearer token whose SHA-256 hash matches the stored profile binding. Status reads update last-seen time and do not acquire a treadmill lease.

## NUC HTTPS preparation

The installed household gateway currently listens on plain private-LAN HTTP, so paired watch status is not deployable until a certificate is configured. Standalone watch recording is unaffected. Use a hostname and certificate chain trusted by Garmin; do not rely on an untrusted self-signed certificate.

1. Allocate an internal DNS name such as `treadmill.example.net` that resolves to the NUC on household Wi-Fi.
2. Obtain and renew a publicly trusted certificate for that name, normally with the operator's DNS provider and an ACME DNS-01 client. Keep the private key/PFX outside the immutable release under a service-readable, administrator-owned ProgramData certificate directory.
3. Grant the TreadmillRunner service identity read access only to that PFX/key. Store its password through protected service configuration, never in source-controlled JSON.
4. Configure Kestrel service environment values and restart during an idle window:

   ```text
   ASPNETCORE_URLS=https://0.0.0.0:5443;http://0.0.0.0:5180
   Kestrel__Certificates__Default__Path=C:\ProgramData\TreadmillRunner\certificates\treadmill.example.net.pfx
   Kestrel__Certificates__Default__Password=<protected secret>
   ```

5. From the phone on household Wi-Fi, open `https://treadmill.example.net:5443/health/ready` and confirm a valid certificate and HTTP 200 without a warning.
6. Run `eng/test-watch-https.ps1 -GatewayUrl https://treadmill.example.net:5443` and enter the one-time token at the secure prompt. Confirm the profile-bound response, then enter the same origin/token in Garmin Connect Mobile app settings.
7. Repeat after every certificate renewal. Do not expose the NUC port to the public internet; public DNS and a public certificate do not require inbound public routing when DNS-01 is used.

## Simulator and physical acceptance

For each device layout verify:

- text is readable and not clipped in Ready and Recording states;
- one Select starts one recording; repeated view refreshes do not start another;
- elapsed time advances and remains legible;
- Back does not discard an active recording;
- Select stops and saves exactly once;
- the saved activity is Running / Treadmill and appears through normal Garmin sync;
- missing/invalid settings show Standalone without blocking recording;
- valid HTTPS pairing shows only the bound runner/session;
- revoked token becomes unavailable on the next refresh;
- no operation starts or controls the treadmill.

Use Garmin's current public documentation at execution time:

- [Connect IQ overview and SDK](https://developer.garmin.com/connect-iq/overview/)
- [Activity Recording guide](https://developer.garmin.com/connect-iq/core-topics/activity-recording/)
- [ActivityRecording API](https://developer.garmin.com/connect-iq/api-docs/Toybox/ActivityRecording.html)
- [HTTPS web requests](https://developer.garmin.com/connect-iq/core-topics/https/)
- [Properties and app settings](https://developer.garmin.com/connect-iq/core-topics/properties-and-app-settings/)
- [Manifest and permissions](https://developer.garmin.com/connect-iq/core-topics/manifest-and-permissions/)
- [Compatible devices](https://developer.garmin.com/connect-iq/compatible-devices/)
- [Publishing to the Connect IQ Store](https://developer.garmin.com/connect-iq/core-topics/publishing-to-the-store/)

## Versioning and rollback

- Increment the Connect IQ app version for every submitted binary; preserve the application ID.
- Keep the prior accepted `.iq`, source revision, SDK version, key identity, SHA-256, screenshots, and test matrix in release evidence.
- If a watch release fails, remove it from availability or submit a corrected higher version through Garmin. The NUC signed-update rollback cannot roll back an IQ Store binary.
- Server protocol changes must remain backward compatible with the released watch app or use a versioned endpoint. `/api/watch/status` currently has no motion authority and should remain read-only.
