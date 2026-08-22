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
    Dynastream.Fit.DateTime? firstTimestamp = records.Select(record => record.GetTimestamp()).Where(value => value is not null).MinBy(value => value!.GetTimeStamp());
    if (firstTimestamp is null)
      throw new InvalidDataException("The watch FIT contains no timestamped record messages.");

    SessionSample[] samples = localSession.Samples.OrderBy(sample => sample.Elapsed).ToArray();
    SessionElevationStatistics elevation = SessionElevationCalculator.Calculate(samples);
    var replacementRecords = new Queue<RecordMesg>(records.Select(record => OverlayRecord(record, firstTimestamp, samples, elevation.Points)));
    SessionSampleStatistics statistics = SessionSampleStatisticsCalculator.Calculate(
      samples,
      SessionCalorieCalculator.ReadWeightKilograms(localSession.Definition.ControllerConfigurationJson));
    double? averageHeartRate = statistics.AverageHeartRateBpm ?? localSession.AverageHeartRateBpm;
    ushort? maximumHeartRate = statistics.MaximumHeartRateBpm ?? localSession.MaximumHeartRateBpm;

    for (var index = 0; index < messages.Count; index++)
    {
      Mesg message = messages[index];
      if (message.Num == MesgNum.Record)
        messages[index] = replacementRecords.Dequeue();
      else if (message.Num == MesgNum.Lap)
        messages[index] = OverlaySummary(new LapMesg(message), localSession, statistics, averageHeartRate, maximumHeartRate);
      else if (message.Num == MesgNum.Session)
        messages[index] = OverlaySummary(new SessionMesg(message), localSession, statistics, averageHeartRate, maximumHeartRate);
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
    Dynastream.Fit.DateTime firstTimestamp,
    IReadOnlyList<SessionSample> samples,
    IReadOnlyList<SessionElevationPoint> elevationPoints)
  {
    Dynastream.Fit.DateTime? timestamp = record.GetTimestamp();
    if (timestamp is null) return record;
    double elapsed = timestamp.GetTimeStamp() - firstTimestamp.GetTimeStamp();
    SessionSample? sample = samples.MinBy(item => Math.Abs(item.Elapsed.TotalSeconds - elapsed));
    if (sample is null || Math.Abs(sample.Elapsed.TotalSeconds - elapsed) > 5) return record;
    float speed = (float)(sample.MeasuredSpeedKph / 3.6);
    record.SetSpeed(speed);
    record.SetEnhancedSpeed(speed);
    record.SetDistance((float)(sample.DistanceKilometers * 1000));
    record.SetGrade((float)sample.MeasuredInclinePercent);
    SessionElevationPoint elevation = elevationPoints.Single(point => point.Sequence == sample.Sequence);
    record.SetAltitude((float)elevation.ElevationMeters);
    record.SetEnhancedAltitude((float)elevation.ElevationMeters);
    if (sample.HeartRateBpm is { } heartRate) record.SetHeartRate((byte)Math.Min(heartRate, byte.MaxValue));
    return record;
  }

  private static LapMesg OverlaySummary(LapMesg message, StoredWorkoutSession session, SessionSampleStatistics statistics, double? averageHeartRate, ushort? maximumHeartRate)
  {
    ApplySummary(
      distance => message.SetTotalDistance(distance), calories => message.SetTotalCalories(calories),
      averageSpeed => { message.SetAvgSpeed(averageSpeed); message.SetEnhancedAvgSpeed(averageSpeed); },
      maximumSpeed => { message.SetMaxSpeed(maximumSpeed); message.SetEnhancedMaxSpeed(maximumSpeed); },
      averageGrade => message.SetAvgGrade(averageGrade), minimumHeartRate => message.SetMinHeartRate(minimumHeartRate),
      ascent => message.SetTotalAscent(ascent), descent => message.SetTotalDescent(descent),
      averageHr => message.SetAvgHeartRate(averageHr), maximumHr => message.SetMaxHeartRate(maximumHr),
      session, statistics, averageHeartRate, maximumHeartRate);
    return message;
  }

  private static SessionMesg OverlaySummary(SessionMesg message, StoredWorkoutSession session, SessionSampleStatistics statistics, double? averageHeartRate, ushort? maximumHeartRate)
  {
    ApplySummary(
      distance => message.SetTotalDistance(distance), calories => message.SetTotalCalories(calories),
      averageSpeed => { message.SetAvgSpeed(averageSpeed); message.SetEnhancedAvgSpeed(averageSpeed); },
      maximumSpeed => { message.SetMaxSpeed(maximumSpeed); message.SetEnhancedMaxSpeed(maximumSpeed); },
      averageGrade => message.SetAvgGrade(averageGrade), minimumHeartRate => message.SetMinHeartRate(minimumHeartRate),
      ascent => message.SetTotalAscent(ascent), descent => message.SetTotalDescent(descent),
      averageHr => message.SetAvgHeartRate(averageHr), maximumHr => message.SetMaxHeartRate(maximumHr),
      session, statistics, averageHeartRate, maximumHeartRate);
    return message;
  }

  private static void ApplySummary(
    Action<float> setDistance, Action<ushort> setCalories, Action<float> setAverageSpeed, Action<float> setMaximumSpeed,
    Action<float> setAverageGrade, Action<byte> setMinimumHeartRate,
    Action<ushort> setTotalAscent, Action<ushort> setTotalDescent,
    Action<byte> setAverageHeartRate, Action<byte> setMaximumHeartRate,
    StoredWorkoutSession session, SessionSampleStatistics statistics, double? averageHeartRate, ushort? maximumHeartRate)
  {
    setDistance((float)(session.DistanceKilometers * 1000));
    setCalories((ushort)Math.Clamp(Math.Round(statistics.EstimatedKilocalories ?? session.EstimatedKilocalories), 0, ushort.MaxValue));
    setAverageSpeed((float)(session.AverageSpeedKph / 3.6));
    if (statistics.MaximumSpeedKph is { } maximumSpeed) setMaximumSpeed((float)(maximumSpeed / 3.6));
    if (statistics.AverageInclinePercent is { } averageGrade) setAverageGrade((float)averageGrade);
    setTotalAscent(ToFitElevation(statistics.TotalAscentMeters));
    setTotalDescent(ToFitElevation(statistics.TotalDescentMeters));
    if (statistics.MinimumHeartRateBpm is { } minimumHr) setMinimumHeartRate(ToFitHeartRate(minimumHr));
    if (averageHeartRate is { } averageHr) setAverageHeartRate(ToFitHeartRate(averageHr));
    if (maximumHeartRate is { } maximumHr) setMaximumHeartRate(ToFitHeartRate(maximumHr));
  }

  private static byte ToFitHeartRate(double value) => (byte)Math.Clamp(Math.Round(value, MidpointRounding.AwayFromZero), 0, byte.MaxValue);
  private static ushort ToFitElevation(double value) => (ushort)Math.Clamp(Math.Round(value, MidpointRounding.AwayFromZero), 0, ushort.MaxValue);
}
