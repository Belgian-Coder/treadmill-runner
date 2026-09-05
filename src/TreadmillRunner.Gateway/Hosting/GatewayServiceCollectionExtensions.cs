using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.ResponseCompression;
using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Gateway.Diagnostics;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Gateway.Health;
using TreadmillRunner.Gateway.Household;
using TreadmillRunner.Gateway.Live;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Gateway.Planning;
using TreadmillRunner.Gateway.Security;
using TreadmillRunner.Gateway.Updates;
using TreadmillRunner.Infrastructure.Bluetooth;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Imports;
using TreadmillRunner.Protocols.Omega;

namespace TreadmillRunner.Gateway.Hosting;

public static class GatewayServiceCollectionExtensions
{
  public static IServiceCollection AddTreadmillRunnerServices(
    this IServiceCollection services,
    IConfiguration configuration)
  {
    services.AddRazorComponents().AddInteractiveWebAssemblyComponents();
    services.AddResponseCompression(options =>
    {
      // The generated entry document and Blazor resource collection are
      // intentionally no-store and change with the running build. Keep
      // compression scoped to those document/script responses so API/SignalR
      // payloads (including sensitive JSON) retain their existing behavior.
      options.EnableForHttps = true;
      options.MimeTypes = ["text/html", "application/javascript"];
    });
    services.AddSignalR();
    services.AddHealthChecks()
      .AddCheck<SimulatorReadyHealthCheck>("simulator-live", tags: ["ready"])
      .AddCheck<DatabaseReadyHealthCheck>("database", tags: ["ready"])
      .AddCheck<BleDiagnosticHealthCheck>("ble", tags: ["ble"]);
    services.AddMetrics();
    services.AddSingleton<OperationalTelemetry>();
    services.AddSingleton(TimeProvider.System);
    services.AddOptions<OperatorAccessOptions>()
      .Bind(configuration.GetSection(OperatorAccessOptions.SectionName))
      .Validate(OperatorAccessOptions.IsValid, "OperatorAccess must contain a valid PBKDF2 secret hash and bounded session/rate-limit settings when enabled.")
      .ValidateOnStart();
    services.AddSingleton<OperatorAccessService>();

    string dataProtectionPath = configuration["Persistence:DataProtectionKeyPath"]
      ?? Path.Combine(AppContext.BaseDirectory, "data", "keys");
    Directory.CreateDirectory(dataProtectionPath);
    services.AddDataProtection()
      .SetApplicationName("TreadmillRunner")
      .PersistKeysToFileSystem(new DirectoryInfo(dataProtectionPath))
      .ProtectKeysWithDpapi();

    services.AddSingleton<Microsoft.EntityFrameworkCore.IDbContextFactory<TreadmillRunnerDbContext>>(serviceProvider =>
    {
      IConfiguration runtimeConfiguration = serviceProvider.GetRequiredService<IConfiguration>();
      string databasePath = runtimeConfiguration["Persistence:DatabasePath"]
        ?? Path.Combine(AppContext.BaseDirectory, "data", "treadmillrunner.db");
      string? databaseDirectory = Path.GetDirectoryName(Path.GetFullPath(databasePath));
      if (databaseDirectory is not null) Directory.CreateDirectory(databaseDirectory);
      return TreadmillRunnerDatabase.CreateFactory(databasePath);
    });
    services.AddSingleton<IDatabaseIntegrityChecker, DatabaseIntegrityChecker>();
    services.AddSingleton<IVerifiedDatabaseBackupService, VerifiedDatabaseBackupService>();
    services.AddSingleton<IDatabaseIntegrityStatusStore, DatabaseIntegrityStatusStore>();
    services.AddSingleton<IDatabaseMaintenanceLeaseProvider, LiveSessionDatabaseMaintenanceLeaseProvider>();
    services.AddSingleton<DatabaseIntegrityCoordinator>();
    services.AddSingleton<IDatabaseIntegrityCoordinator>(static provider => provider.GetRequiredService<DatabaseIntegrityCoordinator>());
    services.AddHostedService(static provider => provider.GetRequiredService<DatabaseIntegrityCoordinator>());
    services.AddScoped<IProfileStore, ProfileStore>();
    services.AddScoped<ILocalFirstExperienceStore, LocalFirstExperienceStore>();
    services.AddSingleton<LocalBackupWorker>();
    services.AddSingleton<ILocalBackupCoordinator>(static provider => provider.GetRequiredService<LocalBackupWorker>());
    services.AddHostedService(static provider => provider.GetRequiredService<LocalBackupWorker>());
    services.AddScoped<IWorkoutStore, WorkoutStore>();
    services.AddScoped<IWorkoutSetImportStore, WorkoutSetImportStore>();
    services.AddScoped<ICalendarStore, CalendarStore>();
    services.AddScoped<IWorkoutProgramStore, WorkoutProgramStore>();
    services.AddScoped<IPremadePlanStore, PremadePlanStore>();
    services.AddScoped<IOperationReceiptStore, OperationReceiptStore>();
    services.AddHostedService<OperationReceiptRetentionWorker>();
    services.AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    services.AddScoped<IBleReliabilityStore, BleReliabilityStore>();
    services.AddScoped<ITreadmillMaintenanceStore, TreadmillMaintenanceStore>();

    services.AddSingleton<IGarminStore, GarminStore>();
    services.AddSingleton<IGarminWatchBindingStore, GarminWatchBindingStore>();
    services.AddSingleton<IGarminActivityUploadStore, GarminActivityUploadStore>();
    services.AddSingleton<GarminActivityBackupStore>();
    services.Configure<GarminActivityAdapterOptions>(configuration.GetSection(GarminActivityAdapterOptions.SectionName));
    services.AddSingleton<PythonGarminActivityAdapter>();
    services.AddSingleton<IGarminActivityAdapter>(static provider => provider.GetRequiredService<PythonGarminActivityAdapter>());
    services.AddSingleton<IGarminActivityAdapterReadiness>(static provider => provider.GetRequiredService<PythonGarminActivityAdapter>());
    services.AddSingleton<GarminActivityConnectionService>();
    services.AddSingleton<GarminActivityUploadWorker>();
    services.AddHostedService(static provider => provider.GetRequiredService<GarminActivityUploadWorker>());
    services.Configure<GarminOptions>(configuration.GetSection(GarminOptions.SectionName));
    services.AddSingleton<DisabledGarminProvider>();
    services.AddSingleton<MockGarminProvider>();
    services.AddSingleton<IGarminTrainingContractAdapter, UnavailableGarminTrainingContractAdapter>();
    services.AddHttpClient("GarminConnect", (provider, client) =>
    {
      GarminOptions options = provider.GetRequiredService<Microsoft.Extensions.Options.IOptionsMonitor<GarminOptions>>().CurrentValue;
      client.Timeout = TimeSpan.FromSeconds(Math.Clamp(options.RequestTimeoutSeconds, 5, 60));
    });
    services.AddSingleton<ConfiguredGarminProvider>();
    services.AddSingleton<IGarminProvider>(provider =>
      provider.GetRequiredService<IConfiguration>()[
        $"{GarminOptions.SectionName}:Provider"]?.Trim().ToUpperInvariant() switch
      {
        "MOCK" => provider.GetRequiredService<MockGarminProvider>(),
        "CONFIGURED" => provider.GetRequiredService<ConfiguredGarminProvider>(),
        _ => provider.GetRequiredService<DisabledGarminProvider>(),
      });
    services.AddScoped<GarminSyncCatalog>();
    services.AddScoped<GarminConnectionService>();
    services.AddSingleton<GarminSyncWorker>();
    services.AddHostedService(static provider => provider.GetRequiredService<GarminSyncWorker>());

    services.AddScoped<TreadmillRunner.Core.Sessions.ISessionStore, SessionStore>();
    services.AddSingleton<SqliteOnlineBackupService>();
    services.AddSingleton<SqliteRestoreService>();
    services.AddSingleton<RestorePreviewStore>();
    services.AddSingleton<ApplicationMaintenanceState>();
    services.AddSingleton<IApplicationMaintenanceState>(static provider => provider.GetRequiredService<ApplicationMaintenanceState>());
    services.AddSingleton<UpdateManager>();
    services.AddSingleton<UpdateFeedFactory>();
    services.AddHttpClient("TreadmillRunnerUpdates", static client => client.Timeout = TimeSpan.FromMinutes(5));
    services.AddHostedService<UpdateCheckWorker>();
    services.AddSingleton<IWorkoutImportPreviewStore, WorkoutImportPreviewStore>();
    services.AddSingleton<WorkoutSetImportPreviewStore>();
    services.AddSingleton<TreadmillWorkoutBundleImporter>();
    services.AddSingleton<IWorkoutImporter, NativeWorkoutJsonImporter>();
    services.AddSingleton<IWorkoutImporter, QDomyosWorkoutXmlImporter>();
    services.AddSingleton<IWorkoutImporter, GarminFitWorkoutImporter>();
    services.AddSingleton<ITreadmillProtocol>(OmegaZCompatibilityProfile.Default);
    services.AddSingleton<TreadmillProtocolRegistry>();
    services.AddSingleton<WindowsBleCentralTransport>();
    services.AddSingleton<IBleCentralTransport>(static provider => provider.GetRequiredService<WindowsBleCentralTransport>());
    services.AddSingleton<IBleAdvertisementBroker, BleAdvertisementBroker>();
    services.AddSingleton<IBleCommandCentralTransport>(static provider => provider.GetRequiredService<WindowsBleCentralTransport>());
    services.AddSingleton(provider => new BleDiagnosticJournal(
      Path.Combine(Path.GetDirectoryName(Path.GetFullPath(configuration["Persistence:DatabasePath"]
        ?? Path.Combine(AppContext.BaseDirectory, "data", "treadmillrunner.db")))!, "diagnostics"),
      provider.GetRequiredService<ILogger<BleDiagnosticJournal>>()));
    services.AddHostedService(provider => provider.GetRequiredService<BleDiagnosticJournal>());
    services.AddSingleton<ReadOnlyDeviceCoordinator>();
    services.AddSingleton<IReadOnlyDeviceCoordinator>(static provider => provider.GetRequiredService<ReadOnlyDeviceCoordinator>());
    services.AddHostedService(static provider => provider.GetRequiredService<ReadOnlyDeviceCoordinator>());
    services.AddSingleton(TreadmillCommandPolicy.Default);
    services.AddSingleton<TreadmillCommandCoordinator>();
    services.AddSingleton<ITreadmillCommandCoordinator>(static provider => provider.GetRequiredService<TreadmillCommandCoordinator>());
    services.AddSingleton<FtmsCommandCommissioningRunner>();
    services.AddSingleton<IFtmsCommandCommissioningRunner>(static provider => provider.GetRequiredService<FtmsCommandCommissioningRunner>());
    services.AddSingleton<ICommissioningDelay, SystemCommissioningDelay>();
    services.AddSingleton<FtmsStartStopCommissioningRunner>();
    services.AddSingleton<FtmsDailyControlSequenceRunner>();
    services.AddSingleton<ControlLeaseManager>();
    services.AddSingleton<IControlLeaseCoordinator, ControlLeaseCoordinator>();
    services.AddSingleton<LiveSessionCoordinator>();
    services.AddSingleton<ILiveSessionCoordinator>(static provider => provider.GetRequiredService<LiveSessionCoordinator>());
    services.AddSingleton<ILiveSnapshotSource>(static provider => provider.GetRequiredService<LiveSessionCoordinator>());
    services.AddHostedService(static provider => provider.GetRequiredService<LiveSessionCoordinator>());
    return services;
  }
}
