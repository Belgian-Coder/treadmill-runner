using System.Globalization;
using System.Text;
using Dynastream.Fit;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Protocols.Exports;

public static class SessionCsvExporter
{
  public static byte[] Export(StoredWorkoutSession session)
  {
    ArgumentNullException.ThrowIfNull(session);
    var output = new StringBuilder(16 * 1024);
    double? weight = SessionCalorieCalculator.ReadWeightKilograms(session.Definition.ControllerConfigurationJson);
    IReadOnlyList<double>? calculatedCalories =
      session.Definition.MetricAlgorithmVersion != SessionMetricAlgorithms.EstimatedCaloriesV2 &&
      weight is { } weightKilograms
        ? SessionCalorieCalculator.CalculateCumulative(session.Samples, weightKilograms)
        : null;
    output.AppendLine("captured_at_utc,elapsed_seconds,planned_speed_kph,requested_speed_kph,measured_speed_kph,planned_incline_percent,requested_incline_percent,measured_incline_percent,heart_rate_bpm,distance_km,estimated_kcal,telemetry_age_ms");
    for (var index = 0; index < session.Samples.Count; index++)
    {
      SessionSample sample = session.Samples[index];
      Append(output, sample.CapturedAt.ToString("O", CultureInfo.InvariantCulture));
      Append(output, sample.Elapsed.TotalSeconds);
      Append(output, sample.PlannedSpeedKph);
      Append(output, sample.RequestedSpeedKph);
      Append(output, sample.MeasuredSpeedKph);
      Append(output, sample.PlannedInclinePercent);
      Append(output, sample.RequestedInclinePercent);
      Append(output, sample.MeasuredInclinePercent);
      Append(output, sample.HeartRateBpm);
      Append(output, sample.DistanceKilometers);
      double exportedCalories = calculatedCalories?[index] ?? sample.EstimatedKilocalories;
      if (session.Definition.MetricAlgorithmVersion == SessionMetricAlgorithms.EstimatedCaloriesV2 &&
          index == session.Samples.Count - 1)
        exportedCalories = Math.Max(exportedCalories, session.EstimatedKilocalories);
      Append(output, exportedCalories);
      output.Append(sample.TelemetryAge.TotalMilliseconds.ToString("0.###", CultureInfo.InvariantCulture));
      output.AppendLine();
    }

    return new UTF8Encoding(encoderShouldEmitUTF8Identifier: false).GetBytes(output.ToString());
  }

  private static void Append(StringBuilder output, object? value)
  {
    if (value is not null)
      output.Append(Convert.ToString(value, CultureInfo.InvariantCulture));
    output.Append(',');
  }
}

public static class SessionFitActivityExporter
{
  private const byte MaximumValidFitHeartRate = byte.MaxValue - 1;

  public static byte[] Export(StoredWorkoutSession session)
  {
    ArgumentNullException.ThrowIfNull(session);
    if (session.StartedAt is null || session.EndedAt is null)
      throw new InvalidOperationException("Only a completed session with start and end timestamps can be exported as FIT Activity.");
    session = session with { Samples = SessionSampleTimeline.Normalize(session.Samples) };

    using var stream = new MemoryStream();
    var encoder = new Encode(ProtocolVersion.V20);
    encoder.Open(stream);
    DateTimeOffset effectiveEnd = GetEffectiveEnd(session);
    var start = new Dynastream.Fit.DateTime(session.StartedAt.Value.UtcDateTime);
    var end = new Dynastream.Fit.DateTime(effectiveEnd.UtcDateTime);
    uint serial = BitConverter.ToUInt32(session.Definition.SessionId.ToByteArray(), 0);
    SessionSampleStatistics statistics = SessionSampleStatisticsCalculator.Calculate(
      session.Samples,
      SessionCalorieCalculator.ReadWeightKilograms(session.Definition.ControllerConfigurationJson));
    SessionElevationStatistics elevation = SessionElevationCalculator.Calculate(session.Samples);
    SessionFitMetrics fitMetrics = SessionFitMetricsCalculator.Calculate(session, elevation);
    double? averageHeartRate = statistics.AverageHeartRateBpm ?? session.AverageHeartRateBpm;
    ushort? maximumHeartRate = statistics.MaximumHeartRateBpm ?? session.MaximumHeartRateBpm;
    float averageSpeed = session.Duration > TimeSpan.Zero
      ? (float)(session.DistanceKilometers * 1000 / session.Duration.TotalSeconds)
      : 0;
    float? maximumSpeed = statistics.MaximumSpeedKph is { } maximumSpeedKph
      ? (float)(maximumSpeedKph / 3.6)
      : null;
    double authoritativeCalories = session.Definition.MetricAlgorithmVersion == SessionMetricAlgorithms.EstimatedCaloriesV2
      ? Math.Max(session.EstimatedKilocalories, session.Samples.LastOrDefault()?.EstimatedKilocalories ?? 0)
      : statistics.EstimatedKilocalories ?? session.EstimatedKilocalories;
    ushort totalCalories = (ushort)Math.Clamp(
      Math.Round(authoritativeCalories),
      0,
      ushort.MaxValue);

    var fileId = new FileIdMesg();
    fileId.SetType(Dynastream.Fit.File.Activity);
    fileId.SetManufacturer(Manufacturer.Development);
    fileId.SetProduct(1);
    fileId.SetSerialNumber(serial);
    fileId.SetTimeCreated(start);
    fileId.SetProductName("TreadmillRunner");
    encoder.Write(fileId);

    var deviceInfo = new DeviceInfoMesg();
    deviceInfo.SetTimestamp(start);
    deviceInfo.SetDeviceIndex(0);
    deviceInfo.SetManufacturer(Manufacturer.Development);
    deviceInfo.SetProduct(1);
    deviceInfo.SetProductName("TreadmillRunner");
    encoder.Write(deviceInfo);

    IReadOnlyList<EventMesg> timerEvents = BuildTimerEvents(session, start, end);
    encoder.Write(timerEvents[0]);
    var nextTimerEvent = 1;
    for (var sampleIndex = 0; sampleIndex < session.Samples.Count; sampleIndex++)
    {
      SessionSample sample = session.Samples[sampleIndex];
      while (nextTimerEvent < timerEvents.Count - 1 &&
             timerEvents[nextTimerEvent].GetTimestamp()?.GetTimeStamp() <=
               new Dynastream.Fit.DateTime(sample.CapturedAt.UtcDateTime).GetTimeStamp())
      {
        encoder.Write(timerEvents[nextTimerEvent++]);
      }

      var record = new RecordMesg();
      record.SetTimestamp(new Dynastream.Fit.DateTime(sample.CapturedAt.UtcDateTime));
      float speed = (float)(sample.MeasuredSpeedKph / 3.6);
      record.SetSpeed(speed);
      record.SetEnhancedSpeed(speed);
      record.SetDistance((float)(sample.DistanceKilometers * 1000));
      if (sample.HeartRateBpm is { } heartRate) record.SetHeartRate((byte)Math.Min(heartRate, MaximumValidFitHeartRate));
      record.SetGrade((float)sample.MeasuredInclinePercent);
      float altitude = (float)elevation.Points[sampleIndex].ElevationMeters;
      record.SetAltitude(altitude);
      record.SetEnhancedAltitude(altitude);
      if (fitMetrics.VerticalSpeedBySequence.TryGetValue(sample.Sequence, out float verticalSpeed))
        record.SetVerticalSpeed(verticalSpeed);
      if (fitMetrics.HeartRateZoneBySequence.TryGetValue(sample.Sequence, out byte zone)) record.SetZone(zone);
      encoder.Write(record);
    }
    while (nextTimerEvent < timerEvents.Count - 1)
      encoder.Write(timerEvents[nextTimerEvent++]);
    encoder.Write(timerEvents[^1]);

    float timer = (float)session.Duration.TotalSeconds;
    float elapsed = (float)Math.Max(timer, (effectiveEnd - session.StartedAt.Value).TotalSeconds);
    float distance = (float)(session.DistanceKilometers * 1000);
    var lap = new LapMesg();
    lap.SetMessageIndex(0);
    lap.SetTimestamp(end);
    lap.SetStartTime(start);
    lap.SetTotalElapsedTime(elapsed);
    lap.SetTotalTimerTime(timer);
    lap.SetTotalDistance(distance);
    lap.SetSport(Sport.Running);
    lap.SetSubSport(SubSport.Treadmill);
    lap.SetEvent(Event.Lap);
    lap.SetEventType(EventType.Stop);
    lap.SetLapTrigger(LapTrigger.SessionEnd);
    lap.SetIntensity(Intensity.Active);
    lap.SetAvgSpeed(averageSpeed);
    lap.SetEnhancedAvgSpeed(averageSpeed);
    lap.SetTotalCalories(totalCalories);
    if (maximumSpeed is { } lapMaximumSpeed)
    {
      lap.SetMaxSpeed(lapMaximumSpeed);
      lap.SetEnhancedMaxSpeed(lapMaximumSpeed);
    }
    if (statistics.MovingTime is { } lapMovingTime)
    {
      lap.SetTotalMovingTime((float)lapMovingTime.TotalSeconds);
      lap.SetActiveTime((float)lapMovingTime.TotalSeconds);
    }
    if (statistics.AverageInclinePercent is { } lapAverageGrade) lap.SetAvgGrade((float)lapAverageGrade);
    if (statistics.AveragePositiveInclinePercent is { } lapAveragePositiveGrade) lap.SetAvgPosGrade((float)lapAveragePositiveGrade);
    if (statistics.AverageNegativeInclinePercent is { } lapAverageNegativeGrade) lap.SetAvgNegGrade((float)lapAverageNegativeGrade);
    if (statistics.MinimumInclinePercent is { } lapMinimumGrade && lapMinimumGrade < 0) lap.SetMaxNegGrade((float)lapMinimumGrade);
    if (statistics.MaximumInclinePercent is { } lapMaximumGrade && lapMaximumGrade > 0) lap.SetMaxPosGrade((float)lapMaximumGrade);
    SetElevationTotals(lap.SetTotalAscent, lap.SetTotalFractionalAscent, statistics.TotalAscentMeters);
    SetElevationTotals(lap.SetTotalDescent, lap.SetTotalFractionalDescent, statistics.TotalDescentMeters);
    SetVerticalSpeedStatistics(
      lap.SetAvgPosVerticalSpeed,
      lap.SetAvgNegVerticalSpeed,
      lap.SetMaxPosVerticalSpeed,
      lap.SetMaxNegVerticalSpeed,
      statistics);
    SetTimeInHeartRateZones(lap.SetTimeInHrZone, fitMetrics.TimeInHeartRateZoneSeconds);
    if (averageHeartRate is { } lapAverageHeartRate) lap.SetAvgHeartRate(ToFitHeartRate(lapAverageHeartRate));
    if (statistics.MinimumHeartRateBpm is { } lapMinimumHeartRate) lap.SetMinHeartRate(ToFitHeartRate(lapMinimumHeartRate));
    if (maximumHeartRate is { } lapMaximumHeartRate) lap.SetMaxHeartRate(ToFitHeartRate(lapMaximumHeartRate));
    encoder.Write(lap);

    var sessionMessage = new SessionMesg();
    sessionMessage.SetMessageIndex(0);
    sessionMessage.SetTimestamp(end);
    sessionMessage.SetStartTime(start);
    sessionMessage.SetTotalElapsedTime(elapsed);
    sessionMessage.SetTotalTimerTime(timer);
    sessionMessage.SetTotalDistance(distance);
    sessionMessage.SetSport(Sport.Running);
    sessionMessage.SetSubSport(SubSport.Treadmill);
    sessionMessage.SetSportProfileName("TreadmillRunner");
    sessionMessage.SetEvent(Event.Session);
    sessionMessage.SetEventType(EventType.Stop);
    sessionMessage.SetTrigger(SessionTrigger.ActivityEnd);
    sessionMessage.SetFirstLapIndex(0);
    sessionMessage.SetNumLaps(1);
    sessionMessage.SetAvgSpeed(averageSpeed);
    sessionMessage.SetEnhancedAvgSpeed(averageSpeed);
    sessionMessage.SetTotalCalories(totalCalories);
    if (maximumSpeed is { } sessionMaximumSpeed)
    {
      sessionMessage.SetMaxSpeed(sessionMaximumSpeed);
      sessionMessage.SetEnhancedMaxSpeed(sessionMaximumSpeed);
    }
    if (statistics.MovingTime is { } sessionMovingTime)
    {
      sessionMessage.SetTotalMovingTime((float)sessionMovingTime.TotalSeconds);
      sessionMessage.SetActiveTime((float)sessionMovingTime.TotalSeconds);
    }
    if (statistics.AverageInclinePercent is { } sessionAverageGrade) sessionMessage.SetAvgGrade((float)sessionAverageGrade);
    if (statistics.AveragePositiveInclinePercent is { } sessionAveragePositiveGrade) sessionMessage.SetAvgPosGrade((float)sessionAveragePositiveGrade);
    if (statistics.AverageNegativeInclinePercent is { } sessionAverageNegativeGrade) sessionMessage.SetAvgNegGrade((float)sessionAverageNegativeGrade);
    if (statistics.MinimumInclinePercent is { } sessionMinimumGrade && sessionMinimumGrade < 0) sessionMessage.SetMaxNegGrade((float)sessionMinimumGrade);
    if (statistics.MaximumInclinePercent is { } sessionMaximumGrade && sessionMaximumGrade > 0) sessionMessage.SetMaxPosGrade((float)sessionMaximumGrade);
    SetElevationTotals(sessionMessage.SetTotalAscent, sessionMessage.SetTotalFractionalAscent, statistics.TotalAscentMeters);
    SetElevationTotals(sessionMessage.SetTotalDescent, sessionMessage.SetTotalFractionalDescent, statistics.TotalDescentMeters);
    SetVerticalSpeedStatistics(
      sessionMessage.SetAvgPosVerticalSpeed,
      sessionMessage.SetAvgNegVerticalSpeed,
      sessionMessage.SetMaxPosVerticalSpeed,
      sessionMessage.SetMaxNegVerticalSpeed,
      statistics);
    SetTimeInHeartRateZones(sessionMessage.SetTimeInHrZone, fitMetrics.TimeInHeartRateZoneSeconds);
    if (averageHeartRate is { } sessionAverageHeartRate) sessionMessage.SetAvgHeartRate(ToFitHeartRate(sessionAverageHeartRate));
    if (statistics.MinimumHeartRateBpm is { } sessionMinimumHeartRate) sessionMessage.SetMinHeartRate(ToFitHeartRate(sessionMinimumHeartRate));
    if (maximumHeartRate is { } sessionMaximumHeartRate) sessionMessage.SetMaxHeartRate(ToFitHeartRate(sessionMaximumHeartRate));
    encoder.Write(sessionMessage);

    var activity = new ActivityMesg();
    activity.SetTimestamp(end);
    activity.SetTotalTimerTime(timer);
    activity.SetNumSessions(1);
    activity.SetType(Activity.Manual);
    activity.SetEvent(Event.Activity);
    activity.SetEventType(EventType.Stop);
    encoder.Write(activity);
    encoder.Close();
    return stream.ToArray();
  }

  internal static DateTimeOffset GetEffectiveEnd(StoredWorkoutSession session)
  {
    DateTimeOffset start = session.StartedAt
      ?? throw new InvalidOperationException("A FIT Activity requires a start timestamp.");
    DateTimeOffset persistedEnd = session.EndedAt
      ?? throw new InvalidOperationException("A FIT Activity requires an end timestamp.");
    if (session.State is not (SessionState.Interrupted or SessionState.Faulted))
      return persistedEnd;

    DateTimeOffset durationEnd = start + session.Duration;
    DateTimeOffset timelineEnd = session.Samples.Count > 0
      ? session.Samples[^1].CapturedAt
      : durationEnd;
    DateTimeOffset? lastTimerEvent = session.Events
      .Where(static item => item is SessionPausedEvent or SessionResumedEvent)
      .Where(item => item.OccurredAt >= start && item.OccurredAt <= persistedEnd)
      .Select(static item => (DateTimeOffset?)item.OccurredAt)
      .Max();
    if (lastTimerEvent is { } timerEventEnd && timerEventEnd > timelineEnd)
      timelineEnd = timerEventEnd;
    DateTimeOffset effectiveEnd = timelineEnd > durationEnd ? timelineEnd : durationEnd;
    if (effectiveEnd > persistedEnd) effectiveEnd = persistedEnd;
    return effectiveEnd < start ? start : effectiveEnd;
  }

  private static EventMesg TimerEvent(Dynastream.Fit.DateTime timestamp, EventType eventType)
  {
    var message = new EventMesg();
    message.SetTimestamp(timestamp);
    message.SetEvent(Event.Timer);
    message.SetEventType(eventType);
    return message;
  }

  private static IReadOnlyList<EventMesg> BuildTimerEvents(
    StoredWorkoutSession session,
    Dynastream.Fit.DateTime start,
    Dynastream.Fit.DateTime end)
  {
    var events = new List<EventMesg>
    {
      TimerEvent(start, EventType.Start),
    };

    foreach (SessionEvent sessionEvent in NormalizeTimerHistory(session))
    {
      if (sessionEvent is SessionPausedEvent)
      {
        events.Add(TimerEvent(new Dynastream.Fit.DateTime(sessionEvent.OccurredAt.UtcDateTime), EventType.Stop));
      }
      else
      {
        events.Add(TimerEvent(new Dynastream.Fit.DateTime(sessionEvent.OccurredAt.UtcDateTime), EventType.Start));
      }
    }

    events.Add(TimerEvent(end, EventType.StopAll));
    return events;
  }

  internal static IReadOnlyList<SessionEvent> NormalizeTimerHistory(StoredWorkoutSession session)
  {
    DateTimeOffset startedAt = session.StartedAt!.Value;
    DateTimeOffset endedAt = GetEffectiveEnd(session);
    var normalized = new List<SessionEvent>();
    var timerRunning = true;
    foreach (SessionEvent sessionEvent in session.Events
      .Where(static item => item is SessionPausedEvent or SessionResumedEvent)
      .OrderBy(static item => item.OccurredAt)
      .ThenBy(static item => item is SessionPausedEvent ? 0 : 1))
    {
      if (sessionEvent.OccurredAt < startedAt || sessionEvent.OccurredAt > endedAt)
        continue;

      if (sessionEvent is SessionPausedEvent)
      {
        if (!timerRunning) continue;
        timerRunning = false;
      }
      else
      {
        if (timerRunning) continue;
        timerRunning = true;
      }
      normalized.Add(sessionEvent);
    }
    return normalized;
  }

  private static byte ToFitHeartRate(double heartRate) =>
    (byte)Math.Clamp(Math.Round(heartRate, MidpointRounding.AwayFromZero), 0, MaximumValidFitHeartRate);

  private static void SetElevationTotals(Action<ushort?> setWhole, Action<float?> setFraction, double meters)
  {
    double bounded = Math.Clamp(meters, 0, ushort.MaxValue);
    double whole = Math.Floor(bounded);
    setWhole((ushort)whole);
    setFraction((float)(bounded - whole));
  }

  private static void SetVerticalSpeedStatistics(
    Action<float?> setAveragePositive,
    Action<float?> setAverageNegative,
    Action<float?> setMaximumPositive,
    Action<float?> setMaximumNegative,
    SessionSampleStatistics statistics)
  {
    if (statistics.AveragePositiveVerticalSpeedMetersPerSecond is { } averagePositive) setAveragePositive((float)averagePositive);
    if (statistics.AverageNegativeVerticalSpeedMetersPerSecond is { } averageNegative) setAverageNegative((float)averageNegative);
    if (statistics.MaximumPositiveVerticalSpeedMetersPerSecond is { } maximumPositive) setMaximumPositive((float)maximumPositive);
    if (statistics.MaximumNegativeVerticalSpeedMetersPerSecond is { } maximumNegative) setMaximumNegative((float)maximumNegative);
  }

  private static void SetTimeInHeartRateZones(
    Action<int, float?> setZoneTime,
    IReadOnlyList<float>? zoneSeconds)
  {
    if (zoneSeconds is null) return;
    for (var index = 0; index < zoneSeconds.Count; index++) setZoneTime(index, zoneSeconds[index]);
  }
}
