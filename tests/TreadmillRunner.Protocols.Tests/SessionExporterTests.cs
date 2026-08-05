using System.Text;
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
    Assert.Contains(",1.2,1.2,1.2,", text, StringComparison.Ordinal);
    Assert.Contains(",135,0.000333", text, StringComparison.Ordinal);
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
    var broadcaster = new MesgBroadcaster();
    broadcaster.FileIdMesgEvent += (_, args) => fileType = ((FileIdMesg)args.mesg).GetType();
    decoder.MesgEvent += broadcaster.OnMesg;
    decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;
    Assert.True(decoder.Read(stream));
    Assert.Equal(Dynastream.Fit.File.Activity, fileType);
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
      "{}",
      "v1");
    var sample = new SessionSample(
      id,
      0,
      started.AddSeconds(1),
      TimeSpan.FromSeconds(1),
      1.2,
      1.2,
      1.2,
      1,
      1,
      1,
      135,
      0.000333,
      0.2,
      TimeSpan.FromMilliseconds(10),
      "v1");
    return new StoredWorkoutSession(
      definition,
      SessionState.Completed,
      started,
      started.AddSeconds(1),
      TimeSpan.FromSeconds(1),
      sample.DistanceKilometers,
      sample.EstimatedKilocalories,
      135,
      135,
      1.2,
      1,
      null,
      [sample],
      []);
  }
}
