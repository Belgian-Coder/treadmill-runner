---
title: TreadmillRunner Release Operations
type: operator-runbook
status: active
owner: project
audience: operator-and-developer
updated: 2026-08-05
---

# TreadmillRunner release operations

This runbook explains how software releases move from source code to the **Operations** page, how the Windows service activates them, and how to recover from a bad or incomplete feed. These procedures update TreadmillRunner itself; they never update treadmill or heart-rate-sensor firmware and never issue a treadmill command.

## The release chain

1. `eng/publish-release.ps1` builds an immutable versioned Windows release, embeds a content fingerprint derived from the source revision plus local source changes, writes that provenance to `build-metadata.json`, adds the hash-verified offline Garmin adapter runtime, and proves its credential-free import probe.
2. `eng/package-update.ps1` creates a deterministic package ZIP, hashes it, writes and signs the stable manifest, and creates a two-entry signed offline bundle containing that manifest and package.
3. The elevated service installer pins the public certificate at `%ProgramFiles%\TreadmillRunner\updater\signing.cer`. GitHub Releases is the default discovery transport and the protected ProgramData local folder remains the fallback. Neither source can replace the trust anchor.
4. **Operations → Check now** obtains one origin-bound release candidate and validates version, channel, schema range, and signature. Its package can only be opened from that same origin.
5. **Verify and stage** streams and verifies the package again, then expands nothing; it stores a verified package and manifest in a versioned staging directory.
6. **Activate staged update** asks for a second confirmation. The gateway makes an online SQLite backup and submits a bounded plan to the preinstalled SYSTEM update task.
7. The helper stops the service, extracts to an immutable release directory, applies the reviewed migration bundle, points the service to the new executable, and waits up to 120 seconds for the expected version and `/health/ready`.
8. Success records `Activated`. Any migration, executable, version, or health failure restores the previous executable path and database and records `RolledBack`.

The browser may briefly disconnect during step 7. Reload after the service is ready; no session or command is resumed after an update or rollback.

The Garmin adapter is release content, not machine state. `eng/new-garmin-portable-runtime.ps1` verifies the pinned official CPython archive, installs only the hash-locked Windows wheels into the publish folder, retains third-party notices/license metadata, and runs `eng/test-garmin-adapter-runtime.ps1`. Both update and installer packaging repeat that offline probe and fail closed. Normal installation never invokes system Python, `pip`, or a package download.

## Install for the first time

Normal users download `TreadmillRunner-<version>-Windows-x64.zip`, extract it, and run `Install-TreadmillRunner.cmd`. The zero-argument elevated installer checks Windows x64, ASP.NET Core Runtime 10, and a Private network profile; installs the service, migrations, firewall rule, protected updater and pinned public key; verifies readiness; and opens the dashboard. See [Install TreadmillRunner on Windows](../installation.md).

Initial installation is the trust bootstrap: obtain the installer from the expected public repository and verify its published SHA-256 when additional assurance is needed. Later GitHub, local-folder, and manual-file updates are authenticated by the already pinned key. Never rotate that key from an ordinary update asset.

## Update sources

- `GitHub`: fixed public `belgian-coder/treadmill-runner` latest release. The service needs no GitHub token and reads only exact release assets; GitHub release notes/tags are not trusted code metadata.
- `Local`: protected `%ProgramData%\TreadmillRunner\updates\feed` or another administrator-configured local/UNC folder.
- `GitHubThenLocal` (default): use GitHub when it provides a channel manifest; fall back only when GitHub is unavailable or has no stable manifest. A present but invalid signed manifest is rejected and never hidden by fallback.
- **Manual signed bundle:** Operations accepts `treadmillrunner-<version>-offline-update.zip`, containing exactly `stable.manifest.json` and the manifest-named package. It verifies and stages only; activation stays separate.

“Force version change” means selecting a signed newer bundle even when discovery is stale or unavailable. It never permits a same-version reinstall, downgrade, unsigned release, supplied certificate, wrong channel/schema, hash mismatch, unsafe archive, previously rolled-back version, active-session stage, or automatic activation.

## Trust model

- The gateway service receives read-only access to `%ProgramFiles%\TreadmillRunner\updater\signing.cer`, which contains a public key. It cannot replace the trust anchor.
- The signing private key must never be copied into ProgramData, Program Files, application configuration, a release ZIP, a feed, logs, screenshots, or source control.
- The household deployment uses a non-exportable certificate in the interactive release operator's `CurrentUser\My` Windows certificate store. The service identity cannot use that private key.
- A separate release workstation or hardware-backed key is stronger and can replace this arrangement later without changing package or UI behavior. Trust rotation is deliberately an administrator action.
- `validation/update-acceptance` and `create-update-acceptance-feed.ps1` are test-only. Their intentionally broken packages must never be installed into the daily stable feed.

## One-time signer and trust setup

Run in a normal, non-elevated PowerShell window from the repository:

```powershell
./eng/initialize-release-signer.ps1
```

The command creates a five-year, RSA-3072, non-exportable signing key in the current user's certificate store and writes only the public `.cer` plus non-secret metadata under `artifacts/release-signing`. Record the displayed thumbprint in the operator's password manager or release notes; do not add local signer artifacts to source control.

Install the service and its public trust from an elevated PowerShell window. This is also the required one-time hardening step for a host installed before TR-007C:

```powershell
.\eng\install-gateway-service.ps1 `
  -Version 1.5.1 `
  -ReleasePath .\artifacts\releases\1.5.6\publish `
  -PublicCertificatePath .\artifacts\release-signing\treadmillrunner-release-signing.cer `
  -RepairUpdateInfrastructureOnly
```

Then install the first stable feed:

```powershell
./eng/install-stable-update-feed.ps1 `
  -Version 1.5.6 `
  -SourceFeed ./artifacts/releases/1.5.6/stable-feed `
  -PublicCertificatePath ./artifacts/release-signing/treadmillrunner-release-signing.cer
```

Use the actually installed version for `-Version`; repair mode leaves that immutable release and service binary untouched, replaces only the protected helper/trust configuration, applies narrow ACLs, and restarts the service. This administrator boundary is required because the certificate and privileged helper pin who may publish and which roots SYSTEM may mutate. Later same-key releases need only the feed command below, followed by the Operations UI. A helper or trust-anchor upgrade always requires rerunning the elevated service installer; a normal UI update intentionally cannot replace its own privileged helper.

## Publish the next release

Use a version higher than the currently installed version. Release output is immutable and commands refuse to overwrite an existing version.

### Local-only build and GitHub cost policy

GitHub Actions is disabled completely in repository settings. The repository has no workflow under `.github/workflows` and no `.github/dependabot.yml`, so commits, pull requests, manual dispatches, Dependabot updates, and tags cannot start a hosted build or consume hosted minutes. Re-enabling Actions or adding either configuration requires a new owner decision.

The release workstation is authoritative for deterministic Release validation, browser acceptance, the offline Garmin runtime probe, building, signing, packaging, checksums, and publication. GitHub receives only committed source, the immutable annotated tag, the public signing certificate, and the finished release assets. The private signing key never leaves the workstation.

Because `publish-release.ps1` is the only release-content entry point, the same locally verified adapter bundle/probe is used by GitHub Release assets and protected local-feed releases.

The client/server compatibility fingerprint is content-derived, not merely the human release number. Two local builds with the same version but different application source therefore cannot silently share a browser identity. The browser compares its embedded fingerprint with the no-cache server version endpoint and blocks mutations when they differ. Release directories remain immutable, so publish with a higher version after any source change rather than attempting to overwrite an earlier build.

Do not create or push release tags manually. A tag is the immutable identity of one published version and must identify the exact `main` commit whose assets were built. The release script creates the annotated tag only after local validation, publishing, signing, and checksum generation have succeeded.

```powershell
./eng/validate.ps1 -Configuration Release
./eng/playwright.ps1 -Configuration Release
./eng/publish-release.ps1 -Version 1.5.8

$signer = Get-Content ./artifacts/release-signing/signer-metadata.json -Raw | ConvertFrom-Json
./eng/package-update.ps1 `
  -Version 1.5.8 `
  -PublishPath ./artifacts/releases/1.5.8/publish `
  -FeedPath ./artifacts/releases/1.5.8/stable-feed `
  -SigningCertificateThumbprint $signer.thumbprint `
  -ReleaseNotes 'Daily-use V1 completion with Polar-first automation, ordered training plans, safe recovery, and responsive full-screen controls.'
```

For later releases signed by the same trusted key, copy only the newly verified stable manifest and package into the configured feed. When the feed is a protected local ProgramData folder, rerun `install-stable-update-feed.ps1`; the UI still performs staging and activation.

### Publish to GitHub Releases

The local signer is deliberately non-exportable and must not be placed in GitHub Actions or a repository secret. From a clean, pushed `main` checkout authenticated with `gh auth login`, run:

```powershell
.\eng\create-github-release.ps1 `
  -Version 1.5.10 `
  -ReleaseNotes 'Describe the user-visible changes in this version.'
```

The script requires `main` to exactly match `origin/main`, runs Release and browser validation locally, publishes and signs locally, creates the end-user installer and checksum file, pushes an annotated `v<version>` tag, creates a draft, uploads and verifies every expected asset, then publishes it as latest. Pushing the tag starts no GitHub workflow. The script never accepts a token, PFX, private-key path, or signing password.

#### Interrupted release recovery

- Rerun the same command with the **same version and exactly the same release notes**. Existing immutable build/package output is reused.
- A local tag is reused only when it resolves to the current `main` commit. A conflicting local or remote tag is rejected and never overwritten.
- An existing GitHub Release is resumed only while it is still a draft. Expected assets are replaced from the locally verified set, checked again by name, and only then published.
- If the release is already published, its tag and assets are immutable. Fixes require a higher version; never delete or move a published tag to reuse its version.
- If local validation or packaging fails, do not create or move a tag. Correct the source, rerun locally, and publish only after all required checks pass.

To run validation deliberately without creating a release, use the local scripts:

```powershell
./eng/validate.ps1 -Configuration Release
./eng/playwright.ps1 -Configuration Release
```

These commands do not create a tag, package, signature, or GitHub Release. `create-github-release.ps1` runs them automatically unless `-SkipValidation` is explicitly used to resume an already validated interrupted release with identical immutable inputs.

Release assets are `stable.manifest.json`, `treadmillrunner-<version>-win-x64.zip`, `treadmillrunner-<version>-offline-update.zip`, `TreadmillRunner-<version>-Windows-x64.zip`, the public `.cer`, and `SHA256SUMS.txt`.

## Update from the UI

1. Confirm no workout is active, armed, recovering, or awaiting debrief.
2. Open **Operations**.
3. Select **Check now**.
4. Review the newer version and release notes. If the state is `Available`, select **Verify and stage**.
5. After state becomes `Staged`, select **Activate staged update**, review the backup/reconnect warning, then **Confirm activation**.
6. Wait for the browser to reconnect and confirm the reported current version is the new version. The lifecycle state is **Activated** until a later check reports no newer release.
7. Confirm `http://127.0.0.1:5180/health/ready` returns HTTP 200 and profiles/history are still present.

After activation is accepted—or if its response is interrupted while the service restarts—the Operations page checks the gateway read-only and never resends activation. A promoted build reloads the page once, returns to the Signed updates card, and remains loop-safe even when browser session storage is unavailable. A rollback returning on the previous build updates the terminal state without reloading. If recovery reaches its hard three-minute deadline, the page cancels outstanding checks and tells the operator to use **Check now** after the gateway reconnects. The explicit update-banner **Reload** action can always perform one user-requested cache-busting reload even after the automatic guard was spent.

### Update from a signed file

While idle, expand **Install from a signed file** in Operations and choose the versioned offline-update ZIP. The gateway bounds the upload, rejects extra, duplicate, or path-bearing outer entries, validates the signed manifest with the installed certificate, verifies the nested package hash and safe archive, and atomically stages it. Review the resulting source/version and use the normal two-step activation. The uploaded file is never extracted directly and never supplies its own trust key.

The page intentionally reveals only the next valid action. It never activates an update automatically.

## Garmin adapter and Connect IQ release assets

The signed Windows release includes `tools/garmin/garmin_activity_adapter.py` and its exact `requirements.lock.txt`. It does not mutate the release by installing Python packages. Before enabling the unsupported per-profile Garmin upload, install the pinned dependency into `C:\ProgramData\TreadmillRunner\garmin-python` with `eng/install-garmin-adapter.ps1`, configure `GarminActivityUpload__PythonPath`, configure `GarminActivityUpload__PythonExecutable` as an absolute Python 3.12 path readable by the Windows Service identity, validate both paths under that identity, and restart the service. A normal signed update can replace the adapter script/lock file; changing the third-party pin requires provenance/license review and a deliberate ProgramData dependency refresh. Rollback restores the prior signed script, while the operator-owned Python environment remains unchanged.

The Connect IQ watch app is released independently through Garmin IQ Store. It is not placed in the Windows update ZIP and cannot be updated or rolled back from TreadmillRunner's Operations page. Build, simulator, physical-watch, export, listing, and Garmin review steps live in [Connect IQ companion setup and store release](connect-iq-companion.md). Keep the watch status API backward compatible with store-released versions.

## 2026-08-04 NUC activation proof

The installed household service was upgraded through the Operations page from `1.5.1` to `1.5.6`. The operator performed **Check now**, **Verify and stage**, **Activate staged update**, and the explicit confirmation. The service restarted, the browser reconnected, `/health/ready` returned HTTP 200, and the update status reported `currentVersion: 1.5.6`.

Two earlier packages exercised the fail-closed path without replacing the working release:

- `1.5.4` was rejected before service shutdown because the helper attempted managed-assembly version inspection on the native application host.
- `1.5.5` was rejected before service shutdown because an unquoted Windows service path was parsed incorrectly and failed the helper's root-containment check.

Both failures retained healthy `1.5.1` service operation and produced rollback-journal evidence. The helper was then corrected to derive the candidate version from its immutable release directory and to normalize unquoted service paths safely. Release `1.5.6` completed the same UI-driven path successfully. These version numbers are historical evidence; always publish a strictly newer version for the next update.

Release `1.5.8` was subsequently signed and packaged under `artifacts/releases/1.5.8/stable-feed` after the Daily-use V1 deterministic completion gate. Its RSA signature, SHA-256 package hash, assembly version, required activation assets, Release build, migrations, 38 browser scenarios, screenshots, and local-quality packet were verified. On 2026-08-04 it was installed into the protected stable feed with `install-stable-update-feed.ps1`; the running 1.5.6 service then reported state `Available`, current `1.5.6`, available `1.5.8`, and message `The release manifest is valid.` The remaining operator path is **Verify and stage** followed by **Activate staged update** in Operations.

## 2026-08-06 operator UI acceptance boundary

Release `1.5.13` proved GitHub discovery, signature and hash verification, staging, backup, promotion, service restart, migration, readiness, and retained profile/history access. Its check, stage, and activation endpoints were invoked directly, however, so that run is backend update evidence and **not** Operations-page acceptance evidence.

Release `1.5.14` is reserved for the owner-operated UI acceptance run. After publishing it, automation must not call `/api/updates/check`, `/api/updates/stage`, or `/api/updates/activate` on the household service. The owner performs **Check now**, reviews the source and notes, selects **Verify and stage**, selects **Activate staged update**, confirms activation, observes browser reconnect/stale-client recovery, and verifies the reported current version. Record the outcome here only after those visible steps have been completed.

From an elevated PowerShell window, install the already verified package into the protected feed:

```powershell
./eng/install-stable-update-feed.ps1 `
  -Version 1.5.8 `
  -SourceFeed ./artifacts/releases/1.5.8/stable-feed `
  -PublicCertificatePath ./artifacts/release-signing/treadmillrunner-release-signing.cer
```

This command validates the administrator-pinned certificate, signature, hash, archive paths, size bounds, and required executables before installing the manifest last. It does not activate the release; activation remains the two-step Operations UI flow.

## 2026-08-07 local 1.5.18 activation

Release `1.5.18` was built and signed locally after deterministic validation. The running `1.5.17` service accepted the signed offline bundle only while idle, reported it as staged, and activated it through the normal helper. The restarted service reported `1.5.18`, a changed build fingerprint, HTTP 200 readiness, no active session, and retained planning data. This was a local signed-bundle installation; it did not create a GitHub tag or public release.

Release `1.5.19` combined TR-028, TR-029, and TR-030. The local deterministic gate passed locked restore, formatting and analyzers, a zero-warning Release build, 453 non-browser tests, public-evidence sanitization, Garmin adapter checks, Connect IQ static validation, and BLE ownership checks; 57 browser cases passed in bounded groups. The running `1.5.18` service accepted the exact signed offline bundle while idle, returned `Staged`, accepted one activation request, became temporarily unavailable during promotion, and returned healthy as `1.5.19` with build fingerprint `77484de2900de8c9`. The service path points at the immutable `1.5.19` release, the update state is `Current`, the application entry document is `no-store`, and existing profile/plan data remains readable. No treadmill command, GitHub tag, or public release was created.

## State meanings

| State | Meaning | Operator action |
|---|---|---|
| `Current` | No newer valid release exists | None |
| `Available` | Newer signed manifest is valid | Review notes, then stage |
| `Rejected` | Signature, version, channel, schema, or manifest is invalid | Do not bypass; repair publisher/feed |
| `Unavailable` | Feed or pinned certificate cannot be read | Follow feed/certificate recovery below |
| `Staged` | Manifest and package passed verification | Activate while idle |
| `Activating` | Privileged helper is promoting or rolling back | Wait; do not copy files manually |
| `Activated` | Expected version and readiness passed | Verify UI/data |
| `RolledBack` | New release failed and previous release recovered | Inspect journal/diagnostics; publish a higher corrected version |
| `Failed` | Promotion and rollback recovery both failed | Use administrator recovery procedure immediately |

## Recovery

### “The release executable is missing”

The selected ZIP is incomplete. Do not retry or restage that version. Publish a higher version from a complete `publish` directory. `package-update.ps1` and `install-stable-update-feed.ps1` both require `TreadmillRunner.Gateway.exe`, `TreadmillRunner.Migrations.exe`, and `Updates\update-helper.ps1`.

### Old state remains `RolledBack`

A rollback journal is version-specific. Install a manifest with a higher valid version and select **Check now**. The newer release becomes `Available`; the rejected version remains blocked from restaging.

### Feed unavailable

Verify these files exist and are readable by `NT SERVICE\TreadmillRunnerGateway`:

- `%ProgramFiles%\TreadmillRunner\updater\signing.cer`
- `%ProgramData%\TreadmillRunner\updates\feed\stable.manifest.json`
- the package named by that manifest

Run `install-stable-update-feed.ps1` again with the expected version and public certificate. It rejects a certificate that differs from the administrator-pinned thumbprint, verifies everything before replacing the package, and installs the manifest last so a partial copy is never selected.

## Household LAN authority boundary

The application is deliberately deployed only on a trusted household private LAN. There is no per-person sign-in in v1, so any client admitted to that LAN is treated as a household operator and can call maintenance endpoints while the app is idle. The two-step activation control prevents accidental taps; it is not authentication. Never expose port 5180 to the public Internet or an untrusted guest network. A future Internet-facing deployment requires authenticated operator authorization and CSRF-bound activation nonces before use.

### Signature rejected

The manifest was signed by a different key, was edited after signing, or the wrong public certificate is pinned. Do not replace only one side casually. Verify the intended signer thumbprint, then perform an explicit administrator trust rotation with a newly packaged release.

### Activation does not reconnect

1. Wait the full 120-second health window plus service startup time.
2. Check the `TreadmillRunnerGateway` Windows service and `TreadmillRunnerUpdate` scheduled task.
3. Inspect `%ProgramData%\TreadmillRunner\updates\plans\transaction-*.json` and download the bounded diagnostics bundle when the UI is reachable.
4. If state is `RolledBack`, keep using the restored version and publish a higher fix.
5. If state is `Failed`, use an elevated terminal and the reviewed installer; never copy files over an active release directory.

## Acceptance-only rollback tests

`eng/create-update-acceptance-feed.ps1` deliberately creates a good B and incomplete C package with a disposable two-day signer. `eng/select-update-acceptance-fixture.ps1` now requires an explicit isolated destination and refuses the daily ProgramData feed. Run those fixtures only against an isolated acceptance `DataRoot`; remove the environment after the rollback proof.
