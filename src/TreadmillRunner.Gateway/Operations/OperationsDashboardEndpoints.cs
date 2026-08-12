using TreadmillRunner.Core.Household;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Gateway.Updates;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Operations;

public sealed record OperationsDashboardUpdateStatus(
  string State,
  string CurrentVersion,
  string? AvailableVersion,
  string? StagedVersion,
  string? ReleaseNotes,
  DateTimeOffset? LastCheckedAtUtc,
  string Message,
  string? FeedSource);

public sealed record OperationsHealthComponentView(string Id, string State, string Detail);
public sealed record OperationsHealthSummaryView(string State, IReadOnlyList<OperationsHealthComponentView> Components);

public sealed record OperationsDashboardView(
  OperationsDashboardUpdateStatus UpdateStatus,
  AppAccessView Access,
  DatabaseIntegrityStatus DatabaseStatus,
  VersionedLocalBackupPolicy? BackupPolicy,
  IReadOnlyList<StoredBackupVerification> BackupVerifications,
  OperationsHealthSummaryView OperationsSummary);

public static class OperationsDashboardEndpoints
{
  public static IEndpointRouteBuilder MapOperationsDashboard(this IEndpointRouteBuilder endpoints)
  {
    endpoints.MapGet("/api/operations/dashboard", GetDashboardAsync);
    return endpoints;
  }

  private static async Task<IResult> GetDashboardAsync(
    IConfiguration configuration,
    IDatabaseIntegrityCoordinator database,
    IReadOnlyDeviceCoordinator devices,
    UpdateManager updates,
    ILocalFirstExperienceStore store,
    CancellationToken cancellationToken)
  {
    Task<VersionedLocalBackupPolicy?> policyTask = store.GetBackupPolicyAsync(cancellationToken);
    Task<IReadOnlyList<StoredBackupVerification>> verificationsTask =
      store.ListBackupVerificationsAsync(10, cancellationToken);
    await Task.WhenAll(policyTask, verificationsTask);

    IReadOnlyList<StoredBackupVerification> verifications = await verificationsTask;
    UpdateStatusSnapshot update = updates.Status;
    return Results.Ok(new OperationsDashboardView(
      new OperationsDashboardUpdateStatus(
        update.State.ToString(), update.CurrentVersion, update.AvailableVersion, update.StagedVersion,
        update.ReleaseNotes, update.LastCheckedAtUtc, update.Message, update.FeedSource),
      new AppAccessUrlService(configuration).GetView(),
      database.Current,
      await policyTask,
      verifications,
      CreateHealthSummary(database, devices, updates, verifications.FirstOrDefault())));
  }

  internal static OperationsHealthSummaryView CreateHealthSummary(
    IDatabaseIntegrityCoordinator database,
    IReadOnlyDeviceCoordinator devices,
    UpdateManager updates,
    StoredBackupVerification? backup)
  {
    var components = new[]
    {
      new LocalHealthComponent("Service", LocalHealthState.Healthy, "The local gateway is responding."),
      new LocalHealthComponent("Database", database.Current.RecoveryRequired
        ? LocalHealthState.ActionRequired
        : database.Current.State == DatabaseIntegrityState.Healthy
          ? LocalHealthState.Healthy
          : LocalHealthState.Degraded, database.Current.Message),
      new LocalHealthComponent("BLE", devices.Current.Treadmill.State.ToString() == "Connected"
        ? LocalHealthState.Healthy
        : LocalHealthState.Degraded,
        $"Treadmill {devices.Current.Treadmill.State}; heart rate {devices.Current.HeartRate.State}."),
      new LocalHealthComponent("Storage", backup?.Status == "Verified"
        ? LocalHealthState.Healthy
        : backup is null ? LocalHealthState.Degraded : LocalHealthState.ActionRequired,
        backup?.Detail ?? "No owner-selected backup has been verified yet."),
      new LocalHealthComponent("Release", updates.Status.State.ToString() is "Failed" or "RollbackFailed"
        ? LocalHealthState.ActionRequired
        : LocalHealthState.Healthy, updates.Status.Message),
    };
    OperationsHealthSummary summary = OperationsHealthAggregator.Combine(components);
    return new OperationsHealthSummaryView(
      summary.State.ToString(),
      summary.Components.Select(static component => new OperationsHealthComponentView(
        component.Id, component.State.ToString(), component.Detail)).ToArray());
  }
}
