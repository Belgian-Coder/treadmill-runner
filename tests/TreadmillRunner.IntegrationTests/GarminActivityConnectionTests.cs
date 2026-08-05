using Microsoft.AspNetCore.DataProtection;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class GarminActivityConnectionTests : IAsyncLifetime
{
  private readonly string directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"));
  public Task InitializeAsync() { Directory.CreateDirectory(directory); return Task.CompletedTask; }
  public Task DisposeAsync() { Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools(); if (Directory.Exists(directory)) Directory.Delete(directory, true); return Task.CompletedTask; }

  [Fact]
  public async Task Mfa_is_profile_bound_and_connected_tokens_are_protected()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(Path.Combine(directory, "connection.db"));
    Guid marc = Guid.NewGuid(), partner = Guid.NewGuid();
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      await context.Database.MigrateAsync();
      DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z");
      context.UserProfiles.AddRange(Profile(marc, "Marc", now), Profile(partner, "Partner", now));
      await context.SaveChangesAsync();
    }

    var adapter = new MfaAdapter();
    var store = new GarminActivityUploadStore(factory);
    await using var service = new GarminActivityConnectionService(
      adapter,
      store,
      DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(directory, "keys"))),
      TimeProvider.System);

    GarminActivityConnectResult first = await service.BeginAsync(marc, "marc@example.test", "never-stored", false, default);
    Assert.Equal("MfaRequired", first.State);
    await Assert.ThrowsAsync<KeyNotFoundException>(() => service.CompleteMfaAsync(partner, first.ChallengeId!.Value, "123456", default));
    Assert.Null(await store.FindAccountAsync(marc));

    GarminActivityConnectResult second = await service.BeginAsync(marc, "marc@example.test", "never-stored", true, default);
    GarminActivityConnectResult connected = await service.CompleteMfaAsync(marc, second.ChallengeId!.Value, "654321", default);
    Assert.Equal("Connected", connected.State);
    GarminActivityUploadAccount account = Assert.IsType<GarminActivityUploadAccount>(await store.FindAccountAsync(marc));
    Assert.True(account.Enabled);
    Assert.DoesNotContain(MfaAdapter.TokenStore, account.ProtectedTokenStore, StringComparison.Ordinal);
    Assert.Equal(MfaAdapter.TokenStore, service.Unprotect(account.ProtectedTokenStore));
    Assert.All(adapter.Passwords, password => Assert.Equal("never-stored", password));
  }

  [Fact]
  public async Task Pending_mfa_is_replaced_per_profile_and_globally_bounded()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(Path.Combine(directory, "connection-bounds.db"));
    var adapter = new MfaAdapter();
    await using var service = new GarminActivityConnectionService(
      adapter,
      new GarminActivityUploadStore(factory),
      DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(directory, "keys-bounds"))),
      TimeProvider.System);

    Guid firstProfile = Guid.NewGuid();
    await service.BeginAsync(firstProfile, "first@example.test", "never-stored", false, default);
    MfaProcess replaced = Assert.Single(adapter.Processes);
    await service.BeginAsync(firstProfile, "first@example.test", "never-stored", false, default);
    Assert.True(replaced.Disposed);

    for (var index = 0; index < 3; index++)
      await service.BeginAsync(Guid.NewGuid(), $"runner{index}@example.test", "never-stored", false, default);
    InvalidOperationException error = await Assert.ThrowsAsync<InvalidOperationException>(() =>
      service.BeginAsync(Guid.NewGuid(), "overflow@example.test", "never-stored", false, default));
    Assert.Contains("too many", error.Message, StringComparison.OrdinalIgnoreCase);
  }

  private static UserProfileEntity Profile(Guid id, string name, DateTimeOffset now) => new()
  {
    Id = id,
    DisplayName = name,
    NormalizedDisplayName = name.ToUpperInvariant(),
    UnitSystem = "Metric",
    WeightKilograms = 70,
    Version = 1,
    CreatedAtUtc = now,
    UpdatedAtUtc = now,
  };

  private sealed class MfaAdapter : IGarminActivityAdapter
  {
    public const string TokenStore = "{\"oauth1_token\":\"secret-token\"}";
    public List<string> Passwords { get; } = [];
    public List<MfaProcess> Processes { get; } = [];
    public Task<IGarminAdapterConnectProcess> BeginConnectAsync(string email, string password, CancellationToken cancellationToken)
    {
      Passwords.Add(password);
      var process = new MfaProcess();
      Processes.Add(process);
      return Task.FromResult<IGarminAdapterConnectProcess>(process);
    }
    public Task<GarminAdapterMessage> UploadAsync(string tokenStore, string activityPath, CancellationToken cancellationToken) => throw new NotSupportedException();
  }

  private sealed class MfaProcess : IGarminAdapterConnectProcess
  {
    public bool Disposed { get; private set; }
    public Task<GarminAdapterMessage> ReadAsync(CancellationToken cancellationToken) =>
      Task.FromResult(new GarminAdapterMessage("mfa-required", null, null, null, null, null));
    public Task<GarminAdapterMessage> CompleteMfaAsync(string code, CancellationToken cancellationToken) =>
      Task.FromResult(new GarminAdapterMessage("connected", null, null, "marc@example.test", MfaAdapter.TokenStore, null));
    public ValueTask DisposeAsync() { Disposed = true; return ValueTask.CompletedTask; }
  }
}
