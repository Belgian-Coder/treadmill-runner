using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;

namespace TreadmillRunner.Infrastructure.Bluetooth;

public sealed class WindowsBleException : InvalidOperationException
{
  public WindowsBleException(string message)
    : base(message)
  {
  }

  internal WindowsBleException(
    string operation,
    GattCommunicationStatus status,
    ushort? protocolError)
    : base(CreateMessage(operation, status, protocolError))
  {
    Status = status;
    ProtocolError = protocolError;
  }

  public GattCommunicationStatus? Status { get; }

  public ushort? ProtocolError { get; }

  private static string CreateMessage(
    string operation,
    GattCommunicationStatus status,
    ushort? protocolError)
  {
    var protocolDetail = protocolError is null
      ? string.Empty
      : $" (ATT protocol error 0x{protocolError:X4})";
    return $"Windows BLE {operation} failed with {status}{protocolDetail}.";
  }
}

public sealed class WindowsBleDeviceUnavailableException : InvalidOperationException
{
  public WindowsBleDeviceUnavailableException()
    : base("Windows could not open the BLE device for read-only access.")
  {
  }
}

/// <summary>
/// The GATT write completed successfully, but the device did not publish a
/// response notification before the bounded response window elapsed.
/// </summary>
public sealed class WindowsBleResponseTimeoutException : TimeoutException
{
  public WindowsBleResponseTimeoutException(
    Guid serviceUuid,
    Guid characteristicUuid,
    Exception innerException)
    : base(
      $"Windows BLE wrote {characteristicUuid:D} on {serviceUuid:D}, but no response notification arrived in time.",
      innerException)
  {
    ServiceUuid = serviceUuid;
    CharacteristicUuid = characteristicUuid;
  }

  public Guid ServiceUuid { get; }

  public Guid CharacteristicUuid { get; }
}

public enum WindowsBleDisconnectOrigin
{
  Unknown,
  ConnectionStatusChanged,
  GattSessionStatusChanged,
  PostCccdConnectionStatusCheck,
}

/// <summary>
/// Safe, non-identifying context captured when Windows reports a BLE
/// disconnect. It intentionally contains no device identifiers, UUIDs, or
/// notification payloads.
/// </summary>
public sealed record WindowsBleDisconnectContext(
  WindowsBleDisconnectOrigin Origin,
  DateTimeOffset? CallbackAtUtc,
  GattSessionStatus? SessionStatus,
  BluetoothError? SessionError,
  bool CancellationRequested,
  bool DisposalRequested)
{
  public static WindowsBleDisconnectContext Unknown => new(
    WindowsBleDisconnectOrigin.Unknown,
    CallbackAtUtc: null,
    SessionStatus: null,
    SessionError: null,
    CancellationRequested: false,
    DisposalRequested: false);
}

public sealed class WindowsBleDisconnectedException : IOException
{
  public WindowsBleDisconnectedException()
    : this(WindowsBleDisconnectContext.Unknown)
  {
  }

  internal WindowsBleDisconnectedException(WindowsBleDisconnectContext context)
    : base("The Windows BLE device disconnected while telemetry was subscribed.")
  {
    ArgumentNullException.ThrowIfNull(context);
    Context = context;
  }

  public WindowsBleDisconnectContext Context { get; }

  public WindowsBleDisconnectOrigin Origin => Context.Origin;

  public DateTimeOffset? CallbackAtUtc => Context.CallbackAtUtc;

  public GattSessionStatus? SessionStatus => Context.SessionStatus;

  public BluetoothError? SessionError => Context.SessionError;

  public bool CancellationRequested => Context.CancellationRequested;

  public bool DisposalRequested => Context.DisposalRequested;
}

internal static class WindowsBleStatus
{
  public static void ThrowIfFailed(
    GattCommunicationStatus status,
    ushort? protocolError,
    string operation)
  {
    if (status != GattCommunicationStatus.Success)
    {
      throw new WindowsBleException(operation, status, protocolError);
    }
  }
}
