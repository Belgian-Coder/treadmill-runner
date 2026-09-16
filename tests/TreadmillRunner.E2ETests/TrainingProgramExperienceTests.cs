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

    ILocator planOverflow = first5KCard.Locator("details.card-overflow");
    ILocator planOverflowTrigger = planOverflow.Locator("summary");
    await planOverflowTrigger.ClickAsync();
    await first5KCard.GetByRole(AriaRole.Button, new() { Name = "Archive", Exact = true }).ClickAsync();
    await Expect(planOverflow).Not.ToHaveAttributeAsync("open", "");
    ILocator archiveDialog = first5KCard.GetByRole(AriaRole.Alertdialog, new() { Name = "Archive First 5K?", Exact = true });
    ILocator archiveCancel = archiveDialog.GetByRole(AriaRole.Button, new() { Name = "Cancel", Exact = true });
    await Expect(archiveCancel).ToBeFocusedAsync();
    await archiveCancel.PressAsync("Escape");
    await Expect(archiveDialog).ToBeHiddenAsync();
    await Expect(planOverflowTrigger).ToBeFocusedAsync();
    await planOverflowTrigger.ClickAsync();
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
    ILocator closeEditor = Page.GetByRole(AriaRole.Button, new() { Name = "Close training plan editor", Exact = true });
    await closeEditor.ClickAsync();
    ILocator discardDialog = Page.GetByRole(AriaRole.Alertdialog, new() { Name = "Discard unsaved changes?", Exact = true });
    await Expect(discardDialog).ToContainTextAsync("Discard unsaved changes?");
    ILocator keepEditing = discardDialog.GetByRole(AriaRole.Button, new() { Name = "Keep editing", Exact = true });
    await Expect(keepEditing).ToBeFocusedAsync();
    await Expect(keepEditing).ToBeInViewportAsync();
    await keepEditing.ClickAsync();
    await Expect(closeEditor).ToBeFocusedAsync();
    await Page.SetViewportSizeAsync(956, 440);
    await closeEditor.ClickAsync();
    await Expect(keepEditing).ToBeFocusedAsync();
    await keepEditing.PressAsync("Escape");
    await Expect(discardDialog).ToBeHiddenAsync();
    await Expect(closeEditor).ToBeFocusedAsync();
    await Page.SetViewportSizeAsync(390, 844);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Edit training plan", Exact = true })).ToBeVisibleAsync();

    Guid scheduledProgramRunId = Guid.NewGuid();
    Guid scheduledProgramItemId = Guid.NewGuid();
    DateOnly today = DateOnly.FromDateTime(DateTime.Today);
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
          from = today,
          to = today.AddDays(30),
          days = new[]
          {
            new
            {
              date = today,
              options = new[]
              {
                new
                {
                  seriesId = scheduledProgramRunId,
                  scheduleGroupId = scheduledProgramRunId,
                  scheduleName = "First 5K",
                  workoutRevisionId = scenario.FeaturedWorkoutRevisionId,
                  workoutName = GalleryScenario.FeaturedWorkoutName,
                  revisionNumber = 1,
                  displayOrder = 0,
                  isSelected = true,
                  source = "Program",
                  programRunId = scheduledProgramRunId,
                  programItemId = scheduledProgramItemId,
                  programPosition = 2,
                  programTotal = 18,
                  weekNumber = 1,
                  phase = "Foundation",
                  programRunVersion = 4,
                  isRepeat = false,
                  originalDate = today,
                  isCompleted = false,
                  programWeekdayMask = 37,
                },
              },
            },
          },
        }, new JsonSerializerOptions(JsonSerializerDefaults.Web)),
      });
    });
    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri);
    await Expect(Page.Locator(".active-runner-picker summary")).ToContainTextAsync("Marc");
    ILocator recommendation = Page.GetByLabel("Recommended next run", new() { Exact = true });
    await Expect(recommendation).ToContainTextAsync("Next for Marc");
    await Expect(recommendation).ToContainTextAsync(GalleryScenario.FeaturedWorkoutName);
    string recommendationText = await recommendation.InnerTextAsync();
    Assert.Single(System.Text.RegularExpressions.Regex.Matches(
      recommendationText,
      "workout\\s+2\\s+of\\s+18",
      System.Text.RegularExpressions.RegexOptions.IgnoreCase).Cast<System.Text.RegularExpressions.Match>());

    ILocator chooseRecommendation = recommendation.GetByRole(AriaRole.Button, new() { Name = "Choose", Exact = true });
    LocatorBoundingBoxResult? recommendationBox = await recommendation.BoundingBoxAsync();
    LocatorBoundingBoxResult? recommendationCopyBox = await recommendation.Locator(".next-run-card__copy").BoundingBoxAsync();
    LocatorBoundingBoxResult? chooseBox = await chooseRecommendation.BoundingBoxAsync();
    Assert.NotNull(recommendationBox);
    Assert.NotNull(recommendationCopyBox);
    Assert.NotNull(chooseBox);
    Assert.True(chooseBox.Y >= recommendationCopyBox.Y + recommendationCopyBox.Height - 1,
      $"The portrait Choose action must wrap below the recommendation copy: card={recommendationBox}, copy={recommendationCopyBox}, action={chooseBox}.");
    Assert.True(chooseBox.Width >= recommendationBox.Width - 24,
      $"The portrait Choose action must span the recommendation card: card={recommendationBox}, action={chooseBox}.");

    ILocator memoryStatusLink = Page.GetByRole(AriaRole.Link, new() { Name = "Review H10 memory status", Exact = true });
    await Expect(memoryStatusLink).ToHaveCSSAsync("color", "rgb(142, 230, 196)");
    await ScreenshotAsync("home-recommendation-iphone-portrait.png");

    await Page.SetViewportSizeAsync(844, 390);
    LocatorBoundingBoxResult? landscapeRunnerBox = await Page.Locator(".active-runner-picker summary").BoundingBoxAsync();
    LocatorBoundingBoxResult? landscapeChooseBox = await chooseRecommendation.BoundingBoxAsync();
    Assert.NotNull(landscapeRunnerBox);
    Assert.NotNull(landscapeChooseBox);
    Assert.True(landscapeRunnerBox.Height >= 44,
      $"The landscape runner selector must retain a 44px touch target: {landscapeRunnerBox}.");
    Assert.True(landscapeChooseBox.Y + landscapeChooseBox.Height <= 390,
      $"The recommended run action must remain in the initial short-landscape viewport: {landscapeChooseBox}.");
    await ScreenshotAsync("home-recommendation-iphone-landscape.png");
    await Page.SetViewportSizeAsync(390, 844);

    await chooseRecommendation.ClickAsync();
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
    ILocator startCancel = confirmation.GetByRole(AriaRole.Button, new() { Name = "Cancel", Exact = true });
    await Expect(startCancel).ToBeFocusedAsync();
    await startCancel.PressAsync("Escape");
    await Expect(confirmation).ToBeHiddenAsync();
    await Expect(stronger10K.GetByRole(AriaRole.Button, new() { Name = "Start plan", Exact = true })).ToBeFocusedAsync();
  }

  private async Task ScreenshotAsync(string fileName)
  {
    string directory = Path.Combine(gateway.ProjectRoot, "validation", "playwright", "accepted");
    Directory.CreateDirectory(directory);
    await Page.ScreenshotAsync(new PageScreenshotOptions { Path = Path.Combine(directory, fileName), FullPage = true });
  }
}
