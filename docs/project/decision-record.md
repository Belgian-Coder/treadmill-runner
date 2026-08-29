---
title: TreadmillRunner Decision Record
type: decision-record
status: reviewed
owner: project
audience: agent-and-developer
updated: 2026-08-29
---

# TreadmillRunner decision record

## Active decision: Windows VM local gateway

The first production host is the existing Windows 11 VM on Proxmox. Its passed-through MediaTek/RZ616 Bluetooth controller (`0e8d:c616`) is the only planned BLE adapter. The gateway is a self-contained .NET 10 ASP.NET Core Windows Service serving a Blazor WebAssembly UI over HTTP on the trusted private LAN.

Validated so far: USB passthrough, Windows driver loading, and Windows discovery of nearby Bluetooth devices. Not yet validated: programmatic BLE scanning, GATT access, Omega telemetry/commands, simultaneous treadmill plus HR, reconnect soak, and BLE from a service before login.

If Session 0 BLE cannot pass its acceptance test, do not enable automatic Windows login. Record the feasibility failure and revisit a physical gateway separately.

## Product decisions

- One active controller lease; other browsers observe.
- Profiles are selected without login on the trusted LAN.
- Trusted private-LAN HTTPS enables install prompts, standalone launch shortcuts, native outbound file sharing, and exactly one cached offline safety document. It does not create an offline app: app shell, data, APIs, live state, credentials, and commands remain network-only, and the worker never forces takeover of an active client.
- The gateway continues an active workout if a browser disappears.
- Heart-rate source and health are profile-scoped. Contact state, source, quality, observation time, and freshness are retained; stale, invalid, unsupported, or no-contact readings become unavailable and suspend automation. A source from another profile is never selected silently.
- A demanded heart-rate source is rediscovered through a bounded active read-only scan before connection and after relevant failures. Its saved address is a locator, not durable identity: only an exact current address or unique standard-HRS name/family match may supply an ephemeral locator. Ambiguous, truncated, or stale enrollment identity data fails closed; treadmill identity never rebinds.
- Physical console Start is the current fallback and remains available. A future enrolled adapter may expose a hold-to-start UI only when its exact model/firmware has passed TR-006B; FTMS advertisement alone never enables it.
- A remote Start intent is single-use, short-lived, lease- and connection-generation-bound, never retried/replayed, and never restored after reconnect, reload, restart, update, or rollback. Running begins only after measured belt movement.
- Each serialized FTMS exchange accepts only a response indication for the opcode it just wrote. A late acknowledgement for an earlier operation is ignored until the same bounded response deadline; it never changes the current command result, and confirmation still requires fresh measured telemetry.
- HR workouts may adjust speed only using conservative bounded changes.
- Workouts have immutable revisions; the calendar supports weekly recurrence, exceptions, and alternative sessions.
- SQLite is local truth. Imports are native JSON, FIT Workout, and QDomyos treadmill XML.
- Completed sessions export Metric TCX Activity, versioned full-resolution native JSON, CSV, and FIT Activity. Immutable workout revisions export FIT Workout. Official Garmin workout/plan publication remains program-approval-gated with an explicit setup-only state: an approved adapter, contract, and credentials are required, and proprietary payloads are never guessed. Separately, a clearly labelled disabled-by-default private consumer adapter may upload completed FIT per profile: enable-time watermark, atomic lease, terminal duplicate/unknown/review-required dispositions, protected tokens, and no password persistence are mandatory. Wearing the Connect IQ companion and recording natively remains preferred.
- The public product is Metric-only. `UnitSystem` is retained in contracts only as the constant `Metric`; profile writes reject any other value and the reviewed migration normalizes legacy rows before enforcing the database check.
- The Connect IQ watch app requires explicit Select interaction, may record enabled watch/HR sensor data independently, and has read-only optional profile/session status. It never starts or controls the treadmill. Paired status is unavailable until the NUC has a Garmin-trusted HTTPS origin; standalone recording remains valid.
- Signed updates use an origin-bound candidate from fixed public GitHub Releases or the configured protected local folder and activate only while idle with rollback. A fallback never mixes one origin's manifest with another origin's package.
- A manual offline ZIP contains exactly the signed manifest and its nested package. Upload can bypass feed discovery only; it cannot bypass the pinned key, strictly newer version, channel/schema, hash/archive, idle, rejected-version, backup, health, or rollback gates, and it never activates automatically.
- Daily releases are signed by an operator-controlled, non-exportable CurrentUser certificate. The service can read but cannot replace the public certificate pinned beside the administrator-owned updater under Program Files; disposable broken acceptance fixtures are isolated from the stable feed.
- GitHub Actions is disabled. Commits, pull requests, and semantic release tags never consume hosted build minutes. The local release script owns validation, building, signing, immutable tag creation, verified draft upload, and publication, and it never force-moves a tag.
- The trusted household LAN remains the deployment boundary and public/guest-network exposure is prohibited. Optional operator access is disabled by default for compatibility; when enabled, anonymous reads remain available while all API mutations require a short-lived opaque bearer. The gateway stores a PBKDF2 passphrase hash and bounded in-memory sessions, the browser stores its bearer only in `sessionStorage`, and no cookie-based authority or CSRF surface is introduced.
- The daily Play/Pause control never substitutes Stop for Pause. It sends the raw FTMS Pause operation only when the exact-device Pause capability is hardware-verified; otherwise Pause stays unavailable. Start and Resume use an explicit single tap, remain single-use and non-replayable, and motion is never inferred from command success.
- Natural hardware workout completion is a two-phase safety boundary. The final planned step creates one gateway-owned, exact-device verified Stop intent; Completed state and History finalization occur only after fresh telemetry confirms stopped motion. A rejected or unknown outcome remains a visible running session, suspends automation, directs the runner to physical Stop, and is never retried automatically.
- Stop/End first sends Stop, then presents explicit keep-paused, reset-progress, or end-and-save decisions. Reset changes only the workout cursor and progress timer; recorded time, distance, telemetry, and events remain append-only. End is the only terminal action and is accepted only after confirmed stop.
- Explicit deletion may remove any terminal local session, including a plan-linked run or one with a settled Garmin upload record. Plan progress is derived again from remaining history, remote Garmin activities are never deleted, and pending/in-flight/unknown upload outcomes remain protected.
- History detail is a bounded display contract: it returns at most 240 representative samples with exact first/last coverage and the full persisted count. Stored data, analytics, event history, CSV, and FIT remain full-resolution so UI responsiveness never changes the authoritative record.
- Calendar is a view-and-manage surface. Workout creation, recurring workout scheduling, premade-template installation, and training-plan start/restart scheduling belong to Plan. Installing an already-installed template version is idempotent; the product does not offer duplicate copies as an ordinary action. A completed-late session may move to its actual date and shift every later incomplete session by the same offset without changing linked History or progression. Calendar mutations fail closed on occupied target dates, including one-session moves, following-session shifts, restores, and training-day changes; clearing upcoming work is available per selected plan and never alters completed history.

## Engineering decisions

- Eight production projects: Core, Protocols, Infrastructure, Gateway, Web, the lazy Web SignalR transport, the lazy Operations feature, and the small shared Web runtime.
- Vertical slices inside Gateway/Web; no mediator, repository facade, message broker, microservices, IIS, container, MSIX, or AOT for v1.
- Release WebAssembly is trimmed with the pinned SDK's `wasm-tools` workload, stale WebCIL output is removed before every release publish, and Release Hot Reload assets are disabled. The release entry point fails closed if the optimization workload is missing. Global prerender and Interactive Auto remain rejected: browser-local profile/control-lease state, JavaScript interop, and live gateway supervision are client-only boundaries, while the static boot shell provides the safe immediate paint.
- Route-critical Operations state is delivered by one read-only in-process projection; command, update, backup, database, and restore mutations remain separate guarded endpoints. The client falls back to the prior reads when paired with an older gateway.
- Operations receives a server-generated, noninteractive first view from in-memory health/release/access snapshots and eagerly requests the local QR while its route-owned lazy assembly starts. Global prerender remains rejected. SignalR and its live transport assembly are lazy and route-owned by Run/Control; read-only routes never open the hub. Browser version probes are single-flight except for explicit forced activation recovery.
- Live-session transition locks cover state changes only. Immutable generation/versioned effect batches run persistence and fan-out after lock release; a serialized writer coalesces same-second checkpoint work and a latest-only broadcaster prevents slow browsers from delaying Stop or heartbeat handling. Terminal operation receipts are session-scoped and retained for 90 days.
- Plan phase/week contents and multi-year history cards are render-bounded independently of authoritative stored data. Browser route readiness, DOM size, lazy-assembly loading, and a three-year SQLite fixture are deterministic acceptance budgets.
- Gateway composition is separated into service-registration and middleware extensions. Request correlation/metrics use normalized route keys and a bounded in-memory snapshot; feature clients begin with the typed operator-access boundary rather than duplicating wire details in UI components.
- Trusted private HTTPS with HTTP/2 is the production browser transport baseline. Plain loopback HTTP remains for local health and update recovery. HTTP/3 may be added by a managed edge but is not required, and no deployment may label a self-signed or otherwise untrusted certificate as household-ready.
- The iPhone Home Screen contract serves one complete opaque 180×180 Apple touch icon from the canonical app artwork. Re-adding an existing Home Screen shortcut may be required after an asset refresh; no physical Safari installation claim is made by source validation.
- Core/Protocols contain no WinRT. Infrastructure owns Windows BLE and SQLite.
- Windows BLE scan-response discovery, public/random address typing, targeted standard-service GATT enumeration, and native timeout cancellation remain Infrastructure concerns. Notification teardown disposes its service/device handles and never starts an unbounded detached CCCD cleanup operation.
- SQLite enforces one active session through a filtered unique active-slot index. Startup/migration reconciles pre-existing conflicts before the constraint is enabled, while recovery and Garmin lease queries select only the chosen candidate/available lease with indexed ordering.
- Treadmill support is adapter-based: portable `ITreadmillProtocol`
  implementations provide identity matching, reported features/ranges, and separately hardware-verified capability declarations, and
  `TreadmillProtocolRegistry` resolves them deterministically. Future Domyos
  support adds an adapter rather than conditionals in the session engine.
- A simulated transport proves behavior before hardware commands.
- QDomyos-Zwift remains GPLv3 external evidence. TreadmillRunner implementation is independently authored with provenance and golden fixtures.

## Superseded decisions

The earlier Raspberry Pi/BlueZ/systemd/linux-arm64 deployment and direct-browser Web Bluetooth designs are superseded. Raspberry Pi remains only a future fallback requiring a new decision record.
