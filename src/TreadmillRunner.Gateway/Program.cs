using TreadmillRunner.Gateway.Components;
using TreadmillRunner.Gateway.Diagnostics;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Gateway.Health;
using TreadmillRunner.Gateway.Hubs;
using TreadmillRunner.Gateway.Live;
using TreadmillRunner.Gateway.Planning;
using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Infrastructure.Bluetooth;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Imports;
using TreadmillRunner.Protocols.Omega;
using TreadmillRunner.Core.Control;
using System.Globalization;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Gateway.Updates;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Gateway.Household;
using Microsoft.AspNetCore.DataProtection;
using TreadmillRunner.Core.System;
using TreadmillRunner.Web;
using TreadmillRunner.Gateway.Hosting;
using TreadmillRunner.Gateway.Security;

var builder = WebApplication.CreateBuilder(new WebApplicationOptions
{
  Args = args,
  ContentRootPath = AppContext.BaseDirectory,
});

string? commissioningMode = builder.Configuration["Commissioning:Mode"];
bool commissionFtmsCommand = string.Equals(
  commissioningMode,
  "FtmsCommand",
  StringComparison.OrdinalIgnoreCase);
bool commissionFtmsStartStop = string.Equals(
  commissioningMode,
  "FtmsStartStop",
  StringComparison.OrdinalIgnoreCase);
bool commissionFtmsDailySequence = string.Equals(
  commissioningMode,
  "FtmsDailyControlSequence",
  StringComparison.OrdinalIgnoreCase);
bool commissioning = commissionFtmsCommand || commissionFtmsStartStop || commissionFtmsDailySequence;

builder.Host.UseWindowsService();

var listenUrls = builder.Configuration["Gateway:Urls"];
if (!GatewayListenerConfiguration.HasStructuredKestrelEndpoints(builder.Configuration) &&
    !string.IsNullOrWhiteSpace(listenUrls))
{
  builder.WebHost.UseUrls(listenUrls);
}
if (commissioning)
{
  builder.WebHost.UseUrls("http://127.0.0.1:0");
}
builder.Services.AddTreadmillRunnerServices(builder.Configuration);

var app = builder.Build();
DateTimeOffset serviceStartedAtUtc = DateTimeOffset.UtcNow;

app.UseResponseCompression();
app.UseTreadmillRunnerRequestTelemetry();
app.UseOptionalOperatorAccess();
app.UseClientBuildContract();
app.UseTreadmillRunnerNoStorePolicy();
app.UseMaintenanceMutationGate();

// Configure the HTTP request pipeline.
if (app.Environment.IsDevelopment())
{
  app.UseWebAssemblyDebugging();
}
else
{
  app.UseExceptionHandler("/Error", createScopeForErrors: true);
}
app.UseStatusCodePagesWithReExecute("/not-found", createScopeForStatusCodePages: true);
app.UseAntiforgery();

app.MapStaticAssets();
app.MapHealthChecks("/health/live", new Microsoft.AspNetCore.Diagnostics.HealthChecks.HealthCheckOptions
{
  Predicate = static _ => false,
});
app.MapHealthChecks("/health/ready", new Microsoft.AspNetCore.Diagnostics.HealthChecks.HealthCheckOptions
{
  Predicate = static check => check.Tags.Contains("ready", StringComparer.Ordinal),
});
app.MapHealthChecks("/health/ble", new Microsoft.AspNetCore.Diagnostics.HealthChecks.HealthCheckOptions
{
  Predicate = static check => check.Tags.Contains("ble", StringComparer.Ordinal),
});
app.MapGet("/api/live/snapshot", static (ILiveSnapshotSource source) => TypedResults.Ok(source.Current));
app.MapGet("/api/system/version", () => Results.Ok(new
{
  releaseVersion = AppBuildInfo.ReleaseVersion,
  buildFingerprint = AppBuildInfo.Fingerprint,
  serviceStartedAtUtc,
})).DisableHttpMetrics();
app.MapOperationalTelemetry();
app.MapOperatorAccess();
app.MapBleDiagnostics();
app.MapDeviceEnrollments();
app.MapTreadmillMaintenance();
app.MapProfilePlanning();
app.MapLocalFirstExperience();
app.MapWorkoutPlanning();
app.MapWorkoutSetPlanning();
app.MapCalendarPlanning();
app.MapWorkoutPrograms();
app.MapPremadePlans();
app.MapLiveSessions(includeSimulatorRoutes: app.Environment.IsDevelopment());
app.MapDataRecovery();
app.MapDatabaseIntegrity();
app.MapOperationsDashboard();
app.MapUpdates();
app.MapGarmin();
app.MapGarminWatch();
app.MapGarminActivityUpload();
app.MapHub<LiveHub>("/hubs/live");
app.MapRazorComponents<App>()
    .AddInteractiveWebAssemblyRenderMode()
    .AddAdditionalAssemblies(
      typeof(TreadmillRunner.Web._Imports).Assembly,
      typeof(TreadmillRunner.Web.Operations.OperationsAssemblyMarker).Assembly);

if (commissionFtmsDailySequence)
{
  Guid sequenceOperationId = Guid.Parse(builder.Configuration["Commissioning:OperationId"]
    ?? throw new InvalidOperationException("Commissioning:OperationId is required."));
  string observer = builder.Configuration["Commissioning:Observer"]
    ?? throw new InvalidOperationException("Commissioning:Observer is required.");
  string expectedModel = builder.Configuration["Commissioning:ExpectedModel"]
    ?? throw new InvalidOperationException("Commissioning:ExpectedModel is required.");
  string expectedFirmware = builder.Configuration["Commissioning:ExpectedFirmware"]
    ?? throw new InvalidOperationException("Commissioning:ExpectedFirmware is required.");
  using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(120));
  await app.StartAsync(timeout.Token);
  try
  {
    FtmsDailyControlSequenceOutcome outcome = await app.Services
      .GetRequiredService<FtmsDailyControlSequenceRunner>()
      .RunAsync(new FtmsDailyControlSequenceRequest(
        sequenceOperationId,
        expectedModel,
        expectedFirmware,
        observer), timeout.Token);
    Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(outcome));
    Environment.ExitCode = outcome.SafetyStopSent &&
      outcome.Steps.All(static step => step.Outcome.Disposition == TreadmillCommandDisposition.Confirmed)
        ? 0
        : outcome.Steps.Any(static step => step.Outcome.Disposition == TreadmillCommandDisposition.Unknown)
          ? 3
          : 2;
  }
  finally
  {
    using var shutdown = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    await app.StopAsync(shutdown.Token);
  }
  return;
}

if (commissionFtmsStartStop)
{
  Guid startOperationId = Guid.Parse(builder.Configuration["Commissioning:OperationId"]
    ?? throw new InvalidOperationException("Commissioning:OperationId is required."));
  Guid stopOperationId = Guid.Parse(builder.Configuration["Commissioning:StopOperationId"]
    ?? throw new InvalidOperationException("Commissioning:StopOperationId is required."));
  string observer = builder.Configuration["Commissioning:Observer"]
    ?? throw new InvalidOperationException("Commissioning:Observer is required.");
  string expectedModel = builder.Configuration["Commissioning:ExpectedModel"]
    ?? throw new InvalidOperationException("Commissioning:ExpectedModel is required.");
  string expectedFirmware = builder.Configuration["Commissioning:ExpectedFirmware"]
    ?? throw new InvalidOperationException("Commissioning:ExpectedFirmware is required.");
  using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(75));
  await app.StartAsync(timeout.Token);
  try
  {
    FtmsStartStopCommissioningOutcome outcome = await app.Services
      .GetRequiredService<FtmsStartStopCommissioningRunner>()
      .RunAsync(new FtmsStartStopCommissioningRequest(
        startOperationId,
        stopOperationId,
        expectedModel,
        expectedFirmware,
        observer), timeout.Token);
    Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(outcome));
    Environment.ExitCode = outcome.Stop is { Disposition: TreadmillCommandDisposition.Confirmed, CapabilityPromoted: true }
      ? 0
      : outcome.Start.Disposition == TreadmillCommandDisposition.Unknown ||
        outcome.Stop?.Disposition == TreadmillCommandDisposition.Unknown
        ? 3
        : 2;
  }
  finally
  {
    using var shutdown = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    await app.StopAsync(shutdown.Token);
  }
  return;
}

if (commissionFtmsCommand)
{
  Guid operationId = Guid.Parse(builder.Configuration["Commissioning:OperationId"]
    ?? throw new InvalidOperationException("Commissioning:OperationId is required."));
  TreadmillCommandKind kind = Enum.Parse<TreadmillCommandKind>(
    builder.Configuration["Commissioning:Command"]
      ?? throw new InvalidOperationException("Commissioning:Command is required."),
    ignoreCase: true);
  string observer = builder.Configuration["Commissioning:Observer"]
    ?? throw new InvalidOperationException("Commissioning:Observer is required.");
  string expectedModel = builder.Configuration["Commissioning:ExpectedModel"]
    ?? throw new InvalidOperationException("Commissioning:ExpectedModel is required.");
  string expectedFirmware = builder.Configuration["Commissioning:ExpectedFirmware"]
    ?? throw new InvalidOperationException("Commissioning:ExpectedFirmware is required.");
  double? requestedValue = double.TryParse(
    builder.Configuration["Commissioning:Target"],
    NumberStyles.Float,
    CultureInfo.InvariantCulture,
    out double parsedTarget)
      ? parsedTarget
      : null;
  using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(45));
  await app.StartAsync(timeout.Token);
  try
  {
    FtmsCommandCommissioningOutcome outcome = await app.Services
      .GetRequiredService<FtmsCommandCommissioningRunner>()
      .RunAsync(new FtmsCommandCommissioningRequest(
        operationId,
        kind,
        expectedModel,
        expectedFirmware,
        observer,
        requestedValue), timeout.Token);
    Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(outcome));
    Environment.ExitCode = outcome.Disposition switch
    {
      TreadmillCommandDisposition.Confirmed when outcome.CapabilityPromoted => 0,
      TreadmillCommandDisposition.Unknown => 3,
      _ => 2,
    };
  }
  finally
  {
    using var shutdown = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    await app.StopAsync(shutdown.Token);
  }
  return;
}

app.Run();

namespace TreadmillRunner.Gateway
{
  public partial class Program;
}
