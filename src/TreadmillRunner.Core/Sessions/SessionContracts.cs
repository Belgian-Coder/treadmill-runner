namespace TreadmillRunner.Core.Sessions;

public static class SessionMetricAlgorithms
{
  public const string EstimatedCaloriesV1 = "estimated-calories/v1";
  public const string AdherenceV1 = "adherence/v1";

  public const double SpeedAdherenceToleranceKph = 0.3;
  public const double InclineAdherenceTolerancePercent = 0.5;
}

public sealed record SessionSample
{
  public SessionSample(
      Guid sessionId,
      long sequence,
      DateTimeOffset capturedAt,
      TimeSpan elapsed,
      double? plannedSpeedKph,
      double requestedSpeedKph,
      double measuredSpeedKph,
      double? plannedInclinePercent,
      double requestedInclinePercent,
      double measuredInclinePercent,
      ushort? heartRateBpm,
      double distanceKilometers,
      double estimatedKilocalories,
      TimeSpan telemetryAge,
      string metricAlgorithmVersion)
  {
    SessionContractValidation.RequireId(sessionId, nameof(sessionId));
    if (sequence < 0)
    {
      throw new ArgumentOutOfRangeException(nameof(sequence));
    }

    SessionContractValidation.RequireUtc(capturedAt, nameof(capturedAt));
    SessionContractValidation.RequireNonNegative(elapsed, nameof(elapsed));
    SessionContractValidation.RequireNullableNonNegativeFinite(plannedSpeedKph, nameof(plannedSpeedKph));
    SessionContractValidation.RequireNonNegativeFinite(requestedSpeedKph, nameof(requestedSpeedKph));
    SessionContractValidation.RequireNonNegativeFinite(measuredSpeedKph, nameof(measuredSpeedKph));
    SessionContractValidation.RequireNullableFinite(plannedInclinePercent, nameof(plannedInclinePercent));
    SessionContractValidation.RequireFinite(requestedInclinePercent, nameof(requestedInclinePercent));
    SessionContractValidation.RequireFinite(measuredInclinePercent, nameof(measuredInclinePercent));
    SessionContractValidation.RequireHeartRate(heartRateBpm, nameof(heartRateBpm));
    SessionContractValidation.RequireNonNegativeFinite(distanceKilometers, nameof(distanceKilometers));
    SessionContractValidation.RequireNonNegativeFinite(estimatedKilocalories, nameof(estimatedKilocalories));
    SessionContractValidation.RequireNonNegative(telemetryAge, nameof(telemetryAge));
    ArgumentException.ThrowIfNullOrWhiteSpace(metricAlgorithmVersion);

    SessionId = sessionId;
    Sequence = sequence;
    CapturedAt = capturedAt;
    Elapsed = elapsed;
    PlannedSpeedKph = plannedSpeedKph;
    RequestedSpeedKph = requestedSpeedKph;
    MeasuredSpeedKph = measuredSpeedKph;
    PlannedInclinePercent = plannedInclinePercent;
    RequestedInclinePercent = requestedInclinePercent;
    MeasuredInclinePercent = measuredInclinePercent;
    HeartRateBpm = heartRateBpm;
    DistanceKilometers = distanceKilometers;
    EstimatedKilocalories = estimatedKilocalories;
    TelemetryAge = telemetryAge;
    MetricAlgorithmVersion = metricAlgorithmVersion.Trim();
  }

  public Guid SessionId { get; }
  public long Sequence { get; }
  public DateTimeOffset CapturedAt { get; }
  public TimeSpan Elapsed { get; }
  public double? PlannedSpeedKph { get; }
  public double RequestedSpeedKph { get; }
  public double MeasuredSpeedKph { get; }
  public double? PlannedInclinePercent { get; }
  public double RequestedInclinePercent { get; }
  public double MeasuredInclinePercent { get; }
  public ushort? HeartRateBpm { get; }
  public double DistanceKilometers { get; }
  public double EstimatedKilocalories { get; }
  public TimeSpan TelemetryAge { get; }
  public string MetricAlgorithmVersion { get; }
}

public sealed record SessionDebrief
{
  public const int MaximumNoteLength = 1_000;

  public SessionDebrief(Guid sessionId, int? perceivedExertion, string? note, DateTimeOffset updatedAt)
  {
    SessionContractValidation.RequireId(sessionId, nameof(sessionId));
    if (perceivedExertion is < 1 or > 10)
    {
      throw new ArgumentOutOfRangeException(nameof(perceivedExertion), "Perceived exertion must be between 1 and 10.");
    }

    var normalizedNote = string.IsNullOrWhiteSpace(note) ? null : note.Trim();
    if (normalizedNote?.Length > MaximumNoteLength)
    {
      throw new ArgumentOutOfRangeException(nameof(note), $"A debrief note cannot exceed {MaximumNoteLength} characters.");
    }

    SessionContractValidation.RequireUtc(updatedAt, nameof(updatedAt));
    SessionId = sessionId;
    PerceivedExertion = perceivedExertion;
    Note = normalizedNote;
    UpdatedAt = updatedAt;
  }

  public Guid SessionId { get; }
  public int? PerceivedExertion { get; }
  public string? Note { get; }
  public DateTimeOffset UpdatedAt { get; }
}

public sealed record SessionSummary
{
  public SessionSummary(
      Guid sessionId,
      Guid userProfileId,
      string userProfileName,
      Guid workoutRevisionId,
      string workoutTitle,
      SessionState status,
      DateTimeOffset startedAt,
      DateTimeOffset endedAt,
      TimeSpan duration,
      double distanceKilometers,
      double estimatedKilocalories,
      double? averageHeartRateBpm,
      ushort? maximumHeartRateBpm,
      double averageSpeedKph,
      double averageInclinePercent)
  {
    SessionContractValidation.RequireId(sessionId, nameof(sessionId));
    SessionContractValidation.RequireId(userProfileId, nameof(userProfileId));
    SessionContractValidation.RequireId(workoutRevisionId, nameof(workoutRevisionId));
    ArgumentException.ThrowIfNullOrWhiteSpace(userProfileName);
    ArgumentException.ThrowIfNullOrWhiteSpace(workoutTitle);
    if (status is not (SessionState.Completed or SessionState.Stopped or SessionState.Interrupted or SessionState.Faulted))
    {
      throw new ArgumentException("A session summary requires a terminal status.", nameof(status));
    }

    SessionContractValidation.RequireUtc(startedAt, nameof(startedAt));
    SessionContractValidation.RequireUtc(endedAt, nameof(endedAt));
    if (endedAt < startedAt)
    {
      throw new ArgumentOutOfRangeException(nameof(endedAt), "Session end cannot precede its start.");
    }

    SessionContractValidation.RequireNonNegative(duration, nameof(duration));
    if (duration > endedAt - startedAt)
    {
      throw new ArgumentOutOfRangeException(nameof(duration), "Active duration cannot exceed wall-clock duration.");
    }

    SessionContractValidation.RequireNonNegativeFinite(distanceKilometers, nameof(distanceKilometers));
    SessionContractValidation.RequireNonNegativeFinite(estimatedKilocalories, nameof(estimatedKilocalories));
    SessionContractValidation.RequireNullableNonNegativeFinite(averageHeartRateBpm, nameof(averageHeartRateBpm));
    SessionContractValidation.RequireHeartRate(maximumHeartRateBpm, nameof(maximumHeartRateBpm));
    if (averageHeartRateBpm > maximumHeartRateBpm)
    {
      throw new ArgumentOutOfRangeException(nameof(averageHeartRateBpm), "Average heart rate cannot exceed maximum heart rate.");
    }

    SessionContractValidation.RequireNonNegativeFinite(averageSpeedKph, nameof(averageSpeedKph));
    SessionContractValidation.RequireFinite(averageInclinePercent, nameof(averageInclinePercent));

    SessionId = sessionId;
    UserProfileId = userProfileId;
    UserProfileName = userProfileName.Trim();
    WorkoutRevisionId = workoutRevisionId;
    WorkoutTitle = workoutTitle.Trim();
    Status = status;
    StartedAt = startedAt;
    EndedAt = endedAt;
    Duration = duration;
    DistanceKilometers = distanceKilometers;
    EstimatedKilocalories = estimatedKilocalories;
    AverageHeartRateBpm = averageHeartRateBpm;
    MaximumHeartRateBpm = maximumHeartRateBpm;
    AverageSpeedKph = averageSpeedKph;
    AverageInclinePercent = averageInclinePercent;
  }

  public Guid SessionId { get; }
  public Guid UserProfileId { get; }
  public string UserProfileName { get; }
  public Guid WorkoutRevisionId { get; }
  public string WorkoutTitle { get; }
  public SessionState Status { get; }
  public DateTimeOffset StartedAt { get; }
  public DateTimeOffset EndedAt { get; }
  public TimeSpan Duration { get; }
  public double DistanceKilometers { get; }
  public double EstimatedKilocalories { get; }
  public double? AverageHeartRateBpm { get; }
  public ushort? MaximumHeartRateBpm { get; }
  public double AverageSpeedKph { get; }
  public double AverageInclinePercent { get; }
}

internal static class SessionContractValidation
{
  public static void RequireId(Guid value, string parameterName)
  {
    if (value == Guid.Empty)
    {
      throw new ArgumentException("ID cannot be empty.", parameterName);
    }
  }

  public static void RequireUtc(DateTimeOffset value, string parameterName)
  {
    if (value.Offset != TimeSpan.Zero)
    {
      throw new ArgumentException("Timestamp must be UTC.", parameterName);
    }
  }

  public static void RequireNonNegative(TimeSpan value, string parameterName)
  {
    if (value < TimeSpan.Zero)
    {
      throw new ArgumentOutOfRangeException(parameterName);
    }
  }

  public static void RequireFinite(double value, string parameterName)
  {
    if (!double.IsFinite(value))
    {
      throw new ArgumentOutOfRangeException(parameterName);
    }
  }

  public static void RequireNonNegativeFinite(double value, string parameterName)
  {
    RequireFinite(value, parameterName);
    if (value < 0)
    {
      throw new ArgumentOutOfRangeException(parameterName);
    }
  }

  public static void RequireNullableFinite(double? value, string parameterName)
  {
    if (value is { } present)
    {
      RequireFinite(present, parameterName);
    }
  }

  public static void RequireNullableNonNegativeFinite(double? value, string parameterName)
  {
    if (value is { } present)
    {
      RequireNonNegativeFinite(present, parameterName);
    }
  }

  public static void RequireHeartRate(ushort? value, string parameterName)
  {
    if (value is 0 or > 250)
    {
      throw new ArgumentOutOfRangeException(parameterName);
    }
  }
}
