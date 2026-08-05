using System.IO.Compression;
using System.Net;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace TreadmillRunner.IntegrationTests;

public sealed class WorkoutSetImportEndpointTests(PlanningGatewayFactory factory)
  : IClassFixture<PlanningGatewayFactory>
{
  [Fact]
  public async Task Preview_and_confirm_create_one_atomic_ordered_program()
  {
    using HttpClient client = factory.CreateClient();
    string planName = $"Generated {Guid.NewGuid():N}";
    byte[] bundle = CreateBundle(planName);
    using var form = new MultipartFormDataContent();
    using var bytes = new ByteArrayContent(bundle);
    bytes.Headers.ContentType = new("application/zip");
    form.Add(bytes, "file", "generated.zip");

    using HttpResponseMessage previewResponse = await client.PostAsync(
      "/api/planning/workout-sets/import/preview", form);
    Assert.Equal(HttpStatusCode.OK, previewResponse.StatusCode);
    JsonElement preview = await previewResponse.Content.ReadFromJsonAsync<JsonElement>();
    Assert.Equal(2, preview.GetProperty("slotCount").GetInt32());
    Assert.Equal(3, preview.GetProperty("variantCount").GetInt32());

    Guid operationId = Guid.NewGuid();
    var confirmation = new
    {
      operationId,
      previewId = preview.GetProperty("previewId").GetGuid(),
      sourceSha256 = preview.GetProperty("sourceSha256").GetString(),
      profileId = (Guid?)null,
      selectionStrategy = "PreferHeartRate",
    };
    using HttpResponseMessage confirmResponse = await client.PostAsJsonAsync(
      "/api/planning/workout-sets/import/confirm", confirmation);
    Assert.Equal(HttpStatusCode.Created, confirmResponse.StatusCode);
    JsonElement confirmed = await confirmResponse.Content.ReadFromJsonAsync<JsonElement>();
    Assert.Equal(2, confirmed.GetProperty("workoutCount").GetInt32());
    Assert.Equal("PreferHeartRate", confirmed.GetProperty("strategy").GetString());

    JsonElement[] programs = (await client.GetFromJsonAsync<JsonElement[]>("/api/planning/programs"))!;
    JsonElement program = Assert.Single(programs, item =>
      item.GetProperty("id").GetGuid() == confirmed.GetProperty("workoutProgramId").GetGuid());
    Assert.Equal(planName, program.GetProperty("name").GetString());
    Assert.Equal(2, program.GetProperty("items").GetArrayLength());

    using HttpResponseMessage replay = await client.PostAsJsonAsync(
      "/api/planning/workout-sets/import/confirm", confirmation);
    Assert.Equal(HttpStatusCode.OK, replay.StatusCode);
  }

  [Fact]
  public async Task Confirmation_with_changed_hash_is_rejected_without_creating_program()
  {
    using HttpClient client = factory.CreateClient();
    string planName = $"Rejected {Guid.NewGuid():N}";
    using var form = new MultipartFormDataContent();
    using var bytes = new ByteArrayContent(CreateBundle(planName));
    form.Add(bytes, "file", "generated.zip");
    using HttpResponseMessage previewResponse = await client.PostAsync(
      "/api/planning/workout-sets/import/preview", form);
    JsonElement preview = await previewResponse.Content.ReadFromJsonAsync<JsonElement>();

    using HttpResponseMessage rejected = await client.PostAsJsonAsync(
      "/api/planning/workout-sets/import/confirm",
      new
      {
        operationId = Guid.NewGuid(),
        previewId = preview.GetProperty("previewId").GetGuid(),
        sourceSha256 = new string('0', 64),
        profileId = (Guid?)null,
        selectionStrategy = "Default",
      });
    Assert.Equal(HttpStatusCode.Conflict, rejected.StatusCode);
    JsonElement[] programs = (await client.GetFromJsonAsync<JsonElement[]>("/api/planning/programs"))!;
    Assert.DoesNotContain(programs, item => item.GetProperty("name").GetString() == planName);
  }

  private static byte[] CreateBundle(string planName)
  {
    const string prefix = "treadmill/horizon-omega-z-dark/sessions/";
    var files = new Dictionary<string, byte[]>(StringComparer.Ordinal)
    {
      ["config.json"] = Utf8(JsonSerializer.Serialize(new { plan_name = planName, preset_id = "5K" })),
      [$"{prefix}W01D1_Easy.xml"] = Xml(4.5),
      [$"{prefix}W01D1H_Easy.xml"] = Xml(5.0, true),
      [$"{prefix}W01D2_Steady.xml"] = Xml(6.0),
    };
    files["workout_index.csv"] = Utf8(string.Join('\n',
      "canonical_slot,session_id,variant,intended_control_mode,week,session,title,horizon_omega_z_file,perform_exactly_one_variant,alternative_of,selection_rule",
      $"W01D1,W01D1,primary,fixed-speed,1,1,Easy,{prefix}W01D1_Easy.xml,true,,Default schedule choice",
      $"W01D1,W01D1H,hr-alternative,heart-rate,1,1,Easy HR,{prefix}W01D1H_Easy.xml,true,W01D1,Use instead of W01D1",
      $"W01D2,W01D2,primary,fixed-speed,1,2,Steady,{prefix}W01D2_Steady.xml,true,,Default schedule choice",
      string.Empty));
    var artifacts = files.ToDictionary(
      static item => item.Key,
      static item => Convert.ToHexStringLower(SHA256.HashData(item.Value)),
      StringComparer.Ordinal);
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
      foreach ((string path, byte[] content) in files)
      {
        ZipArchiveEntry entry = archive.CreateEntry(path, CompressionLevel.NoCompression);
        using Stream target = entry.Open();
        target.Write(content);
      }
      ZipArchiveEntry manifestEntry = archive.CreateEntry("manifest.json", CompressionLevel.NoCompression);
      using Stream targetManifest = manifestEntry.Open();
      targetManifest.Write(manifest);
    }
    return output.ToArray();
  }

  private static byte[] Xml(double speed, bool hr = false) => Utf8(hr
    ? $"<rows device=\"treadmill\"><row duration=\"00:01:00\" zonehr=\"2\" minspeed=\"4.0\" maxspeed=\"8.0\" speed=\"{speed.ToString(System.Globalization.CultureInfo.InvariantCulture)}\" inclination=\"1.0\" /></rows>"
    : $"<rows device=\"treadmill\"><row duration=\"00:01:00\" speed=\"{speed.ToString(System.Globalization.CultureInfo.InvariantCulture)}\" inclination=\"1.0\" /></rows>");
  private static byte[] Utf8(string value) => Encoding.UTF8.GetBytes(value);
}
