# Stories

One folder per user story, numbered in build order. Each starts with `ticket.md`. `plan.md`, `execution-log.md` and `validation/` are added by the workflow in plan section 12.4.

| # | ID | Priority | Phase | Goal |
|---|---|---|---|---|
| [001](001-HAR-01-the-new-repository-has-agents-md/ticket.md) | HAR-01 | P0 | 0b. Harness, foundation and delivery | As the owner, the new repository has `AGENTS.md`, `CLAUDE.md`, `ai/project-conte |
| [002](002-HAR-02-every-build-test-debug-deploy-and/ticket.md) | HAR-02 | P0 | 0b. Harness, foundation and delivery | As an agent, every build, test, debug, deploy and validation action is a determi |
| [003](003-HAR-03-ai-routing-yaml-routes-roles-to/ticket.md) | HAR-03 | P0 | 0b. Harness, foundation and delivery | As the owner, `ai/routing.yaml` routes roles to Anthropic and OpenAI models (Opu |
| [004](004-HAR-04-navigation-files-ai-navigation-modules-md/ticket.md) | HAR-04 | P0 | 0b. Harness, foundation and delivery | As an agent, navigation files (`ai/navigation/modules.md`, `specs.md`, `stories. |
| [005](005-HAR-05-each-story-has-a-folder-created/ticket.md) | HAR-05 | P0 | 0b. Harness, foundation and delivery | As the owner, each story has a folder created by `./gradlew newStory` from the t |
| [006](006-HAR-06-i-produce-plan-md-with-current/ticket.md) | HAR-06 | P0 | 0b. Harness, foundation and delivery | As a planner, I produce `plan.md` with current-state analysis, Mermaid change an |
| [007](007-HAR-07-capturescreens-and-webscreens-put-portrait-and/ticket.md) | HAR-07 | P0 | 0b. Harness, foundation and delivery | As a reviewer, `captureScreens` and `webScreens` put portrait and landscape phon |
| [008](008-FND-01-the-gradle-project-has-the-modules/ticket.md) | FND-01 | P0 | 0b. Harness, foundation and delivery | As a developer, the Gradle project has the modules of 4.1, convention plugins, a |
| [009](009-FND-02-the-test-harness-exists-at-every/ticket.md) | FND-02 | P0 | 0b. Harness, foundation and delivery | As a developer, the test harness exists at every level with one example test eac |
| [010](010-FND-03-simulator-mode-provides-a-fake-treadmill/ticket.md) | FND-03 | P0 | 0b. Harness, foundation and delivery | As a developer, Simulator mode provides a fake treadmill and HR (deterministic,  |
| [011](011-FND-04-ui-design-has-the-tokens-and/ticket.md) | FND-04 | P0 | 0b. Harness, foundation and delivery | As a developer, `ui-design` has the tokens and components (Compose) and the gene |
| [012](012-FND-05-the-app-installs-on-the-moto/ticket.md) | FND-05 | P0 | 0b. Harness, foundation and delivery | As the owner, the app installs on the moto g15 and starts. |
| [013](013-FND-06-the-local-test-rig-of-11/ticket.md) | FND-06 | P0 | 0b. Harness, foundation and delivery | As a developer, the local test rig of 11.0 exists: `ciFast` on the Windows VM, a |
| [014](014-DLV-01-gradlew-release-builds-and-signs-locally/ticket.md) | DLV-01 | P0 | 0b. Harness, foundation and delivery | As the owner, `./gradlew release` builds and signs locally, and uploads the APK  |
| [015](015-DLV-02-the-app-updates-itself-without-a/ticket.md) | DLV-02 | P0 | 0b. Harness, foundation and delivery | As the owner, the app updates itself without a tap on the phone after the one-ti |
| [016](016-DLV-03-i-push-an-update-from-my/ticket.md) | DLV-03 | P0 | 0b. Harness, foundation and delivery | As the owner, I push an update from my laptop with `./gradlew deployToPhone` or  |
| [017](017-DLV-04-updates-install-only-when-idle-after/ticket.md) | DLV-04 | P0 | 0b. Harness, foundation and delivery | As the owner, updates install only when idle, after a verified backup, and are f |
| [018](018-DLV-05-a-crash-loop-after-an-update/ticket.md) | DLV-05 | P0 | 0b. Harness, foundation and delivery | As the owner, a crash loop after an update puts the app in safe mode (web server |
| [019](019-DLV-06-feature-flags-can-be-switched-off/ticket.md) | DLV-06 | P0 | 0b. Harness, foundation and delivery | As the owner, feature flags can be switched off from Admin or through a manifest |
| [020](020-DLV-07-the-web-diagnostics-console-shows-live/ticket.md) | DLV-07 | P0 | 0b. Harness, foundation and delivery | As the owner, the web Diagnostics console shows live logs (filterable, level cha |
| [021](021-DLV-08-logs-and-diagnostic-exports-never-contain/ticket.md) | DLV-08 | P0 | 0b. Harness, foundation and delivery | As the owner, logs and diagnostic exports never contain addresses, names or payl |
| [022](022-DLV-09-wireless-adb-with-scrcpy-works-from/ticket.md) | DLV-09 | P0 | 0b. Harness, foundation and delivery | As a developer, wireless ADB with scrcpy works from the start, and the debuggabl |
| [023](023-DLV-10-the-signing-and-manifest-keys-are/ticket.md) | DLV-10 | P0 | 0b. Harness, foundation and delivery | As the owner, the signing and manifest keys are backed up and a rotation procedu |
| [024](024-DLV-11-gradlew-phonecheck-gives-an-autonomous-health/ticket.md) | DLV-11 | P0 | 0b. Harness, foundation and delivery | As the owner or an automated agent, `./gradlew phoneCheck` gives an autonomous h |
| [025](025-WEB-01-the-app-serves-the-web-ui/ticket.md) | WEB-01 | P0 | 0b. Harness, foundation and delivery | As the owner, the app serves the web UI over plain HTTP on the Wi-Fi address and |
| [026](026-WEB-02-as-any-user-on-the-home/ticket.md) | WEB-02 | P0 | 0b. Harness, foundation and delivery | As any user on the home Wi-Fi, I open the address and use the web UI without log |
| [027](027-BAK-01-automatic-verified-backups-after-each-session/ticket.md) | BAK-01 | P0 | 0b. Harness, foundation and delivery | Automatic verified backups (after each session, daily, before updates and restor |
| [028](028-DEV-01-the-setup-wizard-grants-bluetooth-notifications/ticket.md) | DEV-01 | P0 | 1. Run MVP (offline) | As a runner, the setup wizard grants Bluetooth, notifications, battery exemption |
| [029](029-DEV-02-i-enroll-the-omega-z-and/ticket.md) | DEV-02 | P0 | 1. Run MVP (offline) | As a runner, I enroll the Omega Z and see model, firmware and control status. |
| [030](030-DEV-03-i-enroll-hr-sensors-and-set/ticket.md) | DEV-03 | P0 | 1. Run MVP (offline) | As a runner, I enroll HR sensors and set preferred and fallback order per profil |
| [031](031-DEV-04-i-see-live-device-state-signal/ticket.md) | DEV-04 | P0 | 1. Run MVP (offline) | As a runner, I see live device state, signal and battery (phone and web). |
| [032](032-DEV-07-reconnect-backoff-per-6-1-no/ticket.md) | DEV-07 | P0 | 1. Run MVP (offline) | Reconnect backoff per 6.1; no treadmill scans during a run; HR scans within budg |
| [033](033-DEV-08-i-commission-treadmill-control-on-this/ticket.md) | DEV-08 | P0 | 1. Run MVP (offline) | As the owner, I commission treadmill control on this phone in approved stages, w |
| [034](034-RUN-01-today-recommends-today-s-single-item/ticket.md) | RUN-01 | P0 | 1. Run MVP (offline) | Today recommends: today's single item, then today's alternatives (explicit choic |
| [035](035-RUN-02-pre-run-check-with-readiness-and/ticket.md) | RUN-02 | P0 | 1. Run MVP (offline) | Pre-run check with readiness and preflight (machine, profile maximum, workout, p |
| [036](036-RUN-03-a-single-press-on-start-starts/ticket.md) | RUN-03 | P0 | 1. Run MVP (offline) | A single press on Start starts the belt. |
| [037](037-RUN-04-stepper-rows-with-requested-and-measured/ticket.md) | RUN-04 | P0 | 1. Run MVP (offline) | Stepper rows with requested and measured values and outcome states. |
| [038](038-RUN-05-stop-is-always-visible-the-stop/ticket.md) | RUN-05 | P0 | 1. Run MVP (offline) | STOP is always visible; the Stop sheet choices. |
| [039](039-RUN-06-pause-stops-the-belt-temporarily-and/ticket.md) | RUN-06 | P0 | 1. Run MVP (offline) | Pause stops the belt temporarily and keeps progress; Resume (single press) conti |
| [040](040-RUN-07-segment-advance-fixed-targets-once-per/ticket.md) | RUN-07 | P0 | 1. Run MVP (offline) | Segment advance; fixed targets once per segment; overrides within the segment. |
| [041](041-RUN-08-link-drops-recorded-explained-and-reconciled/ticket.md) | RUN-08 | P0 | 1. Run MVP (offline) | Link drops recorded, explained, and reconciled per 5.7. |
| [042](042-RUN-09b-reboot-the-session-is-interrupted-runservice/ticket.md) | RUN-09b | P0 | 1. Run MVP (offline) | Reboot: the session is Interrupted; RunService does not auto-start; WebService d |
| [043](043-RUN-09a-process-death-recover-within-30-s/ticket.md) | RUN-09a | P0 | 1. Run MVP (offline) | Process death: recover within 30 s or Interrupted. |
| [044](044-RUN-10-hr-automation-in-shadow-decreaseonly-and/ticket.md) | RUN-10 | P0 | 1. Run MVP (offline) | HR automation in Shadow, DecreaseOnly and Full. |
| [045](045-RUN-11-natural-completion-one-stop-completed-only/ticket.md) | RUN-11 | P0 | 1. Run MVP (offline) | Natural completion: one Stop; Completed only after stopped telemetry. |
| [046](046-RUN-13-run-layouts-per-9-6/ticket.md) | RUN-13 | P0 | 1. Run MVP (offline) | Run layouts per 9.6. |
| [047](047-RUN-14-keep-screen-on-for-non-terminal/ticket.md) | RUN-14 | P0 | 1. Run MVP (offline) | Keep screen on for non-terminal sessions. |
| [048](048-RUN-16-read-only-run-the-console-controls/ticket.md) | RUN-16 | P0 | 1. Run MVP (offline) | Read-only run: the console controls the belt; the app records and guides. |
| [049](049-RUN-17-manual-run-without-a-workout/ticket.md) | RUN-17 | P0 | 1. Run MVP (offline) | Manual run without a workout. |
| [050](050-REC-01-1-hz-recording-that-survives-app/ticket.md) | REC-01 | P0 | 1. Run MVP (offline) | 1 Hz recording that survives app death. |
| [051](051-REC-02-rpe-1-10-and-a-note/ticket.md) | REC-02 | P0 | 1. Run MVP (offline) | RPE (1–10) and a note (≤ 1,000 characters) after the run, editable later (phone  |
| [052](052-REC-03-history-with-weekly-groups-totals-and/ticket.md) | REC-03 | P0 | 1. Run MVP (offline) | History with weekly groups, totals and filters. |
| [053](053-REC-07-export-fit-tcx-csv-or-json/ticket.md) | REC-07 | P0 | 1. Run MVP (offline) | Export FIT, TCX, CSV or JSON (share on the phone, download on the web). |
| [054](054-REC-09-a-tests-view-for-simulator-and/ticket.md) | REC-09 | P0 | 1. Run MVP (offline) | A Tests view for Simulator and SystemTest sessions. |
| [055](055-WKT-01-library-cards-plan-internal-hidden/ticket.md) | WKT-01 | P0 | 1. Run MVP (offline) | Library cards; plan-internal hidden. |
| [056](056-PLN-01-install-premade-plans-phase-and-week/ticket.md) | PLN-01 | P0 | 1. Run MVP (offline) | Install premade plans; phase and week grouping. |
| [057](057-PLN-02-only-a-completed-linked-hardware-session/ticket.md) | PLN-02 | P0 | 1. Run MVP (offline) | Only a Completed linked Hardware session advances the plan. |
| [058](058-PLN-06-start-a-plan-with-a-start/ticket.md) | PLN-06 | P0 | 1. Run MVP (offline) | Start a plan with a start date and weekdays; clear upcoming items. |
| [059](059-H10-01-hr-rr-and-contact-via-the/ticket.md) | H10-01 | P0 | 1. Run MVP (offline) | HR, RR and contact via the Polar SDK. |
| [060](060-BAK-02-download-a-backup-from-the-web/ticket.md) | BAK-02 | P0 | 1. Run MVP (offline) | Download a backup from the web UI (admin), encrypted by default. |
| [061](061-BAK-03-restore-from-the-external-folder-or/ticket.md) | BAK-03 | P0 | 1. Run MVP (offline) | Restore from the external folder or a web upload, with a preview and a safety ba |
| [062](062-BAK-04-backup-health-last-success-destination-reachable/ticket.md) | BAK-04 | P0 | 1. Run MVP (offline) | Backup health (last success, destination reachable, free space) is a persistent  |
| [063](063-BAK-05-i-import-my-old-runs-session/ticket.md) | BAK-05 | P0 | 1. Run MVP (offline) | As the owner, I import my old runs (session JSON exports or the `.trb` backup's  |
| [064](064-BAK-06-every-verified-backup-is-also-uploaded/ticket.md) | BAK-06 | P0 | 1. Run MVP (offline) | As the owner, every verified backup is also uploaded to my NAS share over SMB, a |
| [065](065-GAR-06-fit-share-phone-and-download-web/ticket.md) | GAR-06 | P0 | 1. Run MVP (offline) | FIT share (phone) and download (web) for every session. |
| [066](066-WEB-03-i-see-a-read-only-live/ticket.md) | WEB-03 | P0 | 1. Run MVP (offline) | As a user on another device, I see a read-only live view of the run (metrics, ch |
| [067](067-WEB-05-the-management-screens-run-in-the/ticket.md) | WEB-05 | P0 | 1. Run MVP (offline) | As a phone user, the management screens run in the in-app WebView against localh |
| [068](068-WEB-06-as-any-user-the-web-ui/ticket.md) | WEB-06 | P0 | 1. Run MVP (offline) | As any user, the web UI meets the 9.7 layout guide and budgets. |
| [069](069-PRF-01-profile-zones-up-to-10-and/ticket.md) | PRF-01 | P0 | 1. Run MVP (offline) | Profile, zones (up to 10) and HR controller settings within bounds (1.2). |
| [070](070-PRF-02-multiple-profiles-the-runner-is-chosen/ticket.md) | PRF-02 | P0 | 1. Run MVP (offline) | Multiple profiles; the runner is chosen on Today; per-profile sensor assignments |
| [071](071-GAR-00-i-prove-garmin-login-mfa-token/ticket.md) | GAR-00 | P1 | 2. Depth | As a developer, I prove Garmin login, MFA, token refresh and one upload from the |
| [072](072-GAR-01-the-kotlin-garmin-client-in-the/ticket.md) | GAR-01 | P1 | 2. Depth | The Kotlin Garmin client **in the phone app** uploads or matches completed Hardw |
| [073](073-GAR-02-garmin-status-in-plain-words-the/ticket.md) | GAR-02 | P1 | 2. Depth | Garmin status in plain words; the review queue ("Keep one" / "Restore two"). |
| [074](074-RUN-12-cues-step-change-hr-out-of/ticket.md) | RUN-12 | P1 | 2. Depth | Cues (step change, HR out of zone, halfway, connection, completion) with toggles |
| [075](075-RUN-15-resume-planned-controls-after-a-console/ticket.md) | RUN-15 | P1 | 2. Depth | Resume planned controls after a console change or restart. |
| [076](076-REC-04-session-detail-chart-with-a-240/ticket.md) | REC-04 | P1 | 2. Depth | Session detail: chart with a 240-point projection and the true count, splits, zo |
| [077](077-REC-05-compare-sessions-of-the-same-revision/ticket.md) | REC-05 | P1 | 2. Depth | Compare sessions of the same revision. |
| [078](078-REC-06-delete-with-preview/ticket.md) | REC-06 | P1 | 2. Depth | Delete with preview. |
| [079](079-REC-08-goals-and-progression-recommendations/ticket.md) | REC-08 | P1 | 2. Depth | Goals and progression recommendations. |
| [080](080-WKT-02-editor-web-each-save-creates-a/ticket.md) | WKT-02 | P1 | 2. Depth | Editor (web); each save creates a revision. |
| [081](081-WKT-03-optional-workout-imports-native-json-first/ticket.md) | WKT-03 | P2 | 2. Depth | Optional workout imports (native JSON first; QDomyos XML, FIT workout and v4 bun |
| [082](082-PLN-03-move-skip-restore-repeat-and-change/ticket.md) | PLN-03 | P1 | 2. Depth | Move, skip, restore, repeat and change days, each with a preview. |
| [083](083-PLN-04-calendar-series-alternatives-and-exceptions-with/ticket.md) | PLN-04 | P1 | 2. Depth | Calendar series, alternatives and exceptions with the four scopes. |
| [084](084-PLN-07-alternatives-per-plan-item/ticket.md) | PLN-07 | P1 | 2. Depth | Alternatives per plan item. |
| [085](085-H10-02-opt-in-recording-prepared-before-arming/ticket.md) | H10-02 | P1 | 2. Depth | Opt-in recording prepared before arming (6.2 rules). |
| [086](086-H10-03-fetch-align-fill-only-nulls-remove/ticket.md) | H10-03 | P1 | 2. Depth | Fetch, align, fill only nulls, remove. |
| [087](087-H10-04-live-hr-continues-during-prepare-and/ticket.md) | H10-04 | P1 | 2. Depth | Live HR continues during prepare and fetch. |
| [088](088-H10-05-manual-recording-archive-with-csv-export/ticket.md) | H10-05 | P1 | 2. Depth | Manual recording archive with CSV export. |
| [089](089-H10-06-error-106-shown-plainly-and-never/ticket.md) | H10-06 | P1 | 2. Depth | Error 106 shown plainly and never retried. |
| [090](090-DEV-05-h10-multi-connection-setting-via-the/ticket.md) | DEV-05 | P1 | 2. Depth | H10 multi-connection setting via the SDK, or Polar Flow guidance. |
| [091](091-DEV-06-maintenance-reminders-at-3-months-or/ticket.md) | DEV-06 | P1 | 2. Depth | Maintenance reminders at 3 months or 241 km after a baseline (Simulator and Syst |
| [092](092-PRF-03-display-preferences-balanced-large-high-contrast/ticket.md) | PRF-03 | P1 | 2. Depth | Display preferences (balanced, large, high-contrast; 2–3 primary metrics mapped  |
| [093](093-OPS-04-storage-management-journal-and-backup-retention/ticket.md) | OPS-04 | P1 | 2. Depth | Storage management: journal and backup retention, a free-space warning, and the  |
| [094](094-WKT-04-fit-workout-export/ticket.md) | WKT-04 | P2 | 3. Reach | FIT workout export. |
| [095](095-PLN-05-custom-programs/ticket.md) | PLN-05 | P2 | 3. Reach | Custom programs. |
| [096](096-GAR-03-watch-status-via-the-connect-iq/ticket.md) | GAR-03 | P2 | 3. Reach | Watch status via the Connect IQ Mobile SDK (needs the watch paired to the treadm |
| [097](097-GAR-04-connect-iq-store-gar-05-p2/ticket.md) | GAR-04 | P2 | 3. Reach | Connect IQ store. **GAR-05 (P2)** — Official Training API. |
| [098](098-H10-07-fallback-pftp-codec-behind-polarport/ticket.md) | H10-07 | P2 | 3. Reach | Fallback PFTP codec behind `PolarPort`. |
| [099](099-OPS-05-kiosk-mode-via-device-owner-lock/ticket.md) | OPS-05 | P2 | 3. Reach | Kiosk mode via Device Owner (lock-task allow-list: TreadmillRunner, Polar Flow,  |
