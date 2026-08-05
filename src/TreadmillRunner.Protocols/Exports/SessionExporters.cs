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
    output.AppendLine("captured_at_utc,elapsed_seconds,planned_speed_kph,requested_speed_kph,measured_speed_kph,planned_incline_percent,requested_incline_percent,measured_incline_percent,heart_rate_bpm,distance_km,estimated_kcal,telemetry_age_ms");
    foreach (SessionSample sample in session.Samples)
    {
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
      Append(output, sample.EstimatedKilocalories);
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

    var fileId = new FileIdMesg();
    fileId.SetType(Dynastream.Fit.File.Activity);
    fileId.SetManufacturer(Manufacturer.Development);
    fileId.SetProduct(1);
    fileId.SetSerialNumber(serial);
    fileId.SetTimeCreated(start);
    encoder.Write(fileId);

    encoder.Write(TimerEvent(start, EventType.Start));
    foreach (SessionSample sample in session.Samples)
    {
      var record = new RecordMesg();
      record.SetTimestamp(new Dynastream.Fit.DateTime(sample.CapturedAt.UtcDateTime));
      record.SetSpeed((float)(sample.MeasuredSpeedKph / 3.6));
      record.SetDistance((float)(sample.DistanceKilometers * 1000));
      if (sample.HeartRateBpm is { } heartRate) record.SetHeartRate((byte)Math.Min(heartRate, byte.MaxValue));
      record.SetGrade((float)sample.MeasuredInclinePercent);
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
    lap.SetAvgSpeed((float)(session.AverageSpeedKph / 3.6));
    if (session.AverageHeartRateBpm is { } averageHeartRate) lap.SetAvgHeartRate((byte)Math.Min(averageHeartRate, byte.MaxValue));
    if (session.MaximumHeartRateBpm is { } maximumHeartRate) lap.SetMaxHeartRate((byte)Math.Min(maximumHeartRate, byte.MaxValue));
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
    sessionMessage.SetFirstLapIndex(0);
    sessionMessage.SetNumLaps(1);
    sessionMessage.SetAvgSpeed((float)(session.AverageSpeedKph / 3.6));
    sessionMessage.SetTotalCalories((ushort)Math.Clamp(Math.Round(session.EstimatedKilocalories), 0, ushort.MaxValue));
    if (session.AverageHeartRateBpm is { } sessionAverageHeartRate) sessionMessage.SetAvgHeartRate((byte)Math.Min(sessionAverageHeartRate, byte.MaxValue));
    if (session.MaximumHeartRateBpm is { } sessionMaximumHeartRate) sessionMessage.SetMaxHeartRate((byte)Math.Min(sessionMaximumHeartRate, byte.MaxValue));
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
}
