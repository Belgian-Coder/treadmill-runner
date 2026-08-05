using System.Collections.Concurrent;
using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Protocols.Ftms;
using TreadmillRunner.Infrastructure.Bluetooth;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Devices;

public interface ITreadmillCommandContextValidator
{
  bool IsCurrent(TreadmillCommandIntent intent);
}

public interface ITreadmillCommandCoordinator
{
  TreadmillCommandResult? LastResult { get; }

  Task<TreadmillCommandResult> ExecuteAsync(
    TreadmillCommandIntent intent,
    ITreadmillCommandContextValidator contextValidator,
    CancellationToken cancellationToken = default);
}

public sealed record TreadmillCommissioningApproval(
  string ExpectedModelNumber,
  string ExpectedFirmwareRevision,
  string Observer);

public sealed record TreadmillCommandPolicy(
  TimeSpan TelemetryFreshness,
  TimeSpan RequestControlResponseTimeout,
  TimeSpan ResponseTimeout,
  TimeSpan ConfirmationTimeout,
  TimeSpan ConfirmationPollInterval,
  bool AllowMissingRequestControlResponse = true)
{
  public static TreadmillCommandPolicy Default { get; } = new(
    TimeSpan.FromSeconds(5),
    TimeSpan.FromMilliseconds(300),
    TimeSpan.FromSeconds(2),
    TimeSpan.FromSeconds(5),
    TimeSpan.FromMilliseconds(100));
}

public sealed class TreadmillCommandCoordinator(
  TimeProvider timeProvider,
  IServiceScopeFactory scopeFactory,
  IBleCommandCentralTransport transport,
  IReadOnlyDeviceCoordinator deviceCoordinator,
  TreadmillCommandPolicy policy,
  ILogger<TreadmillCommandCoordinator> logger) : ITreadmillCommandCoordinator, IAsyncDisposable
{
  private static readonly Guid FtmsService = Expand(0x1826);
  private static readonly Guid FitnessMachineControlPoint = Expand(0x2AD9);
  private readonly SemaphoreSlim _commandGate = new(1, 1);
  private readonly ConcurrentDictionary<Guid, byte> _consumedOperations = new();
  private IBleCommandConnection? _connection;
  private string? _connectionDeviceId;
  private long _connectionGeneration;
  private bool _controlPointVerified;
  private bool _controlOwned;
  private int _disposed;
  private TreadmillCommandResult? _lastResult;

  public TreadmillCommandResult? LastResult => Volatile.Read(ref _lastResult);

  public Task<TreadmillCommandResult> ExecuteAsync(
    TreadmillCommandIntent intent,
    ITreadmillCommandContextValidator contextValidator,
    CancellationToken cancellationToken = default) =>
    ExecuteCoreAsync(intent, contextValidator, null, cancellationToken);

  public Task<TreadmillCommandResult> ExecuteCommissioningAsync(
    TreadmillCommandIntent intent,
    TreadmillCommissioningApproval approval,
    ITreadmillCommandContextValidator contextValidator,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(approval);
    return ExecuteCoreAsync(intent, contextValidator, approval, cancellationToken);
  }

  private async Task<TreadmillCommandResult> ExecuteCoreAsync(
    TreadmillCommandIntent intent,
    ITreadmillCommandContextValidator contextValidator,
    TreadmillCommissioningApproval? commissioningApproval,
    CancellationToken cancellationToken)
  {
    ArgumentNullException.ThrowIfNull(intent);
    ArgumentNullException.ThrowIfNull(contextValidator);
    if (!_consumedOperations.TryAdd(intent.OperationId, 0))
    {
      return Publish(Reject(intent, "This command operation was already consumed."));
    }

    await _commandGate.WaitAsync(cancellationToken);
    try
    {
      DateTimeOffset now = timeProvider.GetUtcNow();
      if (now > intent.ExpiresAt)
      {
        return Publish(Reject(intent, "The command intent expired before execution."));
      }

      if (!contextValidator.IsCurrent(intent))
      {
        return Publish(Reject(intent, "The session state, version, or control lease changed."));
      }

      VersionedDeviceEnrollment? stored = await LoadEnrollmentAsync(cancellationToken);
      if (!TryValidateEnrollment(
            stored,
            intent,
            commissioningApproval,
            out DeviceEnrollment? enrollment,
            out double? acceptedValue,
            out string? enrollmentError))
      {
        return Publish(Reject(intent, enrollmentError!));
      }

      DeviceTelemetrySnapshot before = deviceCoordinator.Current;
      if (!TryValidateDevice(before, intent, requireStopped: intent.Kind == TreadmillCommandKind.Start, out string? deviceError))
      {
        return Publish(Reject(intent, deviceError!));
      }

      IBleCommandConnection connection;
      try
      {
        connection = await GetOrCreateConnectionAsync(
          enrollment!.DeviceId,
          intent.ConnectionGeneration,
          cancellationToken);
      }
      catch (Exception exception) when (exception is not OperationCanceledException || !cancellationToken.IsCancellationRequested)
      {
        logger.LogWarning(exception, "FTMS command connection failed before a motion command was sent.");
        await ResetConnectionAsync();
        return Publish(Reject(intent, "FTMS command connection failed; no motion command was sent."));
      }

      if (!_controlOwned)
      {
        FtmsControlPointResponse controlResponse;
        try
        {
          BleNotification notification = await connection.ExchangeAsync(
            FtmsService,
            FitnessMachineControlPoint,
            FtmsControlPointCodec.EncodeRequestControl(),
            policy.RequestControlResponseTimeout,
            cancellationToken);
          if (!FtmsControlPointCodec.TryParseResponse(notification.Value.Span, out controlResponse) ||
              controlResponse.RequestOpCode != FtmsControlPointOpCode.RequestControl ||
              !controlResponse.IsSuccess)
          {
            await ResetConnectionAsync();
            return Publish(Reject(intent, "FTMS control was not granted; no motion command was sent."));
          }
          _controlOwned = true;
        }
        catch (WindowsBleResponseTimeoutException) when (policy.AllowMissingRequestControlResponse)
        {
          _controlOwned = true;
          logger.LogInformation(
            "The FTMS Request Control write completed without a response notification; retaining this connection for subsequent commands and continuing with exact-device response and telemetry confirmation for {CommandKind}.",
            intent.Kind);
        }
        catch (Exception exception) when (exception is not OperationCanceledException || !cancellationToken.IsCancellationRequested)
        {
          logger.LogWarning(exception, "FTMS control acquisition failed before a motion command was sent.");
          await ResetConnectionAsync();
          return Publish(Reject(intent, "FTMS control acquisition failed; no motion command was sent."));
        }
      }

      now = timeProvider.GetUtcNow();
      if (now > intent.ExpiresAt ||
          !contextValidator.IsCurrent(intent) ||
          !TryValidateDevice(
            deviceCoordinator.Current,
            intent,
            requireStopped: intent.Kind == TreadmillCommandKind.Start,
            out deviceError))
      {
        return Publish(Reject(intent, deviceError ?? "The command guards changed before the motion write."));
      }

      byte[] payload = intent.Kind switch
      {
        TreadmillCommandKind.Start => FtmsControlPointCodec.EncodeStartOrResume(),
        TreadmillCommandKind.SetSpeed => FtmsControlPointCodec.EncodeTargetSpeed(acceptedValue!.Value),
        TreadmillCommandKind.SetIncline => FtmsControlPointCodec.EncodeTargetInclination(acceptedValue!.Value),
        TreadmillCommandKind.Pause => FtmsControlPointCodec.EncodePause(),
        TreadmillCommandKind.Stop => FtmsControlPointCodec.EncodeStop(),
        _ => throw new InvalidOperationException($"Unsupported treadmill command {intent.Kind}."),
      };
      FtmsControlPointOpCode expectedOpCode = intent.Kind switch
      {
        TreadmillCommandKind.Start => FtmsControlPointOpCode.StartOrResume,
        TreadmillCommandKind.SetSpeed => FtmsControlPointOpCode.SetTargetSpeed,
        TreadmillCommandKind.SetIncline => FtmsControlPointOpCode.SetTargetInclination,
        TreadmillCommandKind.Pause or TreadmillCommandKind.Stop => FtmsControlPointOpCode.StopOrPause,
        _ => throw new InvalidOperationException($"Unsupported treadmill command {intent.Kind}."),
      };
      var motionWriteAttempted = false;
      BleNotification operationNotification;
      try
      {
        motionWriteAttempted = true;
        operationNotification = await connection.ExchangeAsync(
          FtmsService,
          FitnessMachineControlPoint,
          payload,
          policy.ResponseTimeout,
          cancellationToken);
      }
      catch (Exception exception) when (motionWriteAttempted)
      {
        logger.LogError(exception, "The physical outcome of treadmill command {CommandKind} is unknown.", intent.Kind);
        await ResetConnectionAsync();
        return Publish(Unknown(intent, acceptedValue, CurrentMeasuredValue(intent.Kind), "The BLE command outcome is unknown; inspect the treadmill and use physical Stop if needed."));
      }

      if (!FtmsControlPointCodec.TryParseResponse(operationNotification.Value.Span, out FtmsControlPointResponse operationResponse) ||
          operationResponse.RequestOpCode != expectedOpCode)
      {
        await ResetConnectionAsync();
        return Publish(Unknown(intent, acceptedValue, CurrentMeasuredValue(intent.Kind), "The treadmill returned an invalid command response; physical outcome is unknown."));
      }

      if (!operationResponse.IsSuccess)
      {
        return Publish(Reject(intent, $"The treadmill rejected {intent.Kind}: {operationResponse.ResultCode}."));
      }

      double? measured;
      using (var confirmationTimeout = new CancellationTokenSource(
        policy.ConfirmationTimeout + policy.ConfirmationPollInterval + TimeSpan.FromSeconds(1)))
      {
        try
        {
          measured = await WaitForTelemetryConfirmationAsync(
            intent,
            acceptedValue,
            operationNotification.ObservedAt,
            confirmationTimeout.Token);
        }
        catch (OperationCanceledException)
        {
          measured = null;
        }
      }
      if (measured is null)
      {
        await ResetConnectionAsync();
        return Publish(Unknown(intent, acceptedValue, CurrentMeasuredValue(intent.Kind), "Fresh measured telemetry did not confirm the command; physical outcome is unknown."));
      }

      return Publish(new TreadmillCommandResult(
        intent.OperationId,
        intent.Kind,
        TreadmillCommandDisposition.Confirmed,
        intent.RequestedValue,
        acceptedValue,
        measured,
        $"{intent.Kind} was confirmed by FTMS response and fresh treadmill telemetry.",
        intent.ConnectionGeneration,
        intent.IssuedAt,
        timeProvider.GetUtcNow()));
    }
    finally
    {
      _commandGate.Release();
    }
  }

  public async ValueTask DisposeAsync()
  {
    if (Interlocked.Exchange(ref _disposed, 1) != 0)
    {
      return;
    }

    await _commandGate.WaitAsync().ConfigureAwait(false);
    try
    {
      await ResetConnectionAsync().ConfigureAwait(false);
    }
    finally
    {
      _commandGate.Release();
      _commandGate.Dispose();
    }
  }

  private async Task<IBleCommandConnection> GetOrCreateConnectionAsync(
    string deviceId,
    long connectionGeneration,
    CancellationToken cancellationToken)
  {
    if (_connection is not null &&
        _controlPointVerified &&
        _connectionGeneration == connectionGeneration &&
        string.Equals(_connectionDeviceId, deviceId, StringComparison.Ordinal))
    {
      return _connection;
    }

    await ResetConnectionAsync().ConfigureAwait(false);
    IBleCommandConnection connection = await transport.ConnectCommandAsync(deviceId, cancellationToken);
    try
    {
      IReadOnlyList<BleService> services = await connection.DiscoverServicesAsync(cancellationToken);
      BleCharacteristic? controlPoint = services
        .FirstOrDefault(static service => service.Uuid == FtmsService)?
        .Characteristics.FirstOrDefault(static characteristic =>
          characteristic.CharacteristicUuid == FitnessMachineControlPoint);
      if (controlPoint is null || !controlPoint.CanWrite || !controlPoint.CanNotify)
      {
        throw new WindowsBleException("The verified FTMS control point is unavailable.");
      }

      _connection = connection;
      _connectionDeviceId = deviceId;
      _connectionGeneration = connectionGeneration;
      _controlPointVerified = true;
      _controlOwned = false;
      return connection;
    }
    catch
    {
      await connection.DisposeAsync();
      throw;
    }
  }

  private async ValueTask ResetConnectionAsync()
  {
    IBleCommandConnection? connection = _connection;
    _connection = null;
    _connectionDeviceId = null;
    _connectionGeneration = 0;
    _controlPointVerified = false;
    _controlOwned = false;
    if (connection is not null)
    {
      await connection.DisposeAsync();
    }
  }

  private async Task<VersionedDeviceEnrollment?> LoadEnrollmentAsync(CancellationToken cancellationToken)
  {
    using IServiceScope scope = scopeFactory.CreateScope();
    return await scope.ServiceProvider.GetRequiredService<IDeviceEnrollmentStore>()
      .FindActiveAsync(DeviceRole.Treadmill, cancellationToken);
  }

  private bool TryValidateEnrollment(
    VersionedDeviceEnrollment? stored,
    TreadmillCommandIntent intent,
    TreadmillCommissioningApproval? commissioningApproval,
    out DeviceEnrollment? enrollment,
    out double? acceptedValue,
    out string? error)
  {
    enrollment = stored?.Enrollment;
    acceptedValue = null;
    if (enrollment is null)
    {
      error = "No treadmill is enrolled.";
      return false;
    }

    if (enrollment.TelemetryMode != TreadmillTelemetryMode.Ftms)
    {
      error = "FTMS control is not explicitly selected for the enrolled treadmill.";
      return false;
    }

    if (commissioningApproval is not null)
    {
      if (string.IsNullOrWhiteSpace(commissioningApproval.Observer) ||
          commissioningApproval.Observer.Trim().Length > 100)
      {
        error = "A bounded observer label is required for commissioning.";
        return false;
      }

      if (enrollment.Evidence < TreadmillCapabilityEvidence.PassivelyObserved ||
          !string.Equals(enrollment.ProtocolId, "horizon-omega-z", StringComparison.Ordinal) ||
          !string.Equals(enrollment.ModelNumber, commissioningApproval.ExpectedModelNumber, StringComparison.OrdinalIgnoreCase) ||
          !string.Equals(enrollment.FirmwareRevision, commissioningApproval.ExpectedFirmwareRevision, StringComparison.OrdinalIgnoreCase))
      {
        error = "The enrolled treadmill model and firmware do not match this commissioning approval.";
        return false;
      }

      if (intent.Kind is TreadmillCommandKind.Start or TreadmillCommandKind.SetSpeed)
      {
        if (enrollment.Capabilities?.SpeedRange is not { } speedRange)
        {
          error = "The commissioning treadmill has no reported speed range.";
          return false;
        }

        acceptedValue = intent.Kind == TreadmillCommandKind.Start
          ? (double)speedRange.Minimum
          : AlignWithoutAggression(
            speedRange,
            intent.RequestedValue!.Value,
            deviceCoordinator.Current.TreadmillTelemetry?.SpeedKph ?? intent.RequestedValue.Value);
        if (intent.Kind == TreadmillCommandKind.Start &&
            Math.Abs(intent.RequestedValue!.Value - acceptedValue.Value) > 0.0001)
        {
          error = "Commissioning Start must use the reported minimum speed.";
          return false;
        }
      }
      else if (intent.Kind == TreadmillCommandKind.SetIncline)
      {
        if (enrollment.Capabilities?.InclineRange is not { } inclineRange)
        {
          error = "The commissioning treadmill has no reported incline range.";
          return false;
        }

        acceptedValue = AlignWithoutAggression(
          inclineRange,
          intent.RequestedValue!.Value,
          deviceCoordinator.Current.TreadmillTelemetry?.InclinePercent ?? intent.RequestedValue.Value);
      }

      error = null;
      return true;
    }

    if (enrollment.Evidence != TreadmillCapabilityEvidence.HardwareVerified ||
        string.IsNullOrWhiteSpace(enrollment.ModelNumber) ||
        string.IsNullOrWhiteSpace(enrollment.FirmwareRevision))
    {
      error = "Remote control is blocked until the exact treadmill model and firmware are hardware verified.";
      return false;
    }

    TreadmillCapabilities capabilities = enrollment.Capabilities!;
    if (intent.Kind == TreadmillCommandKind.Start)
    {
      if (!capabilities.CanStartRemotely || capabilities.SpeedRange is null)
      {
        error = "Remote Start is not verified for this treadmill model and firmware.";
        return false;
      }

      acceptedValue = (double)capabilities.SpeedRange.Minimum;
      if (Math.Abs(intent.RequestedValue!.Value - acceptedValue.Value) > 0.0001)
      {
        error = "The requested Start speed does not match the verified treadmill minimum.";
        return false;
      }
    }
    else if (intent.Kind == TreadmillCommandKind.SetSpeed)
    {
      if (!capabilities.CanSetSpeedRemotely || capabilities.SpeedRange is null)
      {
        error = "Remote speed control is not verified for this treadmill model and firmware.";
        return false;
      }

      acceptedValue = AlignWithoutAggression(
        capabilities.SpeedRange,
        intent.RequestedValue!.Value,
        deviceCoordinator.Current.TreadmillTelemetry?.SpeedKph ?? intent.RequestedValue.Value);
    }
    else if (intent.Kind == TreadmillCommandKind.SetIncline)
    {
      if (!capabilities.CanSetInclineRemotely || capabilities.InclineRange is null)
      {
        error = "Remote incline control is not verified for this treadmill model and firmware.";
        return false;
      }

      acceptedValue = AlignWithoutAggression(
        capabilities.InclineRange,
        intent.RequestedValue!.Value,
        deviceCoordinator.Current.TreadmillTelemetry?.InclinePercent ?? intent.RequestedValue.Value);
    }
    else if (intent.Kind == TreadmillCommandKind.Pause && !capabilities.CanPauseRemotely)
    {
      error = "Remote Pause is not verified for this treadmill model and firmware.";
      return false;
    }
    else if (intent.Kind == TreadmillCommandKind.Stop && !capabilities.CanStopRemotely)
    {
      error = "Remote Stop is not verified for this treadmill model and firmware.";
      return false;
    }

    error = null;
    return true;
  }

  private static double AlignWithoutAggression(
    TreadmillOperatingRange range,
    double requestedValue,
    double currentValue)
  {
    decimal requested = Math.Clamp((decimal)requestedValue, range.Minimum, range.Maximum);
    decimal rawSteps = (requested - range.Minimum) / range.Increment;
    decimal steps = requested >= (decimal)currentValue
      ? decimal.Floor(rawSteps)
      : decimal.Ceiling(rawSteps);
    return (double)(range.Minimum + (steps * range.Increment));
  }

  private bool TryValidateDevice(
    DeviceTelemetrySnapshot devices,
    TreadmillCommandIntent intent,
    bool requireStopped,
    out string? error)
  {
    if (devices.Treadmill.State != DeviceConnectionState.Ready ||
        devices.Treadmill.ConnectionGeneration != intent.ConnectionGeneration)
    {
      error = "The treadmill connection or connection generation changed.";
      return false;
    }

    if (devices.TreadmillTelemetry is not { } telemetry ||
        devices.TreadmillAge is not { } age ||
        age > policy.TelemetryFreshness)
    {
      error = "Fresh treadmill telemetry is required.";
      return false;
    }

    if (requireStopped && telemetry.SpeedKph > 0.05)
    {
      error = "Remote Start requires fresh telemetry confirming that the belt is stopped.";
      return false;
    }

    error = null;
    return true;
  }

  private async Task<double?> WaitForTelemetryConfirmationAsync(
    TreadmillCommandIntent intent,
    double? acceptedValue,
    DateTimeOffset responseObservedAt,
    CancellationToken cancellationToken)
  {
    DateTimeOffset deadline = timeProvider.GetUtcNow() + policy.ConfirmationTimeout;
    while (timeProvider.GetUtcNow() <= deadline)
    {
      DeviceTelemetrySnapshot devices = deviceCoordinator.Current;
      TreadmillTelemetry? telemetry = devices.TreadmillTelemetry;
      if (devices.Treadmill.ConnectionGeneration != intent.ConnectionGeneration)
      {
        return null;
      }

      if (telemetry is not null &&
          telemetry.ObservedAt >= responseObservedAt &&
          devices.TreadmillAge <= policy.TelemetryFreshness)
      {
        if (intent.Kind is (TreadmillCommandKind.Stop or TreadmillCommandKind.Pause) &&
            telemetry.SpeedKph <= 0.05)
        {
          return telemetry.SpeedKph;
        }

        if (intent.Kind is (TreadmillCommandKind.Start or TreadmillCommandKind.SetSpeed) &&
            acceptedValue is { } expected)
        {
          double tolerance = 0.15;
          if (telemetry.SpeedKph > 0.3 && Math.Abs(telemetry.SpeedKph - expected) <= tolerance)
          {
            return telemetry.SpeedKph;
          }
        }

        if (intent.Kind == TreadmillCommandKind.SetIncline && acceptedValue is { } expectedIncline)
        {
          double tolerance = 0.15;
          if (Math.Abs(telemetry.InclinePercent - expectedIncline) <= tolerance)
          {
            return telemetry.InclinePercent;
          }
        }
      }

      await Task.Delay(policy.ConfirmationPollInterval, timeProvider, cancellationToken);
    }

    return null;
  }

  private double? CurrentMeasuredValue(TreadmillCommandKind kind) => kind == TreadmillCommandKind.SetIncline
    ? deviceCoordinator.Current.TreadmillTelemetry?.InclinePercent
    : deviceCoordinator.Current.TreadmillTelemetry?.SpeedKph;

  private TreadmillCommandResult Publish(TreadmillCommandResult result)
  {
    Volatile.Write(ref _lastResult, result);
    return result;
  }

  private TreadmillCommandResult Reject(TreadmillCommandIntent intent, string reason) => new(
    intent.OperationId,
    intent.Kind,
    TreadmillCommandDisposition.Rejected,
    intent.RequestedValue,
    null,
    CurrentMeasuredValue(intent.Kind),
    reason,
    intent.ConnectionGeneration,
    intent.IssuedAt,
    Max(timeProvider.GetUtcNow(), intent.IssuedAt));

  private TreadmillCommandResult Unknown(
    TreadmillCommandIntent intent,
    double? acceptedValue,
    double? measuredValue,
    string reason) => new(
      intent.OperationId,
      intent.Kind,
      TreadmillCommandDisposition.Unknown,
      intent.RequestedValue,
      acceptedValue,
      measuredValue,
      reason,
      intent.ConnectionGeneration,
      intent.IssuedAt,
      Max(timeProvider.GetUtcNow(), intent.IssuedAt));

  private static DateTimeOffset Max(DateTimeOffset left, DateTimeOffset right) => left >= right ? left : right;

  private static Guid Expand(ushort shortUuid) =>
    Guid.Parse($"0000{shortUuid:x4}-0000-1000-8000-00805f9b34fb");
}
