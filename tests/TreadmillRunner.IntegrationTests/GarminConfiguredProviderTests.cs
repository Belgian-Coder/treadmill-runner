using Microsoft.Extensions.Options;
using TreadmillRunner.Gateway.Garmin;

namespace TreadmillRunner.IntegrationTests;

public sealed class GarminConfiguredProviderTests
{
  [Fact]
  public void Configuration_alone_cannot_enable_production_without_an_approved_contract_adapter()
  {
    var provider = CreateProvider(ValidOptions(), new UnavailableGarminTrainingContractAdapter());

    Assert.False(provider.IsConfigured);
    Assert.Contains("contract adapter", provider.SetupMessage, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public void Production_requires_the_exact_registered_https_callback()
  {
    GarminOptions options = ValidOptions();
    options.CallbackUri = "http://runner.example/api/integrations/garmin/callback";
    Assert.False(CreateProvider(options, new ApprovedTestContractAdapter()).IsConfigured);

    options.CallbackUri = "https://runner.example/api/integrations/garmin/callback";
    ConfiguredGarminProvider provider = CreateProvider(options, new ApprovedTestContractAdapter());
    Assert.True(provider.IsConfigured);
    Assert.Throws<InvalidOperationException>(() => provider.CreateAuthorizationUri(
      Guid.NewGuid(),
      "state",
      "challenge",
      new Uri("https://attacker.example/api/integrations/garmin/callback")));
  }

  private static ConfiguredGarminProvider CreateProvider(GarminOptions options, IGarminTrainingContractAdapter adapter) =>
    new(new TestHttpClientFactory(), new TestOptionsMonitor<GarminOptions>(options), adapter, TimeProvider.System);

  private static GarminOptions ValidOptions() => new()
  {
    ApprovedTrainingContract = true,
    ClientId = "client",
    ClientSecret = "secret",
    AuthorizationEndpoint = "https://garmin.example/authorize",
    TokenEndpoint = "https://garmin.example/token",
    IdentityEndpoint = "https://garmin.example/identity",
    WorkoutEndpoint = "https://garmin.example/workouts",
    TrainingPlanEndpoint = "https://garmin.example/plans",
    CalendarEndpoint = "https://garmin.example/calendar",
    CallbackUri = "https://runner.example/api/integrations/garmin/callback",
  };

  private sealed class ApprovedTestContractAdapter : IGarminTrainingContractAdapter
  {
    public bool IsApproved => true;
    public bool SupportsSafeRetry => false;
    public HttpRequestMessage CreatePublishRequest(string kind, string canonicalPayloadJson, string accessToken, string idempotencyKey, GarminOptions options) =>
      throw new NotSupportedException();
    public Task<GarminPublishResult> ReadPublishResponseAsync(HttpResponseMessage response, CancellationToken cancellationToken) =>
      throw new NotSupportedException();
  }

  private sealed class TestHttpClientFactory : IHttpClientFactory
  {
    public HttpClient CreateClient(string name) => new();
  }

  private sealed class TestOptionsMonitor<T>(T value) : IOptionsMonitor<T>
  {
    public T CurrentValue => value;
    public T Get(string? name) => value;
    public IDisposable? OnChange(Action<T, string?> listener) => null;
  }
}
