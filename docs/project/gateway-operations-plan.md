---
title: Windows Gateway Operations Plan
type: operations-plan
status: active
owner: project
audience: operator-and-developer
updated: 2026-08-04
---

# Windows gateway operations plan

## Host and lifecycle

The existing Windows 11 VM is the only initial host. Proxmox passes the built-in RZ616/MediaTek controller as USB `0e8d:c616`. A framework-dependent .NET 10 `win-x64` ASP.NET Core application runs as `TreadmillRunnerGateway`, starts automatically, serves HTTP on port 5180, and remains healthy when the adapter or treadmill is absent. The installed VM already carries the pinned .NET 10 runtime; framework-dependent publication avoids applying the server RID to the hosted Blazor WebAssembly client and keeps update packages smaller.

The service must pass BLE scanning and connection tests from Session 0 before login. If it cannot, production acceptance stops; automatic login is not an allowed workaround.

## Paths and identity

- Immutable releases: `%ProgramFiles%\TreadmillRunner\releases\<version>`
- Mutable data: `%ProgramData%\TreadmillRunner`
- Database: `%ProgramData%\TreadmillRunner\data\treadmillrunner.db`
- Backups: `%ProgramData%\TreadmillRunner\backups`
- Logs: `%ProgramData%\TreadmillRunner\logs`
- Read-only stable feed: `%ProgramData%\TreadmillRunner\updates\feed`
- Writable staging/plans: `%ProgramData%\TreadmillRunner\updates\staging` and `%ProgramData%\TreadmillRunner\updates\plans`
- Pinned update trust and privileged helper: `%ProgramFiles%\TreadmillRunner\updater`

Use a dedicated least-privilege service identity. SMB update access is granted to that identity or configured by Windows; credentials never enter application JSON.

## Startup and recovery

1. Windows starts the service without requiring a logged-in desktop.
2. Kestrel, health endpoints, UI, and database readiness start independently of BLE.
3. The BLE supervisor waits for the adapter, scans for enrolled fingerprints, and reconnects with bounded jittered backoff.
4. Reconnect returns device state to `Ready`; it never restores a running/armed session or queued writes.
5. Service recovery restarts after a crash. Any unfinished session becomes `Interrupted`.

Health surfaces are `/health/live`, `/health/ready`, and `/health/ble`. Treadmill power-off degrades `/health/ble` diagnostics but does not fail process liveness/readiness.

## Network

Bind to `http://0.0.0.0:5180`, use a DHCP reservation and local DNS alias when available, and document the IP fallback. The installer creates a Windows Firewall rule limited to the Private profile/local subnet. Do not configure public port forwarding.

Use `http://<NUC-hostname>:5180/` or `http://<NUC-LAN-IP>:5180/` on the trusted household LAN. These are private-LAN addresses, not public endpoints. Prefer a router reservation before sharing an address as a permanent household bookmark.

## Data and backup

SQLite uses WAL and short-lived contexts. Use the SQLite online backup mechanism, verify the result, rotate bounded daily/pre-update backups, and provide an explicit full `.trb` export. Proxmox VM backups are an additional disaster-recovery layer, not a substitute for application-consistent export.

## Signed local-folder updates

- Poll the configured local/UNC manifest at startup and every six hours.
- Pin the signing public certificate. The service receives no private key; the household operator key is non-exportable in the interactive user's certificate store, with an external or hardware-backed signer preferred for stronger isolation.
- Verify manifest signature, package SHA-256, schema bounds, and archive paths.
- Download/stage while safe, but activate only with no active/recovering session and after an online backup.
- A one-shot updater task stops the service, applies the reviewed migration bundle, changes the service binary path to the versioned release, starts it, and checks health for 120 seconds.
- Failure restores the previous release path and database backup.
- Feed loss or an invalid release cannot affect the active version.

### Operator procedure

1. Open **Operations**, then select **Check for updates**. Check is read-only and can run while the service is otherwise idle.
2. Review the available version, signer verification, hash, schema compatibility, and release notes.
3. Select **Verify and stage**. Staging verifies the signature, hash, and every archive path without changing the active service.
4. When no session is active or recovering, select **Activate**, review the warning, then confirm activation a second time.
5. The UI reports `Activating` while the SYSTEM updater task backs up the database, promotes the immutable release, and checks `/health/ready` plus the expected version.
6. If readiness, migration, or release validation fails, the helper restores the previous service path and database and records `RolledBack`. Do not manually copy files over an active release.

The acceptance signer used for local A/B/C proof is an expiring test fixture and is never a daily feed. Daily packages use the durable operator-controlled signer described in the [release operations runbook](release-operations.md); only its public certificate is service-readable.

### Installation and recovery commands

Publish and install from an elevated PowerShell prompt:

```powershell
./eng/publish-release.ps1 -Version <version>
./eng/install-gateway-service.ps1 -ReleasePath <published-path> -Version <version>
./eng/accept-gateway-service.ps1
```

The installer is rerunnable for helper/trust hardening but immutable release versions are never overwritten. It assigns the dedicated virtual service identity, gives that service Modify only on data, backups, staging, and plans, leaves the feed and Program Files trust anchor read-only, grants scheduled-task execute access, and limits the firewall rule to the Private profile/local subnet. If a rejected activation leaves a pending plan from an older build, use `eng/grant-update-task-access.ps1` once; current builds remove an unlaunched plan automatically.

## Operational acceptance

- VM reboot without login makes UI and health available.
- RZ616 scanning/connect works before login.
- Treadmill-first and gateway-first power orders reconnect without control.
- Corrupt packages and failed health promotion roll back.
- Backup restores into a clean installation.
- Update, restore, and reboot refuse to start while a session is active or recovering.

As of 2026-08-04, service installation, private-LAN access, UI-triggered `1.5.0` to `1.5.1` promotion, and deliberate broken-`1.5.2` rollback are proven. The service is healthy on `1.5.1` after rollback. A reboot-without-login BLE proof remains a separate interactive hardware acceptance step; no reboot or treadmill command was issued during deployment.
