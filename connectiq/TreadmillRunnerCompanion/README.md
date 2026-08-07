# TreadmillRunner Companion for Garmin watches

This Connect IQ watch app records a native treadmill activity on a Garmin watch. Recording begins only when the runner presses **Select** on the watch and ends with another explicit **Select**, which stops and saves the activity. The app does not start, stop, pause, or otherwise control the physical treadmill.

The app works in two modes:

- **Standalone:** records a Garmin treadmill activity without the NUC. Garmin Connect receives it through the watch's normal synchronization.
- **Paired companion:** also displays the TreadmillRunner profile, planned session title, and gateway state. Pairing is optional and uses a revocable profile-owned token. It still requires an explicit watch interaction to record.

Supported manifest targets are the 43 mm and 47 mm AMOLED Fenix 8, the 47 mm and 51 mm Fenix 8 Solar, Vivoactive 5, and Vivoactive 6. Add another device only after a simulator/build pass confirms its API, layout, and input behavior.

## Build and simulator

Install Garmin's current Connect IQ SDK through SDK Manager and the Monkey C extension for Visual Studio Code. Generate a local developer key, keep it outside source control, and back it up securely. From the repository root:

```powershell
./eng/validate-connectiq.ps1 -RequireSdk
```

The script discovers SDK Manager's active SDK and the protected local key, performs structural safety checks, compiles all six declared targets warning-free, and runs three Run No Evil tests on representative `fenix847mm` and `vivoactive5` simulators. Pass `-DeveloperKey` or set `TREADMILLRUNNER_CONNECTIQ_DEVELOPER_KEY` on another workstation. Open `monkey.jungle` in the Monkey C extension to inspect every supported layout and verify Ready, recording, elapsed time, stop-and-save, Back protection, standalone state, paired state, and unavailable-gateway state.

Create the signed multi-device store package and its sanitized hash manifest with:

```powershell
./eng/package-connectiq.ps1
```

Without the SDK, `./eng/validate-connectiq.ps1` performs static preparation checks and explicitly reports that executable acceptance is pending. `-RequireSdk` makes that absence fail. Compiler/unit-test success is still not physical-watch or IQ Store acceptance.

## Pairing

1. In TreadmillRunner, open **Profiles**, edit the runner, and locate **Connect IQ watch app**.
2. Enter a watch label and select **Create watch pairing**.
3. Copy the one-time token immediately. The gateway stores only its SHA-256 hash.
4. In Garmin Connect Mobile, open the companion app settings and set:
   - Runner name: optional fallback label.
   - Gateway HTTPS URL: the trusted HTTPS origin for the household NUC, without a trailing slash.
   - Watch pairing token: the one-time token.
5. Open the app on the watch. Its footer changes from Standalone to the current gateway/session state after the next refresh.

Garmin's web-request API requires HTTPS. A plain `http://192.168.x.x` NUC address is not accepted by this companion. Standalone activity recording remains available when pairing or the gateway is unavailable.

## Release and store preparation

The `store` folder contains the listing copy, privacy disclosure, test matrix, and submission checklist. Store publishing requires a Garmin developer account, a local signing/developer key, compatible-device simulator evidence, listing graphics/screenshots, and Garmin review. Follow [the repository runbook](../../docs/project/connect-iq-companion.md); do not call the app published until Garmin accepts the submitted package.
