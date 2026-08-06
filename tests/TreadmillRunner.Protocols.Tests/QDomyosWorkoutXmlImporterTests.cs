using System.Text;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Protocols.Imports;

namespace TreadmillRunner.Protocols.Tests;

public sealed class QDomyosWorkoutXmlImporterTests
{
  private readonly QDomyosWorkoutXmlImporter importer = new();

  [Fact]
  public async Task Imports_repeat_ramp_hr_and_text_from_treadmill_xml()
  {
    const string xml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rows device="treadmill">
          <row duration="00:05:00" speedfrom="5.0" speedto="8.0" inclination="1" forcespeed="1">
            <textevent timeoffset="5" message="Relax your shoulders" />
          </row>
          <repeat times="3">
            <row duration="00:01:30" zonehr="4" minspeed="10.0" maxspeed="14.0" looptimehr="5" />
            <row distance="0.4" hrmin="125" hrmax="135" />
          </repeat>
        </rows>
        """;

    WorkoutImportResult result = await ImportAsync(xml);

    Assert.Equal("intervals", result.Definition.Title);
    Assert.Equal(7, result.ExpandedStepCount);
    Assert.Null(result.TotalDuration);
    WorkoutStep ramp = Assert.IsType<WorkoutStep>(result.Definition.Blocks[0]);
    SpeedRamp rampSpeed = Assert.IsType<SpeedRamp>(ramp.Speed);
    Assert.Equal(5, rampSpeed.StartKilometersPerHour);
    Assert.Equal(8, rampSpeed.EndKilometersPerHour);
    Assert.Equal("Relax your shoulders", ramp.Cue);
    WorkoutRepeat repeat = Assert.IsType<WorkoutRepeat>(result.Definition.Blocks[1]);
    WorkoutStep hr = Assert.IsType<WorkoutStep>(repeat.Blocks[0]);
    HeartRateZoneSpeed zone = Assert.IsType<HeartRateZoneSpeed>(hr.Speed);
    Assert.Equal(4, zone.ZoneNumber);
    Assert.Equal(10, zone.MinimumKilometersPerHour);
    Assert.Equal(14, zone.MaximumKilometersPerHour);
    WorkoutStep distance = Assert.IsType<WorkoutStep>(repeat.Blocks[1]);
    Assert.Equal(0.4, Assert.IsType<DistanceGoal>(distance.Goal).Kilometers);
    Assert.Equal(125, Assert.IsType<HeartRateSpeed>(distance.Speed).MinimumBpm);
    Assert.Contains(result.Warnings, warning => warning.Code == "qdomyos.assumed-speed-units");
    Assert.Contains(result.Warnings, warning => warning.Code == "qdomyos.forcespeed-ignored");
    Assert.Contains(result.Warnings, warning => warning.Code == "qdomyos.hr-loop-ignored");
  }

  [Fact]
  public async Task Rejects_dtd_and_external_entity_documents()
  {
    const string xml = """
        <!DOCTYPE rows [ <!ENTITY xxe SYSTEM "file:///c:/windows/win.ini"> ]>
        <rows><row duration="00:01:00"><textevent message="&xxe;" /></row></rows>
        """;

    WorkoutImportException error = await Assert.ThrowsAsync<WorkoutImportException>(() => ImportAsync(xml));

    Assert.Contains("prohibited", error.Message, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Warns_for_non_treadmill_and_bike_fields_without_guessing()
  {
    const string xml = """
        <rows device="bike">
          <row duration="00:01:00" resistance="20" cadence="90" power="200" />
        </rows>
        """;

    WorkoutImportResult result = await ImportAsync(xml);

    Assert.Contains(result.Warnings, warning => warning.Code == "qdomyos.non-treadmill-device");
    Assert.Equal(3, result.Warnings.Count(warning => warning.Code == "qdomyos.unsupported-bike-field"));
    WorkoutStep step = Assert.IsType<WorkoutStep>(Assert.Single(result.Definition.Blocks));
    Assert.IsType<OpenSpeed>(step.Speed);
  }

  [Fact]
  public async Task Explicit_heart_rate_bounds_take_precedence_over_a_conflicting_zone()
  {
    const string xml = """
        <rows>
          <row duration="00:01:00" zonehr="4" hrmin="125" hrmax="135" minspeed="6" maxspeed="10" />
        </rows>
        """;

    WorkoutImportResult result = await ImportAsync(xml);

    WorkoutStep step = Assert.IsType<WorkoutStep>(Assert.Single(result.Definition.Blocks));
    HeartRateSpeed speed = Assert.IsType<HeartRateSpeed>(step.Speed);
    Assert.Equal((ushort)125, speed.MinimumBpm);
    Assert.Equal((ushort)135, speed.MaximumBpm);
    WorkoutImportWarning warning = Assert.Single(
        result.Warnings,
        warning => warning.Code == "qdomyos.conflicting-heart-rate-targets");
    Assert.Contains("explicit bounds were retained and the zone was ignored", warning.Message, StringComparison.Ordinal);
  }

  [Fact]
  public async Task Standalone_xml_keeps_explicit_speed_when_heart_rate_and_bounds_are_combined()
  {
    const string xml = """
        <rows device="treadmill">
          <row duration="00:05:00" speed="6" zonehr="2" minspeed="4" maxspeed="8" />
        </rows>
        """;

    WorkoutImportResult result = await ImportAsync(xml);

    WorkoutStep step = Assert.IsType<WorkoutStep>(Assert.Single(result.Definition.Blocks));
    Assert.Equal(6, Assert.IsType<FixedSpeed>(step.Speed).KilometersPerHour);
    Assert.Contains(result.Warnings, warning => warning.Code == "qdomyos.conflicting-speed-target");
    Assert.DoesNotContain(result.Warnings, warning => warning.Code.StartsWith("qdomyos.v4-", StringComparison.Ordinal));
  }

  [Fact]
  public async Task Rejects_corrupt_xml_and_repeat_bomb()
  {
    await Assert.ThrowsAsync<WorkoutImportException>(() => ImportAsync("<rows><row></rows>"));
    await Assert.ThrowsAsync<WorkoutImportException>(() => ImportAsync(
        "<rows><repeat times=\"10000\"><repeat times=\"2\"><row duration=\"00:00:01\" /></repeat></repeat></rows>"));
  }

  [Fact]
  public async Task File_size_limit_is_enforced_before_xml_parsing()
  {
    byte[] bytes = new byte[WorkoutImportLimits.MaximumBytes + 1];
    await using MemoryStream stream = new(bytes);

    WorkoutImportException error = await Assert.ThrowsAsync<WorkoutImportException>(
        async () => await importer.ImportAsync(stream, "large.xml"));

    Assert.Contains("10 MB", error.Message, StringComparison.Ordinal);
  }

  private async Task<WorkoutImportResult> ImportAsync(string text)
  {
    await using MemoryStream stream = new(Encoding.UTF8.GetBytes(text));
    return await importer.ImportAsync(stream, "intervals.xml");
  }
}
