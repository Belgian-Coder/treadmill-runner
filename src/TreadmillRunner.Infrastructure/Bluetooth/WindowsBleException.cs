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

public sealed class WindowsBleDisconnectedException : IOException
{
  public WindowsBleDisconnectedException()
    : base("The Windows BLE device disconnected while telemetry was subscribed.")
  {
  }
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
