# IQ Store submission checklist

- [x] Install Garmin Connect IQ SDK 9.2.0 and record its exact version.
- [ ] Securely back up the generated developer key; do not commit it.
- [x] Generate the protected developer key outside the repository; secure backup remains pending.
- [x] Run `eng/validate-connectiq.ps1 -RequireSdk` with all six targets warning-free.
- [x] Pass all Run No Evil tests on representative Fenix 8 47 mm and Vivoactive 5 simulators.
- [ ] Run every manifest target in the Garmin simulator and complete `test-matrix.md`.
- [ ] Test on the owner's Fenix 8 and identify/test the exact household Vivoactive model.
- [x] Verify Select starts recording only once and Select stops/saves only once in both representative simulators.
- [x] Verify Back cannot accidentally discard an active recording in both representative simulators.
- [ ] Verify Standalone recording with no URL/token and paired status with trusted HTTPS.
- [ ] Verify an invalid/revoked token reveals no profile or session information.
- [x] Export the signed multi-device `.iq` and record its SHA-256 through `eng/package-connectiq.ps1`.
- [x] Inspect package permissions: only Fit, Communications, and Sensor.
- [x] Capture and visually approve real simulator screenshots for both round and Vivoactive layouts.
- [ ] Supply stable support and privacy HTTPS URLs and operator contact details.
- [ ] Proofread `listing.md`; verify current Garmin asset/copy rules.
- [ ] Upload through the Connect IQ developer dashboard and complete Garmin declarations.
- [ ] Record store listing URL, submitted version, review result, and release date here.
- [ ] After approval, install from IQ Store and repeat start/save/sync on both watches.

The implementation is prepared but not store-published until all unchecked external steps are complete.

The corrected source captures were approved on 2026-08-07. Physical-watch, paired HTTPS, invalid-token, and all-target interactive checks remain separate unchecked gates.
