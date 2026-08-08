using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;

namespace TreadmillRunner.E2ETests;

public sealed class PremadePlanExperienceTests(GatewayFixture gateway) : PageTest, IClassFixture<GatewayFixture>
{
  [Fact]
  [Trait("Category", "Browser")]
  public async Task Catalog_preview_materialization_and_grouped_long_plan_work_on_desktop_and_phone()
  {
    Guid profileId = await CreateDemoProfileAsync("Demo Runner");
    await CreateDemoProfileAsync("Second Runner");
    await Page.Context.AddInitScriptAsync($"window.localStorage.getItem('treadmillrunner.active-profile') ?? window.localStorage.setItem('treadmillrunner.active-profile', '{profileId:D}');");
    await Page.SetViewportSizeAsync(1180, 820);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.DOMContentLoaded });
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Plan templates", Exact = true })).ToBeVisibleAsync();

    await Page.GetByRole(AriaRole.Button, new() { Name = "Plan templates", Exact = true }).ClickAsync();
    await Expect(Page.Locator(".premade-plan-card")).ToHaveCountAsync(16);
    await Page.Locator(".premade-plan-filters select").Nth(0).SelectOptionAsync("10K");
    await Expect(Page.Locator(".premade-plan-card")).ToHaveCountAsync(6);
    await Page.GetByLabel("Search", new() { Exact = true }).FillAsync("Distance First");
    ILocator longPlan = Page.Locator(".premade-plan-card").Filter(new() { HasText = "5K to 10K Distance First" });
    await Expect(longPlan).ToHaveCountAsync(1);
    await longPlan.ClickAsync();
    await Expect(Page.Locator(".premade-plan-preview")).ToContainTextAsync("58");
    await Expect(Page.Locator(".premade-plan-preview")).ToContainTextAsync("174");
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Add for Demo Runner", Exact = true })).ToBeEnabledAsync();
    await Page.Locator(".premade-plan-phases > details").First.Locator("summary").First.ClickAsync();
    await Page.Locator(".premade-plan-phases > details").First.Locator("details > summary").First.ClickAsync();
    ILocator previewSession = Page.Locator(".premade-plan-phases .program-session-detail").First;
    await Expect(previewSession).ToBeVisibleAsync();
    await previewSession.ClickAsync();
    ILocator workoutDialog = Page.GetByRole(AriaRole.Dialog);
    await Expect(workoutDialog.GetByRole(AriaRole.Heading, new() { Name = "Planned graph", Exact = true })).ToBeVisibleAsync();
    await Expect(workoutDialog.GetByRole(AriaRole.Heading, new() { Name = "All planned changes", Exact = true })).ToBeVisibleAsync();
    await workoutDialog.GetByRole(AriaRole.Button, new() { Name = "Close workout details", Exact = true }).ClickAsync();
    await AssertNoHorizontalOverflowAsync();
    await SaveShowcaseAsync("tr-024-premade-plan-catalog.png");

    await Page.GetByRole(AriaRole.Button, new() { Name = "Add for Demo Runner", Exact = true }).ClickAsync();
    await Expect(Page.GetByText("Choose the calendar days now", new() { Exact = false })).ToBeVisibleAsync();
    ILocator installed = Page.Locator(".program-card").Filter(new() { HasText = "5K to 10K Distance First" });
    await Expect(installed).ToContainTextAsync("For Demo Runner");
    await Expect(installed).ToContainTextAsync("174 workouts");
    ILocator schedule = installed.Locator(".program-start-confirm");
    await Expect(schedule.GetByText("Choose exactly 3 training day(s)", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(schedule.Locator(".program-training-days button.is-selected")).ToHaveCountAsync(3);
    await SaveShowcaseAsync("tr-025-profile-plan-schedule.png");
    await schedule.GetByRole(AriaRole.Button, new() { Name = "Start plan", Exact = true }).ClickAsync();
    await Expect(Page.GetByText("ordered sessions are on the calendar", new() { Exact = false })).ToBeVisibleAsync();
    await Expect(installed).ToContainTextAsync("Calendar starts");
    await installed.Locator(".program-card__select").ClickAsync();
    ILocator installedDialog = Page.GetByRole(AriaRole.Dialog);
    await Expect(installedDialog.GetByText("Foundation", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(installedDialog.GetByText("Distance consolidation", new() { Exact = true })).ToBeVisibleAsync();
    await installedDialog.GetByText("Week 1 · 3 session(s)", new() { Exact = true }).ClickAsync();
    await Expect(installedDialog.GetByText("Session 1", new() { Exact = true }).First).ToBeVisibleAsync();
    await SaveShowcaseAsync("tr-024-long-plan-grouped.png");
    await installedDialog.GetByRole(AriaRole.Button, new() { Name = "Close training plan details", Exact = true }).ClickAsync();

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/calendar").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.DOMContentLoaded });
    await Expect(Page.Locator(".active-runner-picker summary")).ToContainTextAsync("Demo Runner");
    await Expect(Page.Locator(".calendar-option-shell--program").First).ToContainTextAsync("5K to 10K Distance First");
    await SaveShowcaseAsync("tr-025-profile-plan-calendar.png");

    await Page.SelectActiveRunnerAsync("Second Runner");
    await Expect(Page.Locator(".calendar-option-shell--program")).ToHaveCountAsync(0);
    await Page.SelectActiveRunnerAsync("Demo Runner");
    await Expect(Page.Locator(".calendar-option-shell--program").First).ToContainTextAsync("5K to 10K Distance First");

    await Page.SetViewportSizeAsync(440, 956);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.DOMContentLoaded });
    ILocator myPlansButton = Page.GetByRole(AriaRole.Button, new() { Name = "My training plans", Exact = true });
    await Expect(myPlansButton).ToBeVisibleAsync();
    await myPlansButton.ClickAsync();
    await AssertNoHorizontalOverflowAsync();
    LocatorBoundingBoxResult? firstCard = await Page.Locator(".program-card").First.BoundingBoxAsync();
    Assert.NotNull(firstCard);
    Assert.True(firstCard.Width >= 44 && firstCard.Height >= 44);
    await SaveShowcaseAsync("tr-025-profile-plan-mobile.png");
  }

  private async Task<Guid> CreateDemoProfileAsync(string displayName)
  {
    using HttpClient client = new() { BaseAddress = gateway.BaseAddress };
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/profiles", new
    {
      operationId = Guid.NewGuid(),
      displayName,
      unitSystem = "Metric",
      weightKilograms = 70,
      maximumHeartRateBpm = 190,
      maximumSpeedKph = 12.0,
      heartRateZones = new[]
      {
        new { number = 1, name = "Warm up", minimumBpm = 95, maximumBpm = 113 },
        new { number = 2, name = "Easy", minimumBpm = 114, maximumBpm = 132 },
        new { number = 3, name = "Aerobic", minimumBpm = 133, maximumBpm = 151 },
        new { number = 4, name = "Threshold", minimumBpm = 152, maximumBpm = 170 },
        new { number = 5, name = "Maximum", minimumBpm = 171, maximumBpm = 190 },
      },
      expectedVersion = (int?)null,
    });
    Assert.Equal(HttpStatusCode.Created, response.StatusCode);
    using JsonDocument document = JsonDocument.Parse(await response.Content.ReadAsStreamAsync());
    return document.RootElement.GetProperty("id").GetGuid();
  }

  private async Task AssertNoHorizontalOverflowAsync()
  {
    double overflow = await Page.EvaluateAsync<double>("() => document.documentElement.scrollWidth - document.documentElement.clientWidth");
    Assert.InRange(overflow, 0, 1);
  }

  private Task SaveShowcaseAsync(string fileName)
  {
    string directory = Path.Combine(gateway.ProjectRoot, "screenshots", "showcase");
    Directory.CreateDirectory(directory);
    return Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(directory, fileName),
      FullPage = true,
    });
  }
}
