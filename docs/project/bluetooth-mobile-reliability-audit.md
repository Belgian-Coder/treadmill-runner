---
title: Bluetooth, reliability, and mobile usability audit
type: audit
status: active
owner: project
audience: operator-and-developer
updated: 2026-09-08
---

# Bluetooth, reliability, and mobile usability audit

This audit reviews the existing Windows gateway and all eleven browser screens. iPhone portrait and landscape are the primary browser targets, followed by iPad portrait and landscape. Changes preserve the existing application structure, signed updater, gateway-owned sessions, and command authority.

## Scope and findings

| Area | Review and resulting behavior |
|---|---|
| Bluetooth | Reviewed connection generations, native handle ownership, cancellation, advertisement sharing, service discovery, notification freshness, reconnect delay and fallback-source stability. For passive enrollments, optional model/firmware reads follow first-valid telemetry and cannot abort or delay startup. Hardware-verified enrollments require complete fresh identity before becoming Ready. A changed model or firmware durably clears hardware verification before readiness; a failed evidence write prevents readiness. FTMS capability bounds remain required before subscription. |
| Browser recovery | Retry authoritative reads after transient failures even when SignalR remains connected. Malformed version, session, snapshot and lease responses fail closed. Heartbeat recovery uses the supervisor lifetime instead of the heartbeat token it cancels. Recovery never replays an arm or motion request. |
| Session persistence | Retry terminal event/summary persistence at most three times with 100/200-millisecond delays; matching terminal writes are idempotent after uncertain commit outcomes. Persistent failure remains logged; a process crash retains the existing startup-interruption behavior. |
| Backup health | Record known SQLite/EF backup creation failures so Operations sees the failed verification when the status database remains writable. |
| Forms | Use at least 16 CSS pixel input text and 44 CSS pixel field heights on phone/tablet layouts and touch devices. Opening New/Edit profile focuses and scrolls to the form heading without opening the keyboard. |
| Navigation and dialogs | Bound open menus by dynamic viewport height, allow internal scrolling, and preserve safe-area spacing around landscape navigation and dialogs. |
| Training plans | Remove the obsolete tablet grid rule that reserved an empty editor column while browsing. Cards and filters use the available width. |
| Landscape density | Compact non-control page headings without reducing control sizes or changing the immersive live chart layout. |
| Validation reliability | Build focused .NET dependencies before starting the test watchdog, using the shared serial builder. Cold compilation no longer consumes the one-minute execution budget before integration tests start. |

The baseline browser inspection measured profile input text at 12.16 CSS pixels and a 258.9-pixel plan browser inside a 755.8-pixel iPad workspace. The new profile focus regression failed against the baseline build before the implementation was changed.

## Validation

Validation is performed against isolated simulator gateways and temporary databases. Populated gallery coverage includes Run, Control, Training plans, Workout editor, Import, Calendar, History, History detail, Devices, Profiles, and Operations.

- iPhone: 390×844 and 844×390, with existing 440×956/956×440 control coverage and short-landscape navigation checks at 667×280 and 844×320.
- iPad: 820×1180 and 1180×820.
- Desktop and accessibility: existing 1920×1080, large-text, high-contrast, keyboard, chart-inspector, and touch-control checks remain part of acceptance.
- Focused regression runs precede a reviewed commit and one complete acceptance run. Results, screenshots, and the commit-bound receipt remain under ignored `artifacts/` and `output/playwright/`.

Focused acceptance covered browser recovery from failed and malformed responses, terminal persistence retries, backup failures, delayed optional Bluetooth reads, and the identity approval boundary. All eleven populated screens were inspected across phone and tablet viewports. Six touch-form and short-landscape navigation cases also passed using WebKit. These checks complement the final complete acceptance gate; exact results and installed-release evidence are recorded in the workflow report.

## Boundaries and rejected changes

No physical treadmill command, pairing, connection demand, firmware change, Garmin upload, production data edit, or radio setting change is part of this audit. Browser viewport and WebKit tests do not prove physical iPhone/iPad Safari behavior or sustained Bluetooth continuity.

The restore upload size concern was rejected after checking Kestrel framing: missing or excessive Content-Length is rejected and the handler cannot consume an arbitrarily larger body under a smaller declared length. Restore preview tokens intentionally remain single use after confirmation; retrying a possibly stale reviewed restore candidate was not introduced.

Existing generation invalidation, stable-source hold and bounded reconnect backoff are retained. More aggressive polling, blind reconnect loops, automatic motion replay and speculative protocol changes would need evidence showing a benefit. Hardware continuity, native-disconnect causes, Session 0 behavior and power-cycle recovery still require separate field observation.

## References and working context

Safe-area handling follows [WebKit's safe-area guidance](https://webkit.org/blog/7929/designing-websites-for-iphone-x/). The optional rendering-engine check uses the supported [Playwright .NET browser selection](https://playwright.dev/dotnet/docs/browsers).

Working context: `AGENTS.md`, `docs/project/agent-rules.md`, `project-context.md`, the skill/workflow routing indexes, `orchestration.md`, navigation `HANDOFF.md`, the .NET engineering skill, and the signed-release skill. Raw navigation indexes, generated routing changes and Connect IQ validation are outside this change. The canonical delivery command remains `eng/create-github-release.ps1`; installation uses the signed updater only after the live-session endpoint returns HTTP 204.
