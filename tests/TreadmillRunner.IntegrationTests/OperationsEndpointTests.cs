using System.IO.Compression;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.DependencyInjection;
using TreadmillRunner.Gateway.Operations;

namespace TreadmillRunner.IntegrationTests;

public sealed class OperationsEndpointTests(PlanningGatewayFactory factory) :
  IClassFixture<PlanningGatewayFactory>
{
  [Fact]
  public async Task Idle_backup_diagnostics_and_update_status_are_bounded_and_available()
  {
    using HttpClient client = factory.CreateClient();
    using (HttpResponseMessage reset = await client.PostAsJsonAsync("/api/live/simulator/reset", new { }))
      Assert.Equal(HttpStatusCode.NoContent, reset.StatusCode);

    byte[] backup = await client.GetByteArrayAsync("/api/operations/backup");
    Assert.True(backup.Length > 16);
    Assert.Equal("SQLite format 3\0"u8.ToArray(), backup[..16]);

    using HttpResponseMessage diagnosticResponse = await client.GetAsync("/api/operations/diagnostics");
    Assert.Contains("no-store", diagnosticResponse.Headers.CacheControl?.ToString() ?? string.Empty, StringComparison.OrdinalIgnoreCase);
    byte[] diagnostics = await diagnosticResponse.Content.ReadAsByteArrayAsync();
    Assert.InRange(diagnostics.Length, 1, 5 * 1024 * 1024);
    using var archive = new ZipArchive(new MemoryStream(diagnostics), ZipArchiveMode.Read);
    ZipArchiveEntry entry = Assert.Single(archive.Entries);
    Assert.Equal("diagnostics.json", entry.FullName);
    using JsonDocument payload = await JsonDocument.ParseAsync(entry.Open());
    Assert.Equal(2, payload.RootElement.GetProperty("schemaVersion").GetInt32());

    using HttpResponseMessage verifiedBackupResponse = await client.GetAsync("/api/operations/database/verified-backup");
    Assert.Equal(HttpStatusCode.OK, verifiedBackupResponse.StatusCode);
    Assert.Contains("no-store", verifiedBackupResponse.Headers.CacheControl?.ToString() ?? string.Empty, StringComparison.OrdinalIgnoreCase);
    byte[] verifiedBackup = await verifiedBackupResponse.Content.ReadAsByteArrayAsync();
    Assert.True(verifiedBackup.Length > 16);
    Assert.Equal("SQLite format 3\0"u8.ToArray(), verifiedBackup[..16]);

    using HttpResponseMessage status = await client.GetAsync("/api/updates/status");
    Assert.Equal(HttpStatusCode.OK, status.StatusCode);
  }

  [Fact]
  public async Task Combined_operations_summary_uses_displayable_health_state_names()
  {
    using HttpClient client = factory.CreateClient();

    using HttpResponseMessage response = await client.GetAsync("/api/local-first/operations-summary");
    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    JsonElement summary = await response.Content.ReadFromJsonAsync<JsonElement>();

    Assert.Contains(summary.GetProperty("state").GetString(), new[] { "Healthy", "Degraded", "ActionRequired" });
    JsonElement[] components = summary.GetProperty("components").EnumerateArray().ToArray();
    Assert.NotEmpty(components);
    Assert.All(components, component =>
      Assert.Contains(component.GetProperty("state").GetString(), new[] { "Healthy", "Degraded", "ActionRequired" }));
  }

  [Fact]
  public async Task Operations_dashboard_aggregates_route_critical_read_models()
  {
    using HttpClient client = factory.CreateClient();

    using HttpResponseMessage response = await client.GetAsync("/api/operations/dashboard");
    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    JsonElement dashboard = await response.Content.ReadFromJsonAsync<JsonElement>();

    Assert.Contains(
      dashboard.GetProperty("updateStatus").GetProperty("state").GetString(),
      Enum.GetNames<TreadmillRunner.Gateway.Updates.UpdateLifecycleState>());
    Assert.True(dashboard.GetProperty("access").GetProperty("available").GetBoolean());
    Assert.Contains(
      dashboard.GetProperty("databaseStatus").GetProperty("state").GetString(),
      new[] { "Healthy", "HealthyWithBackupWarning" });
    Assert.Equal(JsonValueKind.Array, dashboard.GetProperty("backupVerifications").ValueKind);
    Assert.Contains(
      dashboard.GetProperty("operationsSummary").GetProperty("state").GetString(),
      new[] { "Healthy", "Degraded", "ActionRequired" });
  }

  [Fact]
  public async Task Restore_requires_preview_and_exact_confirmation_then_consumes_token()
  {
    using HttpClient client = factory.CreateClient();
    using (HttpResponseMessage reset = await client.PostAsJsonAsync("/api/live/simulator/reset", new { }))
      Assert.Equal(HttpStatusCode.NoContent, reset.StatusCode);

    byte[] backup = await client.GetByteArrayAsync("/api/operations/backup");
    using var upload = new ByteArrayContent(backup);
    upload.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("application/vnd.sqlite3");
    using HttpResponseMessage previewResponse = await client.PostAsync("/api/operations/restore/preview", upload);
    Assert.True(
      previewResponse.StatusCode == HttpStatusCode.OK,
      $"Restore preview failed with {previewResponse.StatusCode}: {await previewResponse.Content.ReadAsStringAsync()}");
    JsonElement preview = await previewResponse.Content.ReadFromJsonAsync<JsonElement>();
    Guid token = preview.GetProperty("token").GetGuid();
    Assert.Equal(backup.Length, preview.GetProperty("sizeBytes").GetInt64());
    Assert.Equal(64, preview.GetProperty("sha256").GetString()!.Length);

    using HttpResponseMessage rejected = await client.PostAsJsonAsync(
      "/api/operations/restore/confirm",
      new { token, confirmation = "restore" });
    Assert.Equal(HttpStatusCode.BadRequest, rejected.StatusCode);

    IApplicationMaintenanceState maintenance = factory.Services.GetRequiredService<IApplicationMaintenanceState>();
    Assert.True(maintenance.TryBeginMutation());
    try
    {
      using HttpResponseMessage busy = await client.PostAsJsonAsync(
        "/api/operations/restore/confirm",
        new { token, confirmation = "RESTORE" });
      Assert.Equal(HttpStatusCode.Conflict, busy.StatusCode);
    }
    finally
    {
      maintenance.EndMutation();
    }

    using HttpResponseMessage restored = await client.PostAsJsonAsync(
      "/api/operations/restore/confirm",
      new { token, confirmation = "RESTORE" });
    Assert.True(
      restored.StatusCode == HttpStatusCode.OK,
      $"Restore failed with {restored.StatusCode}: {await restored.Content.ReadAsStringAsync()}");
    JsonElement restoredPayload = await restored.Content.ReadFromJsonAsync<JsonElement>();
    Assert.True(restoredPayload.GetProperty("restored").GetBoolean());
    Assert.Contains(
      restoredPayload.GetProperty("databaseIntegrity").GetProperty("state").GetString(),
      new[] { "Healthy", "HealthyWithBackupWarning" });

    using HttpResponseMessage replay = await client.PostAsJsonAsync(
      "/api/operations/restore/confirm",
      new { token, confirmation = "RESTORE" });
    Assert.Equal(HttpStatusCode.NotFound, replay.StatusCode);
  }

  [Fact]
  public async Task Update_mutations_fail_closed_when_feed_is_not_configured_or_confirmation_is_wrong()
  {
    using HttpClient client = factory.CreateClient();
    using (HttpResponseMessage reset = await client.PostAsJsonAsync("/api/live/simulator/reset", new { }))
      Assert.Equal(HttpStatusCode.NoContent, reset.StatusCode);

    using HttpResponseMessage check = await client.PostAsJsonAsync("/api/updates/check", new { });
    Assert.Equal(HttpStatusCode.ServiceUnavailable, check.StatusCode);
    using HttpResponseMessage stage = await client.PostAsJsonAsync("/api/updates/stage", new { expectedVersion = "2.0.0" });
    Assert.Equal(HttpStatusCode.ServiceUnavailable, stage.StatusCode);
    using var invalidUpload = new ByteArrayContent("not-a-signed-bundle"u8.ToArray());
    using HttpResponseMessage upload = await client.PostAsync("/api/updates/upload", invalidUpload);
    Assert.Equal(HttpStatusCode.BadRequest, upload.StatusCode);
    using HttpResponseMessage badConfirmation = await client.PostAsJsonAsync(
      "/api/updates/activate",
      new { confirmation = "activate", expectedVersion = "2.0.0" });
    Assert.Equal(HttpStatusCode.BadRequest, badConfirmation.StatusCode);
    using HttpResponseMessage noStage = await client.PostAsJsonAsync(
      "/api/updates/activate",
      new { confirmation = "ACTIVATE", expectedVersion = "2.0.0" });
    Assert.Equal(HttpStatusCode.ServiceUnavailable, noStage.StatusCode);
  }
}
