---
title: Install TreadmillRunner on Windows
type: operator-guide
status: active
owner: project
audience: user-and-operator
updated: 2026-08-11
---

# Install TreadmillRunner on Windows

TreadmillRunner runs as a Windows service on a Windows 11 x64 NUC or VM and opens from any phone, tablet, or computer on the same trusted private household network. Do not expose port 5180 to the Internet or a guest network.

## Before you start

- Windows 11 x64 with its network profile set to **Private**.
- A Bluetooth adapter available to Windows.
- [Microsoft ASP.NET Core Runtime 10 for Windows x64](https://dotnet.microsoft.com/download/dotnet/10.0). The installer checks this and stops with a clear link if it is missing.
- Administrator access for the one-time Windows service, firewall, and update-helper setup.

The .NET SDK, Git, GitHub CLI, Python, and `pip` are not required for normal use. Signed releases include the pinned offline Python 3.12 runtime used by the separately labelled unsupported Garmin completed-activity uploader; it remains disabled per profile until a runner explicitly connects and enables it.

## Install in four steps

1. Open [TreadmillRunner Releases](https://github.com/belgian-coder/treadmill-runner/releases/latest) and download `TreadmillRunner-<version>-Windows-x64.zip`.
2. In File Explorer, select **Extract all**. Do not run the installer from inside the ZIP preview. The included `INSTALL.txt` contains the same short checklist for offline use.
3. Double-click `Install-TreadmillRunner.cmd` and accept the Windows administrator prompt.
4. Wait until the installer reports that the gateway is ready. It opens `http://localhost:5180`; household devices should use the configured trusted private-LAN HTTPS address when available.

The installer checks the runtime and private-network profile, installs the immutable application release and database migrations, pins the public update-signing certificate, creates the least-privilege Windows service and update task, restricts the firewall rule to the private local subnet, and verifies `/health/ready` before opening the dashboard.

After installation, open **Operations → Open on another device** to scan a locally generated QR code from an iPhone on the same private Wi-Fi. Set `Gateway__PublicUrl` to the trusted household HTTPS address (for example, `https://treadmill.home.example/`). The gateway never sends the URL to a QR service. Private HTTP remains usable for basic access, but install prompts, Screen Wake Lock, native file sharing, and the offline safety page require HTTPS or a loopback development address.

For the fastest compatible private-LAN delivery, configure a structured Kestrel HTTPS endpoint with a certificate already trusted by every household device. Keep loopback HTTP on port 5180 for service health and updates, and add an HTTPS endpoint such as port 5443 with `Protocols=Http1AndHttp2AndHttp3`. HTTP/2 is the immediate compatibility path; Kestrel advertises HTTP/3 with `Alt-Svc` when QUIC is available, while clients that cannot use QUIC continue over HTTP/2 or HTTP/1.1. Open TCP and UDP for the selected HTTPS port only on the Private firewall profile. Configure these service environment values in an elevated maintenance window, then restart and verify `/health/ready` before replacing an installed browser app:

```text
Gateway__PublicUrl=https://treadmill.home.example:5443/
Gateway__AllowedPublicHostSuffixes__0=home.example
Kestrel__Endpoints__Http__Url=http://0.0.0.0:5180
Kestrel__Endpoints__Http__Protocols=Http1
Kestrel__Endpoints__Https__Url=https://0.0.0.0:5443
Kestrel__Endpoints__Https__Protocols=Http1AndHttp2AndHttp3
Kestrel__Endpoints__Https__Certificate__Path=C:\ProgramData\TreadmillRunner\certificates\treadmill.home.example.pfx
Kestrel__Endpoints__Https__Certificate__Password=<protected secret>
```

The installer preserves those allow-listed HTTPS values on reinstall or infrastructure repair. Do not use a self-signed certificate for household clients: certificate errors prevent a trustworthy PWA origin and usually prevent HTTP/3. A reverse proxy remains valid when it terminates the trusted certificate and offers HTTP/2/3, provided the gateway stays private and the proxy never caches API, SignalR, HTML, or control traffic.

## Install the browser app

Open **Operations → Installed experience** from the HTTPS address. Chrome or Edge shows **Install app** when its install prompt is available; otherwise use the browser menu. On iPhone or iPad, open the address in Safari, tap **Share**, then **Add to Home Screen**. An old icon installed from HTTP is a separate browser app and must be replaced by a new installation from HTTPS.

The installed app still requires the NUC and household Wi-Fi for all live UI, history, workouts, commands, and data. Only one data-free safety document is stored for failed reloads. It warns that gateway/treadmill state is unknown and that Wi-Fi or Bluetooth loss does not stop the belt; use the physical console, safety key, or physical **Stop** control.

Session CSV/FIT exports and full or verified backups offer **Share** when the device supports sharing files. The file is fetched only after that click and retains the gateway filename and type. **Download** remains available in every browser and is the fallback for unsupported file types. Diagnostics remain download-only.

## First run

1. Open **Devices** and enroll the Horizon treadmill and preferred heart-rate sensor. Polar H10 takes priority over a watch broadcast.
2. Open **Profiles** and create or select the runner.
3. Use **Workouts** and **Calendar** for planned runs, or choose **Manual run** on **Run**.
4. Fit the treadmill safety key and keep physical **Stop** reachable. The physical console and safety key remain authoritative.

## Update online

Open **Operations → Signed updates** while no workout is active:

1. Select **Check now**. GitHub Releases is checked first; the protected local folder remains the offline fallback.
2. Review the signed release notes and source.
3. Select **Verify and stage**.
4. Select **Activate staged update**, review the database-backup/reconnect message, then confirm.

The browser briefly disconnects. The service verifies the expected version and readiness and automatically restores the previous application and database if activation fails. It never resumes a treadmill command or session after an update.

## Update from a downloaded file

If the NUC cannot reach GitHub, download `treadmillrunner-<version>-offline-update.zip` on another computer and copy it to the household network:

1. Open **Operations → Signed updates → Install from a signed file**.
2. Choose the offline update ZIP. The UI verifies and stages it; uploading does not activate it.
3. Review the staged version, then use the normal activation confirmation.

The offline file may bypass a stale or unavailable feed, but it cannot bypass the installed trust key, strictly newer version rule, channel, database schema range, SHA-256 package hash, safe-archive checks, idle requirement, prior rollback block, backup, health verification, or rollback.

## Local-folder alternative

Administrators may keep using the protected `%ProgramData%\TreadmillRunner\updates\feed` folder. Run `eng/install-stable-update-feed.ps1` from a source checkout in an elevated terminal to validate and copy a signed package into it. The service uses `GitHubThenLocal` by default; `Updates__FeedProvider` also accepts `GitHub` or `Local` for an explicit single source.

## Repair and troubleshooting

- **Installer says runtime missing:** install ASP.NET Core Runtime 10 x64, then run the installer again.
- **No household URL:** confirm the NUC network profile is Private and use `http://<NUC-LAN-IP>:5180`.
- **Update unavailable:** confirm Internet/DNS access or import the signed offline ZIP. A local-feed recovery procedure is in the [release operations runbook](project/release-operations.md).
- **Update rolls back:** keep using the restored version and download diagnostics from Operations. Do not retry the rejected version; publish or install a higher corrected release.
- **Bluetooth unavailable:** confirm Windows sees the adapter and the service is running; never treat Bluetooth disconnect as a Stop mechanism.
- **Garmin upload says adapter setup required:** install or repair the latest signed release. Normal installation contains the runtime and dependencies and does not use system Python or download packages. Open the profile again and select **Check again**. Developer-only external Python overrides are documented in the Garmin runbook.

Application data is stored under `%ProgramData%\TreadmillRunner`. Removing the Windows service or application files does not automatically delete profiles/history. Make a full backup from Operations before deliberate data removal.

## Developer setup

Developers need the SDK pinned by `global.json`, PowerShell, Python 3.12 for the deterministic validation harness, and Node/npm for browser tooling:

```powershell
.\eng\bootstrap.ps1
.\eng\database.ps1 -Action Update -DatabasePath .\data\treadmillrunner.db
.\eng\run-simulator.ps1
.\eng\validate.ps1 -Configuration Release
.\eng\playwright.ps1 -Configuration Release -InstallBrowsers
```
