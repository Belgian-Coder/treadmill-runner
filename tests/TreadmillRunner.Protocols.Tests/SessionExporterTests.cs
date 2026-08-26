using System.Text;
using System.Text.Json;
using System.Xml.Linq;
using Dynastream.Fit;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Protocols.Exports;

namespace TreadmillRunner.Protocols.Tests;

public sealed class SessionExporterTests
{
  [Fact]
  public void Csv_export_is_invariant_bounded_and_contains_reproducible_samples()
  {
    byte[] csv = SessionCsvExporter.Export(Session());
    string text = Encoding.UTF8.GetString(csv);

    Assert.DoesNotContain('\uFEFF', text);
    Assert.Contains("captured_at_utc,elapsed_seconds", text, StringComparison.Ordinal);
    Assert.Contains(",8,8,8,", text, StringComparison.Ordinal);
    Assert.Contains(",150,0.002222", text, StringComparison.Ordinal);
  }

  [Fact]
  public void Fit_activity_export_has_valid_header_crc_and_activity_file_id()
  {
    byte[] fit = SessionFitActivityExporter.Export(Session());
    using var stream = new MemoryStream(fit);
    var decoder = new Decode();

    Assert.True(decoder.IsFIT(stream));
    stream.Position = 0;
    Assert.True(decoder.CheckIntegrity(stream));
    stream.Position = 0;
    Dynastream.Fit.File? fileType = null;
    SessionMesg? decodedSession = null;
    LapMesg? decodedLap = null;
    DeviceInfoMesg? decodedDevice = null;
    var broadcaster = new MesgBroadcaster();
    broadcaster.FileIdMesgEvent += (_, args) => fileType = ((FileIdMesg)args.mesg).GetType();
    broadcaster.SessionMesgEvent += (_, args) => decodedSession = (SessionMesg)args.mesg;
    broadcaster.LapMesgEvent += (_, args) => decodedLap = (LapMesg)args.mesg;
    broadcaster.DeviceInfoMesgEvent += (_, args) => decodedDevice = new DeviceInfoMesg(args.mesg);
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;
    Assert.True(decoder.Read(stream));
    Assert.Equal(Dynastream.Fit.File.Activity, fileType);
    Assert.Equal((byte)135, decodedSession?.GetAvgHeartRate());
    Assert.Equal((byte)150, decodedSession?.GetMaxHeartRate());
    Assert.Equal((byte)120, decodedSession?.GetMinHeartRate());
    Assert.Equal(2, decodedSession?.GetTotalMovingTime());
    Assert.Equal(2, decodedSession?.GetActiveTime());
    Assert.InRange(decodedSession?.GetMaxSpeed() ?? 0, 2.221f, 2.223f);
    Assert.Equal(1, decodedSession?.GetAvgGrade());
    Assert.Equal((ushort)0, decodedSession?.GetTotalAscent());
    Assert.Equal((ushort)0, decodedSession?.GetTotalDescent());
    Assert.Equal((ushort)0, decodedLap?.GetTotalCalories());
    Assert.Equal((byte)150, decodedLap?.GetMaxHeartRate());
    Assert.Equal("TreadmillRunner", decodedDevice?.GetProductNameAsString());
  }

  [Fact]
  public void Fit_activity_export_writes_calculated_altitude_ascent_and_descent()
  {
    byte[] fit = SessionFitActivityExporter.Export(ElevationSession());
    using var stream = new MemoryStream(fit);
    var decoder = new Decode();
    SessionMesg? decodedSession = null;
    var records = new List<RecordMesg>();
    var broadcaster = new MesgBroadcaster();
    broadcaster.SessionMesgEvent += (_, args) => decodedSession = new SessionMesg(args.mesg);
    broadcaster.RecordMesgEvent += (_, args) => records.Add(new RecordMesg(args.mesg));
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

    Assert.True(decoder.Read(stream));
    Assert.Equal((ushort)9, decodedSession?.GetTotalAscent());
    Assert.InRange(decodedSession?.GetTotalFractionalAscent() ?? float.NaN, 0.95f, 0.951f);
    Assert.Equal((ushort)4, decodedSession?.GetTotalDescent());
    Assert.InRange(decodedSession?.GetTotalFractionalDescent() ?? float.NaN, 0.989f, 0.991f);
    Assert.InRange(decodedSession?.GetAvgPosGrade() ?? float.NaN, 9.99f, 10.01f);
    Assert.InRange(decodedSession?.GetAvgNegGrade() ?? float.NaN, -5.01f, -4.99f);
    Assert.Equal(1, decodedSession?.GetTimeInHrZone(1));
    Assert.Equal(1, decodedSession?.GetTimeInHrZone(4));
    Assert.True(records[1].GetVerticalSpeed() > 0);
    Assert.True(records[2].GetVerticalSpeed() < 0);
    Assert.Equal((byte)5, records[1].GetZone());
    Assert.Equal((byte)2, records[2].GetZone());
    Assert.InRange(records[^1].GetEnhancedAltitude() ?? float.NaN, 4.9f, 5.1f);
  }

  [Fact]
  public void Native_json_export_is_metric_versioned_and_keeps_full_resolution_samples()
  {
    using JsonDocument document = JsonDocument.Parse(SessionNativeJsonExporter.Export(Session()));
    JsonElement root = document.RootElement;

    Assert.Equal("treadmillrunner.session/v1", root.GetProperty("schema").GetString());
    Assert.Equal("Metric", root.GetProperty("unitSystem").GetString());
    Assert.Equal("Export test", root.GetProperty("session").GetProperty("workoutTitle").GetString());
    Assert.Equal(3, root.GetProperty("samples").GetArrayLength());
    Assert.Equal(8, root.GetProperty("samples")[1].GetProperty("measuredSpeedKph").GetDouble());
  }

  [Fact]
  public void Tcx_activity_export_is_metric_and_contains_trackpoint_heart_rate_and_speed()
  {
    XDocument document = XDocument.Parse(Encoding.UTF8.GetString(SessionTcxActivityExporter.Export(Session())));
    XNamespace tcx = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2";
    XNamespace activity = "http://www.garmin.com/xmlschemas/ActivityExtension/v2";

    XElement lap = Assert.Single(document.Descendants(tcx + "Lap"));
    Assert.Equal("3.333", lap.Element(tcx + "DistanceMeters")?.Value);
    Assert.Equal(3, document.Descendants(tcx + "Trackpoint").Count());
    XElement secondTrackpoint = document.Descendants(tcx + "Trackpoint").Skip(1).First();
    Assert.Equal("150", secondTrackpoint.Element(tcx + "HeartRateBpm")?.Element(tcx + "Value")?.Value);
    Assert.Equal("2.222", document.Descendants(activity + "Speed").Skip(1).First().Value);
  }

  [Fact]
  public void Fit_workout_export_encodes_an_immutable_metric_revision()
  {
    const string definition = """
      {"schemaVersion":1,"title":"Metric intervals","description":null,"blocks":[{"kind":"step","goal":{"kind":"time","durationTicks":600000000},"speed":{"kind":"fixed","kilometersPerHour":7.2},"incline":{"kind":"fixed","percent":1.5},"cue":"Steady","notes":null}]}
      """;
    byte[] fit = WorkoutFitExporter.Export(
      Guid.Parse("c0123456-789a-4bcd-8ef0-123456789abc"),
      definition,
      new DateTimeOffset(2026, 8, 23, 9, 0, 0, TimeSpan.Zero));
    using var stream = new MemoryStream(fit);
    var decoder = new Decode();
    Dynastream.Fit.File? fileType = null;
    WorkoutMesg? workout = null;
    WorkoutStepMesg? step = null;
    var broadcaster = new MesgBroadcaster();
    broadcaster.FileIdMesgEvent += (_, args) => fileType = new FileIdMesg(args.mesg).GetType();
    broadcaster.WorkoutMesgEvent += (_, args) => workout = new WorkoutMesg(args.mesg);
    broadcaster.WorkoutStepMesgEvent += (_, args) => step = new WorkoutStepMesg(args.mesg);
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

    Assert.True(decoder.Read(stream));
    Assert.Equal(Dynastream.Fit.File.Workout, fileType);
    Assert.Equal("Metric intervals", workout?.GetWktNameAsString());
    Assert.Equal((ushort)1, workout?.GetNumValidSteps());
    Assert.Equal(WktStepDuration.Time, step?.GetDurationType());
    Assert.Equal(60, step?.GetDurationTime());
    Assert.Equal(WktStepTarget.Speed, step?.GetTargetType());
    Assert.InRange(step?.GetCustomTargetSpeedLow() ?? float.NaN, 1.999f, 2.001f);
    Assert.Contains("incline 1.5%", step?.GetNotesAsString(), StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public void Garmin_merge_preserves_watch_fields_and_overlays_local_heart_rate_and_treadmill_values()
  {
    StoredWorkoutSession local = ElevationSession();
    byte[] watch = WatchFit(local.StartedAt!.Value);

    byte[] merged = GarminFitActivityMerger.Merge(watch, local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    FileIdMesg? decodedFileId = null;
    SessionMesg? decodedSession = null;
    var records = new List<RecordMesg>();
    var broadcaster = new MesgBroadcaster();
    broadcaster.FileIdMesgEvent += (_, args) => decodedFileId = new FileIdMesg(args.mesg);
    broadcaster.SessionMesgEvent += (_, args) => decodedSession = new SessionMesg(args.mesg);
    broadcaster.RecordMesgEvent += (_, args) => records.Add(new RecordMesg(args.mesg));
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;
    Assert.True(decoder.Read(stream));

    Assert.Equal(Manufacturer.Garmin, decodedFileId?.GetManufacturer());
    Assert.Equal((ushort)4242, decodedFileId?.GetProduct());
    Assert.Equal("fenix 8", decodedFileId?.GetProductNameAsString());
    Assert.Equal(
      123456u ^ BitConverter.ToUInt32(local.Definition.SessionId.ToByteArray(), 0) ^ 0x4D455247u,
      decodedFileId?.GetSerialNumber());
    Assert.Equal(3.4f, decodedSession?.GetTotalTrainingEffect());
    Assert.Equal(2.1f, decodedSession?.GetTotalAnaerobicTrainingEffect());
    Assert.Equal(44f, decodedSession?.GetTrainingStressScore());
    Assert.Equal((byte)135, decodedSession?.GetAvgHeartRate());
    Assert.Equal((byte)150, decodedSession?.GetMaxHeartRate());
    Assert.Equal((ushort)9, decodedSession?.GetTotalAscent());
    Assert.InRange(decodedSession?.GetTotalFractionalAscent() ?? float.NaN, 0.95f, 0.951f);
    Assert.Equal((ushort)4, decodedSession?.GetTotalDescent());
    Assert.InRange(decodedSession?.GetTotalFractionalDescent() ?? float.NaN, 0.989f, 0.991f);
    Assert.All(records, record => Assert.Equal((byte)88, record.GetCadence()));
    Assert.All(records, record => Assert.Null(record.GetField("compressed_speed_distance")));
    Assert.All(records, record =>
    {
      DeveloperField developerField = Assert.Single(record.DeveloperFields);
      Assert.Equal("watch_metric", developerField.GetName());
      Assert.Equal(42f, Convert.ToSingle(developerField.GetValue()));
    });
    Assert.Equal(new byte?[] { 135, 150, 120 }, records.Select(record => record.GetHeartRate()).ToArray());
    Assert.InRange(records[0].GetEnhancedAltitude() ?? float.NaN, 99.99f, 100.01f);
    Assert.InRange(records[1].GetEnhancedAltitude() ?? float.NaN, 109.9f, 110.1f);
  }

  [Fact]
  public void Garmin_merge_preserves_multiple_watch_laps_instead_of_copying_full_session_totals_into_each()
  {
    StoredWorkoutSession local = ElevationSession();
    byte[] merged = GarminFitActivityMerger.Merge(WatchFit(local.StartedAt!.Value, includeTwoLaps: true), local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    var laps = new List<LapMesg>();
    var broadcaster = new MesgBroadcaster();
    broadcaster.LapMesgEvent += (_, args) => laps.Add(new LapMesg(args.mesg));
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

    Assert.True(decoder.Read(stream));
    Assert.Equal(new float?[] { 25, 75 }, laps.Select(static lap => lap.GetTotalDistance()).ToArray());
  }

  [Fact]
  public void Garmin_merge_rebuilds_the_complete_local_timeline_when_the_watch_started_late()
  {
    StoredWorkoutSession local = ElevationSession();
    byte[] merged = GarminFitActivityMerger.Merge(WatchFit(local.StartedAt!.Value.AddMinutes(2)), local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    FileIdMesg? file = null;
    SessionMesg? session = null;
    var records = new List<RecordMesg>();
    var broadcaster = new MesgBroadcaster();
    broadcaster.FileIdMesgEvent += (_, args) => file = new FileIdMesg(args.mesg);
    broadcaster.SessionMesgEvent += (_, args) => session = new SessionMesg(args.mesg);
    broadcaster.RecordMesgEvent += (_, args) => records.Add(new RecordMesg(args.mesg));
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

    Assert.True(decoder.Read(stream));
    Assert.Equal(local.Samples.Count, records.Count);
    Assert.Equal(local.StartedAt.Value.UtcDateTime, file?.GetTimeCreated()?.GetDateTime());
    Assert.Equal(local.StartedAt.Value.UtcDateTime, session?.GetStartTime()?.GetDateTime());
    Assert.Equal(local.EndedAt!.Value.UtcDateTime, session?.GetTimestamp()?.GetDateTime());
    Assert.Equal((float)local.Duration.TotalSeconds, session?.GetTotalElapsedTime());
    Assert.Equal((float)local.Duration.TotalSeconds, session?.GetTotalTimerTime());
    Assert.Equal(local.Samples.Select(sample => (byte?)sample.HeartRateBpm).ToArray(), records.Select(record => record.GetHeartRate()).ToArray());
    Assert.Equal(
      local.Samples.Select(sample => sample.CapturedAt.UtcDateTime).ToArray(),
      records.Select(record => record.GetTimestamp()!.GetDateTime()).ToArray());
  }

  private static byte[] WatchFit(DateTimeOffset started, bool includeTwoLaps = false)
  {
    using var stream = new MemoryStream();
    var encoder = new Encode(ProtocolVersion.V20);
    encoder.Open(stream);
    var file = new FileIdMesg();
    file.SetType(Dynastream.Fit.File.Activity);
    file.SetManufacturer(Manufacturer.Garmin);
    file.SetProduct(4242);
    file.SetSerialNumber(123456);
    file.SetProductName("fenix 8");
    file.SetTimeCreated(new Dynastream.Fit.DateTime(started.UtcDateTime));
    encoder.Write(file);
    var developerData = new DeveloperDataIdMesg();
    developerData.SetDeveloperDataIndex(0);
    developerData.SetManufacturerId(Manufacturer.Garmin);
    for (var index = 0; index < 16; index++) developerData.SetApplicationId(index, (byte)(index + 1));
    encoder.Write(developerData);
    var fieldDescription = new FieldDescriptionMesg();
    fieldDescription.SetDeveloperDataIndex(0);
    fieldDescription.SetFieldDefinitionNumber(0);
    fieldDescription.SetFitBaseTypeId(FitBaseType.Float32);
    fieldDescription.SetFieldName(0, "watch_metric");
    fieldDescription.SetUnits(0, "score");
    encoder.Write(fieldDescription);
    for (var second = 0; second <= 2; second++)
    {
      var record = new RecordMesg();
      record.SetTimestamp(new Dynastream.Fit.DateTime(started.AddSeconds(second).UtcDateTime));
      record.SetCadence(88);
      record.SetHeartRate(90);
      record.SetAltitude(100);
      record.SetEnhancedAltitude(100);
      record.SetCompressedSpeedDistance(0, 1);
      record.SetCompressedSpeedDistance(1, 2);
      record.SetCompressedSpeedDistance(2, 3);
      var developerField = new DeveloperField(fieldDescription, developerData);
      developerField.SetValue(42f);
      record.SetDeveloperField(developerField);
      encoder.Write(record);
    }
    if (includeTwoLaps)
    {
      encoder.Write(WatchLap(started, started.AddSeconds(1), 25));
      encoder.Write(WatchLap(started.AddSeconds(1), started.AddSeconds(2), 75));
    }
    var session = new SessionMesg();
    session.SetTimestamp(new Dynastream.Fit.DateTime(started.AddSeconds(2).UtcDateTime));
    session.SetStartTime(new Dynastream.Fit.DateTime(started.UtcDateTime));
    session.SetSport(Sport.Running);
    session.SetSubSport(SubSport.Treadmill);
    session.SetTotalTrainingEffect(3.4f);
    session.SetTotalAnaerobicTrainingEffect(2.1f);
    session.SetTrainingStressScore(44f);
    session.SetAvgHeartRate(90);
    encoder.Write(session);
    encoder.Close();
    return stream.ToArray();
  }

  private static LapMesg WatchLap(DateTimeOffset started, DateTimeOffset ended, float distance)
  {
    var lap = new LapMesg();
    lap.SetTimestamp(new Dynastream.Fit.DateTime(ended.UtcDateTime));
    lap.SetStartTime(new Dynastream.Fit.DateTime(started.UtcDateTime));
    lap.SetTotalElapsedTime((float)(ended - started).TotalSeconds);
    lap.SetTotalTimerTime((float)(ended - started).TotalSeconds);
    lap.SetTotalDistance(distance);
    lap.SetSport(Sport.Running);
    lap.SetSubSport(SubSport.Treadmill);
    return lap;
  }

  private static StoredWorkoutSession Session()
  {
    var started = new DateTimeOffset(2026, 8, 4, 20, 0, 0, TimeSpan.Zero);
    Guid id = Guid.Parse("11111111-2222-3333-4444-555555555555");
    var definition = new NewWorkoutSession(
      id,
      Guid.Parse("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
      "Runner",
      Guid.Parse("12345678-1234-1234-1234-123456789abc"),
      "Export test",
      started.AddSeconds(-5),
      JsonSerializer.Serialize(new SessionExecutionConfiguration(
        "simulator",
        "disabled",
        new SessionProfileSnapshot(70, null, null,
        [
          new SessionHeartRateZoneSnapshot(1, "Zone 1", 100, 119),
          new SessionHeartRateZoneSnapshot(2, "Zone 2", 120, 129),
          new SessionHeartRateZoneSnapshot(3, "Zone 3", 130, 139),
          new SessionHeartRateZoneSnapshot(4, "Zone 4", 140, 149),
          new SessionHeartRateZoneSnapshot(5, "Zone 5", 150, 200),
        ]))),
      "v1");
    SessionSample[] samples =
    [
      Sample(id, 0, started, 0, 0, 0, 135, 0, 0),
      Sample(id, 1, started.AddSeconds(1), 1, 8, 2, 150, 8 / 3600d, 0.5),
      Sample(id, 2, started.AddSeconds(2), 2, 4, 0, 120, 12 / 3600d, 1),
    ];
    return new StoredWorkoutSession(
      definition,
      SessionState.Completed,
      started,
      started.AddSeconds(2),
      TimeSpan.FromSeconds(2),
      samples[^1].DistanceKilometers,
      samples[^1].EstimatedKilocalories,
      99,
      99,
      6,
      0,
      null,
      samples,
      []);
  }

  private static StoredWorkoutSession ElevationSession()
  {
    StoredWorkoutSession source = Session();
    DateTimeOffset started = source.StartedAt!.Value;
    Guid id = source.Definition.SessionId;
    SessionSample[] samples =
    [
      Sample(id, 0, started, 0, 0, 0, 135, 0, 0),
      Sample(id, 1, started.AddSeconds(1), 1, 8, 10, 150, 0.1, 0.5),
      Sample(id, 2, started.AddSeconds(2), 2, 4, -5, 120, 0.2, 1),
    ];
    return source with
    {
      DistanceKilometers = samples[^1].DistanceKilometers,
      AverageInclinePercent = 2.5,
      Samples = samples,
    };
  }

  private static SessionSample Sample(
    Guid sessionId,
    long sequence,
    DateTimeOffset capturedAt,
    double elapsedSeconds,
    double speedKph,
    double inclinePercent,
    ushort heartRate,
    double distanceKilometers,
    double calories) => new(
      sessionId,
      sequence,
      capturedAt,
      TimeSpan.FromSeconds(elapsedSeconds),
      speedKph,
      speedKph,
      speedKph,
      inclinePercent,
      inclinePercent,
      inclinePercent,
      heartRate,
      distanceKilometers,
      calories,
      TimeSpan.FromMilliseconds(10),
      "v1");
}
