# IQ Store submission checklist

- [ ] Install the current Garmin Connect IQ SDK and record its exact version.
- [ ] Generate and securely back up the developer key; do not commit it.
- [ ] Run `eng/validate-connectiq.ps1 -DeveloperKey <path> -RequireSdk` with no warnings.
- [ ] Run every manifest target in the Garmin simulator and complete `test-matrix.md`.
- [ ] Test on the owner's Fenix 8 and identify/test the exact household Vivoactive model.
- [ ] Verify Select starts recording only once and Select stops/saves only once.
- [ ] Verify Back cannot accidentally discard an active recording.
- [ ] Verify Standalone recording with no URL/token and paired status with trusted HTTPS.
- [ ] Verify an invalid/revoked token reveals no profile or session information.
- [ ] Export a release `.iq` package with the Monkey C extension and record SHA-256.
- [ ] Inspect package permissions: only Fit and Communications.
- [ ] Capture real simulator screenshots for both round and Vivoactive layouts.
- [ ] Supply stable support and privacy HTTPS URLs and operator contact details.
- [ ] Proofread `listing.md`; verify current Garmin asset/copy rules.
- [ ] Upload through the Connect IQ developer dashboard and complete Garmin declarations.
- [ ] Record store listing URL, submitted version, review result, and release date here.
- [ ] After approval, install from IQ Store and repeat start/save/sync on both watches.

The implementation is prepared but not store-published until all unchecked external steps are complete.
