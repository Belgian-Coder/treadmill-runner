using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Devices;

public sealed record FtmsCommandCommissioningRequest(
  Guid OperationId,
  TreadmillCommandKind Kind,
  string ExpectedModelNumber,
  string ExpectedFirmwareRevision,
  string Observer,
  double? RequestedValue);

public sealed record FtmsCommandCommissioningOutcome(
  Guid OperationId,
  TreadmillCommandKind Kind,
  TreadmillCommandDisposition Disposition,
  bool CapabilityPromoted,
  string Reason,
  double? RequestedValue,
  double? AcceptedValue,
  double? MeasuredValue,
  long ConnectionGeneration,
  DateTimeOffset IssuedAt,
  DateTimeOffset CompletedAt);

public interface IFtmsCommandCommissioningRunner
{
  Task<FtmsCommandCommissioningOutcome> RunAsync(
    FtmsCommandCommissioningRequest request,
    CancellationToken cancellationToken);
}

public sealed record FtmsStartStopCommissioningRequest(
  Guid StartOperationId,
  Guid StopOperationId,
  string ExpectedModelNumber,
  string ExpectedFirmwareRevision,
  string Observer);

public sealed record FtmsStartStopCommissioningOutcome(
  FtmsCommandCommissioningOutcome Start,
  FtmsCommandCommissioningOutcome? Stop,
  TimeSpan RequestedMovingDuration)
{
  public TimeSpan StartEndToEndLatency => Start.CompletedAt - Start.IssuedAt;
  public TimeSpan? StopEndToEndLatency => Stop is null ? null : Stop.CompletedAt - Stop.IssuedAt;
  public TimeSpan? ConfirmedStartToStopIntentInterval =>
    Stop is null ? null : Stop.IssuedAt - Start.CompletedAt;
  public TimeSpan? ConfirmedStartToConfirmedStopInterval =>
    Stop is null ? null : Stop.CompletedAt - Start.CompletedAt;
  public TimeSpan? PairEndToEndLatency =>
    Stop is null ? null : Stop.CompletedAt - Start.IssuedAt;
}

public interface ICommissioningDelay
{
  Task DelayAsync(TimeSpan duration, CancellationToken cancellationToken);
}

public sealed class SystemCommissioningDelay(TimeProvider timeProvider) : ICommissioningDelay
{
  public Task DelayAsync(TimeSpan duration, CancellationToken cancellationToken) =>
    Task.Delay(duration, timeProvider, cancellationToken);
}

public sealed class FtmsStartStopCommissioningRunner(
  IFtmsCommandCommissioningRunner stages,
  ICommissioningDelay delay)
{
  public static TimeSpan MovingDuration { get; } = TimeSpan.FromSeconds(3);

  public async Task<FtmsStartStopCommissioningOutcome> RunAsync(
    FtmsStartStopCommissioningRequest request,
    CancellationToken cancellationToken)
  {
    if (request.StartOperationId == Guid.Empty || request.StopOperationId == Guid.Empty)
      throw new ArgumentException("Start and Stop require separate non-empty operation IDs.");
    if (request.StartOperationId == request.StopOperationId)
      throw new ArgumentException("Start and Stop operation IDs must be different.");

    FtmsCommandCommissioningOutcome start = await stages.RunAsync(
      new FtmsCommandCommissioningRequest(
        request.StartOperationId,
        TreadmillCommandKind.Start,
        request.ExpectedModelNumber,
        request.ExpectedFirmwareRevision,
        request.Observer,
        RequestedValue: null),
      cancellationToken);
    if (start.Disposition != TreadmillCommandDisposition.Confirmed || !start.CapabilityPromoted)
    {
      return new FtmsStartStopCommissioningOutcome(start, Stop: null, MovingDuration);
    }

    await delay.DelayAsync(MovingDuration, cancellationToken);
    FtmsCommandCommissioningOutcome stop = await stages.RunAsync(
      new FtmsCommandCommissioningRequest(
        request.StopOperationId,
        TreadmillCommandKind.Stop,
        request.ExpectedModelNumber,
        request.ExpectedFirmwareRevision,
        request.Observer,
        RequestedValue: null),
      cancellationToken);
    return new FtmsStartStopCommissioningOutcome(start, stop, MovingDuration);
  }
}

public sealed record FtmsDailyControlSequenceRequest(
  Guid SequenceOperationId,
  string ExpectedModelNumber,
  string ExpectedFirmwareRevision,
  string Observer);

public sealed record FtmsDailyControlSequenceStep(
  string Name,
  FtmsCommandCommissioningOutcome Outcome)
{
  public TimeSpan EndToEndLatency => Outcome.CompletedAt - Outcome.IssuedAt;
}

public sealed record FtmsDailyControlSequenceOutcome(
  Guid SequenceOperationId,
  IReadOnlyList<FtmsDailyControlSequenceStep> Steps,
  bool SafetyStopSent,
  double? FinalSpeedKph,
  double? FinalInclinePercent,
  bool FinalTelemetryFresh,
  bool SpeedAndInclineReturnedToZero,
  DateTimeOffset CompletedAt);

public sealed class FtmsDailyControlSequenceRunner(
  TimeProvider timeProvider,
  IFtmsCommandCommissioningRunner stages,
  ICommissioningDelay delay,
  IReadOnlyDeviceCoordinator devices)
{
  public static TimeSpan ObservationHold { get; } = TimeSpan.FromSeconds(2);

  private static readonly (string Name, TreadmillCommandKind Kind, double? Target)[] PlannedSteps =
  [
    ("Start at verified minimum 0.8 km/h", TreadmillCommandKind.Start, null),
    ("Set speed 1.2 km/h", TreadmillCommandKind.SetSpeed, 1.2),
    ("Set speed 1.5 km/h", TreadmillCommandKind.SetSpeed, 1.5),
    ("Set incline 1.0%", TreadmillCommandKind.SetIncline, 1.0),
    ("Set speed 1.0 km/h", TreadmillCommandKind.SetSpeed, 1.0),
    ("Set incline 0.5%", TreadmillCommandKind.SetIncline, 0.5),
  ];

  public async Task<FtmsDailyControlSequenceOutcome> RunAsync(
    FtmsDailyControlSequenceRequest request,
    CancellationToken cancellationToken)
  {
    if (request.SequenceOperationId == Guid.Empty)
      throw new ArgumentException("A non-empty sequence operation ID is required.");

    var results = new List<FtmsDailyControlSequenceStep>(PlannedSteps.Length + 1);
    bool started = false;
    for (var index = 0; index < PlannedSteps.Length; index++)
    {
      (string name, TreadmillCommandKind kind, double? target) = PlannedSteps[index];
      FtmsCommandCommissioningOutcome outcome = await stages.RunAsync(
        CreateRequest(request, index, kind, target),
        cancellationToken);
      results.Add(new FtmsDailyControlSequenceStep(name, outcome));
      started |= kind == TreadmillCommandKind.Start &&
        outcome.Disposition == TreadmillCommandDisposition.Confirmed;
      if (outcome.Disposition != TreadmillCommandDisposition.Confirmed)
      {
        break;
      }

      await delay.DelayAsync(ObservationHold, cancellationToken);
    }

    bool safetyStopSent = false;
    if (started)
    {
      FtmsCommandCommissioningOutcome stop = await stages.RunAsync(
        CreateRequest(request, PlannedSteps.Length, TreadmillCommandKind.Stop, target: null),
        cancellationToken);
      results.Add(new FtmsDailyControlSequenceStep("Stop", stop));
      safetyStopSent = true;
    }

    DeviceTelemetrySnapshot final = devices.Current;
    for (var sample = 0; sample < 20; sample++)
    {
      if (IsFreshAndZero(final)) break;
      await delay.DelayAsync(TimeSpan.FromMilliseconds(250), cancellationToken);
      final = devices.Current;
    }

    double? speed = final.TreadmillTelemetry?.SpeedKph;
    double? incline = final.TreadmillTelemetry?.InclinePercent;
    bool fresh = final.TreadmillAge <= TimeSpan.FromSeconds(5);
    return new FtmsDailyControlSequenceOutcome(
      request.SequenceOperationId,
      results,
      safetyStopSent,
      speed,
      incline,
      fresh,
      fresh && speed is <= 0.05 && incline is >= -0.05 and <= 0.05,
      timeProvider.GetUtcNow());
  }

  private static FtmsCommandCommissioningRequest CreateRequest(
    FtmsDailyControlSequenceRequest sequence,
    int index,
    TreadmillCommandKind kind,
    double? target) => new(
      DeriveOperationId(sequence.SequenceOperationId, index),
      kind,
      sequence.ExpectedModelNumber,
      sequence.ExpectedFirmwareRevision,
      sequence.Observer,
      target);

  private static Guid DeriveOperationId(Guid sequenceId, int index)
  {
    byte[] hash = SHA256.HashData(Encoding.UTF8.GetBytes($"{sequenceId:N}:{index}"));
    return new Guid(hash.AsSpan(0, 16));
  }

  private static bool IsFreshAndZero(DeviceTelemetrySnapshot snapshot) =>
    snapshot.TreadmillAge <= TimeSpan.FromSeconds(5) &&
    snapshot.TreadmillTelemetry is { SpeedKph: <= 0.05, InclinePercent: >= -0.05 and <= 0.05 };
}

public static class CommissioningCapabilityPromotion
{
  public static TreadmillCapabilities Promote(
    TreadmillCapabilities existing,
    TreadmillCommandKind kind) => kind switch
    {
      TreadmillCommandKind.Start => existing with { CanStartRemotely = true },
      TreadmillCommandKind.SetSpeed => existing with { CanSetSpeedRemotely = true },
      TreadmillCommandKind.SetIncline => existing with { CanSetInclineRemotely = true },
      TreadmillCommandKind.Pause => existing with { CanPauseRemotely = true },
      TreadmillCommandKind.Stop => existing with { CanStopRemotely = true },
      _ => throw new ArgumentOutOfRangeException(nameof(kind)),
    };
}

public sealed class FtmsCommandCommissioningRunner(
  TimeProvider timeProvider,
  IServiceScopeFactory scopeFactory,
  IReadOnlyDeviceCoordinator deviceCoordinator,
  TreadmillCommandCoordinator commandCoordinator) : IFtmsCommandCommissioningRunner
{
  private static readonly TimeSpan TelemetryWait = TimeSpan.FromSeconds(30);

  public async Task<FtmsCommandCommissioningOutcome> RunAsync(
    FtmsCommandCommissioningRequest request,
    CancellationToken cancellationToken)
  {
    Validate(request);
    VersionedDeviceEnrollment enrollment = await LoadEnrollmentAsync(cancellationToken);
    ValidateIdentity(enrollment.Enrollment, request);
    DeviceTelemetrySnapshot devices = await WaitForFreshDeviceAsync(cancellationToken);
    double observedSpeed = devices.TreadmillTelemetry!.SpeedKph;
    if (request.Kind == TreadmillCommandKind.Start && observedSpeed > 0.05)
      throw new InvalidOperationException("The Start stage requires fresh telemetry confirming a stopped belt; no attempt was reserved.");
    if (request.Kind is (TreadmillCommandKind.SetSpeed or TreadmillCommandKind.Pause) && observedSpeed <= 0.3)
      throw new InvalidOperationException($"The {request.Kind} stage requires a belt already moving above 0.3 km/h; no attempt was reserved.");

    using IServiceScope reservationScope = scopeFactory.CreateScope();
    IOperationReceiptStore receipts = reservationScope.ServiceProvider.GetRequiredService<IOperationReceiptStore>();
    string fingerprint = Fingerprint(request, enrollment.Enrollment.IdentityFingerprint);
    bool reserved = await receipts.TryAddAsync(new OperationReceipt(
      Guid.NewGuid(),
      request.OperationId,
      "ftms-command-commissioning-attempt",
      102,
      "{\"status\":\"reserved-before-command\"}",
      timeProvider.GetUtcNow(),
      fingerprint), cancellationToken);
    if (!reserved)
    {
      throw new InvalidOperationException(
        "This commissioning operation ID was already reserved. Generate a new ID; the previous command must never be replayed.");
    }

    double? target = ResolveTarget(request, enrollment.Enrollment.Capabilities!);
    DateTimeOffset issuedAt = timeProvider.GetUtcNow();
    var intent = new TreadmillCommandIntent(
      request.OperationId,
      Guid.NewGuid(),
      request.Kind,
      issuedAt,
      issuedAt.AddSeconds(4),
      0,
      request.Kind == TreadmillCommandKind.Start
        ? SessionState.ArmedWaitingForPhysicalStart
        : SessionState.Running,
      Guid.NewGuid(),
      $"commissioning:{request.Observer.Trim()}",
      devices.Treadmill.ConnectionGeneration,
      target,
      TreadmillCommandOrigin.Commissioning);
    var context = new FixedCommissioningContext(intent);
    TreadmillCommandResult result = await commandCoordinator.ExecuteCommissioningAsync(
      intent,
      new TreadmillCommissioningApproval(
        request.ExpectedModelNumber.Trim(),
        request.ExpectedFirmwareRevision.Trim(),
        request.Observer.Trim()),
      context,
      cancellationToken);

    bool promoted = false;
    if (result.Disposition == TreadmillCommandDisposition.Confirmed)
    {
      using IServiceScope promotionScope = scopeFactory.CreateScope();
      IDeviceEnrollmentStore store = promotionScope.ServiceProvider.GetRequiredService<IDeviceEnrollmentStore>();
      VersionedDeviceEnrollment latest = await store.FindActiveAsync(DeviceRole.Treadmill, cancellationToken)
        ?? throw new InvalidOperationException("The treadmill enrollment disappeared after command confirmation.");
      ValidateIdentity(latest.Enrollment, request);
      TreadmillCapabilities promotedCapabilities = CommissioningCapabilityPromotion.Promote(
        latest.Enrollment.Capabilities!,
        request.Kind);
      await store.UpdateEvidenceAsync(
        latest.Enrollment.Id,
        latest.Version,
        request.ExpectedModelNumber,
        request.ExpectedFirmwareRevision,
        promotedCapabilities,
        TreadmillCapabilityEvidence.HardwareVerified,
        result.CompletedAt,
        cancellationToken);
      promoted = true;
    }

    return new FtmsCommandCommissioningOutcome(
      result.OperationId,
      result.Kind,
      result.Disposition,
      promoted,
      result.Reason,
      result.RequestedValue,
      result.AcceptedValue,
      result.MeasuredValue,
      result.ConnectionGeneration,
      result.IssuedAt,
      result.CompletedAt);
  }

  private async Task<VersionedDeviceEnrollment> LoadEnrollmentAsync(CancellationToken cancellationToken)
  {
    using IServiceScope scope = scopeFactory.CreateScope();
    return await scope.ServiceProvider.GetRequiredService<IDeviceEnrollmentStore>()
      .FindActiveAsync(DeviceRole.Treadmill, cancellationToken)
      ?? throw new InvalidOperationException("No treadmill is enrolled.");
  }

  private async Task<DeviceTelemetrySnapshot> WaitForFreshDeviceAsync(CancellationToken cancellationToken)
  {
    DateTimeOffset deadline = timeProvider.GetUtcNow() + TelemetryWait;
    while (timeProvider.GetUtcNow() <= deadline)
    {
      DeviceTelemetrySnapshot snapshot = deviceCoordinator.Current;
      if (snapshot.Treadmill.State == DeviceConnectionState.Ready &&
          snapshot.Treadmill.ConnectionGeneration > 0 &&
          snapshot.TreadmillTelemetry is not null &&
          snapshot.TreadmillAge <= TimeSpan.FromSeconds(5))
      {
        return snapshot;
      }

      await Task.Delay(TimeSpan.FromMilliseconds(250), timeProvider, cancellationToken);
    }

    throw new InvalidOperationException("Fresh treadmill telemetry was not available within 30 seconds; no attempt was reserved and no command was sent.");
  }

  private static double? ResolveTarget(
    FtmsCommandCommissioningRequest request,
    TreadmillCapabilities capabilities) => request.Kind switch
    {
      TreadmillCommandKind.Start => (double)(capabilities.SpeedRange
        ?? throw new InvalidOperationException("The treadmill has no reported speed range.")).Minimum,
      TreadmillCommandKind.SetSpeed or TreadmillCommandKind.SetIncline => request.RequestedValue,
      _ => null,
    };

  private static void Validate(FtmsCommandCommissioningRequest request)
  {
    if (request.OperationId == Guid.Empty) throw new ArgumentException("A unique operation ID is required.");
    ArgumentException.ThrowIfNullOrWhiteSpace(request.ExpectedModelNumber);
    ArgumentException.ThrowIfNullOrWhiteSpace(request.ExpectedFirmwareRevision);
    ArgumentException.ThrowIfNullOrWhiteSpace(request.Observer);
    if (request.Observer.Trim().Length > 100) throw new ArgumentOutOfRangeException(nameof(request.Observer));
    bool needsTarget = request.Kind is TreadmillCommandKind.SetSpeed or TreadmillCommandKind.SetIncline;
    if (needsTarget != (request.RequestedValue is not null) ||
        request.RequestedValue is { } value && (!double.IsFinite(value) || value < 0))
    {
      throw new ArgumentException("Only speed and incline stages require one finite non-negative target value.");
    }
  }

  private static void ValidateIdentity(DeviceEnrollment enrollment, FtmsCommandCommissioningRequest request)
  {
    if (enrollment.TelemetryMode != TreadmillTelemetryMode.Ftms ||
        enrollment.Evidence < TreadmillCapabilityEvidence.PassivelyObserved ||
        !string.Equals(enrollment.ProtocolId, "horizon-omega-z", StringComparison.Ordinal) ||
        !string.Equals(enrollment.ModelNumber, request.ExpectedModelNumber, StringComparison.OrdinalIgnoreCase) ||
        !string.Equals(enrollment.FirmwareRevision, request.ExpectedFirmwareRevision, StringComparison.OrdinalIgnoreCase) ||
        enrollment.Capabilities is null)
    {
      throw new InvalidOperationException("The enrolled FTMS treadmill model, firmware, or evidence does not match this stage.");
    }
  }

  private static string Fingerprint(FtmsCommandCommissioningRequest request, string identityFingerprint)
  {
    string canonical = JsonSerializer.Serialize(new
    {
      request.Kind,
      request.ExpectedModelNumber,
      request.ExpectedFirmwareRevision,
      request.Observer,
      request.RequestedValue,
      IdentityFingerprint = identityFingerprint,
    });
    return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(canonical)));
  }

  private sealed class FixedCommissioningContext(TreadmillCommandIntent expected)
    : ITreadmillCommandContextValidator
  {
    public bool IsCurrent(TreadmillCommandIntent intent) => ReferenceEquals(intent, expected);
  }
}
