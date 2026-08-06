using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Protocols.Imports;

namespace TreadmillRunner.Protocols.Tests;

public sealed class TreadmillWorkoutBundleImporterTests
{
  [Fact]
  public async Task Imports_v4_omega_bundle_and_selects_one_variant_per_slot()
  {
    byte[] source = CreateBundle();
    var importer = new TreadmillWorkoutBundleImporter();

    await using var stream = new MemoryStream(source, writable: false);
    TreadmillWorkoutBundle bundle = await importer.ImportAsync(stream, "five-k.zip");

    Assert.Equal("Household 5K", bundle.PlanName);
    Assert.Equal(2, bundle.Slots.Count);
    Assert.Equal(["primary", "primary"], bundle.Select(WorkoutSetSelectionStrategy.Default).Select(static item => item.Variant));
    Assert.Equal(["hr-alternative", "primary"], bundle.Select(WorkoutSetSelectionStrategy.PreferHeartRate).Select(static item => item.Variant));
    Assert.All(bundle.Select(WorkoutSetSelectionStrategy.PreferHeartRate), item => Assert.StartsWith(item.CanonicalSlot, item.Definition.Title));
    WorkoutStep adaptive = Assert.IsType<WorkoutStep>(bundle.Select(WorkoutSetSelectionStrategy.PreferHeartRate)[0].Definition.Blocks[0]);
    HeartRateZoneSpeed heartRate = Assert.IsType<HeartRateZoneSpeed>(adaptive.Speed);
    Assert.Equal(2, heartRate.ZoneNumber);
    Assert.Equal(5, heartRate.InitialKilometersPerHour);
    Assert.Equal(4, heartRate.MinimumKilometersPerHour);
    Assert.Equal(8, heartRate.MaximumKilometersPerHour);
  }

  [Fact]
  public async Task Rejects_manifest_hash_mismatch()
  {
    byte[] source = CreateBundle(tamperManifestHash: true);
    var importer = new TreadmillWorkoutBundleImporter();
    await using var stream = new MemoryStream(source, writable: false);

    WorkoutImportException exception = await Assert.ThrowsAsync<WorkoutImportException>(async () =>
      await importer.ImportAsync(stream, "set.zip"));

    Assert.Contains("digest mismatch", exception.Message, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Rejects_unsafe_zip_path()
  {
    byte[] source = CreateBundle(unsafeEntry: true);
    var importer = new TreadmillWorkoutBundleImporter();
    await using var stream = new MemoryStream(source, writable: false);

    WorkoutImportException exception = await Assert.ThrowsAsync<WorkoutImportException>(async () =>
      await importer.ImportAsync(stream, "set.zip"));

    Assert.Contains("unsafe", exception.Message, StringComparison.OrdinalIgnoreCase);
  }

  [Theory]
  [InlineData("duplicate-variant")]
  [InlineData("inconsistent-slot")]
  [InlineData("oversized-metadata")]
  [InlineData("duplicate-session")]
  public async Task Rejects_ambiguous_or_unbounded_index_metadata(string defect)
  {
    byte[] source = CreateBundle(indexDefect: defect);
    var importer = new TreadmillWorkoutBundleImporter();
    await using var stream = new MemoryStream(source, writable: false);

    await Assert.ThrowsAsync<WorkoutImportException>(async () =>
      await importer.ImportAsync(stream, "set.zip"));
  }

  [Fact]
  public async Task Rejects_wrong_config_value_types_as_validation_failure()
  {
    byte[] source = CreateBundle(malformedConfig: true);
    var importer = new TreadmillWorkoutBundleImporter();
    await using var stream = new MemoryStream(source, writable: false);

    WorkoutImportException exception = await Assert.ThrowsAsync<WorkoutImportException>(async () =>
      await importer.ImportAsync(stream, "set.zip"));

    Assert.Contains("config.json", exception.Message, StringComparison.OrdinalIgnoreCase);
  }

  private static byte[] CreateBundle(
    bool tamperManifestHash = false,
    bool unsafeEntry = false,
    string? indexDefect = null,
    bool malformedConfig = false)
  {
    const string prefix = "treadmill/horizon-omega-z-dark/sessions/";
    var files = new Dictionary<string, byte[]>(StringComparer.Ordinal)
    {
      ["config.json"] = Utf8(malformedConfig
        ? "{\"plan_name\":{},\"preset_id\":\"5k\"}"
        : "{\"plan_name\":\"Household 5K\",\"preset_id\":\"5k\"}"),
      [$"{prefix}W01D1_Easy.xml"] = Xml(4.5),
      [$"{prefix}W01D1H_Easy.xml"] = Xml(5.0, hr: true),
      [$"{prefix}W01D2_Steady.xml"] = Xml(6.0),
    };
    string primarySlot = indexDefect == "oversized-metadata" ? new string('S', 65) : "W01D1";
    string alternativeVariant = indexDefect == "duplicate-variant" ? "primary" : "hr-alternative";
    string alternativeSessionId = indexDefect == "duplicate-session" ? "W01D1" : "W01D1H";
    string alternativeSession = indexDefect == "inconsistent-slot" ? "2" : "1";
    files["workout_index.csv"] = Utf8(string.Join('\n',
      "canonical_slot,session_id,variant,intended_control_mode,week,session,title,horizon_omega_z_file,perform_exactly_one_variant,alternative_of,selection_rule",
      $"{primarySlot},W01D1,primary,fixed-speed,1,1,Easy,{prefix}W01D1_Easy.xml,true,,Default schedule choice",
      $"{primarySlot},{alternativeSessionId},{alternativeVariant},heart-rate,1,{alternativeSession},Easy HR,{prefix}W01D1H_Easy.xml,true,{primarySlot},Use instead of W01D1",
      $"W01D2,W01D2,primary,fixed-speed,1,2,Steady,{prefix}W01D2_Steady.xml,true,,Default schedule choice",
      string.Empty));
    if (unsafeEntry) files["../outside.txt"] = Utf8("unsafe");

    var artifacts = files.ToDictionary(
      static item => item.Key,
      static item => Convert.ToHexStringLower(SHA256.HashData(item.Value)),
      StringComparer.Ordinal);
    if (tamperManifestHash) artifacts[$"{prefix}W01D2_Steady.xml"] = new string('0', 64);
    byte[] manifest = JsonSerializer.SerializeToUtf8Bytes(new
    {
      format_version = 2,
      tool = "treadmill-workout",
      tool_version = "4.0.1",
      config_sha256 = new string('1', 64),
      compatibility_profile = "treadmill-multi-device-bundle-v4",
      device_profile_ids = new[] { "horizon-omega-z-dark-2023-ftms" },
      artifacts,
    });

    using var output = new MemoryStream();
    using (var archive = new ZipArchive(output, ZipArchiveMode.Create, leaveOpen: true))
    {
      foreach ((string path, byte[] contents) in files)
      {
        ZipArchiveEntry entry = archive.CreateEntry(path, CompressionLevel.NoCompression);
        using Stream target = entry.Open();
        target.Write(contents);
      }
      ZipArchiveEntry manifestEntry = archive.CreateEntry("manifest.json", CompressionLevel.NoCompression);
      using Stream manifestTarget = manifestEntry.Open();
      manifestTarget.Write(manifest);
    }
    return output.ToArray();
  }

  private static byte[] Xml(double speed, bool hr = false) => Utf8(hr
    ? $"<rows device=\"treadmill\"><row duration=\"00:01:00\" zonehr=\"2\" minspeed=\"4.0\" maxspeed=\"8.0\" speed=\"{speed.ToString(System.Globalization.CultureInfo.InvariantCulture)}\" inclination=\"1.0\" /></rows>"
    : $"<rows device=\"treadmill\"><row duration=\"00:01:00\" speed=\"{speed.ToString(System.Globalization.CultureInfo.InvariantCulture)}\" inclination=\"1.0\" /></rows>");

  private static byte[] Utf8(string value) => Encoding.UTF8.GetBytes(value);
}
