using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;

namespace TreadmillRunner.E2ETests;

public sealed class TrainingProgramExperienceTests(GatewayFixture gateway)
  : PageTest, IClassFixture<GatewayFixture>
{
  [Fact]
  [Trait("Category", "Browser")]
  public async Task Plan_editor_creates_a_detailed_internal_workout_and_adds_it_without_using_the_standalone_library()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    await Page.SetViewportSizeAsync(1920, 1080);
    string planName = $"Direct plan {Guid.NewGuid():N}";
    string workoutName = $"Direct session {Guid.NewGuid():N}";

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.GetByRole(AriaRole.Button, new() { Name = "New training plan", Exact = true }).ClickAsync();
    await Page.GetByLabel("Plan name", new() { Exact = true }).FillAsync(planName);
    await Page.GetByText("Household template", new() { Exact = true }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Create workout", Exact = true }).ClickAsync();

    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "New workout", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Start from an existing workout", new() { Exact = true })).ToHaveCountAsync(0);
    await Page.GetByLabel("Workout name", new() { Exact = true }).FillAsync(workoutName);
    ILocator createWorkout = Page.Locator(".workout-builder__save button[type='submit']");
    await Expect(createWorkout).ToBeVisibleAsync();
    await Expect(createWorkout).ToBeEnabledAsync();
    await createWorkout.ClickAsync();

    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "New training plan", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByLabel("Plan name", new() { Exact = true })).ToHaveValueAsync(planName);
    await Expect(Page.Locator(".program-item")).ToHaveCountAsync(1);
    await Expect(Page.Locator(".program-item").First).ToContainTextAsync(workoutName);
    await ScreenshotAsync("training-plan-builder-desktop.png");
    await Page.GetByRole(AriaRole.Button, new() { Name = "Create plan", Exact = true }).ClickAsync();
    await Expect(Page.Locator(".program-card").Filter(new() { HasText = planName })).ToBeVisibleAsync();

    using HttpClient client = new() { BaseAddress = gateway.BaseAddress };
    JsonElement[] standalone = (await client.GetFromJsonAsync<JsonElement[]>("/api/planning/workouts"))!;
    Assert.DoesNotContain(standalone, workout => workout.GetProperty("name").GetString() == workoutName);

    JsonElement[] plans = (await client.GetFromJsonAsync<JsonElement[]>("/api/planning/programs"))!;
    JsonElement createdPlan = Assert.Single(plans, plan => plan.GetProperty("name").GetString() == planName);
    Assert.Equal(JsonValueKind.Null, createdPlan.GetProperty("ownerProfileId").ValueKind);
    using HttpResponseMessage archived = await client.PostAsJsonAsync(
      $"/api/planning/programs/{createdPlan.GetProperty("id").GetGuid():D}/archive",
      new { operationId = Guid.NewGuid() });
    archived.EnsureSuccessStatusCode();
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Training_plans_are_touch_editable_and_recommend_the_runners_next_exact_workout()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    await scenario.InstallVisualDataRoutesAsync(Page);
    await Page.SetViewportSizeAsync(390, 844);

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts").AbsoluteUri);
    await Page.GetByRole(AriaRole.Button, new() { Name = "My training plans", Exact = true }).ClickAsync();
    await Expect(Page.GetByText("First 5K", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Stronger 10K", new() { Exact = true })).ToBeVisibleAsync();
    Assert.True(await Page.Locator(".program-card").CountAsync() >= 2);
    await Expect(Page.GetByText("0 complete · 3 remaining", new() { Exact = true })).ToBeVisibleAsync();
    ILocator first5KCard = Page.Locator(".program-card").Filter(new() { HasText = "First 5K" });
    await first5KCard.Locator(".program-card__select").ClickAsync();
    ILocator planDialog = Page.GetByRole(AriaRole.Dialog);
    await Expect(planDialog.GetByRole(AriaRole.Heading, new() { Name = "First 5K", Exact = true })).ToBeVisibleAsync();
    await Expect(planDialog.Locator(".program-session-summary-list li")).ToHaveCountAsync(3);
    await planDialog.GetByRole(AriaRole.Button, new() { Name = "Close training plan details", Exact = true }).ClickAsync();

    await first5KCard.Locator("details.card-overflow summary").ClickAsync();
    await first5KCard.GetByRole(AriaRole.Button, new() { Name = "Edit plan", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Edit training plan", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.Locator(".program-item")).ToHaveCountAsync(3);
    ILocator items = Page.Locator(".program-item");
    await Expect(items.Nth(0)).ToContainTextAsync(GalleryScenario.FeaturedWorkoutName);
    await items.Nth(0).GetByRole(AriaRole.Button, new() { Name = $"Move {GalleryScenario.FeaturedWorkoutName} down" }).ClickAsync();
    await Expect(items.Nth(1)).ToContainTextAsync(GalleryScenario.FeaturedWorkoutName);
    await items.Nth(1).GetByRole(AriaRole.Button, new() { Name = $"Move {GalleryScenario.FeaturedWorkoutName} up" }).ClickAsync();
    await Expect(items.Nth(0)).ToContainTextAsync(GalleryScenario.FeaturedWorkoutName);
    await Page.GetByLabel("Description", new() { Exact = true }).FillAsync("Unsaved mobile edit");
    await Page.GetByRole(AriaRole.Button, new() { Name = "Close training plan editor", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Alertdialog)).ToContainTextAsync("Discard unsaved changes?");
    await Page.GetByRole(AriaRole.Button, new() { Name = "Keep editing", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Edit training plan", Exact = true })).ToBeVisibleAsync();

    await Page.RouteAsync("**/api/planning/calendar/**", route =>
    {
      string path = new Uri(route.Request.Url).AbsolutePath;
      if (path.EndsWith("/calendar/series", StringComparison.OrdinalIgnoreCase))
      {
        return route.ContinueAsync();
      }

      return route.FulfillAsync(new RouteFulfillOptions
      {
        Status = 200,
        ContentType = "application/json",
        Body = JsonSerializer.Serialize(new
        {
          profileId = scenario.MarcProfileId,
          from = DateOnly.FromDateTime(DateTime.Today),
          to = DateOnly.FromDateTime(DateTime.Today.AddDays(30)),
          days = Array.Empty<object>(),
        }, new JsonSerializerOptions(JsonSerializerDefaults.Web)),
      });
    });
    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri);
    await Expect(Page.Locator(".active-runner-picker summary")).ToContainTextAsync("Marc");
    ILocator recommendation = Page.GetByLabel("Recommended next run", new() { Exact = true });
    await Expect(recommendation).ToContainTextAsync("Next for Marc");
    await Expect(recommendation).ToContainTextAsync(GalleryScenario.FeaturedWorkoutName);
    await recommendation.GetByRole(AriaRole.Button, new() { Name = "Choose", Exact = true }).ClickAsync();
    await Expect(Page.GetByLabel("Selected workout", new() { Exact = true }))
      .ToHaveTextAsync(GalleryScenario.FeaturedWorkoutName);

    await Page.OpenRunChoicesAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Training plans", Exact = true }).ClickAsync();
    ILocator first5K = Page.Locator(".run-program-card").Filter(new() { HasText = "First 5K" });
    await Expect(first5K).ToContainTextAsync("0/3 complete");
    await Expect(first5K.GetByRole(AriaRole.Button, new() { Name = "Next workout", Exact = true })).ToBeVisibleAsync();

    int immediateStartRequests = 0;
    await Page.RouteAsync("**/api/planning/programs/*/start", route =>
    {
      Interlocked.Increment(ref immediateStartRequests);
      return route.ContinueAsync();
    });
    ILocator stronger10K = Page.Locator(".run-program-card").Filter(new() { HasText = "Stronger 10K" });
    await stronger10K.GetByRole(AriaRole.Button, new() { Name = "Start plan", Exact = true }).ClickAsync();
    ILocator confirmation = stronger10K.GetByRole(AriaRole.Alertdialog);
    await Expect(confirmation).ToContainTextAsync("First 5K");
    await Expect(confirmation).ToContainTextAsync("will be abandoned");
    Assert.Equal(0, immediateStartRequests);
    await confirmation.GetByRole(AriaRole.Button, new() { Name = "Cancel", Exact = true }).ClickAsync();
    await Expect(confirmation).ToBeHiddenAsync();
  }

  private async Task ScreenshotAsync(string fileName)
  {
    string directory = Path.Combine(gateway.ProjectRoot, "validation", "playwright", "accepted");
    Directory.CreateDirectory(directory);
    await Page.ScreenshotAsync(new PageScreenshotOptions { Path = Path.Combine(directory, fileName), FullPage = true });
  }
}
