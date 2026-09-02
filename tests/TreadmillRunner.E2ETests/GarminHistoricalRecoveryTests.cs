using System.Collections.Concurrent;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;

namespace TreadmillRunner.E2ETests;

public sealed class GarminHistoricalRecoveryTests(GatewayFixture gateway) : PageTest, IClassFixture<GatewayFixture>
{
  [Fact]
  [Trait("Category", "Browser")]
  public async Task Historical_recovery_actions_require_confirmation_and_post_exact_guarded_commands()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    await scenario.InstallVisualDataRoutesAsync(Page);
    await Page.SetViewportSizeAsync(1180, 820);

    string detailRoute = $"**/api/history/{scenario.HistorySessionId:D}";
    string recoveryRoute =
      $"**/api/integrations/garmin/activity-upload/profiles/{scenario.MarcProfileId:D}/sessions/{scenario.HistorySessionId:D}/historical-recovery";
    bool recoveryStarted = false;
    string? startedAction = null;
    ConcurrentQueue<string> postBodies = new();

    await Page.RouteAsync(detailRoute, async route =>
    {
      JsonObject payload = JsonSerializer.SerializeToNode(
        scenario.HistoryDetail(),
        new JsonSerializerOptions(JsonSerializerDefaults.Web))!.AsObject();
      payload["garmin"] = GarminReconciliationPayload(recoveryStarted);
      await route.FulfillAsync(new RouteFulfillOptions
      {
        Status = 200,
        ContentType = "application/json",
        Body = payload.ToJsonString(),
      });
    });

    await Page.RouteAsync(recoveryRoute, async route =>
    {
      if (string.Equals(route.Request.Method, "GET", StringComparison.OrdinalIgnoreCase))
      {
        await route.FulfillAsync(new RouteFulfillOptions
        {
          Status = 200,
          ContentType = "application/json",
          Body = JsonSerializer.Serialize(new
          {
            available = !recoveryStarted,
            busy = recoveryStarted,
            canMergeIntoOne = true,
            canUndoMerge = true,
            state = recoveryStarted ? "Queued" : "Available",
            message = recoveryStarted
              ? $"{startedAction} is queued for FIT-verified Garmin recovery."
              : "A historical Garmin recovery is available for this session.",
          }),
        });
        return;
      }

      if (!string.Equals(route.Request.Method, "POST", StringComparison.OrdinalIgnoreCase))
      {
        await route.FallbackAsync();
        return;
      }

      string body = route.Request.PostData ?? throw new Xunit.Sdk.XunitException("Garmin recovery POST had no request body.");
      using (JsonDocument request = JsonDocument.Parse(body))
      {
        JsonElement root = request.RootElement;
        string action = root.GetProperty("action").GetString() ?? string.Empty;
        string confirmation = root.GetProperty("confirmation").GetString() ?? string.Empty;
        Assert.True(Guid.TryParse(root.GetProperty("operationId").GetString(), out _), "The recovery operation must include a valid operation ID.");
        Assert.Contains(action, new[] { "MergeIntoOne", "UndoMerge" });
        Assert.Equal(action == "MergeIntoOne" ? "MERGE INTO ONE" : "UNDO GARMIN MERGE", confirmation);
        startedAction = action == "MergeIntoOne" ? "Keeping one Garmin activity" : "Restoring two Garmin activities";
        postBodies.Enqueue(body);
      }

      recoveryStarted = true;
      await route.FulfillAsync(new RouteFulfillOptions
      {
        Status = 202,
        ContentType = "application/json",
        Body = JsonSerializer.Serialize(new { state = "Queued", message = "Historical Garmin recovery queued." }),
      });
    });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, $"/history/{scenario.HistorySessionId:D}").AbsoluteUri, new PageGotoOptions
    {
      WaitUntil = WaitUntilState.NetworkIdle,
    });

    foreach (string action in new[] { "MergeIntoOne", "UndoMerge" })
    {
      recoveryStarted = false;
      startedAction = null;
      while (postBodies.TryDequeue(out _)) { }

      if (action != "MergeIntoOne")
      {
        await Page.SetViewportSizeAsync(390, 844);
        await Page.GotoAsync(new Uri(gateway.BaseAddress, $"/history/{scenario.HistorySessionId:D}").AbsoluteUri, new PageGotoOptions
        {
          WaitUntil = WaitUntilState.NetworkIdle,
        });
      }

      ILocator garminPanel = Page.Locator("section[aria-labelledby='garmin-reconciliation-title']");
      await Expect(garminPanel.GetByRole(AriaRole.Heading, new() { Name = "Choose what Garmin should keep", Exact = true })).ToBeVisibleAsync();
      await Expect(garminPanel.GetByText("Your run stays in TreadmillRunner either way. Only the Garmin activities are changed.", new() { Exact = true })).ToBeVisibleAsync();
      await Expect(garminPanel.GetByRole(AriaRole.Button, new() { Name = "Keep one Garmin activity", Exact = true })).ToBeEnabledAsync();
      await Expect(garminPanel.GetByRole(AriaRole.Button, new() { Name = "Restore two Garmin activities", Exact = true })).ToBeEnabledAsync();
      ILocator technicalDetails = garminPanel.Locator("details.garmin-technical-details");
      await Expect(technicalDetails).Not.ToHaveAttributeAsync("open", "");
      await technicalDetails.Locator("summary").ClickAsync();
      await Expect(technicalDetails).ToHaveAttributeAsync("open", "");
      await Expect(technicalDetails).ToContainTextAsync("FIT duration, start time, and activity type agree.");

      string actionLabel = action == "MergeIntoOne" ? "Keep one Garmin activity" : "Restore two Garmin activities";
      string confirmationText = action == "MergeIntoOne"
        ? "Keep one Garmin activity? The app will keep the verified combined activity in Garmin and remove only the backed-up original and proven duplicates. Your local History remains unchanged."
        : "Restore two Garmin activities? The app will restore the original watch activity and one verified TreadmillRunner activity, then remove only proven merged or duplicate copies. Your local History remains unchanged.";

      await garminPanel.GetByRole(AriaRole.Button, new() { Name = actionLabel, Exact = true }).ClickAsync();
      await Expect(garminPanel.GetByRole(AriaRole.Alert)).ToContainTextAsync(confirmationText);
      await Expect(garminPanel.GetByRole(AriaRole.Button, new() { Name = "Confirm", Exact = true })).ToBeVisibleAsync();
      await Expect(garminPanel.GetByRole(AriaRole.Button, new() { Name = "Cancel", Exact = true })).ToBeVisibleAsync();

      await garminPanel.GetByRole(AriaRole.Button, new() { Name = "Cancel", Exact = true }).ClickAsync();
      await Expect(garminPanel.GetByRole(AriaRole.Alert)).ToHaveCountAsync(0);
      await Expect(garminPanel.GetByRole(AriaRole.Button, new() { Name = actionLabel, Exact = true })).ToBeEnabledAsync();
      Assert.Empty(postBodies);

      await garminPanel.GetByRole(AriaRole.Button, new() { Name = actionLabel, Exact = true }).ClickAsync();
      Task<IRequest> recoveryPost = Page.WaitForRequestAsync(
        request => string.Equals(request.Method, "POST", StringComparison.OrdinalIgnoreCase) && request.Url.Contains("/historical-recovery", StringComparison.Ordinal),
        new() { Timeout = 10_000 });
      await garminPanel.GetByRole(AriaRole.Button, new() { Name = "Confirm", Exact = true }).ClickAsync();
      await recoveryPost;

      string queuedMessage = action == "MergeIntoOne"
        ? "Keeping one Garmin activity is queued for FIT-verified Garmin recovery."
        : "Restoring two Garmin activities is queued for FIT-verified Garmin recovery.";
      await Expect(Page.GetByText(queuedMessage, new() { Exact = true }))
        .ToBeVisibleAsync(new() { Timeout = 10_000 });
      await Expect(garminPanel.GetByRole(AriaRole.Heading, new() { Name = "Updating Garmin…", Exact = true })).ToBeVisibleAsync();
      Assert.False(await Page.EvaluateAsync<bool>("() => document.documentElement.scrollWidth > document.documentElement.clientWidth"));
      Assert.Single(postBodies);
    }
  }

  private static JsonObject GarminReconciliationPayload(bool started) => new()
  {
    ["id"] = "90f72113-58ec-4e15-b39c-f7cf21bd02cd",
    ["status"] = started ? "Queued" : "ReviewRequired",
    ["reviewRequired"] = !started,
    ["operationPhase"] = started ? "Queued" : "AwaitingRecovery",
    ["remoteId"] = "24128760511",
    ["matchedRemoteId"] = "24128760511",
    ["replacementRemoteId"] = "24128760512",
    ["matchEvidence"] = "FIT duration, start time, and activity type agree.",
    ["failureKind"] = null,
    ["canRetry"] = false,
    ["retryAtUtc"] = null,
    ["lastError"] = null,
    ["updatedAtUtc"] = "2026-08-29T08:00:00Z",
    ["acknowledgedAtUtc"] = null,
  };
}
