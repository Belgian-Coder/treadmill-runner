using TreadmillRunner.Core.Bluetooth;

namespace TreadmillRunner.Gateway.Diagnostics;

public static class BleDiagnosticsEndpoints
{
  private const int MinimumScanDurationSeconds = 1;
  private const int MaximumScanDurationSeconds = 30;
  private const int MaximumDeviceIdLength = 256;
  private static readonly TimeSpan GattEnumerationTimeout = TimeSpan.FromSeconds(15);

  public static IEndpointRouteBuilder MapBleDiagnostics(this IEndpointRouteBuilder endpoints)
  {
    var group = endpoints.MapGroup("/api/diagnostics/ble");
    group.MapGet("/scan", ScanAsync);
    group.MapGet("/devices/{deviceId}/gatt", EnumerateGattAsync);
    return endpoints;
  }

  private static async Task<IResult> ScanAsync(
      int durationSeconds,
      IBleAdvertisementBroker advertisementBroker,
      HttpContext httpContext,
      ILoggerFactory loggerFactory,
      CancellationToken cancellationToken)
  {
    if (durationSeconds is < MinimumScanDurationSeconds or > MaximumScanDurationSeconds)
    {
      return TypedResults.ValidationProblem(new Dictionary<string, string[]>
      {
        ["durationSeconds"] = [$"Use a duration between {MinimumScanDurationSeconds} and {MaximumScanDurationSeconds} seconds."],
      });
    }

    using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, httpContext.RequestAborted);
    timeout.CancelAfter(TimeSpan.FromSeconds(durationSeconds));
    var advertisements = new Dictionary<string, BleAdvertisement>(StringComparer.Ordinal);

    try
    {
      await foreach (var advertisement in advertisementBroker.ScanAsync(timeout.Token).WithCancellation(timeout.Token))
      {
        if (string.IsNullOrWhiteSpace(advertisement.DeviceId))
        {
          continue;
        }

        advertisements[advertisement.DeviceId] = MergeAdvertisement(
            advertisements.GetValueOrDefault(advertisement.DeviceId),
            advertisement);
      }
    }
    catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested && !httpContext.RequestAborted.IsCancellationRequested)
    {
      // A bounded scan ends when its configured duration elapses.
    }
    catch (Exception exception)
    {
      loggerFactory.CreateLogger("BleDiagnostics").LogWarning(exception, "Passive BLE scan was unavailable.");
      return TypedResults.Problem("The Windows BLE adapter is unavailable.", statusCode: StatusCodes.Status503ServiceUnavailable);
    }

    httpContext.Response.Headers.CacheControl = "no-store";
    return TypedResults.Ok(new BleScanDiagnosticsResponse(
    durationSeconds,
    [.. advertisements.Values
                .OrderBy(static advertisement => advertisement.DeviceId, StringComparer.Ordinal)
                .Select(ToDiagnosticAdvertisement)]));
  }

  private static async Task<IResult> EnumerateGattAsync(
      string deviceId,
      IBleCentralTransport transport,
      HttpContext httpContext,
      ILoggerFactory loggerFactory,
      CancellationToken cancellationToken)
  {
    if (string.IsNullOrWhiteSpace(deviceId) || deviceId.Length > MaximumDeviceIdLength)
    {
      return TypedResults.ValidationProblem(new Dictionary<string, string[]>
      {
        ["deviceId"] = [$"Provide a non-empty device ID no longer than {MaximumDeviceIdLength} characters."],
      });
    }

    using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, httpContext.RequestAborted);
    timeout.CancelAfter(GattEnumerationTimeout);

    try
    {
      await using var connection = await transport.ConnectAsync(deviceId, timeout.Token);
      var services = await connection.DiscoverServicesAsync(timeout.Token);

      httpContext.Response.Headers.CacheControl = "no-store";
      return TypedResults.Ok(new BleGattDiagnosticsResponse(
    connection.DeviceId,
    [.. services
                    .OrderBy(static service => service.Uuid)
                    .Select(ToDiagnosticService)]));
    }
    catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested && !httpContext.RequestAborted.IsCancellationRequested)
    {
      return TypedResults.Problem("GATT enumeration exceeded its 15-second diagnostic limit.", statusCode: StatusCodes.Status504GatewayTimeout);
    }
    catch (Exception exception)
    {
      loggerFactory.CreateLogger("BleDiagnostics").LogWarning(exception, "Read-only GATT enumeration was unavailable for {DeviceId}.", deviceId);
      return TypedResults.Problem("The Windows BLE adapter is unavailable.", statusCode: StatusCodes.Status503ServiceUnavailable);
    }
  }

  private static BleAdvertisement MergeAdvertisement(BleAdvertisement? current, BleAdvertisement incoming)
  {
    if (current is null)
    {
      return incoming with { ServiceUuids = [.. incoming.ServiceUuids.Distinct().OrderBy(static uuid => uuid)] };
    }

    var useIncoming = incoming.SignalStrength is not null &&
        (current.SignalStrength is null || incoming.SignalStrength > current.SignalStrength);
    var strongest = useIncoming ? incoming : current;
    var name = strongest.Name ?? (useIncoming ? current.Name : incoming.Name);

    return strongest with
    {
      Name = name,
      ServiceUuids = [.. current.ServiceUuids.Concat(incoming.ServiceUuids).Distinct().OrderBy(static uuid => uuid)],
    };
  }

  private static BleDiagnosticAdvertisement ToDiagnosticAdvertisement(BleAdvertisement advertisement) => new(
      advertisement.DeviceId,
      advertisement.Name,
      advertisement.SignalStrength,
      [.. advertisement.ServiceUuids.OrderBy(static uuid => uuid)]);

  private static BleDiagnosticService ToDiagnosticService(BleService service) => new(
      service.Uuid,
      [.. service.Characteristics
            .OrderBy(static characteristic => characteristic.CharacteristicUuid)
            .Select(static characteristic => new BleDiagnosticCharacteristic(
                characteristic.CharacteristicUuid,
                characteristic.CanRead,
                characteristic.CanWrite,
                characteristic.CanNotify))]);
}

public sealed record BleScanDiagnosticsResponse(int DurationSeconds, IReadOnlyList<BleDiagnosticAdvertisement> Devices);

public sealed record BleDiagnosticAdvertisement(string DeviceId, string? Name, short? SignalStrength, IReadOnlyList<Guid> ServiceUuids);

public sealed record BleGattDiagnosticsResponse(string DeviceId, IReadOnlyList<BleDiagnosticService> Services);

public sealed record BleDiagnosticService(Guid Uuid, IReadOnlyList<BleDiagnosticCharacteristic> Characteristics);

public sealed record BleDiagnosticCharacteristic(Guid Uuid, bool CanRead, bool CanWrite, bool CanNotify);
