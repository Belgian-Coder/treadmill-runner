using System.Diagnostics;
using System.Net;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;

namespace TreadmillRunner.E2ETests;

public sealed class BrowserConnectionRecoveryTests(GatewayFixture gateway) : PageTest, IClassFixture<GatewayFixture>
{
  [Theory]
  [InlineData("session", HttpStatusCode.ServiceUnavailable)]
  [InlineData("session", HttpStatusCode.OK)]
  [InlineData("snapshot", HttpStatusCode.OK)]
  [Trait("Category", "Browser")]
  public async Task Invalid_authoritative_reads_retry_without_enabling_or_reloading(
    string failureTarget,
    HttpStatusCode failureStatus)
  {
    await ResetSimulatorAsync();
    SeededPlan plan = await SeedPlanAsync($"authoritative-{failureTarget}-{(int)failureStatus}-recovery");
    int sessionReads = 0;
    int failureResponses = 0;
    int armRequests = 0;
    bool allowRecovery = false;
    await Page.RouteAsync("**/api/live/session", async route =>
    {
      int attempt = Interlocked.Increment(ref sessionReads);
      if (failureTarget == "session" &&
          route.Request.Method == "GET" &&
          attempt > 1 &&
          !Volatile.Read(ref allowRecovery))
      {
        Interlocked.Increment(ref failureResponses);
        await route.FulfillAsync(new RouteFulfillOptions
        {
          Status = (int)failureStatus,
          ContentType = "application/json",
          Body = "{}",
        });
        return;
      }

      await route.ContinueAsync();
    });
    await Page.RouteAsync("**/api/live/snapshot", async route =>
    {
      if (failureTarget == "snapshot" && !Volatile.Read(ref allowRecovery))
      {
        Interlocked.Increment(ref failureResponses);
        await route.FulfillAsync(new RouteFulfillOptions
        {
          Status = (int)failureStatus,
          ContentType = "application/json",
          Body = "{}",
        });
        return;
      }

      await route.ContinueAsync();
    });
    await Page.RouteAsync("**/api/live/sessions/arm", async route =>
    {
      Interlocked.Increment(ref armRequests);
      await route.ContinueAsync();
    });

    try
    {
      await NavigateAndSelectPlanAsync(plan);
      await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();

      await WaitUntilAsync(() => Volatile.Read(ref failureResponses) >= 2, TimeSpan.FromSeconds(10));
      Assert.Equal(0, Volatile.Read(ref armRequests));
      await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready to run", Exact = true })).ToBeVisibleAsync();
      await Expect(Page.Locator(".connection-state")).ToContainTextAsync("retrying");

      Volatile.Write(ref allowRecovery, true);

      await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
        .ToBeVisibleAsync(new() { Timeout = 20_000 });
      await Expect(Page.Locator(".connection-state")).ToContainTextAsync("Gateway ready");
      Assert.Equal(1, Volatile.Read(ref armRequests));
    }
    finally
    {
      Volatile.Write(ref allowRecovery, true);
      await ResetSimulatorAsync();
    }
  }

  [Theory]
  [InlineData("aborted")]
  [InlineData("malformed")]
  [Trait("Category", "Browser")]
  public async Task Failed_lease_heartbeat_reconnects_with_a_live_token_and_does_not_replay_arm(
    string failureMode)
  {
    await ResetSimulatorAsync();
    SeededPlan plan = await SeedPlanAsync($"heartbeat-{failureMode}-recovery");
    int acquireRequests = 0;
    int armRequests = 0;
    int heartbeatRequests = 0;
    await Page.RouteAsync("**/api/live/lease/acquire", async route =>
    {
      Interlocked.Increment(ref acquireRequests);
      await route.ContinueAsync();
    });
    await Page.RouteAsync("**/api/live/sessions/arm", async route =>
    {
      Interlocked.Increment(ref armRequests);
      await route.ContinueAsync();
    });
    await Page.RouteAsync("**/api/live/lease/heartbeat", async route =>
    {
      if (Interlocked.Increment(ref heartbeatRequests) == 1)
      {
        if (failureMode == "aborted")
        {
          await route.AbortAsync("internetdisconnected");
        }
        else
        {
          await route.FulfillAsync(new RouteFulfillOptions
          {
            Status = (int)HttpStatusCode.OK,
            ContentType = "application/json",
            Body = "{}",
          });
        }
        return;
      }

      await route.ContinueAsync();
    });

    try
    {
      await NavigateAndSelectPlanAsync(plan);
      await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();
      await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
        .ToBeVisibleAsync();
      Assert.Equal(1, Volatile.Read(ref armRequests));

      await WaitUntilAsync(() => Volatile.Read(ref acquireRequests) >= 2, TimeSpan.FromSeconds(20));

      await Expect(Page.Locator(".connection-state")).ToContainTextAsync("Gateway ready");
      Assert.True(Volatile.Read(ref heartbeatRequests) >= 1);
      Assert.Equal(1, Volatile.Read(ref armRequests));
    }
    finally
    {
      await ResetSimulatorAsync();
    }
  }

  private async Task NavigateAndSelectPlanAsync(SeededPlan plan)
  {
    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready to run", Exact = true }))
      .ToBeVisibleAsync(new() { Timeout = 15_000 });
    await Page.SelectActiveRunnerAsync(plan.ProfileName);
    await Page.OpenRunChoicesAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true })).ToBeEnabledAsync();
  }

  private async Task ResetSimulatorAsync()
  {
    using HttpClient client = CreateClient();
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/live/simulator/reset", new { });
    Assert.Equal(HttpStatusCode.NoContent, response.StatusCode);
  }

  private async Task<SeededPlan> SeedPlanAsync(string scenario)
  {
    string stableSuffix = Convert.ToHexString(
      SHA256.HashData(Encoding.UTF8.GetBytes(scenario)))[..8].ToLowerInvariant();
    string profileName = $"Recovery runner {stableSuffix}";
    string workoutName = $"Recovery workout {stableSuffix}";
    using HttpClient client = CreateClient();
    using HttpResponseMessage profileResponse = await client.PostAsJsonAsync("/api/planning/profiles", new
    {
      operationId = Guid.NewGuid(),
      displayName = profileName,
      unitSystem = "Metric",
      weightKilograms = 72.5,
      maximumHeartRateBpm = 190,
      maximumSpeedKph = 15.0,
      heartRateZones = new[]
      {
        new { number = 2, name = "Aerobic", minimumBpm = 125, maximumBpm = 145 },
      },
      expectedVersion = (int?)null,
    });
    Assert.Equal(HttpStatusCode.Created, profileResponse.StatusCode);

    using HttpResponseMessage workoutResponse = await client.PostAsJsonAsync("/api/planning/workouts", new
    {
      operationId = Guid.NewGuid(),
      name = workoutName,
      description = "Browser recovery test fixture",
      blocks = new[]
      {
        new
        {
          kind = "step",
          repetitions = 1,
          blocks = Array.Empty<object>(),
          goalKind = "time",
          goalValue = 20.0,
          speedKind = "fixed",
          speedStartKph = 6.5,
          speedEndKph = 0.0,
          heartRateMinimumBpm = 125,
          heartRateMaximumBpm = 145,
          heartRateZoneNumber = 0,
          heartRateInitialSpeedKph = 6.5,
          heartRateMinimumSpeedKph = 5.0,
          heartRateMaximumSpeedKph = 8.0,
          inclineKind = "fixed",
          inclineStartPercent = 1.0,
          inclineEndPercent = 0.0,
          cue = "Hold a steady rhythm.",
          notes = "Recovery fixture",
        },
      },
    });
    Assert.Equal(HttpStatusCode.Created, workoutResponse.StatusCode);
    return new SeededPlan(profileName, workoutName);
  }

  private static async Task WaitUntilAsync(Func<bool> condition, TimeSpan timeout)
  {
    var stopwatch = Stopwatch.StartNew();
    while (!condition() && stopwatch.Elapsed < timeout)
      await Task.Delay(50);
    Assert.True(condition(), $"Condition was not met within {timeout.TotalSeconds:0} seconds.");
  }

  private HttpClient CreateClient() => new() { BaseAddress = gateway.BaseAddress };

  private sealed record SeededPlan(string ProfileName, string WorkoutName);
}
