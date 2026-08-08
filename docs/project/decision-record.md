---
title: TreadmillRunner Decision Record
type: decision-record
status: reviewed
owner: project
audience: agent-and-developer
updated: 2026-08-08
---

# TreadmillRunner decision record

## Active decision: Windows VM local gateway

The first production host is the existing Windows 11 VM on Proxmox. Its passed-through MediaTek/RZ616 Bluetooth controller (`0e8d:c616`) is the only planned BLE adapter. The gateway is a self-contained .NET 10 ASP.NET Core Windows Service serving a Blazor WebAssembly UI over HTTP on the trusted private LAN.

Validated so far: USB passthrough, Windows driver loading, and Windows discovery of nearby Bluetooth devices. Not yet validated: programmatic BLE scanning, GATT access, Omega telemetry/commands, simultaneous treadmill plus HR, reconnect soak, and BLE from a service before login.

If Session 0 BLE cannot pass its acceptance test, do not enable automatic Windows login. Record the feasibility failure and revisit a physical gateway separately.

## Product decisions

- One active controller lease; other browsers observe.
- Profiles are selected without login on the trusted LAN.
- The gateway continues an active workout if a browser disappears.
- Polar H10 is primary; a standard-BLE Garmin broadcast is fallback only between sessions.
- Physical console Start is the current fallback and remains available. A future enrolled adapter may expose a hold-to-start UI only when its exact model/firmware has passed TR-006B; FTMS advertisement alone never enables it.
- A remote Start intent is single-use, short-lived, lease- and connection-generation-bound, never retried/replayed, and never restored after reconnect, reload, restart, update, or rollback. Running begins only after measured belt movement.
- Each serialized FTMS exchange accepts only a response indication for the opcode it just wrote. A late acknowledgement for an earlier operation is ignored until the same bounded response deadline; it never changes the current command result, and confirmation still requires fresh measured telemetry.
- HR workouts may adjust speed only using conservative bounded changes.
- Workouts have immutable revisions; the calendar supports weekly recurrence, exceptions, and alternative sessions.
- SQLite is local truth. Imports are native JSON, FIT Workout, and QDomyos treadmill XML.
- Completed sessions export native/CSV/FIT. Official Garmin workout/plan publication remains program-approval-gated. Separately, a clearly labelled disabled-by-default private consumer adapter may upload completed FIT per profile: enable-time watermark, atomic lease, terminal duplicate/unknown dispositions, protected tokens, and no password persistence are mandatory. Wearing the Connect IQ companion and recording natively remains preferred.
- The Connect IQ watch app requires explicit Select interaction, may record enabled watch/HR sensor data independently, and has read-only optional profile/session status. It never starts or controls the treadmill. Paired status is unavailable until the NUC has a Garmin-trusted HTTPS origin; standalone recording remains valid.
- Signed updates use an origin-bound candidate from fixed public GitHub Releases or the configured protected local folder and activate only while idle with rollback. A fallback never mixes one origin's manifest with another origin's package.
- A manual offline ZIP contains exactly the signed manifest and its nested package. Upload can bypass feed discovery only; it cannot bypass the pinned key, strictly newer version, channel/schema, hash/archive, idle, rejected-version, backup, health, or rollback gates, and it never activates automatically.
- Daily releases are signed by an operator-controlled, non-exportable CurrentUser certificate. The service can read but cannot replace the public certificate pinned beside the administrator-owned updater under Program Files; disposable broken acceptance fixtures are isolated from the stable feed.
- GitHub Actions is disabled. Commits, pull requests, and semantic release tags never consume hosted build minutes. The local release script owns validation, building, signing, immutable tag creation, verified draft upload, and publication, and it never force-moves a tag.
- The v1 deployment treats every client on the trusted household LAN as an operator. Update activation's two-step UI is an accident guard, not authentication; public/guest-network exposure is prohibited until operator authentication and CSRF-bound activation are added.
- The daily Play/Pause control does not use the unverified FTMS Pause opcode. While running it sends the exact-device verified Stop operation and retains the active session in a resumable paused state. A fresh hold-to-Start intent is still required, and motion is never inferred from command success.
- Stop/End first sends Stop, then presents explicit keep-paused, reset-progress, or end-and-save decisions. Reset changes only the workout cursor and progress timer; recorded time, distance, telemetry, and events remain append-only. End is the only terminal action and is accepted only after confirmed stop.
- Explicit deletion may remove any terminal local session, including a plan-linked run or one with a settled Garmin upload record. Plan progress is derived again from remaining history, remote Garmin activities are never deleted, and pending/in-flight/unknown upload outcomes remain protected.
- Calendar is a view-and-manage surface. Workout creation, recurring workout scheduling, premade-template installation, and training-plan start/restart scheduling belong to Plan. Installing an already-installed template version is idempotent; the product does not offer duplicate copies as an ordinary action.

## Engineering decisions

- Five production projects: Core, Protocols, Infrastructure, Gateway, Web.
- Vertical slices inside Gateway/Web; no mediator, repository facade, message broker, microservices, IIS, container, MSIX, trimming, or AOT for v1.
- Core/Protocols contain no WinRT. Infrastructure owns Windows BLE and SQLite.
- Treadmill support is adapter-based: portable `ITreadmillProtocol`
  implementations provide identity matching, reported features/ranges, and separately hardware-verified capability declarations, and
  `TreadmillProtocolRegistry` resolves them deterministically. Future Domyos
  support adds an adapter rather than conditionals in the session engine.
- A simulated transport proves behavior before hardware commands.
- QDomyos-Zwift remains GPLv3 external evidence. TreadmillRunner implementation is independently authored with provenance and golden fixtures.

## Superseded decisions

The earlier Raspberry Pi/BlueZ/systemd/linux-arm64 deployment and direct-browser Web Bluetooth designs are superseded. Raspberry Pi remains only a future fallback requiring a new decision record.
