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
    var decodedMessages = new List<Mesg>();
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
  public void Fit_exports_use_the_authoritative_v2_terminal_calorie_total()
  {
    StoredWorkoutSession source = Session();
    NewWorkoutSession original = source.Definition;
    var definition = new NewWorkoutSession(
      original.SessionId,
      original.UserProfileId,
      original.UserProfileName,
      original.WorkoutRevisionId,
      original.WorkoutTitle,
      original.ArmedAt,
      original.ControllerConfigurationJson,
      SessionMetricAlgorithms.EstimatedCaloriesV2,
      original.Selection,
      original.Origin);
    StoredWorkoutSession session = source with
    {
      Definition = definition,
      EstimatedKilocalories = 42.6,
    };

    byte[][] exports =
    [
      SessionFitActivityExporter.Export(session),
      GarminFitActivityMerger.Merge(WatchFit(session.StartedAt!.Value), session),
    ];
    foreach (byte[] fit in exports)
    {
      using var stream = new MemoryStream(fit);
      var decoder = new Decode();
      SessionMesg? decodedSession = null;
      var broadcaster = new MesgBroadcaster();
      broadcaster.SessionMesgEvent += (_, args) => decodedSession = new SessionMesg(args.mesg);
      decoder.MesgEvent += broadcaster.OnMesg;
      decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

      Assert.True(decoder.Read(stream));
      Assert.Equal((ushort)43, decodedSession?.GetTotalCalories());
    }

    string[] csvRows = Encoding.UTF8.GetString(SessionCsvExporter.Export(session))
      .Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries);
    Assert.Equal(
      session.Samples.Select((sample, index) => index == session.Samples.Count - 1
        ? Math.Max(sample.EstimatedKilocalories, session.EstimatedKilocalories)
        : sample.EstimatedKilocalories),
      csvRows.Skip(1).Select(row => double.Parse(row.Split(',')[10], System.Globalization.CultureInfo.InvariantCulture)));
  }

  [Fact]
  public void V2_fit_exports_floor_stored_calories_at_the_final_cumulative_sample_for_direct_and_merged_exports()
  {
    StoredWorkoutSession source = Session();
    NewWorkoutSession original = source.Definition;
    var definition = new NewWorkoutSession(
      original.SessionId,
      original.UserProfileId,
      original.UserProfileName,
      original.WorkoutRevisionId,
      original.WorkoutTitle,
      original.ArmedAt,
      original.ControllerConfigurationJson,
      SessionMetricAlgorithms.EstimatedCaloriesV2,
      original.Selection,
      original.Origin);
    StoredWorkoutSession session = source with
    {
      Definition = definition,
      EstimatedKilocalories = 0.4,
    };

    byte[][] exports =
    [
      SessionFitActivityExporter.Export(session),
      GarminFitActivityMerger.Merge(WatchFit(session.StartedAt!.Value), session),
    ];
    foreach (byte[] fit in exports)
    {
      using var stream = new MemoryStream(fit);
      var decoder = new Decode();
      SessionMesg? decodedSession = null;
      var broadcaster = new MesgBroadcaster();
      broadcaster.SessionMesgEvent += (_, args) => decodedSession = new SessionMesg(args.mesg);
      decoder.MesgEvent += broadcaster.OnMesg;
      decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

      Assert.True(decoder.Read(stream));
      Assert.Equal((ushort)1, decodedSession?.GetTotalCalories());
    }

    string[] csvRows = Encoding.UTF8.GetString(SessionCsvExporter.Export(session))
      .Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries);
    Assert.Equal((double)session.Samples[^1].EstimatedKilocalories,
      double.Parse(csvRows[^1].Split(',')[10], System.Globalization.CultureInfo.InvariantCulture));
  }

  [Theory]
  [InlineData(251)]
  [InlineData(255)]
  [InlineData(500)]
  public void Fit_exports_keep_out_of_range_sample_heart_rate_symmetric_and_avoid_the_fit_invalid_sentinel(int heartRate)
  {
    StoredWorkoutSession source = Session();
    SessionSample[] samples = source.Samples.ToArray();
    samples[1] = WithUnsafeHeartRate(samples[1], (ushort)heartRate);
    StoredWorkoutSession session = source with { Samples = samples };
    byte expected = (byte)Math.Min(heartRate, byte.MaxValue - 1);

    byte[][] exports =
    [
      SessionFitActivityExporter.Export(session),
      GarminFitActivityMerger.Merge(WatchFit(session.StartedAt!.Value), session),
    ];
    foreach (byte[] fit in exports)
    {
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
      Assert.Equal(expected, records[1].GetHeartRate());
      Assert.Equal(expected, decodedSession?.GetMaxHeartRate());
    }
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
  public void Garmin_merge_preserves_static_watch_provenance_and_emits_only_local_activity_data()
  {
    StoredWorkoutSession local = ElevationSession();
    byte[] watch = WatchFit(local.StartedAt!.Value);

    byte[] merged = GarminFitActivityMerger.Merge(watch, local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    FileIdMesg? decodedFileId = null;
    SessionMesg? decodedSession = null;
    LapMesg? decodedLap = null;
    ActivityMesg? decodedActivity = null;
    UserProfileMesg? decodedProfile = null;
    ZonesTargetMesg? decodedZones = null;
    var decodedHeartRateZones = new List<HrZoneMesg>();
    DeviceInfoMesg? decodedDevice = null;
    var records = new List<RecordMesg>();
    var decodedMessages = new List<Mesg>();
    var broadcaster = new MesgBroadcaster();
    broadcaster.FileIdMesgEvent += (_, args) => decodedFileId = new FileIdMesg(args.mesg);
    broadcaster.SessionMesgEvent += (_, args) => decodedSession = new SessionMesg(args.mesg);
    broadcaster.LapMesgEvent += (_, args) => decodedLap = new LapMesg(args.mesg);
    broadcaster.ActivityMesgEvent += (_, args) => decodedActivity = new ActivityMesg(args.mesg);
    broadcaster.UserProfileMesgEvent += (_, args) => decodedProfile = new UserProfileMesg(args.mesg);
    broadcaster.ZonesTargetMesgEvent += (_, args) => decodedZones = new ZonesTargetMesg(args.mesg);
    broadcaster.HrZoneMesgEvent += (_, args) => decodedHeartRateZones.Add(new HrZoneMesg(args.mesg));
    broadcaster.DeviceInfoMesgEvent += (_, args) => decodedDevice = new DeviceInfoMesg(args.mesg);
    broadcaster.RecordMesgEvent += (_, args) => records.Add(new RecordMesg(args.mesg));
    decoder.MesgEvent += (_, args) => decodedMessages.Add(new Mesg(args.mesg));
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;
    Assert.True(decoder.Read(stream));

    Assert.Equal(Manufacturer.Garmin, decodedFileId?.GetManufacturer());
    Assert.Equal((ushort)4242, decodedFileId?.GetProduct());
    Assert.Equal("fenix 8", decodedFileId?.GetProductNameAsString());
    Assert.Equal(
      123456u ^ BitConverter.ToUInt32(local.Definition.SessionId.ToByteArray(), 0) ^ 0x4D455247u,
      decodedFileId?.GetSerialNumber());
    Assert.Equal("Watch runner", decodedProfile?.GetFriendlyNameAsString());
    Assert.Equal((byte)190, decodedZones?.GetMaxHeartRate());
    Assert.Equal(HrZoneCalc.Custom, decodedZones?.GetHrCalcType());
    Assert.Equal(new byte?[] { 119, 139, 159, 179, 199 }, decodedHeartRateZones.Select(zone => zone.GetHighBpm()).ToArray());
    Assert.Equal(Manufacturer.Garmin, decodedDevice?.GetManufacturer());
    Assert.Equal(local.StartedAt.Value.UtcDateTime, decodedDevice?.GetTimestamp()?.GetDateTime());
    Assert.Null(decodedSession?.GetTotalTrainingEffect());
    Assert.Null(decodedSession?.GetTotalAnaerobicTrainingEffect());
    Assert.Null(decodedSession?.GetTrainingStressScore());
    Assert.Equal("Watch treadmill profile", decodedSession?.GetSportProfileNameAsString());
    Assert.Null(decodedSession?.GetAvgCadence());
    Assert.Null(decodedLap?.GetAvgCadence());
    Assert.Null(decodedSession?.GetAvgPower());
    Assert.Null(decodedLap?.GetAvgPower());
    Assert.Null(decodedSession?.GetNormalizedPower());
    Assert.Null(decodedLap?.GetNormalizedPower());
    Assert.Equal(
      new Dynastream.Fit.DateTime(local.EndedAt!.Value.UtcDateTime).GetTimeStamp() + 7_200u,
      decodedActivity?.GetLocalTimestamp());
    Assert.Equal((byte)135, decodedSession?.GetAvgHeartRate());
    Assert.Equal((byte)150, decodedSession?.GetMaxHeartRate());
    Assert.Equal((ushort)9, decodedSession?.GetTotalAscent());
    Assert.InRange(decodedSession?.GetTotalFractionalAscent() ?? float.NaN, 0.95f, 0.951f);
    Assert.Equal((ushort)4, decodedSession?.GetTotalDescent());
    Assert.InRange(decodedSession?.GetTotalFractionalDescent() ?? float.NaN, 0.989f, 0.991f);
    Assert.All(records, record => Assert.Equal((byte)88, record.GetCadence()));
    Assert.All(records, record => Assert.Equal((ushort)220, record.GetPower()));
    Assert.All(records, record => Assert.Equal((sbyte)21, record.GetTemperature()));
    Assert.All(records, record => Assert.Equal(37.2f, record.GetCoreTemperature()));
    Assert.All(records, record => Assert.Equal(45f, record.GetLeftPowerPhase(0)));
    Assert.All(records, record => Assert.Null(record.GetZone()));
    Assert.All(records, record => Assert.Null(record.GetField("compressed_speed_distance")));
    Assert.All(records, record => Assert.Empty(record.DeveloperFields));
    Assert.All(decodedMessages, message => Assert.Empty(message.DeveloperFields));
    Assert.Equal(new byte?[] { 135, 150, 120 }, records.Select(record => record.GetHeartRate()).ToArray());
    Assert.InRange(records[0].GetEnhancedAltitude() ?? float.NaN, 99.9f, 100.1f);
    Assert.InRange(records[1].GetEnhancedAltitude() ?? float.NaN, 109.9f, 110.1f);
  }

  [Fact]
  public void Garmin_merge_removes_implausible_user_profile_heart_rate_metadata()
  {
    StoredWorkoutSession local = ElevationSession();
    byte[] merged = GarminFitActivityMerger.Merge(
      WatchFit(local.StartedAt!.Value, userProfileMaxHeartRate: 0, userProfileRestingHeartRate: 255),
      local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    UserProfileMesg? profile = null;
    var broadcaster = new MesgBroadcaster();
    broadcaster.UserProfileMesgEvent += (_, args) => profile = new UserProfileMesg(args.mesg);
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

    Assert.True(decoder.Read(stream));
    Assert.NotNull(profile);
    Assert.Null(profile.GetDefaultMaxHeartRate());
    Assert.Null(profile.GetRestingHeartRate());
  }

  [Fact]
  public void Fit_exports_normalize_a_legacy_elapsed_time_regression()
  {
    StoredWorkoutSession source = ElevationSession();
    SessionSample[] samples = source.Samples.ToArray();
    SessionSample malformedSample = samples[1];
    samples[1] = new SessionSample(
      malformedSample.SessionId,
      malformedSample.Sequence,
      malformedSample.CapturedAt.AddHours(1),
      TimeSpan.FromHours(1),
      malformedSample.PlannedSpeedKph,
      malformedSample.RequestedSpeedKph,
      malformedSample.MeasuredSpeedKph,
      malformedSample.PlannedInclinePercent,
      malformedSample.RequestedInclinePercent,
      malformedSample.MeasuredInclinePercent,
      malformedSample.HeartRateBpm,
      malformedSample.DistanceKilometers,
      malformedSample.EstimatedKilocalories,
      malformedSample.TelemetryAge,
      malformedSample.MetricAlgorithmVersion);
    StoredWorkoutSession malformed = source with { Samples = samples };
    Assert.Equal(new long[] { 0, 2 },
      SessionSampleTimeline.Normalize(malformed.Samples).Select(static sample => sample.Sequence));

    byte[] standalone = SessionFitActivityExporter.Export(malformed);
    byte[] merged = GarminFitActivityMerger.Merge(
      WatchFit(source.StartedAt!.Value),
      malformed);

    using var standaloneStream = new MemoryStream(standalone);
    using var mergedStream = new MemoryStream(merged);
    var decoder = new Decode();
    Assert.True(decoder.IsFIT(standaloneStream));
    standaloneStream.Position = 0;
    Assert.True(decoder.CheckIntegrity(standaloneStream));
    Assert.True(decoder.IsFIT(mergedStream));
    mergedStream.Position = 0;
    Assert.True(decoder.CheckIntegrity(mergedStream));
  }

  [Fact]
  public void Garmin_merge_uses_plausible_matched_watch_heart_rate_only_for_a_local_gap()
  {
    StoredWorkoutSession source = ElevationSession();
    SessionSample[] samples = source.Samples.ToArray();
    samples[1] = WithoutHeartRate(samples[1]);
    StoredWorkoutSession local = source with { Samples = samples };

    byte[] merged = GarminFitActivityMerger.Merge(WatchFit(local.StartedAt!.Value), local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    var records = new List<RecordMesg>();
    SessionMesg? session = null;
    LapMesg? lap = null;
    var broadcaster = new MesgBroadcaster();
    broadcaster.RecordMesgEvent += (_, args) => records.Add(new RecordMesg(args.mesg));
    broadcaster.SessionMesgEvent += (_, args) => session = new SessionMesg(args.mesg);
    broadcaster.LapMesgEvent += (_, args) => lap = new LapMesg(args.mesg);
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

    Assert.True(decoder.Read(stream));
    Assert.Equal(new byte?[] { 135, 90, 120 }, records.Select(record => record.GetHeartRate()).ToArray());
    Assert.Equal((byte)105, session?.GetAvgHeartRate());
    Assert.Equal((byte)135, session?.GetMaxHeartRate());
    Assert.Equal((byte)90, session?.GetMinHeartRate());
    Assert.Equal(session?.GetAvgHeartRate(), lap?.GetAvgHeartRate());
    Assert.Equal(session?.GetMaxHeartRate(), lap?.GetMaxHeartRate());
    Assert.Equal(session?.GetMinHeartRate(), lap?.GetMinHeartRate());
  }

  [Fact]
  public void Garmin_merge_preserves_an_unfilled_heart_rate_gap()
  {
    StoredWorkoutSession source = ElevationSession();
    SessionSample[] samples = source.Samples.ToArray();
    samples[1] = WithoutHeartRate(samples[1]);
    StoredWorkoutSession local = source with { Samples = samples };

    byte[] merged = GarminFitActivityMerger.Merge(
      WatchFit(local.StartedAt!.Value, includeRecordHeartRate: false),
      local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    var records = new List<RecordMesg>();
    SessionMesg? session = null;
    LapMesg? lap = null;
    var broadcaster = new MesgBroadcaster();
    broadcaster.RecordMesgEvent += (_, args) => records.Add(new RecordMesg(args.mesg));
    broadcaster.SessionMesgEvent += (_, args) => session = new SessionMesg(args.mesg);
    broadcaster.LapMesgEvent += (_, args) => lap = new LapMesg(args.mesg);
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

    Assert.True(decoder.Read(stream));
    Assert.Equal(new byte?[] { 135, byte.MaxValue, 120 }, records.Select(record => record.GetHeartRate()).ToArray());
    Assert.Equal((byte)120, session?.GetAvgHeartRate());
    Assert.Equal((byte)120, session?.GetMinHeartRate());
    Assert.Equal((byte)135, session?.GetMaxHeartRate());
    Assert.Equal(session?.GetAvgHeartRate(), lap?.GetAvgHeartRate());
    Assert.Equal(session?.GetMinHeartRate(), lap?.GetMinHeartRate());
    Assert.Equal(session?.GetMaxHeartRate(), lap?.GetMaxHeartRate());
  }

  [Fact]
  public void Garmin_merge_accepts_a_partially_filled_late_heart_rate_timeline()
  {
    StoredWorkoutSession source = ElevationSession();
    SessionSample[] samples = source.Samples.Select(WithoutHeartRate).ToArray();
    StoredWorkoutSession local = source with { Samples = samples };

    byte[] merged = GarminFitActivityMerger.Merge(
      WatchFit(local.StartedAt!.Value, recordSeconds: [0, 1]),
      local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    var records = new List<RecordMesg>();
    var broadcaster = new MesgBroadcaster();
    broadcaster.RecordMesgEvent += (_, args) => records.Add(new RecordMesg(args.mesg));
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

    Assert.True(decoder.Read(stream));
    Assert.Equal(new byte?[] { 90, 90, byte.MaxValue }, records.Select(record => record.GetHeartRate()).ToArray());
  }

  [Fact]
  public void Garmin_merge_discards_stale_watch_timeline_and_rebuilds_one_coherent_local_activity()
  {
    StoredWorkoutSession local = ElevationSession();
    byte[] merged = GarminFitActivityMerger.Merge(WatchFit(local.StartedAt!.Value, includeTwoLaps: true), local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    var messages = new List<Mesg>();
    var laps = new List<LapMesg>();
    var events = new List<EventMesg>();
    SessionMesg? session = null;
    ActivityMesg? activity = null;
    var broadcaster = new MesgBroadcaster();
    broadcaster.LapMesgEvent += (_, args) => laps.Add(new LapMesg(args.mesg));
    broadcaster.EventMesgEvent += (_, args) => events.Add(new EventMesg(args.mesg));
    broadcaster.SessionMesgEvent += (_, args) => session = new SessionMesg(args.mesg);
    broadcaster.ActivityMesgEvent += (_, args) => activity = new ActivityMesg(args.mesg);
    decoder.MesgEvent += (_, args) => messages.Add(new Mesg(args.mesg));
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

    Assert.True(decoder.Read(stream));
    LapMesg lap = Assert.Single(laps);
    Assert.Equal((ushort)0, lap.GetMessageIndex());
    Assert.Equal((ushort)0, session?.GetFirstLapIndex());
    Assert.Equal((ushort)1, session?.GetNumLaps());
    Assert.Equal((float)(local.DistanceKilometers * 1000), lap.GetTotalDistance());
    Assert.Equal(session?.GetTotalDistance(), lap.GetTotalDistance());
    Assert.Equal(session?.GetTotalTimerTime(), lap.GetTotalTimerTime());
    Assert.Equal(session?.GetTotalCalories(), lap.GetTotalCalories());
    Assert.Null(lap.GetAvgCadence());
    Assert.Null(lap.GetAvgPower());
    Assert.Equal(session?.GetTotalTimerTime(), activity?.GetTotalTimerTime());
    Assert.Equal(0, lap.GetNumTimeInHrZone());
    Assert.Equal(0, session?.GetNumTimeInHrZone());
    Assert.DoesNotContain(messages, static message => message.Num is MesgNum.TimeInZone or MesgNum.Split or MesgNum.SplitSummary or 534);
    Assert.Collection(events,
      start =>
      {
        Assert.Equal(Event.Timer, start.GetEvent());
        Assert.Equal(EventType.Start, start.GetEventType());
        Assert.Equal(local.StartedAt.Value.UtcDateTime, start.GetTimestamp()?.GetDateTime());
      },
      stop =>
      {
        Assert.Equal(Event.Timer, stop.GetEvent());
        Assert.Equal(EventType.StopAll, stop.GetEventType());
        Assert.Equal(local.EndedAt!.Value.UtcDateTime, stop.GetTimestamp()?.GetDateTime());
      });
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
    Assert.Null(session?.GetAvgCadence());
    Assert.Null(session?.GetAvgPower());
    Assert.All(records, record => Assert.Equal(byte.MaxValue, record.GetCadence()));
    Assert.Equal(local.Samples.Select(sample => (byte?)sample.HeartRateBpm).ToArray(), records.Select(record => record.GetHeartRate()).ToArray());
    Assert.Equal(
      local.Samples.Select(sample => sample.CapturedAt.UtcDateTime).ToArray(),
      records.Select(record => record.GetTimestamp()!.GetDateTime()).ToArray());
  }

  [Fact]
  public void Garmin_merge_rebuilds_persisted_pause_resume_timer_history_and_separates_elapsed_from_timer_time()
  {
    StoredWorkoutSession local = PausedSession();
    byte[] merged = GarminFitActivityMerger.Merge(
      WatchFit(local.StartedAt!.Value, recordSeconds: [0, 3, 7, 12]),
      local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    var events = new List<EventMesg>();
    SessionMesg? session = null;
    LapMesg? lap = null;
    ActivityMesg? activity = null;
    var broadcaster = new MesgBroadcaster();
    broadcaster.EventMesgEvent += (_, args) => events.Add(new EventMesg(args.mesg));
    broadcaster.SessionMesgEvent += (_, args) => session = new SessionMesg(args.mesg);
    broadcaster.LapMesgEvent += (_, args) => lap = new LapMesg(args.mesg);
    broadcaster.ActivityMesgEvent += (_, args) => activity = new ActivityMesg(args.mesg);
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

    Assert.True(decoder.Read(stream));
    Assert.Collection(events,
      start => AssertTimerEvent(start, EventType.Start, local.StartedAt!.Value),
      pause => AssertTimerEvent(pause, EventType.Stop, local.StartedAt.Value.AddSeconds(3)),
      resume => AssertTimerEvent(resume, EventType.Start, local.StartedAt.Value.AddSeconds(7)),
      stop => AssertTimerEvent(stop, EventType.StopAll, local.EndedAt!.Value));
    Assert.Equal(12, session?.GetTotalElapsedTime());
    Assert.Equal(8, session?.GetTotalTimerTime());
    Assert.Equal(12, lap?.GetTotalElapsedTime());
    Assert.Equal(8, lap?.GetTotalTimerTime());
    Assert.Equal(8, activity?.GetTotalTimerTime());
    Assert.Equal((byte)131, session?.GetAvgHeartRate());
    Assert.Equal(session?.GetAvgHeartRate(), lap?.GetAvgHeartRate());
    Assert.Equal(session?.GetMinHeartRate(), lap?.GetMinHeartRate());
    Assert.Equal(session?.GetMaxHeartRate(), lap?.GetMaxHeartRate());
  }

  [Fact]
  public void Interrupted_fit_elapsed_time_stops_at_the_last_record_instead_of_the_later_reconciliation_time()
  {
    StoredWorkoutSession source = Session();
    StoredWorkoutSession interrupted = source with
    {
      State = SessionState.Interrupted,
      EndedAt = source.StartedAt!.Value.AddHours(8),
      Events =
      [
        new SessionPausedEvent(
          SessionPauseReason.WebControl,
          source.Samples[^1].CapturedAt.AddSeconds(1)),
      ],
    };
    byte[] fit = SessionFitActivityExporter.Export(interrupted);
    using var stream = new MemoryStream(fit);
    var decoder = new Decode();
    var events = new List<EventMesg>();
    SessionMesg? session = null;
    LapMesg? lap = null;
    ActivityMesg? activity = null;
    var broadcaster = new MesgBroadcaster();
    broadcaster.EventMesgEvent += (_, args) => events.Add(new EventMesg(args.mesg));
    broadcaster.SessionMesgEvent += (_, args) => session = new SessionMesg(args.mesg);
    broadcaster.LapMesgEvent += (_, args) => lap = new LapMesg(args.mesg);
    broadcaster.ActivityMesgEvent += (_, args) => activity = new ActivityMesg(args.mesg);
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

    Assert.True(decoder.Read(stream));
    double recordTimelineSeconds = Math.Max(
      interrupted.Duration.TotalSeconds,
      (interrupted.Samples[^1].CapturedAt - interrupted.StartedAt!.Value).TotalSeconds);
    recordTimelineSeconds = Math.Max(
      recordTimelineSeconds,
      (interrupted.Events[^1].OccurredAt - interrupted.StartedAt.Value).TotalSeconds);
    DateTimeOffset effectiveEnd = interrupted.StartedAt.Value.AddSeconds(recordTimelineSeconds);
    Assert.Equal((float)recordTimelineSeconds, session?.GetTotalElapsedTime());
    Assert.Equal((float)recordTimelineSeconds, lap?.GetTotalElapsedTime());
    Assert.Equal(3, events.Count);
    AssertTimerEvent(events[^2], EventType.Stop, interrupted.Events[^1].OccurredAt);
    AssertTimerEvent(events[^1], EventType.StopAll, effectiveEnd);
    uint expectedTimestamp = new Dynastream.Fit.DateTime(effectiveEnd.UtcDateTime).GetTimeStamp();
    Assert.Equal(expectedTimestamp, session?.GetTimestamp()?.GetTimeStamp());
    Assert.Equal(expectedTimestamp, lap?.GetTimestamp()?.GetTimeStamp());
    Assert.Equal(expectedTimestamp, activity?.GetTimestamp()?.GetTimeStamp());
  }

  [Fact]
  public void Garmin_merge_skips_redundant_pause_and_resume_history()
  {
    StoredWorkoutSession source = PausedSession();
    DateTimeOffset started = source.StartedAt!.Value;
    StoredWorkoutSession local = source with
    {
      Events =
      [
        new SessionPausedEvent(SessionPauseReason.WebControl, started.AddSeconds(3)),
        new SessionPausedEvent(SessionPauseReason.PhysicalConsole, started.AddSeconds(4)),
        new SessionResumedEvent(started.AddSeconds(7)),
        new SessionResumedEvent(started.AddSeconds(8)),
      ],
    };

    var events = DecodeTimerEvents(GarminFitActivityMerger.Merge(
      WatchFit(started, recordSeconds: [0, 3, 7, 12]),
      local));

    Assert.Collection(events,
      start => AssertTimerEvent(start, EventType.Start, started),
      pause => AssertTimerEvent(pause, EventType.Stop, started.AddSeconds(3)),
      resume => AssertTimerEvent(resume, EventType.Start, started.AddSeconds(7)),
      stop => AssertTimerEvent(stop, EventType.StopAll, local.EndedAt!.Value));
  }

  [Fact]
  public void Garmin_merge_ignores_pause_and_resume_history_outside_the_completed_session_window()
  {
    StoredWorkoutSession source = PausedSession();
    DateTimeOffset started = source.StartedAt!.Value;
    StoredWorkoutSession local = source with
    {
      Events =
      [
        new SessionPausedEvent(SessionPauseReason.WebControl, started.AddSeconds(-1)),
        new SessionResumedEvent(started.AddSeconds(-0.5)),
        new SessionPausedEvent(SessionPauseReason.PhysicalConsole, started.AddSeconds(3)),
        new SessionResumedEvent(started.AddSeconds(7)),
        new SessionPausedEvent(SessionPauseReason.TreadmillStopped, source.EndedAt!.Value.AddSeconds(1)),
      ],
    };

    var events = DecodeTimerEvents(GarminFitActivityMerger.Merge(
      WatchFit(started, recordSeconds: [0, 3, 7, 12]),
      local));

    Assert.Collection(events,
      start => AssertTimerEvent(start, EventType.Start, started),
      pause => AssertTimerEvent(pause, EventType.Stop, started.AddSeconds(3)),
      resume => AssertTimerEvent(resume, EventType.Start, started.AddSeconds(7)),
      stop => AssertTimerEvent(stop, EventType.StopAll, local.EndedAt!.Value));
  }

  [Fact]
  public void Garmin_merge_omits_watch_aggregates_when_record_coverage_has_a_large_gap()
  {
    StoredWorkoutSession source = ElevationSession();
    DateTimeOffset started = source.StartedAt!.Value;
    Guid sessionId = source.Definition.SessionId;
    SessionSample[] samples =
    [
      Sample(sessionId, 0, started, 0, 0, 0, 135, 0, 0),
      Sample(sessionId, 1, started.AddSeconds(6), 6, 8, 1, 150, .01, .5),
      Sample(sessionId, 2, started.AddSeconds(12), 12, 4, 0, 120, .02, 1),
    ];
    StoredWorkoutSession local = source with
    {
      EndedAt = started.AddSeconds(12),
      Duration = TimeSpan.FromSeconds(12),
      DistanceKilometers = samples[^1].DistanceKilometers,
      Samples = samples,
    };

    byte[] merged = GarminFitActivityMerger.Merge(
      WatchFit(
        started,
        recordSeconds: [0, 12],
        recordCadences: new Dictionary<int, byte> { [0] = 80, [12] = 92 }),
      local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    SessionMesg? session = null;
    LapMesg? lap = null;
    var records = new List<RecordMesg>();
    var broadcaster = new MesgBroadcaster();
    broadcaster.SessionMesgEvent += (_, args) => session = new SessionMesg(args.mesg);
    broadcaster.LapMesgEvent += (_, args) => lap = new LapMesg(args.mesg);
    broadcaster.RecordMesgEvent += (_, args) => records.Add(new RecordMesg(args.mesg));
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

    Assert.True(decoder.Read(stream));
    Assert.Null(session?.GetAvgCadence());
    Assert.Null(session?.GetAvgPower());
    Assert.Null(lap?.GetAvgCadence());
    Assert.Null(lap?.GetAvgPower());
    Assert.Equal(new byte?[] { 80, byte.MaxValue, 92 }, records.Select(record => record.GetCadence()).ToArray());
  }

  [Fact]
  public void Garmin_merge_reserves_an_exact_record_match_before_assigning_a_nearby_sample()
  {
    StoredWorkoutSession source = ElevationSession();
    DateTimeOffset started = source.StartedAt!.Value;
    Guid sessionId = source.Definition.SessionId;
    SessionSample[] samples =
    [
      Sample(sessionId, 0, started, 0, 0, 0, 135, 0, 0),
      Sample(sessionId, 1, started.AddSeconds(5), 5, 8, 1, 150, .01, .5),
    ];
    StoredWorkoutSession local = source with
    {
      EndedAt = started.AddSeconds(5),
      Duration = TimeSpan.FromSeconds(5),
      DistanceKilometers = samples[^1].DistanceKilometers,
      Samples = samples,
    };
    byte[] merged = GarminFitActivityMerger.Merge(
      WatchFit(
        started,
        recordSeconds: [4, 5],
        recordCadences: new Dictionary<int, byte> { [4] = 80, [5] = 92 }),
      local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    var records = new List<RecordMesg>();
    var broadcaster = new MesgBroadcaster();
    broadcaster.RecordMesgEvent += (_, args) => records.Add(new RecordMesg(args.mesg));
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

    Assert.True(decoder.Read(stream));
    Assert.Equal(new byte?[] { 80, 92 }, records.Select(record => record.GetCadence()).ToArray());
  }

  [Fact]
  public void Garmin_merge_preserves_watch_only_measurements_on_the_local_timeline()
  {
    StoredWorkoutSession local = ElevationSession();

    byte[] merged = GarminFitActivityMerger.Merge(WatchFit(local.StartedAt!.Value.AddSeconds(1)), local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    var records = new List<RecordMesg>();
    var broadcaster = new MesgBroadcaster();
    broadcaster.RecordMesgEvent += (_, args) => records.Add(new RecordMesg(args.mesg));
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

    Assert.True(decoder.Read(stream));
    Assert.Equal(local.Samples.Count, records.Count);
    Assert.Equal(new byte?[] { byte.MaxValue, 88, 88 }, records.Select(record => record.GetCadence()).ToArray());
    Assert.All(records, record => Assert.Empty(record.DeveloperFields));
    Assert.Equal(
      local.Samples.Select(sample => sample.CapturedAt.UtcDateTime).ToArray(),
      records.Select(record => record.GetTimestamp()!.GetDateTime()).ToArray());
  }

  [Fact]
  public void Garmin_merge_omits_the_entire_zone_model_when_zone_indexes_conflict()
  {
    StoredWorkoutSession local = ElevationSession();
    byte[] merged = GarminFitActivityMerger.Merge(
      WatchFit(local.StartedAt!.Value, includeConflictingZones: true),
      local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    var messages = new List<Mesg>();
    decoder.MesgEvent += (_, args) => messages.Add(new Mesg(args.mesg));

    Assert.True(decoder.Read(stream));
    Assert.DoesNotContain(messages, static message => message.Num is MesgNum.ZonesTarget or MesgNum.HrZone);
  }

  [Theory]
  [InlineData(29)]
  [InlineData(251)]
  public void Garmin_merge_omits_the_entire_zone_model_when_a_zone_high_bpm_is_outside_the_app_range(int highBpm)
  {
    StoredWorkoutSession local = ElevationSession();
    byte[] merged = GarminFitActivityMerger.Merge(
      WatchFit(local.StartedAt!.Value, outOfRangeZoneHighBpm: (byte)highBpm),
      local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    var messages = new List<Mesg>();
    decoder.MesgEvent += (_, args) => messages.Add(new Mesg(args.mesg));

    Assert.True(decoder.Read(stream));
    Assert.DoesNotContain(messages, static message => message.Num is MesgNum.ZonesTarget or MesgNum.HrZone);
  }

  [Theory]
  [InlineData(29)]
  [InlineData(251)]
  public void Garmin_merge_omits_the_entire_zone_model_when_target_max_heart_rate_is_outside_the_app_range(int maxHeartRate)
  {
    StoredWorkoutSession local = ElevationSession();
    byte[] merged = GarminFitActivityMerger.Merge(
      WatchFit(local.StartedAt!.Value, targetMaxHeartRate: (byte)maxHeartRate),
      local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    var messages = new List<Mesg>();
    decoder.MesgEvent += (_, args) => messages.Add(new Mesg(args.mesg));

    Assert.True(decoder.Read(stream));
    Assert.DoesNotContain(messages, static message => message.Num is MesgNum.ZonesTarget or MesgNum.HrZone);
  }

  [Theory]
  [InlineData(29)]
  [InlineData(251)]
  public void Garmin_merge_omits_the_entire_zone_model_when_target_threshold_heart_rate_is_outside_the_app_range(int thresholdHeartRate)
  {
    StoredWorkoutSession local = ElevationSession();
    byte[] merged = GarminFitActivityMerger.Merge(
      WatchFit(local.StartedAt!.Value, targetThresholdHeartRate: (byte)thresholdHeartRate),
      local);
    using var stream = new MemoryStream(merged);
    var decoder = new Decode();
    var messages = new List<Mesg>();
    decoder.MesgEvent += (_, args) => messages.Add(new Mesg(args.mesg));

    Assert.True(decoder.Read(stream));
    Assert.DoesNotContain(messages, static message => message.Num is MesgNum.ZonesTarget or MesgNum.HrZone);
  }

  [Fact]
  public void Garmin_merge_scales_to_a_four_hour_one_hertz_activity()
  {
    const int sampleCount = 14_400;
    StoredWorkoutSession source = Session();
    DateTimeOffset started = source.StartedAt!.Value;
    Guid sessionId = source.Definition.SessionId;
    SessionSample[] samples = Enumerable.Range(0, sampleCount)
      .Select(second => Sample(
        sessionId,
        second,
        started.AddSeconds(second),
        second,
        8,
        1,
        135,
        second * 8d / 3600d,
        second * 0.1))
      .ToArray();
    StoredWorkoutSession local = source with
    {
      EndedAt = samples[^1].CapturedAt,
      Duration = samples[^1].Elapsed,
      DistanceKilometers = samples[^1].DistanceKilometers,
      EstimatedKilocalories = samples[^1].EstimatedKilocalories,
      Samples = samples,
    };
    int[] recordSeconds = Enumerable.Range(0, sampleCount).ToArray();

    var stopwatch = System.Diagnostics.Stopwatch.StartNew();
    byte[] merged = GarminFitActivityMerger.Merge(
      WatchFit(started, recordSeconds: recordSeconds),
      local);
    stopwatch.Stop();

    Assert.NotEmpty(merged);
    Assert.True(stopwatch.Elapsed < TimeSpan.FromSeconds(30), $"Merge took {stopwatch.Elapsed}.");
  }

  [Fact]
  public void Garmin_merge_rejects_a_non_activity_fit_file()
  {
    StoredWorkoutSession local = ElevationSession();
    byte[] workout = WatchFit(local.StartedAt!.Value, fileType: Dynastream.Fit.File.Workout);

    InvalidDataException error = Assert.Throws<InvalidDataException>(() =>
      GarminFitActivityMerger.Merge(workout, local));

    Assert.Contains("Activity File Id", error.Message, StringComparison.Ordinal);
  }

  [Fact]
  public void Garmin_merge_rejects_a_watch_fit_without_a_file_id_as_invalid_data()
  {
    StoredWorkoutSession local = ElevationSession();
    byte[] watch = WatchFit(local.StartedAt!.Value, omitFileId: true);

    InvalidDataException error = Assert.Throws<InvalidDataException>(() =>
      GarminFitActivityMerger.Merge(watch, local));

    Assert.Equal("The watch FIT must contain exactly one Activity File Id message.", error.Message);
  }

  [Fact]
  public void Garmin_merge_rejects_a_watch_fit_with_multiple_file_ids_as_invalid_data()
  {
    StoredWorkoutSession local = ElevationSession();
    byte[] watch = WatchFit(local.StartedAt!.Value, includeSecondFileId: true);

    InvalidDataException error = Assert.Throws<InvalidDataException>(() =>
      GarminFitActivityMerger.Merge(watch, local));

    Assert.Equal("The watch FIT must contain exactly one Activity File Id message.", error.Message);
  }

  private static byte[] WatchFit(
    DateTimeOffset started,
    bool includeTwoLaps = false,
    Dynastream.Fit.File fileType = Dynastream.Fit.File.Activity,
    IReadOnlyList<int>? recordSeconds = null,
    bool includeConflictingZones = false,
    IReadOnlyDictionary<int, byte>? recordCadences = null,
    byte? outOfRangeZoneHighBpm = null,
    byte? targetMaxHeartRate = 190,
    byte? targetThresholdHeartRate = null,
    bool omitFileId = false,
    bool includeSecondFileId = false,
    bool includeRecordHeartRate = true,
    byte? userProfileMaxHeartRate = 190,
    byte? userProfileRestingHeartRate = 60)
  {
    using var stream = new MemoryStream();
    var encoder = new Encode(ProtocolVersion.V20);
    encoder.Open(stream);
    var file = new FileIdMesg();
    file.SetType(fileType);
    file.SetManufacturer(Manufacturer.Garmin);
    file.SetProduct(4242);
    file.SetSerialNumber(123456);
    file.SetProductName("fenix 8");
    file.SetTimeCreated(new Dynastream.Fit.DateTime(started.UtcDateTime));
    if (!omitFileId)
    {
      encoder.Write(file);
      if (includeSecondFileId) encoder.Write(new FileIdMesg(file));
    }
    var creator = new FileCreatorMesg();
    creator.SetSoftwareVersion(2310);
    creator.SetHardwareVersion(8);
    encoder.Write(creator);
    var profile = new UserProfileMesg();
    profile.SetFriendlyName("Watch runner");
    if (userProfileMaxHeartRate is { } maxHeartRate)
      profile.SetDefaultMaxHeartRate(maxHeartRate);
    if (userProfileRestingHeartRate is { } restingHeartRate)
      profile.SetRestingHeartRate(restingHeartRate);
    encoder.Write(profile);
    var zones = new ZonesTargetMesg();
    zones.SetMaxHeartRate(targetMaxHeartRate);
    if (targetThresholdHeartRate is { } thresholdHeartRate)
      zones.SetThresholdHeartRate(thresholdHeartRate);
    zones.SetHrCalcType(HrZoneCalc.Custom);
    encoder.Write(zones);
    byte[] zoneUpperBounds = [119, 139, 159, 179, 199];
    for (var zoneIndex = 0; zoneIndex < zoneUpperBounds.Length; zoneIndex++)
    {
      var zone = new HrZoneMesg();
      zone.SetMessageIndex((ushort)zoneIndex);
      zone.SetHighBpm(zoneIndex == 0 && outOfRangeZoneHighBpm is { } invalidHighBpm
        ? invalidHighBpm
        : zoneUpperBounds[zoneIndex]);
      zone.SetName($"Watch zone {zoneIndex + 1}");
      encoder.Write(zone);
    }
    if (includeConflictingZones)
    {
      var conflictingZone = new HrZoneMesg();
      conflictingZone.SetMessageIndex(2);
      conflictingZone.SetHighBpm(170);
      conflictingZone.SetName("Conflicting watch zone");
      encoder.Write(conflictingZone);
    }
    var sport = new SportMesg();
    sport.SetSport(Sport.Running);
    sport.SetSubSport(SubSport.Treadmill);
    sport.SetName("Watch treadmill");
    encoder.Write(sport);
    var device = new DeviceInfoMesg();
    device.SetTimestamp(new Dynastream.Fit.DateTime(started.UtcDateTime));
    device.SetDeviceIndex(0);
    device.SetManufacturer(Manufacturer.Garmin);
    device.SetProduct(4242);
    device.SetSerialNumber(123456);
    device.SetProductName("fenix 8");
    encoder.Write(device);
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
    var developerDevice = new DeviceInfoMesg();
    developerDevice.SetTimestamp(new Dynastream.Fit.DateTime(started.UtcDateTime));
    developerDevice.SetDeviceIndex(1);
    developerDevice.SetManufacturer(Manufacturer.Garmin);
    developerDevice.SetProduct(4242);
    developerDevice.SetSerialNumber(123456);
    developerDevice.SetProductName("fenix 8");
    var metadataDeveloperField = new DeveloperField(fieldDescription, developerData);
    metadataDeveloperField.SetValue(7f);
    developerDevice.SetDeveloperField(metadataDeveloperField);
    encoder.Write(developerDevice);
    encoder.Write(WatchTimerEvent(started, EventType.Start));
    IReadOnlyList<int> timelineSeconds = recordSeconds ?? [0, 1, 2];
    foreach (int second in timelineSeconds)
    {
      var record = new RecordMesg();
      record.SetTimestamp(new Dynastream.Fit.DateTime(started.AddSeconds(second).UtcDateTime));
      record.SetCadence(recordCadences?.GetValueOrDefault(second) ?? 88);
      record.SetPower(220);
      record.SetTemperature(21);
      record.SetCoreTemperature(37.2f);
      record.SetLeftPowerPhase(0, 45f);
      record.SetLeftPowerPhase(1, 120f);
      if (includeRecordHeartRate) record.SetHeartRate(90);
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
    int finalSecond = timelineSeconds.Count == 0 ? 0 : timelineSeconds.Max();
    encoder.Write(WatchTimerEvent(started.AddSeconds(finalSecond), EventType.StopAll));
    if (includeTwoLaps)
    {
      encoder.Write(WatchLap(started, started.AddSeconds(1), 25));
      encoder.Write(WatchLap(started.AddSeconds(1), started.AddSeconds(2), 75));

      var timeInZone = new TimeInZoneMesg();
      timeInZone.SetTimestamp(new Dynastream.Fit.DateTime(started.AddSeconds(2).UtcDateTime));
      timeInZone.SetReferenceMesg(MesgNum.Session);
      timeInZone.SetReferenceIndex(0);
      timeInZone.SetTimeInHrZone(0, 1);
      timeInZone.SetTimeInHrZone(1, 1);
      timeInZone.SetHrZoneHighBoundary(0, 100);
      timeInZone.SetHrZoneHighBoundary(1, 150);
      encoder.Write(timeInZone);

      var split = new SplitMesg();
      split.SetMessageIndex(0);
      split.SetStartTime(new Dynastream.Fit.DateTime(started.UtcDateTime));
      split.SetEndTime(new Dynastream.Fit.DateTime(started.AddSeconds(2).UtcDateTime));
      split.SetTotalTimerTime(2);
      split.SetTotalDistance(100);
      encoder.Write(split);

      var splitSummary = new SplitSummaryMesg();
      splitSummary.SetMessageIndex(0);
      splitSummary.SetNumSplits(1);
      splitSummary.SetTotalTimerTime(2);
      splitSummary.SetTotalDistance(100);
      encoder.Write(splitSummary);

      var proprietaryTimeline = new Mesg("unknown", 534);
      proprietaryTimeline.SetFieldValue(253, new Dynastream.Fit.DateTime(started.AddSeconds(2).UtcDateTime).GetTimeStamp());
      proprietaryTimeline.SetFieldValue(0, (byte)100);
      encoder.Write(proprietaryTimeline);
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
    session.SetSportProfileName("Watch treadmill profile");
    session.SetAvgCadence(88);
    session.SetMaxCadence(90);
    session.SetAvgPower(220);
    session.SetMaxPower(300);
    session.SetNormalizedPower(230);
    session.SetAvgTemperature(21);
    session.SetMaxTemperature(24);
    session.SetMinTemperature(18);
    session.SetAvgCoreTemperature(37.2f);
    session.SetMinCoreTemperature(36.8f);
    session.SetMaxCoreTemperature(37.8f);
    session.SetAvgLeftPowerPhase(0, 45f);
    session.SetAvgLeftPowerPhase(1, 120f);
    if (includeTwoLaps)
    {
      session.SetFirstLapIndex(0);
      session.SetNumLaps(2);
      session.SetTimeInHrZone(0, 1);
      session.SetTimeInHrZone(1, 1);
    }
    encoder.Write(session);
    var activity = new ActivityMesg();
    activity.SetTimestamp(new Dynastream.Fit.DateTime(started.AddSeconds(finalSecond).UtcDateTime));
    activity.SetTotalTimerTime(finalSecond);
    activity.SetNumSessions(1);
    activity.SetType(Activity.Manual);
    activity.SetEvent(Event.Activity);
    activity.SetEventType(EventType.Stop);
    activity.SetLocalTimestamp(
      new Dynastream.Fit.DateTime(started.AddSeconds(finalSecond).UtcDateTime).GetTimeStamp() + 7_200u);
    encoder.Write(activity);
    encoder.Close();
    return stream.ToArray();
  }

  private static EventMesg WatchTimerEvent(DateTimeOffset timestamp, EventType eventType)
  {
    var message = new EventMesg();
    message.SetTimestamp(new Dynastream.Fit.DateTime(timestamp.UtcDateTime));
    message.SetEvent(Event.Timer);
    message.SetEventType(eventType);
    return message;
  }

  private static SessionSample WithUnsafeHeartRate(SessionSample sample, ushort heartRate)
  {
    System.Reflection.FieldInfo? field = typeof(SessionSample).GetField(
      "<HeartRateBpm>k__BackingField",
      System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);
    Assert.NotNull(field);
    field!.SetValue(sample, (ushort?)heartRate);
    return sample;
  }

  private static SessionSample WithoutHeartRate(SessionSample sample) => new(
    sample.SessionId,
    sample.Sequence,
    sample.CapturedAt,
    sample.Elapsed,
    sample.PlannedSpeedKph,
    sample.RequestedSpeedKph,
    sample.MeasuredSpeedKph,
    sample.PlannedInclinePercent,
    sample.RequestedInclinePercent,
    sample.MeasuredInclinePercent,
    null,
    sample.DistanceKilometers,
    sample.EstimatedKilocalories,
    sample.TelemetryAge,
    sample.MetricAlgorithmVersion);

  private static void AssertTimerEvent(EventMesg message, EventType eventType, DateTimeOffset timestamp)
  {
    Assert.Equal(Event.Timer, message.GetEvent());
    Assert.Equal(eventType, message.GetEventType());
    Assert.Equal(timestamp.UtcDateTime, message.GetTimestamp()?.GetDateTime());
  }

  private static IReadOnlyList<EventMesg> DecodeTimerEvents(byte[] fit)
  {
    using var stream = new MemoryStream(fit);
    var decoder = new Decode();
    var events = new List<EventMesg>();
    var broadcaster = new MesgBroadcaster();
    broadcaster.EventMesgEvent += (_, args) =>
    {
      var message = new EventMesg(args.mesg);
      if (message.GetEvent() == Event.Timer) events.Add(message);
    };
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;
    Assert.True(decoder.Read(stream));
    return events;
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
    lap.SetAvgCadence(88);
    lap.SetMaxCadence(90);
    lap.SetAvgPower(220);
    lap.SetMaxPower(300);
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

  private static StoredWorkoutSession PausedSession()
  {
    StoredWorkoutSession source = Session();
    DateTimeOffset started = source.StartedAt!.Value;
    Guid id = source.Definition.SessionId;
    SessionSample[] samples =
    [
      Sample(id, 0, started, 0, 0, 0, 135, 0, 0),
      Sample(id, 1, started.AddSeconds(3), 3, 8, 1, 150, 0.01, 0.5),
      Sample(id, 2, started.AddSeconds(7), 4, 4, 0, 120, 0.02, 0.75),
      Sample(id, 3, started.AddSeconds(12), 8, 4, 0, 120, 0.03, 1),
    ];
    return source with
    {
      EndedAt = started.AddSeconds(12),
      Duration = TimeSpan.FromSeconds(8),
      DistanceKilometers = samples[^1].DistanceKilometers,
      EstimatedKilocalories = samples[^1].EstimatedKilocalories,
      Samples = samples,
      Events =
      [
        new SessionPausedEvent(SessionPauseReason.PhysicalConsole, started.AddSeconds(3)),
        new SessionResumedEvent(started.AddSeconds(7)),
      ],
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
