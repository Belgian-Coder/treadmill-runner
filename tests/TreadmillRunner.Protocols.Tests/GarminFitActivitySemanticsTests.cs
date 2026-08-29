using Dynastream.Fit;
using TreadmillRunner.Protocols.Exports;

namespace TreadmillRunner.Protocols.Tests;

public sealed class GarminFitActivitySemanticsTests
{
  [Fact]
  public void Metadata_and_header_rewrites_are_semantically_equivalent()
  {
    byte[] expected = EncodeActivity(new ActivityOptions());
    byte[] candidate = EncodeActivity(new ActivityOptions(
      ProtocolVersion.V10,
      Manufacturer.Development,
      99,
      987654,
      "other-device",
      150,
      2.5f,
      DeviceManufacturer: Manufacturer.Development,
      DeviceProduct: 99,
      DeviceName: "other-device",
      IncludeDeveloperData: false));

    Assert.NotEqual(expected, candidate);
    Assert.True(GarminFitActivitySemantics.AreEquivalent(expected, candidate));
  }

  [Theory]
  [InlineData("record-heart-rate")]
  [InlineData("record-speed")]
  [InlineData("record-distance")]
  [InlineData("session-summary")]
  [InlineData("timing")]
  public void Core_record_summary_and_timing_mutations_are_not_equivalent(string mutation)
  {
    ActivityOptions candidate = new();
    candidate = mutation switch
    {
      "record-heart-rate" => candidate with { HeartRate = 151 },
      "record-speed" => candidate with { SpeedMetersPerSecond = 2.51f },
      "record-distance" => candidate with { DistanceMeters = 125.5f },
      "session-summary" => candidate with { SessionDistanceMeters = 125.5f },
      "timing" => candidate with { EndedAt = candidate.EndedAt.AddSeconds(1) },
      _ => throw new ArgumentOutOfRangeException(nameof(mutation)),
    };

    Assert.False(GarminFitActivitySemantics.AreEquivalent(EncodeActivity(), EncodeActivity(candidate)));
  }

  [Fact]
  public void Missing_core_messages_are_not_equivalent()
  {
    byte[] expected = EncodeActivity();
    Assert.False(GarminFitActivitySemantics.AreEquivalent(expected, EncodeActivity(new ActivityOptions(OmitRecords: true))));
    Assert.False(GarminFitActivitySemantics.AreEquivalent(expected, EncodeActivity(new ActivityOptions(OmitSession: true))));
    Assert.False(GarminFitActivitySemantics.AreEquivalent(expected, EncodeActivity(new ActivityOptions(OmitActivity: true))));
  }

  [Fact]
  public void Repeated_array_value_counts_remain_semantic()
  {
    byte[] expected = EncodeActivity(new ActivityOptions(TimeInHeartRateZoneValueCount: 2));
    byte[] candidate = EncodeActivity(new ActivityOptions(TimeInHeartRateZoneValueCount: 1));

    Assert.False(GarminFitActivitySemantics.AreEquivalent(expected, candidate));
  }

  [Fact]
  public void Compressed_speed_distance_field_is_ignored_by_profile_number()
  {
    var record = new RecordMesg();
    record.SetCompressedSpeedDistance(0, 1);
    Field compressed = Assert.Single(record.Fields, field => field.Num == 8);
    Assert.Equal("CompressedSpeedDistance", compressed.Name);
    System.Reflection.MethodInfo? method = typeof(GarminFitActivitySemantics).GetMethod(
      "IsIgnoredField",
      System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static);
    Assert.NotNull(method);

    Assert.True(Assert.IsType<bool>(method.Invoke(null, [(ushort)MesgNum.Record, compressed])));
  }

  [Fact]
  public void Wrong_file_type_and_bad_crc_are_not_equivalent()
  {
    byte[] expected = EncodeActivity();
    byte[] wrongType = EncodeActivity(new ActivityOptions(FileType: Dynastream.Fit.File.Workout));
    Assert.False(GarminFitActivitySemantics.AreEquivalent(expected, wrongType));

    byte[] badCrc = (byte[])expected.Clone();
    badCrc[^1] ^= 0xFF;
    Assert.False(GarminFitActivitySemantics.AreEquivalent(expected, badCrc));
  }

  [Fact]
  public void Null_empty_oversized_and_non_fit_inputs_fail_closed()
  {
    byte[] valid = EncodeActivity();
    Assert.False(GarminFitActivitySemantics.AreEquivalent(valid, Array.Empty<byte>()));
    Assert.False(GarminFitActivitySemantics.AreEquivalent(valid, [1, 2, 3, 4]));
    Assert.False(GarminFitActivitySemantics.AreEquivalent(valid, new byte[(16 * 1024 * 1024) + 1]));
    Assert.False(GarminFitActivitySemantics.AreEquivalent(null!, valid));
  }

  private static byte[] EncodeActivity(ActivityOptions? options = null)
  {
    options ??= new ActivityOptions();
    using var stream = new MemoryStream();
    var encoder = new Encode(options.ProtocolVersion);
    encoder.Open(stream);
    DateTimeOffset startedAt = options.StartedAt == default
      ? new DateTimeOffset(2026, 8, 4, 20, 0, 0, TimeSpan.Zero)
      : options.StartedAt;
    DateTimeOffset endedAt = options.EndedAt == default
      ? startedAt.AddSeconds(2)
      : options.EndedAt;
    System.DateTime start = startedAt.UtcDateTime;

    var file = new FileIdMesg();
    file.SetType(options.FileType);
    file.SetManufacturer(options.Manufacturer);
    file.SetProduct(options.Product);
    file.SetSerialNumber(options.SerialNumber);
    file.SetTimeCreated(new Dynastream.Fit.DateTime(start));
    file.SetProductName(options.ProductName);
    encoder.Write(file);

    DeveloperDataIdMesg? developerData = null;
    FieldDescriptionMesg? fieldDescription = null;
    if (options.IncludeDeveloperData)
    {
      developerData = new DeveloperDataIdMesg();
      developerData.SetDeveloperDataIndex(0);
      developerData.SetManufacturerId(Manufacturer.Garmin);
      for (var index = 0; index < 16; index++) developerData.SetApplicationId(index, (byte)(index + 1));
      encoder.Write(developerData);

      fieldDescription = new FieldDescriptionMesg();
      fieldDescription.SetDeveloperDataIndex(0);
      fieldDescription.SetFieldDefinitionNumber(0);
      fieldDescription.SetFitBaseTypeId(FitBaseType.Float32);
      fieldDescription.SetFieldName(0, "watch_metric");
      fieldDescription.SetUnits(0, "score");
      encoder.Write(fieldDescription);
    }

    var device = new DeviceInfoMesg();
    device.SetTimestamp(new Dynastream.Fit.DateTime(start));
    device.SetDeviceIndex(0);
    device.SetManufacturer(options.DeviceManufacturer);
    device.SetProduct(options.DeviceProduct);
    device.SetProductName(options.DeviceName);
    encoder.Write(device);

    encoder.Write(TimerEvent(startedAt, EventType.Start));
    for (var index = 0; index < 2; index++)
    {
      if (options.OmitRecords) continue;
      var record = new RecordMesg();
      record.SetTimestamp(new Dynastream.Fit.DateTime(startedAt.AddSeconds(index).UtcDateTime));
      record.SetSpeed(options.SpeedMetersPerSecond);
      record.SetEnhancedSpeed(options.SpeedMetersPerSecond);
      record.SetDistance(index == 0 ? 0 : options.DistanceMeters);
      record.SetGrade(1.5f);
      record.SetAltitude(100.25f);
      record.SetEnhancedAltitude(100.25f);
      record.SetHeartRate(options.HeartRate);
      record.SetVerticalSpeed(0.25f);
      record.SetZone(2);
      record.SetCompressedSpeedDistance(index, (byte)(index + 1));
      if (options.IncludeDeveloperData)
      {
        var developerField = new DeveloperField(fieldDescription!, developerData!);
        developerField.SetValue(42f + index);
        record.SetDeveloperField(developerField);
      }
      encoder.Write(record);
    }
    encoder.Write(TimerEvent(endedAt, EventType.StopAll));

    if (!options.OmitSession)
    {
      var lap = new LapMesg();
      lap.SetTimestamp(new Dynastream.Fit.DateTime(endedAt.UtcDateTime));
      lap.SetStartTime(new Dynastream.Fit.DateTime(startedAt.UtcDateTime));
      lap.SetTotalElapsedTime((float)(endedAt - startedAt).TotalSeconds);
      lap.SetTotalTimerTime((float)(endedAt - startedAt).TotalSeconds);
      lap.SetTotalDistance(options.SessionDistanceMeters);
      lap.SetAvgSpeed(options.SpeedMetersPerSecond);
      lap.SetMaxSpeed(options.SpeedMetersPerSecond);
      lap.SetAvgHeartRate(options.HeartRate);
      lap.SetMaxHeartRate(options.HeartRate);
      lap.SetSport(Sport.Running);
      lap.SetSubSport(SubSport.Treadmill);
      encoder.Write(lap);

      var session = new SessionMesg();
      session.SetTimestamp(new Dynastream.Fit.DateTime(endedAt.UtcDateTime));
      session.SetStartTime(new Dynastream.Fit.DateTime(startedAt.UtcDateTime));
      session.SetTotalElapsedTime((float)(endedAt - startedAt).TotalSeconds);
      session.SetTotalTimerTime((float)(endedAt - startedAt).TotalSeconds);
      session.SetTotalDistance(options.SessionDistanceMeters);
      session.SetAvgSpeed(options.SpeedMetersPerSecond);
      session.SetMaxSpeed(options.SpeedMetersPerSecond);
      session.SetAvgHeartRate(options.HeartRate);
      session.SetMaxHeartRate(options.HeartRate);
      session.SetSport(Sport.Running);
      session.SetSubSport(SubSport.Treadmill);
      for (var index = 0; index < options.TimeInHeartRateZoneValueCount; index++)
        session.SetTimeInHrZone(index, 10);
      encoder.Write(session);
    }

    if (!options.OmitActivity)
    {
      var activity = new ActivityMesg();
      activity.SetTimestamp(new Dynastream.Fit.DateTime(endedAt.UtcDateTime));
      activity.SetTotalTimerTime((float)(endedAt - startedAt).TotalSeconds);
      activity.SetNumSessions(1);
      encoder.Write(activity);
    }

    encoder.Close();
    return stream.ToArray();
  }

  private static EventMesg TimerEvent(DateTimeOffset timestamp, EventType eventType)
  {
    var message = new EventMesg();
    message.SetTimestamp(new Dynastream.Fit.DateTime(timestamp.UtcDateTime));
    message.SetEvent(Event.Timer);
    message.SetEventType(eventType);
    return message;
  }

  private sealed record ActivityOptions(
    ProtocolVersion ProtocolVersion = ProtocolVersion.V20,
    ushort Manufacturer = Manufacturer.Garmin,
    ushort Product = 4242,
    uint SerialNumber = 123456,
    string ProductName = "fenix 8",
    byte HeartRate = 150,
    float SpeedMetersPerSecond = 2.5f,
    float DistanceMeters = 120,
    float SessionDistanceMeters = 120,
    DateTimeOffset StartedAt = default,
    DateTimeOffset EndedAt = default,
    Dynastream.Fit.File FileType = Dynastream.Fit.File.Activity,
    ushort DeviceManufacturer = Manufacturer.Garmin,
    ushort DeviceProduct = 4242,
    string DeviceName = "fenix 8",
    int TimeInHeartRateZoneValueCount = 0,
    bool OmitRecords = false,
    bool OmitSession = false,
    bool OmitActivity = false,
    bool IncludeDeveloperData = true);
}
