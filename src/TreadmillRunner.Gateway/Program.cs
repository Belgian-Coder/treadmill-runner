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

var builder = WebApplication.CreateBuilder(args);

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
if (!string.IsNullOrWhiteSpace(listenUrls))
{
  builder.WebHost.UseUrls(listenUrls);
}
if (commissioning)
{
  builder.WebHost.UseUrls("http://127.0.0.1:0");
}
builder.Services.AddRazorComponents()
    .AddInteractiveWebAssemblyComponents();
builder.Services.AddSignalR();
builder.Services.AddHealthChecks()
    .AddCheck<SimulatorReadyHealthCheck>("simulator-live", tags: ["ready"])
    .AddCheck<DatabaseReadyHealthCheck>("database", tags: ["ready"])
    .AddCheck<BleDiagnosticHealthCheck>("ble", tags: ["ble"]);

builder.Services.AddSingleton(TimeProvider.System);
string dataProtectionPath = builder.Configuration["Persistence:DataProtectionKeyPath"]
  ?? Path.Combine(AppContext.BaseDirectory, "data", "keys");
Directory.CreateDirectory(dataProtectionPath);
builder.Services.AddDataProtection()
  .SetApplicationName("TreadmillRunner")
  .PersistKeysToFileSystem(new DirectoryInfo(dataProtectionPath))
  .ProtectKeysWithDpapi();
builder.Services.AddSingleton<Microsoft.EntityFrameworkCore.IDbContextFactory<TreadmillRunnerDbContext>>(services =>
{
  IConfiguration configuration = services.GetRequiredService<IConfiguration>();
  string databasePath = configuration["Persistence:DatabasePath"]
    ?? Path.Combine(AppContext.BaseDirectory, "data", "treadmillrunner.db");
  string? databaseDirectory = Path.GetDirectoryName(Path.GetFullPath(databasePath));
  if (databaseDirectory is not null)
  {
    Directory.CreateDirectory(databaseDirectory);
  }

  return TreadmillRunnerDatabase.CreateFactory(databasePath);
});
builder.Services.AddSingleton<IDatabaseIntegrityChecker, DatabaseIntegrityChecker>();
builder.Services.AddSingleton<IVerifiedDatabaseBackupService, VerifiedDatabaseBackupService>();
builder.Services.AddSingleton<IDatabaseIntegrityStatusStore, DatabaseIntegrityStatusStore>();
builder.Services.AddSingleton<IDatabaseMaintenanceLeaseProvider, LiveSessionDatabaseMaintenanceLeaseProvider>();
builder.Services.AddSingleton<DatabaseIntegrityCoordinator>();
builder.Services.AddSingleton<IDatabaseIntegrityCoordinator>(static services =>
  services.GetRequiredService<DatabaseIntegrityCoordinator>());
// This startup pass intentionally runs before any background worker that writes the database.
builder.Services.AddHostedService(static services => services.GetRequiredService<DatabaseIntegrityCoordinator>());
builder.Services.AddScoped<IProfileStore, ProfileStore>();
builder.Services.AddScoped<ILocalFirstExperienceStore, LocalFirstExperienceStore>();
builder.Services.AddSingleton<LocalBackupWorker>();
builder.Services.AddSingleton<ILocalBackupCoordinator>(static services => services.GetRequiredService<LocalBackupWorker>());
builder.Services.AddHostedService(static services => services.GetRequiredService<LocalBackupWorker>());
builder.Services.AddScoped<IWorkoutStore, WorkoutStore>();
builder.Services.AddScoped<IWorkoutSetImportStore, WorkoutSetImportStore>();
builder.Services.AddScoped<ICalendarStore, CalendarStore>();
builder.Services.AddScoped<IWorkoutProgramStore, WorkoutProgramStore>();
builder.Services.AddScoped<IPremadePlanStore, PremadePlanStore>();
builder.Services.AddScoped<IOperationReceiptStore, OperationReceiptStore>();
builder.Services.AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
builder.Services.AddScoped<IBleReliabilityStore, BleReliabilityStore>();
builder.Services.AddScoped<ITreadmillMaintenanceStore, TreadmillMaintenanceStore>();
builder.Services.AddSingleton<IGarminStore, GarminStore>();
builder.Services.AddSingleton<IGarminWatchBindingStore, GarminWatchBindingStore>();
builder.Services.AddSingleton<IGarminActivityUploadStore, GarminActivityUploadStore>();
builder.Services.Configure<GarminActivityAdapterOptions>(builder.Configuration.GetSection(GarminActivityAdapterOptions.SectionName));
builder.Services.AddSingleton<PythonGarminActivityAdapter>();
builder.Services.AddSingleton<IGarminActivityAdapter>(static services => services.GetRequiredService<PythonGarminActivityAdapter>());
builder.Services.AddSingleton<IGarminActivityAdapterReadiness>(static services => services.GetRequiredService<PythonGarminActivityAdapter>());
builder.Services.AddSingleton<GarminActivityConnectionService>();
builder.Services.AddSingleton<GarminActivityUploadWorker>();
builder.Services.AddHostedService(static services => services.GetRequiredService<GarminActivityUploadWorker>());
builder.Services.Configure<GarminOptions>(builder.Configuration.GetSection(GarminOptions.SectionName));
builder.Services.AddSingleton<DisabledGarminProvider>();
builder.Services.AddSingleton<MockGarminProvider>();
builder.Services.AddSingleton<IGarminTrainingContractAdapter, UnavailableGarminTrainingContractAdapter>();
builder.Services.AddHttpClient("GarminConnect", (services, client) =>
{
  GarminOptions options = services.GetRequiredService<Microsoft.Extensions.Options.IOptionsMonitor<GarminOptions>>().CurrentValue;
  client.Timeout = TimeSpan.FromSeconds(Math.Clamp(options.RequestTimeoutSeconds, 5, 60));
});
builder.Services.AddSingleton<ConfiguredGarminProvider>();
builder.Services.AddSingleton<IGarminProvider>(services =>
  builder.Configuration[$"{GarminOptions.SectionName}:Provider"]?.Trim().ToUpperInvariant() switch
  {
    "MOCK" => services.GetRequiredService<MockGarminProvider>(),
    "CONFIGURED" => services.GetRequiredService<ConfiguredGarminProvider>(),
    _ => services.GetRequiredService<DisabledGarminProvider>(),
  });
builder.Services.AddScoped<GarminSyncCatalog>();
builder.Services.AddScoped<GarminConnectionService>();
builder.Services.AddSingleton<GarminSyncWorker>();
builder.Services.AddHostedService(static services => services.GetRequiredService<GarminSyncWorker>());
builder.Services.AddScoped<TreadmillRunner.Core.Sessions.ISessionStore, SessionStore>();
builder.Services.AddSingleton<SqliteOnlineBackupService>();
builder.Services.AddSingleton<SqliteRestoreService>();
builder.Services.AddSingleton<RestorePreviewStore>();
builder.Services.AddSingleton<ApplicationMaintenanceState>();
builder.Services.AddSingleton<IApplicationMaintenanceState>(static services => services.GetRequiredService<ApplicationMaintenanceState>());
builder.Services.AddSingleton<UpdateManager>();
builder.Services.AddSingleton<UpdateFeedFactory>();
builder.Services.AddHttpClient("TreadmillRunnerUpdates", client =>
{
  client.Timeout = TimeSpan.FromMinutes(5);
});
builder.Services.AddHostedService<UpdateCheckWorker>();
builder.Services.AddSingleton<IWorkoutImportPreviewStore, WorkoutImportPreviewStore>();
builder.Services.AddSingleton<WorkoutSetImportPreviewStore>();
builder.Services.AddSingleton<TreadmillWorkoutBundleImporter>();
builder.Services.AddSingleton<IWorkoutImporter, NativeWorkoutJsonImporter>();
builder.Services.AddSingleton<IWorkoutImporter, QDomyosWorkoutXmlImporter>();
builder.Services.AddSingleton<IWorkoutImporter, GarminFitWorkoutImporter>();
builder.Services.AddSingleton<ITreadmillProtocol>(OmegaZCompatibilityProfile.Default);
builder.Services.AddSingleton<TreadmillProtocolRegistry>();
builder.Services.AddSingleton<WindowsBleCentralTransport>();
builder.Services.AddSingleton<IBleCentralTransport>(static services => services.GetRequiredService<WindowsBleCentralTransport>());
builder.Services.AddSingleton<IBleCommandCentralTransport>(static services => services.GetRequiredService<WindowsBleCentralTransport>());
builder.Services.AddSingleton<ReadOnlyDeviceCoordinator>();
builder.Services.AddSingleton<IReadOnlyDeviceCoordinator>(static services => services.GetRequiredService<ReadOnlyDeviceCoordinator>());
builder.Services.AddHostedService(static services => services.GetRequiredService<ReadOnlyDeviceCoordinator>());
builder.Services.AddSingleton(TreadmillCommandPolicy.Default);
builder.Services.AddSingleton<TreadmillCommandCoordinator>();
builder.Services.AddSingleton<ITreadmillCommandCoordinator>(static services => services.GetRequiredService<TreadmillCommandCoordinator>());
builder.Services.AddSingleton<FtmsCommandCommissioningRunner>();
builder.Services.AddSingleton<IFtmsCommandCommissioningRunner>(static services =>
  services.GetRequiredService<FtmsCommandCommissioningRunner>());
builder.Services.AddSingleton<ICommissioningDelay, SystemCommissioningDelay>();
builder.Services.AddSingleton<FtmsStartStopCommissioningRunner>();
builder.Services.AddSingleton<FtmsDailyControlSequenceRunner>();
builder.Services.AddSingleton<TreadmillRunner.Core.Control.ControlLeaseManager>();
builder.Services.AddSingleton<IControlLeaseCoordinator, ControlLeaseCoordinator>();
builder.Services.AddSingleton<LiveSessionCoordinator>();
builder.Services.AddSingleton<ILiveSessionCoordinator>(static services => services.GetRequiredService<LiveSessionCoordinator>());
builder.Services.AddSingleton<ILiveSnapshotSource>(static services => services.GetRequiredService<LiveSessionCoordinator>());
builder.Services.AddHostedService(static services => services.GetRequiredService<LiveSessionCoordinator>());

var app = builder.Build();
DateTimeOffset serviceStartedAtUtc = DateTimeOffset.UtcNow;

app.Use(async (context, next) =>
{
  bool mutation = !HttpMethods.IsGet(context.Request.Method) &&
    !HttpMethods.IsHead(context.Request.Method) &&
    !HttpMethods.IsOptions(context.Request.Method);
  bool browserApiMutation = mutation && context.Request.Path.StartsWithSegments("/api", StringComparison.OrdinalIgnoreCase) &&
    context.Request.Headers.ContainsKey("Sec-Fetch-Site");
  bool hasFingerprint = context.Request.Headers.TryGetValue(ClientRuntimeState.HeaderName, out var fingerprint);
  // Browser fetches always send Sec-Fetch-Site. Non-browser local operator and watch clients are
  // intentionally exempt unless they opt into the build contract by sending the fingerprint header.
  if (mutation && (browserApiMutation || hasFingerprint) &&
      (!hasFingerprint || !string.Equals(fingerprint.ToString(), AppBuildInfo.Fingerprint, StringComparison.Ordinal)))
  {
    context.Response.StatusCode = StatusCodes.Status409Conflict;
    context.Response.Headers["X-TreadmillRunner-Server-Build"] = AppBuildInfo.Fingerprint;
    await context.Response.WriteAsJsonAsync(new
    {
      type = "https://treadmillrunner.local/problems/client-update-required",
      title = "Client update required",
      status = StatusCodes.Status409Conflict,
      code = "ClientUpdateRequired",
      detail = "Reload the application before changing state.",
    });
    return;
  }
  await next();
});

app.Use(async (context, next) =>
{
  string path = context.Request.Path.Value ?? string.Empty;
  if (path.StartsWith("/api/", StringComparison.OrdinalIgnoreCase) ||
      path.StartsWith("/hubs/", StringComparison.OrdinalIgnoreCase) ||
      path.Equals("/manifest.webmanifest", StringComparison.OrdinalIgnoreCase) ||
      path.Equals("/runner-sound.js", StringComparison.OrdinalIgnoreCase) ||
      path.Equals("/apple-touch-icon-180.png", StringComparison.OrdinalIgnoreCase) ||
      path.StartsWith("/app-icon-", StringComparison.OrdinalIgnoreCase) ||
      path.Equals("/_framework/blazor.boot.json", StringComparison.OrdinalIgnoreCase) ||
      path.Equals("/_framework/blazor.web.js", StringComparison.OrdinalIgnoreCase) ||
      path.Equals("/_framework/resource-collection.js", StringComparison.OrdinalIgnoreCase) ||
      string.IsNullOrEmpty(Path.GetExtension(path)))
  {
    context.Response.OnStarting(static state =>
    {
      HttpResponse response = (HttpResponse)state;
      response.Headers.CacheControl = "no-store";
      response.Headers.Pragma = "no-cache";
      response.Headers.Expires = "0";
      return Task.CompletedTask;
    }, context.Response);
  }
  await next();
});

app.Use(async (context, next) =>
{
  IApplicationMaintenanceState maintenance = context.RequestServices.GetRequiredService<IApplicationMaintenanceState>();
  bool mutation = !HttpMethods.IsGet(context.Request.Method) &&
    !HttpMethods.IsHead(context.Request.Method) &&
    !HttpMethods.IsOptions(context.Request.Method);
  bool maintenanceRequest = context.Request.Path.Equals("/api/operations/restore/confirm", StringComparison.OrdinalIgnoreCase) ||
    context.Request.Path.Equals("/api/updates/activate", StringComparison.OrdinalIgnoreCase) ||
    context.Request.Path.Equals("/api/operations/database/check", StringComparison.OrdinalIgnoreCase);
  if (mutation && maintenanceRequest)
  {
    if (maintenance.IsActive)
    {
      context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
      await context.Response.WriteAsJsonAsync(new { error = "The application is completing an idle maintenance operation; mutations are temporarily unavailable." });
      return;
    }
    await next();
    return;
  }

  if (mutation)
  {
    if (!maintenance.TryBeginMutation())
    {
      context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
      await context.Response.WriteAsJsonAsync(new { error = "The application is completing an idle maintenance operation; mutations are temporarily unavailable." });
      return;
    }
    try
    {
      await next();
      if (context.Response.StatusCode < StatusCodes.Status400BadRequest &&
        (context.Request.Path.StartsWithSegments("/api/planning/workouts", StringComparison.OrdinalIgnoreCase) ||
         context.Request.Path.StartsWithSegments("/api/planning/programs", StringComparison.OrdinalIgnoreCase) ||
         context.Request.Path.StartsWithSegments("/api/planning/calendar", StringComparison.OrdinalIgnoreCase)))
      {
        context.RequestServices.GetRequiredService<GarminSyncWorker>().Wake();
      }
    }
    finally { maintenance.EndMutation(); }
    return;
  }

  await next();
});

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
app.MapUpdates();
app.MapGarmin();
app.MapGarminWatch();
app.MapGarminActivityUpload();
app.MapHub<LiveHub>("/hubs/live");
app.MapRazorComponents<App>()
    .AddInteractiveWebAssemblyRenderMode()
    .AddAdditionalAssemblies(typeof(TreadmillRunner.Web._Imports).Assembly);

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
