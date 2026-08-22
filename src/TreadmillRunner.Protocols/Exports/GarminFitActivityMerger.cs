using Dynastream.Fit;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Protocols.Exports;

public static class GarminFitActivityMerger
{
  public static byte[] Merge(byte[] watchFit, StoredWorkoutSession localSession)
  {
    ArgumentNullException.ThrowIfNull(watchFit);
    ArgumentNullException.ThrowIfNull(localSession);
    if (watchFit.Length is 0 or > 16 * 1024 * 1024)
      throw new ArgumentOutOfRangeException(nameof(watchFit), "The watch FIT must be non-empty and at most 16 MiB.");
    if (localSession.StartedAt is null || localSession.EndedAt is null || localSession.Samples.Count == 0)
      throw new InvalidOperationException("A completed local session with samples is required for FIT merge.");

    List<Mesg> messages = DecodeAndClone(watchFit);
    List<RecordMesg> records = messages.Where(message => message.Num == MesgNum.Record).Select(message => new RecordMesg(message)).ToList();
    RecordMesg? firstRecord = records
      .Where(record => record.GetTimestamp() is not null)
      .MinBy(record => record.GetTimestamp()!.GetTimeStamp());
    if (firstRecord?.GetTimestamp() is null)
      throw new InvalidDataException("The watch FIT contains no timestamped record messages.");

    SessionSample[] samples = localSession.Samples.OrderBy(sample => sample.Elapsed).ToArray();
    SessionElevationStatistics elevation = SessionElevationCalculator.Calculate(samples);
    StoredWorkoutSession orderedSession = localSession with { Samples = samples };
    SessionFitMetrics fitMetrics = SessionFitMetricsCalculator.Calculate(orderedSession, elevation);
    float altitudeBaseline = firstRecord.GetEnhancedAltitude() ?? firstRecord.GetAltitude() ?? 0;
    var replacementRecords = new Queue<RecordMesg>(records.Select(record => OverlayRecord(
      record,
      samples,
      elevation.Points,
      fitMetrics,
      altitudeBaseline)));
    SessionSampleStatistics statistics = SessionSampleStatisticsCalculator.Calculate(
      samples,
      SessionCalorieCalculator.ReadWeightKilograms(localSession.Definition.ControllerConfigurationJson));
    double? averageHeartRate = statistics.AverageHeartRateBpm ?? localSession.AverageHeartRateBpm;
    ushort? maximumHeartRate = statistics.MaximumHeartRateBpm ?? localSession.MaximumHeartRateBpm;

    int lapCount = messages.Count(message => message.Num == MesgNum.Lap);
    int sessionCount = messages.Count(message => message.Num == MesgNum.Session);
    for (var index = 0; index < messages.Count; index++)
    {
      Mesg message = messages[index];
      if (message.Num == MesgNum.FileId)
      {
        var fileId = new FileIdMesg(message);
        uint watchSerial = fileId.GetSerialNumber() ?? 0u;
        uint replacementSerial = watchSerial ^ BitConverter.ToUInt32(localSession.Definition.SessionId.ToByteArray(), 0) ^ 0x4D455247u;
        if (replacementSerial == watchSerial) replacementSerial ^= 1u;
        fileId.SetSerialNumber(replacementSerial);
        messages[index] = fileId;
      }
      else if (message.Num == MesgNum.Record)
        messages[index] = replacementRecords.Dequeue();
      else if (message.Num == MesgNum.Lap && lapCount == 1)
        messages[index] = OverlaySummary(new LapMesg(message), localSession, statistics, fitMetrics, averageHeartRate, maximumHeartRate);
      else if (message.Num == MesgNum.Session && sessionCount == 1)
        messages[index] = OverlaySummary(new SessionMesg(message), localSession, statistics, fitMetrics, averageHeartRate, maximumHeartRate);
    }

    using var output = new MemoryStream();
    var encoder = new Encode(ProtocolVersion.V20);
    encoder.Open(output);
    foreach (Mesg message in messages) encoder.Write(message);
    encoder.Close();
    byte[] merged = output.ToArray();
    using var validation = new MemoryStream(merged, writable: false);
    var decoder = new Decode();
    if (!decoder.IsFIT(validation)) throw new InvalidDataException("The merged FIT header is invalid.");
    validation.Position = 0;
    if (!decoder.CheckIntegrity(validation)) throw new InvalidDataException("The merged FIT CRC is invalid.");
    return merged;
  }

  private static List<Mesg> DecodeAndClone(byte[] source)
  {
    using var input = new MemoryStream(source, writable: false);
    var decoder = new Decode();
    if (!decoder.IsFIT(input)) throw new InvalidDataException("The watch activity is not a FIT file.");
    input.Position = 0;
    if (!decoder.CheckIntegrity(input)) throw new InvalidDataException("The watch FIT CRC is invalid.");
    input.Position = 0;
    var messages = new List<Mesg>();
    decoder.MesgEvent += (_, args) => messages.Add(new Mesg(args.mesg));
    if (!decoder.Read(input) || messages.Count == 0) throw new InvalidDataException("The watch FIT could not be decoded.");
    return messages;
  }

  private static RecordMesg OverlayRecord(
    RecordMesg record,
    IReadOnlyList<SessionSample> samples,
    IReadOnlyList<SessionElevationPoint> elevationPoints,
    SessionFitMetrics fitMetrics,
    float altitudeBaseline)
  {
    Dynastream.Fit.DateTime? timestamp = record.GetTimestamp();
    if (timestamp is null) return record;
    long recordTimestamp = timestamp.GetTimeStamp();
    SessionSample? sample = samples.MinBy(item => Math.Abs(
      new Dynastream.Fit.DateTime(item.CapturedAt.UtcDateTime).GetTimeStamp() - recordTimestamp));
    if (sample is null || Math.Abs(
          new Dynastream.Fit.DateTime(sample.CapturedAt.UtcDateTime).GetTimeStamp() - recordTimestamp) > 5) return record;
    float speed = (float)(sample.MeasuredSpeedKph / 3.6);
    Field? compressedSpeedDistance = record.GetField("compressed_speed_distance");
    if (compressedSpeedDistance is not null) record.RemoveField(compressedSpeedDistance);
    record.SetSpeed(speed);
    record.SetEnhancedSpeed(speed);
    record.SetDistance((float)(sample.DistanceKilometers * 1000));
    record.SetGrade((float)sample.MeasuredInclinePercent);
    SessionElevationPoint elevation = elevationPoints.Single(point => point.Sequence == sample.Sequence);
    record.SetAltitude(altitudeBaseline + (float)elevation.ElevationMeters);
    record.SetEnhancedAltitude(altitudeBaseline + (float)elevation.ElevationMeters);
    if (fitMetrics.VerticalSpeedBySequence.TryGetValue(sample.Sequence, out float verticalSpeed))
      record.SetVerticalSpeed(verticalSpeed);
    if (fitMetrics.HeartRateZoneBySequence.TryGetValue(sample.Sequence, out byte zone)) record.SetZone(zone);
    if (sample.HeartRateBpm is { } heartRate) record.SetHeartRate((byte)Math.Min(heartRate, byte.MaxValue));
    return record;
  }

  private static LapMesg OverlaySummary(LapMesg message, StoredWorkoutSession session, SessionSampleStatistics statistics, SessionFitMetrics fitMetrics, double? averageHeartRate, ushort? maximumHeartRate)
  {
    ApplySummary(
      distance => message.SetTotalDistance(distance), calories => message.SetTotalCalories(calories),
      averageSpeed => { message.SetAvgSpeed(averageSpeed); message.SetEnhancedAvgSpeed(averageSpeed); },
      maximumSpeed => { message.SetMaxSpeed(maximumSpeed); message.SetEnhancedMaxSpeed(maximumSpeed); },
      averageGrade => message.SetAvgGrade(averageGrade), minimumHeartRate => message.SetMinHeartRate(minimumHeartRate),
      averageHr => message.SetAvgHeartRate(averageHr), maximumHr => message.SetMaxHeartRate(maximumHr),
      session, statistics, averageHeartRate, maximumHeartRate);
    ApplyExtendedSummary(
      message.SetTotalMovingTime,
      message.SetActiveTime,
      message.SetAvgPosGrade,
      message.SetAvgNegGrade,
      message.SetMaxPosGrade,
      message.SetMaxNegGrade,
      message.SetAvgPosVerticalSpeed,
      message.SetAvgNegVerticalSpeed,
      message.SetMaxPosVerticalSpeed,
      message.SetMaxNegVerticalSpeed,
      message.SetTotalAscent,
      message.SetTotalFractionalAscent,
      message.SetTotalDescent,
      message.SetTotalFractionalDescent,
      message.SetTimeInHrZone,
      statistics,
      fitMetrics);
    return message;
  }

  private static SessionMesg OverlaySummary(SessionMesg message, StoredWorkoutSession session, SessionSampleStatistics statistics, SessionFitMetrics fitMetrics, double? averageHeartRate, ushort? maximumHeartRate)
  {
    ApplySummary(
      distance => message.SetTotalDistance(distance), calories => message.SetTotalCalories(calories),
      averageSpeed => { message.SetAvgSpeed(averageSpeed); message.SetEnhancedAvgSpeed(averageSpeed); },
      maximumSpeed => { message.SetMaxSpeed(maximumSpeed); message.SetEnhancedMaxSpeed(maximumSpeed); },
      averageGrade => message.SetAvgGrade(averageGrade), minimumHeartRate => message.SetMinHeartRate(minimumHeartRate),
      averageHr => message.SetAvgHeartRate(averageHr), maximumHr => message.SetMaxHeartRate(maximumHr),
      session, statistics, averageHeartRate, maximumHeartRate);
    ApplyExtendedSummary(
      message.SetTotalMovingTime,
      message.SetActiveTime,
      message.SetAvgPosGrade,
      message.SetAvgNegGrade,
      message.SetMaxPosGrade,
      message.SetMaxNegGrade,
      message.SetAvgPosVerticalSpeed,
      message.SetAvgNegVerticalSpeed,
      message.SetMaxPosVerticalSpeed,
      message.SetMaxNegVerticalSpeed,
      message.SetTotalAscent,
      message.SetTotalFractionalAscent,
      message.SetTotalDescent,
      message.SetTotalFractionalDescent,
      message.SetTimeInHrZone,
      statistics,
      fitMetrics);
    return message;
  }

  private static void ApplySummary(
    Action<float> setDistance, Action<ushort> setCalories, Action<float> setAverageSpeed, Action<float> setMaximumSpeed,
    Action<float> setAverageGrade, Action<byte> setMinimumHeartRate,
    Action<byte> setAverageHeartRate, Action<byte> setMaximumHeartRate,
    StoredWorkoutSession session, SessionSampleStatistics statistics, double? averageHeartRate, ushort? maximumHeartRate)
  {
    setDistance((float)(session.DistanceKilometers * 1000));
    setCalories((ushort)Math.Clamp(Math.Round(statistics.EstimatedKilocalories ?? session.EstimatedKilocalories), 0, ushort.MaxValue));
    setAverageSpeed(session.Duration > TimeSpan.Zero
      ? (float)(session.DistanceKilometers * 1000 / session.Duration.TotalSeconds)
      : 0);
    if (statistics.MaximumSpeedKph is { } maximumSpeed) setMaximumSpeed((float)(maximumSpeed / 3.6));
    if (statistics.AverageInclinePercent is { } averageGrade) setAverageGrade((float)averageGrade);
    if (statistics.MinimumHeartRateBpm is { } minimumHr) setMinimumHeartRate(ToFitHeartRate(minimumHr));
    if (averageHeartRate is { } averageHr) setAverageHeartRate(ToFitHeartRate(averageHr));
    if (maximumHeartRate is { } maximumHr) setMaximumHeartRate(ToFitHeartRate(maximumHr));
  }

  private static void ApplyExtendedSummary(
    Action<float?> setMovingTime,
    Action<float?> setActiveTime,
    Action<float?> setAveragePositiveGrade,
    Action<float?> setAverageNegativeGrade,
    Action<float?> setMaximumPositiveGrade,
    Action<float?> setMaximumNegativeGrade,
    Action<float?> setAveragePositiveVerticalSpeed,
    Action<float?> setAverageNegativeVerticalSpeed,
    Action<float?> setMaximumPositiveVerticalSpeed,
    Action<float?> setMaximumNegativeVerticalSpeed,
    Action<ushort?> setTotalAscent,
    Action<float?> setFractionalAscent,
    Action<ushort?> setTotalDescent,
    Action<float?> setFractionalDescent,
    Action<int, float?> setTimeInHeartRateZone,
    SessionSampleStatistics statistics,
    SessionFitMetrics fitMetrics)
  {
    if (statistics.MovingTime is { } movingTime)
    {
      setMovingTime((float)movingTime.TotalSeconds);
      setActiveTime((float)movingTime.TotalSeconds);
    }
    if (statistics.AveragePositiveInclinePercent is { } averagePositiveGrade) setAveragePositiveGrade((float)averagePositiveGrade);
    if (statistics.AverageNegativeInclinePercent is { } averageNegativeGrade) setAverageNegativeGrade((float)averageNegativeGrade);
    if (statistics.MaximumInclinePercent is { } maximumPositiveGrade && maximumPositiveGrade > 0) setMaximumPositiveGrade((float)maximumPositiveGrade);
    if (statistics.MinimumInclinePercent is { } maximumNegativeGrade && maximumNegativeGrade < 0) setMaximumNegativeGrade((float)maximumNegativeGrade);
    if (statistics.AveragePositiveVerticalSpeedMetersPerSecond is { } averagePositiveVerticalSpeed) setAveragePositiveVerticalSpeed((float)averagePositiveVerticalSpeed);
    if (statistics.AverageNegativeVerticalSpeedMetersPerSecond is { } averageNegativeVerticalSpeed) setAverageNegativeVerticalSpeed((float)averageNegativeVerticalSpeed);
    if (statistics.MaximumPositiveVerticalSpeedMetersPerSecond is { } maximumPositiveVerticalSpeed) setMaximumPositiveVerticalSpeed((float)maximumPositiveVerticalSpeed);
    if (statistics.MaximumNegativeVerticalSpeedMetersPerSecond is { } maximumNegativeVerticalSpeed) setMaximumNegativeVerticalSpeed((float)maximumNegativeVerticalSpeed);
    SetElevationTotals(setTotalAscent, setFractionalAscent, statistics.TotalAscentMeters);
    SetElevationTotals(setTotalDescent, setFractionalDescent, statistics.TotalDescentMeters);
    if (fitMetrics.TimeInHeartRateZoneSeconds is { } zoneSeconds)
      for (var index = 0; index < zoneSeconds.Count; index++) setTimeInHeartRateZone(index, zoneSeconds[index]);
  }

  private static void SetElevationTotals(Action<ushort?> setWhole, Action<float?> setFraction, double meters)
  {
    double bounded = Math.Clamp(meters, 0, ushort.MaxValue);
    double whole = Math.Floor(bounded);
    setWhole((ushort)whole);
    setFraction((float)(bounded - whole));
  }

  private static byte ToFitHeartRate(double value) => (byte)Math.Clamp(Math.Round(value, MidpointRounding.AwayFromZero), 0, byte.MaxValue);
}
