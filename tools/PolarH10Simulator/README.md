# Polar H10 BLE peripheral simulator

This is a free, standalone Windows test peripheral for cross-machine Bluetooth
testing. It advertises the standard Bluetooth SIG Heart Rate Service (`0x180D`)
and Heart Rate Measurement (`0x2A37`), a readable/notifiable Battery Level
(`0x180F`/`0x2A19`), plus the public Polar PFTP service and
MTU/D2H/H2D characteristic UUIDs used by the TreadmillRunner protocol client.
The simulator state machine is transport-independent and does not reference
TreadmillRunner production APIs or dependency injection.

## Requirement and launch

The host must be Windows 10/11 with a Bluetooth adapter and driver that support
GATT peripheral advertising. A normal laptop adapter may be central-only; in
that case Windows will refuse `GattServiceProvider.CreateAsync` or advertising.
The test client must run on another machine (or another supported BLE host) so
the radio path is real:

```powershell
dotnet run --project tools/PolarH10Simulator --framework net10.0-windows10.0.22621.0 -- `
  --name "Polar H10 Simulator" --heart-rate 72 --frame-size 20
```

Use `--help` for all options. The useful fault controls are:

* `--notification-delay-ms N` delays each PFTP and heart-rate notification.
* `--drop-every N` drops every Nth notification, causing a real client timeout.
* `--disconnect-after N` closes subscribed GATT sessions after notification N.
* `--mtu-write-without-response-only` removes acknowledged MTU writes so the
  production fallback path can be exercised.
* `--no-seed` starts with no recording; otherwise `simulated-run/SAMPLES.BPB`
  is available for list/read tests. `start`, `stop`, list/read, and remove are
  handled entirely in the simulator's bounded in-memory store.

The Windows API cannot promise a stable Bluetooth address or a custom local
name through `GattServiceProvider`; use the name shown by the Windows scanner
and inspect the advertised UUIDs before enrollment.

## Evidence boundary

Unit tests prove the deterministic state machine, framed responses, UUIDs, and
fault decisions only. A successful build or browser/emulator scan is not proof
of Windows radio advertising, cross-machine discovery, GATT connection, or a
physical Polar H10. Those require a supported peripheral-capable adapter and a
separate machine/physical-radio test; this repository does not claim that proof.
