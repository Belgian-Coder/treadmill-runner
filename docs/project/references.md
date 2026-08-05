---
title: Research References
type: reference-index
status: active
owner: project
audience: agent-and-developer
updated: 2026-08-02
---

# Research references

## Official platform sources

- [.NET support policy](https://dotnet.microsoft.com/en-us/platform/support/policy)
- [ASP.NET Core Blazor](https://learn.microsoft.com/en-us/aspnet/core/blazor/?view=aspnetcore-10.0)
- [Blazor render modes](https://learn.microsoft.com/en-us/aspnet/core/blazor/fundamentals/?view=aspnetcore-10.0)
- [Blazor JavaScript interop](https://learn.microsoft.com/en-us/aspnet/core/blazor/javascript-interoperability/call-javascript-from-dotnet?view=aspnetcore-10.0)
- [Chrome Web Bluetooth guide](https://developer.chrome.com/docs/capabilities/bluetooth)
- [Web Bluetooth specification](https://webbluetoothcg.github.io/web-bluetooth/)
- [Web Bluetooth implementation status](https://github.com/WebBluetoothCG/web-bluetooth/blob/main/implementation-status.md)
- [Microsoft Edge Web Bluetooth policy/platform support](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-browser-policies/defaultwebbluetoothguardsetting)
- [Chrome page lifecycle](https://developer.chrome.com/docs/web-platform/page-lifecycle-api)
- [WebKit position explaining Web Bluetooth non-support](https://webkit.org/tracking-prevention/)
- [WebKit Web Bluetooth issue](https://bugs.webkit.org/show_bug.cgi?id=101034)
- [Mozilla Web Bluetooth standards position](https://mozilla.github.io/standards-positions/#web-bluetooth)

## Gateway, data, and delivery sources

- [Host ASP.NET Core in a Windows Service](https://learn.microsoft.com/en-us/aspnet/core/host-and-deploy/windows-service?view=aspnetcore-10.0)
- [Windows Bluetooth LE sample](https://learn.microsoft.com/en-us/samples/microsoft/windows-universal-samples/bluetoothle/)
- [Windows BLE advertisement watcher](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.advertisement.bluetoothleadvertisementwatcher)
- [Windows GATT device service](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.genericattributeprofile.gattdeviceservice)
- [ASP.NET Core hosted services](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/host/hosted-services?view=aspnetcore-10.0)
- [ASP.NET Core SignalR](https://learn.microsoft.com/en-us/aspnet/core/signalr/introduction?view=aspnetcore-10.0)
- [ASP.NET Core health checks](https://learn.microsoft.com/en-us/aspnet/core/host-and-deploy/health-checks?view=aspnetcore-10.0)
- [Microsoft.Data.Sqlite online backup](https://learn.microsoft.com/en-us/dotnet/standard/data/sqlite/backup)
- [EF Core migration bundles](https://learn.microsoft.com/en-us/ef/core/managing-schemas/migrations/applying#bundles)
- [Garmin FIT SDK downloads and official C# package](https://developer.garmin.com/fit/get-the-sdk/)
- [Garmin FIT Workout file type](https://developer.garmin.com/fit/file-types/workout/)

### Superseded gateway alternatives

The following Raspberry Pi, BlueZ, and systemd sources are retained only as historical fallback research. They are not the active platform decision.

- [Raspberry Pi 5 product information](https://www.raspberrypi.com/products/raspberry-pi-5/)
- [Raspberry Pi configuration, hostname, mDNS, Wi-Fi, and DHCP](https://www.raspberrypi.com/documentation/configuration/computers/raspberry-pi.html)
- [.NET deployment to ARM single-board computers](https://learn.microsoft.com/en-us/dotnet/iot/deployment)
- [BlueZ D-Bus adapter API](https://bluez.readthedocs.io/en/latest/adapter-api/)
- [BlueZ D-Bus device API](https://bluez.readthedocs.io/en/latest/device-api/)
- [BlueZ D-Bus GATT API](https://bluez.readthedocs.io/en/latest/gatt-api/)
- [SignalR JavaScript automatic reconnect](https://learn.microsoft.com/en-us/aspnet/core/signalr/javascript-client?view=aspnetcore-10.0)
- [systemd service restart and watchdog options](https://manpages.debian.org/bookworm-backports/systemd/systemd.service.5.en.html)
- [GitHub build provenance attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)
- [GitHub immutable release verification](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/verify-release-integrity)
- [Sigstore Cosign blob signing](https://docs.sigstore.dev/cosign/signing/signing_with_blobs/)
- [Sigstore Cosign blob verification](https://docs.sigstore.dev/cosign/verifying/verify/)
- [balenaCloud pricing](https://www.balena.io/pricing)
- [balena update locks](https://docs.balena.io/runtime/update-locking/)
- [Mender artifact signing](https://docs.mender.io/artifact-creation/sign-and-verify)
- [RAUC signed A/B update system](https://rauc.io/)

## QZ evidence

- [qdomyos-zwift repository](https://github.com/cagnulein/qdomyos-zwift)
- [Omega Z issue 841](https://github.com/cagnulein/qdomyos-zwift/issues/841)
- [Omega Z telemetry issue 3137](https://github.com/cagnulein/qdomyos-zwift/issues/3137)
- [Force FTMS recommendation](https://github.com/cagnulein/qdomyos-zwift/issues/3137#issuecomment-2629857424)
- [Force FTMS user confirmation](https://github.com/cagnulein/qdomyos-zwift/issues/3137#issuecomment-2642476993)
- [Omega Z incline issue 3809](https://github.com/cagnulein/qdomyos-zwift/issues/3809)
- [Omega Z two-setting confirmation](https://github.com/cagnulein/qdomyos-zwift/issues/3809#issuecomment-4638764830)
- [Omega Z compatibility toggle pull request 4698](https://github.com/cagnulein/qdomyos-zwift/pull/4698)
- [Omega Z compatibility toggle commit 78256d9](https://github.com/cagnulein/qdomyos-zwift/commit/78256d96c)

Local evidence is in the sibling `qdomyos-zwift` checkout. Its most relevant
files are:

- `src/devices/horizontreadmill/horizontreadmill.cpp`
- `src/devices/horizontreadmill/horizontreadmill.h`
- `src/devices/bluetooth.cpp`
- `src/devices/domyostreadmill/domyostreadmill.cpp`
- `src/devices/ftmsbike/ftmsbike.h`
- `src/settings.qml`
- `LICENSE`

## Evidence labels

- "Verified" means directly supported by current source code, a captured issue
  log, or official platform documentation.
- "Inferred" means an engineering conclusion from those sources.
- "Unresolved" means the physical Omega Z must decide the result.

The protocol notes explicitly label the main unresolved question: proprietary
versus FTMS incline control on the exact treadmill firmware.
