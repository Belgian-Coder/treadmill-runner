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
    IReadOnlyList<double>? calculatedCalories = weight is { } weightKilograms
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
      Append(output, calculatedCalories?[index] ?? sample.EstimatedKilocalories);
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
  public static byte[] Export(StoredWorkoutSession session)
  {
    ArgumentNullException.ThrowIfNull(session);
    if (session.StartedAt is null || session.EndedAt is null)
      throw new InvalidOperationException("Only a completed session with start and end timestamps can be exported as FIT Activity.");

    using var stream = new MemoryStream();
    var encoder = new Encode(ProtocolVersion.V20);
    encoder.Open(stream);
    var start = new Dynastream.Fit.DateTime(session.StartedAt.Value.UtcDateTime);
    var end = new Dynastream.Fit.DateTime(session.EndedAt.Value.UtcDateTime);
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
    ushort totalCalories = (ushort)Math.Clamp(
      Math.Round(statistics.EstimatedKilocalories ?? session.EstimatedKilocalories),
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

    encoder.Write(TimerEvent(start, EventType.Start));
    for (var sampleIndex = 0; sampleIndex < session.Samples.Count; sampleIndex++)
    {
      SessionSample sample = session.Samples[sampleIndex];
      var record = new RecordMesg();
      record.SetTimestamp(new Dynastream.Fit.DateTime(sample.CapturedAt.UtcDateTime));
      float speed = (float)(sample.MeasuredSpeedKph / 3.6);
      record.SetSpeed(speed);
      record.SetEnhancedSpeed(speed);
      record.SetDistance((float)(sample.DistanceKilometers * 1000));
      if (sample.HeartRateBpm is { } heartRate) record.SetHeartRate((byte)Math.Min(heartRate, byte.MaxValue));
      record.SetGrade((float)sample.MeasuredInclinePercent);
      float altitude = (float)elevation.Points[sampleIndex].ElevationMeters;
      record.SetAltitude(altitude);
      record.SetEnhancedAltitude(altitude);
      if (fitMetrics.VerticalSpeedBySequence.TryGetValue(sample.Sequence, out float verticalSpeed))
        record.SetVerticalSpeed(verticalSpeed);
      if (fitMetrics.HeartRateZoneBySequence.TryGetValue(sample.Sequence, out byte zone)) record.SetZone(zone);
      encoder.Write(record);
    }
    encoder.Write(TimerEvent(end, EventType.StopAll));

    float elapsed = (float)session.Duration.TotalSeconds;
    float distance = (float)(session.DistanceKilometers * 1000);
    var lap = new LapMesg();
    lap.SetMessageIndex(0);
    lap.SetTimestamp(end);
    lap.SetStartTime(start);
    lap.SetTotalElapsedTime(elapsed);
    lap.SetTotalTimerTime(elapsed);
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
    sessionMessage.SetTotalTimerTime(elapsed);
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
    activity.SetTotalTimerTime(elapsed);
    activity.SetNumSessions(1);
    activity.SetType(Activity.Manual);
    activity.SetEvent(Event.Activity);
    activity.SetEventType(EventType.Stop);
    encoder.Write(activity);
    encoder.Close();
    return stream.ToArray();
  }

  private static EventMesg TimerEvent(Dynastream.Fit.DateTime timestamp, EventType eventType)
  {
    var message = new EventMesg();
    message.SetTimestamp(timestamp);
    message.SetEvent(Event.Timer);
    message.SetEventType(eventType);
    return message;
  }

  private static byte ToFitHeartRate(double heartRate) =>
    (byte)Math.Clamp(Math.Round(heartRate, MidpointRounding.AwayFromZero), 0, byte.MaxValue);

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
