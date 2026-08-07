# IQ Store submission checklist

- [x] Install Garmin Connect IQ SDK 9.2.0 and record its exact version.
- [ ] Securely back up the generated developer key; do not commit it.
- [x] Generate the protected developer key outside the repository; secure backup remains pending.
- [x] Run `eng/validate-connectiq.ps1 -RequireSdk` with all six targets warning-free.
- [x] Pass all Run No Evil tests on representative Fenix 8 47 mm and Vivoactive 5 simulators.
- [ ] Run every manifest target in the Garmin simulator and complete `test-matrix.md`.
- [ ] Test on the owner's Fenix 8 and identify/test the exact household Vivoactive model.
- [ ] Verify Select starts recording only once and Select stops/saves only once.
- [ ] Verify Back cannot accidentally discard an active recording.
- [ ] Verify Standalone recording with no URL/token and paired status with trusted HTTPS.
- [ ] Verify an invalid/revoked token reveals no profile or session information.
- [ ] Export a release `.iq` package with the Monkey C extension and record SHA-256.
- [ ] Inspect package permissions: only Fit, Communications, and Sensor.
- [ ] Capture real simulator screenshots for both round and Vivoactive layouts.
- [ ] Supply stable support and privacy HTTPS URLs and operator contact details.
- [ ] Proofread `listing.md`; verify current Garmin asset/copy rules.
- [ ] Upload through the Connect IQ developer dashboard and complete Garmin declarations.
- [ ] Record store listing URL, submitted version, review result, and release date here.
- [ ] After approval, install from IQ Store and repeat start/save/sync on both watches.

The implementation is prepared but not store-published until all unchecked external steps are complete.
