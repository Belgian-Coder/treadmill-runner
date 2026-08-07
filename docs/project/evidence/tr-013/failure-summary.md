---
title: TR-013 Connect IQ SDK resolved failures
type: validation-failure-summary
status: resolved
owner: project
audience: developer
updated: 2026-08-07
---

# TR-013 Connect IQ SDK resolved failures

The real compiler exposed issues that the earlier static source check could not detect:

- Unsupported `String.startsWith` usage was replaced with the compatible prefix-position check.
- Missing `Toybox.Lang` and `Toybox.WatchUi` imports and the initial-view return type were corrected.
- The required `Sensor` permission was added for explicit heart-rate sensor enablement.
- An unsupported `maxLength` attribute was removed from the URL setting.
- Device-specific launcher resources removed Vivoactive and Solar-size warnings.
- The first test-run parser expected an older Garmin success string even though all tests passed; it now accepts both supported zero-failure formats.
- Full validation exposed source lock files left with migration-bundle runtime sections from an earlier local release. The project bootstrap restored SDK 10.0.110 lock state, and `publish-release.ps1` now snapshots and byte-restores every source lock around EF bundle creation.

Every failure was rerun through the same real SDK command. The final Connect IQ invocation completed in 41.2 seconds with zero compiler warnings and no failed tests. Full deterministic validation then passed in 155.6 seconds, and a 69.8-second local packaging proof preserved all five source lock files byte-for-byte.
