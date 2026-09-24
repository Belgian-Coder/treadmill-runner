---
title: Polar H10 Bluetooth connectivity research and improvement options
type: protocol-evidence
status: open
owner: project
audience: agent-and-developer
updated: 2026-09-24
---

# Polar H10 Bluetooth connectivity research and improvement options

## Current answer

This is desk research: public sources plus a read-only review of the source at commit `aee99d5`. No hardware test, HCI trace, or code change backs it yet. It follows the 22–23 September incident (installed v1.5.104). The H10 in use runs firmware **4.2.0**.

The most probable primary cause of the H10 drops is the passed-through MediaTek RZ616 (MT7922) Bluetooth controller and its virtual USB path. The H10 link is the first to hit its supervision timeout because a chest strap has less radio margin than the treadmill. The evidence:
- thousands of Windows `BTHUSB` Event 3 adapter-command timeouts;
- scans that saw zero advertisements while the radio reported On;
- Code 45 after a guest reboot;
- the notification-silence-then-disconnect signature.

This is a hypothesis. Only an HCI disconnect reason code can confirm it.

The application code is not shown to cause the drops. It makes each drop more expensive, at about 7–12 s. It does so by tearing down the WinRT device and session, running uncached discovery, and running an active scan after repeated failures. That adds radio load when the link is weakest.

Changing to another .NET BLE package would not change the Windows stack. InTheHand.BluetoothLE, Plugin.BLE, DSoft.System.BluetoothLe, Shiny.BluetoothLE and SimpleBLE's default backend all wrap WinRT on Windows.

## Decisive next experiments, in order

1. **Capture one drop with an HCI trace.** Use the [Bluetooth Virtual Sniffer](https://learn.microsoft.com/en-us/windows-hardware/drivers/bluetooth/testing-btp-tools-btvs) or the [WPR Bluetooth profile](https://github.com/microsoft/busiotools/blob/master/bluetooth/tracing/readme.md) and read the `Disconnection Complete` reason. What each code points to:
   - `0x08`, supervision timeout: radio or controller.
   - `0x13`, remote termination by the H10: skin contact, security, or multi-connection.
   - `0x16`, local termination: Windows stack or app.
   - `0x3E`: the connection failed to establish.
2. **Compare against a dedicated USB BLE dongle.** An RTL8761BU-class dongle such as the ASUS USB-BT500 goes on a USB 2.0 extension cable near the treadmill. It is passed through to the VM *with the VM stopped*, in place of the RZ616 Bluetooth function. Keep the same app, strap and workout. No code change is needed.
3. **Turn "2 Bluetooth devices" off for the H10** in Polar Flow, and keep one central only. Firmware 4.0.4 turned this setting on by default, and with it on the H10 keeps advertising while connected.
4. **On the Proxmox host**, check whether the MT7922 Wi-Fi function (`mt7921e`) or `btusb`/`btmtk` is loaded, and blacklist them if so. Never hot-unplug the passed-through device; a cold power cycle is the only known reset for a wedged controller.

## Firmware 4.x facts relevant to this project

| Version | Relevant change (official notes) |
| --- | --- |
| [4.0.4](https://support.polar.com/en/updates/polar-h10-firmware-update-404) (Dec 2025) | Secure pairing (the sensor-initiated security request); only the initiator can read a training recording; "2 Bluetooth devices" on by default. |
| [4.1.10](https://support.polar.com/en/updates/polar-h10-firmware-update-4110) (Dec 2025) | Lower power use while the sensor searches for a connection. Apps must use Polar SDK 6.12.0 or later. |
| [4.2.0](https://support.polar.com/en/updates/polar-h10-42-firmware-update) (Mar 2026) | The offline recording issue is fixed. |

- Downgrade is not possible.
- [polar-ble-sdk#774](https://github.com/polarofficial/polar-ble-sdk/issues/774): 4.0.4 straps disconnect during service enumeration on Windows 11. Polar has not answered.
- [polar-ble-sdk#778](https://github.com/polarofficial/polar-ble-sdk/issues/778): PFTP error 106 `OPERATION_NOT_PERMITTED` when the recording was started by another host. Treat 106 as terminal, and fetch only recordings this app started.
- The H10 ends the link 20–30 s after losing skin contact, and after 45 s when removed from the strap during a download ([SDK known issues](https://github.com/polarofficial/polar-ble-sdk/blob/master/documentation/KnownIssues.md)). The journal already records contact state.
- Heart rate service (HRS) data does not need bonding. Pairing the H10 in Windows caused auto-connect and hangs in [bleak#1943](https://github.com/hbldh/bleak/issues/1943). Do not pair the H10 for the HR path.
- Linux: there is no public report of 4.x disconnects, but also no evidence that Linux is immune. BlueZ pairing for PFTP would need an agent.

## Source findings (commit `aee99d5`)

| Finding | Location |
| --- | --- |
| Every failure disposes `BluetoothLEDevice` + `GattSession`, so `MaintainConnection` never gets to reconnect. The app does all reconnecting itself. | `WindowsBleReadOnlyConnection.cs` dispose; per-attempt `await using` in `ReadOnlyDeviceCoordinator.cs` |
| `GattSession` is created lazily at subscribe time; a null session is silently tolerated. | `WindowsBleReadOnlyConnection.cs`, `WindowsGattSessionPolicy.cs` |
| Rediscovery and PFTP-locator scans are `Active` and unfiltered. | `WindowsBleCentralTransport.cs`, `PolarH10ConnectionLocator.cs` |
| Targeted HRS discovery on each reconnect is always `Uncached`. | `WindowsBleReadOnlyConnection.cs` |
| No connection-parameter or PHY request or logging. | none in `src` |
| The HR silence watchdog is 30 s (15 s for the first notification). | `ReadOnlyDeviceCoordinator.cs` |
| The PFTP frame size is read from `MaxPduSize` right after the CCCD writes and falls back silently. | `PolarPftpGattTransport.cs` |

`Balanced` vs `PowerOptimized`: Microsoft publishes no numeric values, and the H10 may request its own parameters anyway. Log `GetConnectionParameters()`/`GetConnectionPhy()` before choosing. If an A/B test is run, `PowerOptimized` is the more defensible first trial for the HR link, and `ThroughputOptimized` fits only a PFTP download window ([Microsoft](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.bluetoothlepreferredconnectionparameters)).

## Proposed low-risk code changes, not yet implemented

These are additive diagnostics that avoid the reconnect cadence, disconnect grace and PFTP exclusion changed by the uncommitted R15 candidate:

1. Log connection interval, latency, supervision timeout and PHY at the first notification.
2. Log whether a `GattSession` was created, plus `CanMaintainConnection` and `MaintainConnection`.
3. Log pairing state and protection level.
4. Journal the inner `HResult` for silence failures.
5. Use passive, optionally `CoexistenceOptimized` (24H2+), scanning for reconnect rediscovery and PFTP location, keeping active scans for enrollment.

Defer a shared link owner that relies on `MaintainConnection` auto-reconnect. Revisit it after the HCI trace and the dongle A/B, and after R15 lands.

## Architecture options if a dongle does not fix it

| Option | Eliminates | Keeps or adds | Verdict |
| --- | --- | --- | --- |
| USB dongle into the Windows VM | RZ616 radio, Wi-Fi coexistence, antenna position | Still QEMU USB passthrough and the WinRT stack | Do first: cheap, no code |
| LXC on Proxmox | Nothing on its own: Linux only permits Bluetooth sockets in the initial network namespace, so `bluetoothd` must run on the host with a D-Bus proxy ([lucid-fabrics/proxmox-bluetooth](https://github.com/lucid-fabrics/proxmox-bluetooth)) | Bluetooth moves onto the hypervisor; same MT7922 radio | Not recommended |
| Raspberry Pi 4 (or 3B+) bridge near the treadmill, with a USB BLE dongle, Wi-Fi on 5 GHz, read-only overlay filesystem | VM USB passthrough, WinRT stack, combo-chip coexistence; full BlueZ tooling and `btmon` | New bridge service behind the existing transport seams; ~20–30 s boot | Preferred structural fix |
| ESP32 with Ethernet or ESP32-C5 on 5 GHz Wi-Fi | Same as the Pi; ~1–2 s boot | Custom ESP-IDF firmware; ESPHome `bluetooth_proxy` has no .NET client | Second choice |
| Pico W / Pico 2 W | — | Combo chip over Wi-Fi only; bare-metal BTstack/MicroPython | Not recommended |
| Android TV box (Shield / Google TV Streamer) as combined TV and bridge | Gains Polar's official Android SDK | 4K Wi-Fi, AirPods A2DP, H10 and treadmill on one combo chip; no external dongle; standby may kill services | Not recommended as the bridge |

The site has no Ethernet at the treadmill. A bridge therefore uses 5 GHz Wi-Fi on its onboard chip, with Bluetooth on a separate USB dongle, or a powerline adapter. Any network bridge needs a bridge-side dead-man for FTMS commands:
- refuse speed changes without a recent heartbeat from the app;
- stop locally if the app channel drops mid-run;
- use idempotent request IDs, and never replay or auto-retry a motion command.

## Library survey (September 2026)

| Package | Windows backend | Note |
| --- | --- | --- |
| [InTheHand.BluetoothLE](https://www.nuget.org/packages/InTheHand.BluetoothLE) 4.0.45 | WinRT | Sets `MaintainConnection`; uncached discovery for unpaired devices; Linux backend via BlueZ |
| [Plugin.BLE](https://www.nuget.org/packages/Plugin.BLE/) 3.2.1 | WinRT | Cached discovery by default; connection parameters on Windows 11; hang on connection loss ([#1009](https://github.com/dotnet-bluetooth-le/dotnet-bluetooth-le/issues/1009)) |
| [DSoft.System.BluetoothLe](https://www.nuget.org/packages/DSoft.System.BluetoothLe) | WinRT | Plugin.BLE fork |
| [Shiny.BluetoothLE](https://www.nuget.org/packages/Shiny.BluetoothLE/) 5.7.2 | WinRT / BlueZ | Built for app hosting |
| [Linux.Bluetooth](https://www.nuget.org/packages/Linux.Bluetooth) | BlueZ D-Bus | Candidate for a Linux bridge |
| [Bluetooth Framework](https://www.btframework.com/bluetoothframework.htm) | Own stack via BLED112 dongle | Commercial, Bluetooth 4.0 hardware; useful only as a diagnostic |

Polar's official SDK supports Android and iOS only.

## Additional sources

- [Microsoft GATT client guide](https://learn.microsoft.com/en-us/windows/apps/develop/devices-sensors/gatt-client) and [GattSession.MaintainConnection](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.genericattributeprofile.gattsession.maintainconnection)
- [Home Assistant Bluetooth guidance on USB 3 interference and virtualisation](https://www.home-assistant.io/integrations/bluetooth/)
- [QEMU USB passthrough documentation](https://qemu-project.gitlab.io/qemu/system/devices/usb.html)
- [bleak#1290: scanning pauses notifications on Linux](https://github.com/hbldh/bleak/issues/1290)
- [tinygo-bluetooth PR #481: do not dispose inside `ConnectionStatusChanged`](https://github.com/tinygo-org/bluetooth/pull/481)
