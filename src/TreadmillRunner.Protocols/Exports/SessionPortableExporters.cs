using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Xml;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Protocols.Exports;

public static class SessionNativeJsonExporter
{
  private static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web)
  {
    WriteIndented = true,
  };

  public static byte[] Export(StoredWorkoutSession session)
  {
    ArgumentNullException.ThrowIfNull(session);
    object payload = new
    {
      schema = "treadmillrunner.session/v1",
      unitSystem = "Metric",
      exportedAtUtc = DateTimeOffset.UtcNow,
      session = new
      {
        session.Definition.SessionId,
        session.Definition.UserProfileId,
        session.Definition.UserProfileName,
        session.Definition.WorkoutRevisionId,
        session.Definition.WorkoutTitle,
        session.Definition.ArmedAt,
        session.Definition.Selection,
        session.Definition.Origin,
        ControllerConfiguration = ParseJson(session.Definition.ControllerConfigurationJson),
        session.Definition.MetricAlgorithmVersion,
        session.State,
        session.StartedAt,
        session.EndedAt,
        DurationSeconds = session.Duration.TotalSeconds,
        session.DistanceKilometers,
        session.EstimatedKilocalories,
        session.AverageHeartRateBpm,
        session.MaximumHeartRateBpm,
        session.AverageSpeedKph,
        session.AverageInclinePercent,
        session.Debrief,
      },
      samples = session.Samples.Select(static sample => new
      {
        sample.Sequence,
        sample.CapturedAt,
        ElapsedSeconds = sample.Elapsed.TotalSeconds,
        sample.PlannedSpeedKph,
        sample.RequestedSpeedKph,
        sample.MeasuredSpeedKph,
        sample.PlannedInclinePercent,
        sample.RequestedInclinePercent,
        sample.MeasuredInclinePercent,
        sample.HeartRateBpm,
        sample.DistanceKilometers,
        sample.EstimatedKilocalories,
        TelemetryAgeMilliseconds = sample.TelemetryAge.TotalMilliseconds,
        sample.MetricAlgorithmVersion,
      }),
      events = session.Events.Select(MapEvent),
    };
    return JsonSerializer.SerializeToUtf8Bytes(payload, Options);
  }

  private static JsonElement ParseJson(string json)
  {
    try { return JsonSerializer.Deserialize<JsonElement>(json, Options); }
    catch (JsonException) { return JsonSerializer.SerializeToElement(json, Options); }
  }

  private static object MapEvent(SessionEvent value) => value switch
  {
    ManualSpeedOverrideEvent item => new { item.EventType, item.OccurredAt, item.ExpectedSpeedKph, item.ObservedSpeedKph },
    ManualInclineOverrideEvent item => new { item.EventType, item.OccurredAt, item.PreviousInclinePercent, item.RequestedInclinePercent },
    WorkoutStepTransitionEvent item => new { item.EventType, item.OccurredAt, item.CompletedStepIndex, item.CurrentStepIndex, item.Cue },
    WorkoutProgressResetEvent item => new { item.EventType, item.OccurredAt, item.PreviousStepIndex, PreviousWorkoutElapsedSeconds = item.PreviousWorkoutElapsed.TotalSeconds },
    SessionPausedEvent item => new { item.EventType, item.OccurredAt, item.Reason },
    SessionResumedEvent item => new { item.EventType, item.OccurredAt },
    DeviceDisconnectedEvent item => new { item.EventType, item.OccurredAt, item.DeviceRole, item.Reason },
    DeviceReconnectedEvent item => new { item.EventType, item.OccurredAt, item.DeviceRole },
    SessionWarningEvent item => new { item.EventType, item.OccurredAt, item.Code, item.Message },
    ControlLeaseEvent item => new { item.EventType, item.OccurredAt, item.Kind, item.LeaseId, item.HolderId },
    SessionCompletedEvent item => new { item.EventType, item.OccurredAt },
    SessionStoppedEvent item => new { item.EventType, item.OccurredAt },
    SessionInterruptedEvent item => new { item.EventType, item.OccurredAt, item.Reason },
    SessionFaultedEvent item => new { item.EventType, item.OccurredAt, item.Code, item.Message },
    _ => new { value.EventType, value.OccurredAt },
  };
}

public static class SessionTcxActivityExporter
{
  private const string TrainingCenterNamespace = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2";
  private const string ActivityExtensionNamespace = "http://www.garmin.com/xmlschemas/ActivityExtension/v2";

  public static byte[] Export(StoredWorkoutSession session)
  {
    ArgumentNullException.ThrowIfNull(session);
    if (session.StartedAt is null || session.EndedAt is null)
      throw new InvalidOperationException("Only a terminal session with start and end timestamps can be exported as TCX Activity.");

    using var output = new MemoryStream();
    var settings = new XmlWriterSettings
    {
      Encoding = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false),
      Indent = true,
      OmitXmlDeclaration = false,
    };
    using (XmlWriter writer = XmlWriter.Create(output, settings))
    {
      writer.WriteStartDocument();
      writer.WriteStartElement("TrainingCenterDatabase", TrainingCenterNamespace);
      writer.WriteAttributeString("xmlns", "xsi", null, "http://www.w3.org/2001/XMLSchema-instance");
      writer.WriteAttributeString("xmlns", "ns3", null, ActivityExtensionNamespace);
      writer.WriteStartElement("Activities", TrainingCenterNamespace);
      writer.WriteStartElement("Activity", TrainingCenterNamespace);
      writer.WriteAttributeString("Sport", "Running");
      Element(writer, "Id", session.StartedAt.Value.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture));
      writer.WriteStartElement("Lap", TrainingCenterNamespace);
      writer.WriteAttributeString("StartTime", session.StartedAt.Value.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture));
      Element(writer, "TotalTimeSeconds", Number(session.Duration.TotalSeconds));
      Element(writer, "DistanceMeters", Number(session.DistanceKilometers * 1000));
      Element(writer, "Calories", Math.Clamp((int)Math.Round(session.EstimatedKilocalories), 0, ushort.MaxValue).ToString(CultureInfo.InvariantCulture));
      if (session.AverageHeartRateBpm is { } average) HeartRateElement(writer, "AverageHeartRateBpm", average);
      if (session.MaximumHeartRateBpm is { } maximum) HeartRateElement(writer, "MaximumHeartRateBpm", maximum);
      Element(writer, "Intensity", "Active");
      Element(writer, "TriggerMethod", "Manual");
      writer.WriteStartElement("Track", TrainingCenterNamespace);
      foreach (SessionSample sample in session.Samples)
      {
        writer.WriteStartElement("Trackpoint", TrainingCenterNamespace);
        Element(writer, "Time", sample.CapturedAt.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture));
        Element(writer, "DistanceMeters", Number(sample.DistanceKilometers * 1000));
        if (sample.HeartRateBpm is { } heartRate) HeartRateElement(writer, "HeartRateBpm", heartRate);
        writer.WriteStartElement("Extensions", TrainingCenterNamespace);
        writer.WriteStartElement("TPX", ActivityExtensionNamespace);
        Element(writer, "Speed", Number(sample.MeasuredSpeedKph / 3.6), ActivityExtensionNamespace);
        writer.WriteEndElement();
        writer.WriteEndElement();
        writer.WriteEndElement();
      }
      writer.WriteEndElement();
      writer.WriteEndElement();
      writer.WriteEndElement();
      writer.WriteEndElement();
      writer.WriteEndElement();
      writer.WriteEndDocument();
    }
    return output.ToArray();
  }

  private static void HeartRateElement(XmlWriter writer, string name, double value)
  {
    writer.WriteStartElement(name, TrainingCenterNamespace);
    Element(writer, "Value", Math.Clamp((int)Math.Round(value), 0, byte.MaxValue).ToString(CultureInfo.InvariantCulture));
    writer.WriteEndElement();
  }

  private static void Element(XmlWriter writer, string name, string value, string? xmlNamespace = TrainingCenterNamespace)
  {
    writer.WriteStartElement(name, xmlNamespace);
    writer.WriteString(value);
    writer.WriteEndElement();
  }

  private static string Number(double value) => value.ToString("0.###", CultureInfo.InvariantCulture);
}
