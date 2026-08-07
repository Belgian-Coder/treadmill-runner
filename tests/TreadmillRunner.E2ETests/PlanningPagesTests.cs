using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;
using Xunit.Abstractions;

namespace TreadmillRunner.E2ETests;

public sealed class PlanningPagesTests(GatewayFixture gateway, ITestOutputHelper output) : PageTest, IClassFixture<GatewayFixture>
{
  public static TheoryData<string, int, int> Viewports => new()
  {
    { "planning-iphone17-pro-max", 440, 956 },
    { "planning-tablet", 1180, 820 },
    { "planning-desktop", 1920, 1080 },
  };

  [Theory]
  [MemberData(nameof(Viewports))]
  [Trait("Category", "Browser")]
  public async Task Planning_pages_are_responsive_touchable_and_keep_browser_profile_selection(
    string name,
    int width,
    int height)
  {
    Page.Console += (_, message) =>
    {
      if (string.Equals(message.Type, "error", StringComparison.OrdinalIgnoreCase))
      {
        output.WriteLine("Browser console error: {0}", message.Text);
      }
    };
    Page.PageError += (_, exception) => output.WriteLine("Browser page error: {0}", exception);

    await Page.SetViewportSizeAsync(width, height);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/profiles").AbsoluteUri, new PageGotoOptions
    {
      WaitUntil = WaitUntilState.NetworkIdle,
    });

    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Profiles", Exact = true })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "New profile", Exact = true }).ClickAsync();
    string profileName = $"{name}-{Guid.NewGuid():N}";
    await Page.GetByLabel("Display name").FillAsync(profileName);
    await Page.GetByLabel("Maximum heart rate").FillAsync("190");
    await Page.GetByLabel("Maximum heart rate").BlurAsync();
    await Page.GetByText("Advanced heart-rate settings", new() { Exact = true }).ClickAsync();
    ILocator zoneEditor = Page.GetByRole(AriaRole.Group, new() { Name = "Heart-rate zones", Exact = true });
    await Expect(zoneEditor.GetByLabel("Name").Nth(0)).ToHaveValueAsync("Warm up");
    await Expect(zoneEditor.GetByLabel("Minimum bpm").Nth(0)).ToHaveValueAsync("95");
    await Expect(zoneEditor.GetByLabel("Maximum bpm").Nth(0)).ToHaveValueAsync("113");
    await Expect(zoneEditor.GetByLabel("Name").Nth(4)).ToHaveValueAsync("Maximum");
    await Expect(zoneEditor.GetByLabel("Maximum bpm").Nth(4)).ToHaveValueAsync("190");
    await Page.GetByLabel("Maximum heart rate").FillAsync("200");
    await Page.GetByLabel("Maximum heart rate").BlurAsync();
    await Expect(zoneEditor.GetByLabel("Minimum bpm").Nth(0)).ToHaveValueAsync("100");
    await Expect(zoneEditor.GetByLabel("Maximum bpm").Nth(0)).ToHaveValueAsync("119");
    await Expect(zoneEditor.GetByLabel("Maximum bpm").Nth(4)).ToHaveValueAsync("200");
    await zoneEditor.GetByLabel("Name").Nth(0).FillAsync("Recovery");
    await zoneEditor.GetByLabel("Name").Nth(0).BlurAsync();
    await Expect(Page.GetByText("Custom zones — reset anytime.", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(zoneEditor.GetByLabel("Name").Nth(0)).ToHaveValueAsync("Recovery");
    await Page.GetByLabel("Maximum heart rate").FillAsync("190");
    await Page.GetByLabel("Maximum heart rate").BlurAsync();
    await Expect(zoneEditor.GetByLabel("Name").Nth(0)).ToHaveValueAsync("Recovery");
    await Expect(zoneEditor.GetByLabel("Minimum bpm").Nth(0)).ToHaveValueAsync("100");
    await Page.GetByRole(AriaRole.Button, new() { Name = "Reset to suggested zones", Exact = true }).ClickAsync();
    await Expect(zoneEditor.GetByLabel("Name").Nth(0)).ToHaveValueAsync("Warm up");
    await Expect(zoneEditor.GetByLabel("Minimum bpm").Nth(0)).ToHaveValueAsync("95");
    await Expect(Page.GetByText("Z1–Z5 update automatically.", new() { Exact = true })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Create profile" }).ClickAsync();
    await Expect(Page.GetByText($"{profileName} saved.")).ToBeVisibleAsync();
    Assert.False(string.IsNullOrWhiteSpace(await Page.EvaluateAsync<string?>(
      "() => localStorage.getItem('treadmillrunner.active-profile')")));
    await AssertTouchTargetsAsync(Page.GetByRole(AriaRole.Button), name);
    await AssertNoOverflowAsync();
    await ScreenshotAsync($"{name}-profiles.png");

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts/new").AbsoluteUri);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "New workout" })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Add repeat block" }).ClickAsync();
    await Expect(Page.Locator(".step-card > summary").Filter(new() { HasText = "Repeat block 2" })).ToBeVisibleAsync();
    await Page.Locator("select").Nth(0).SelectOptionAsync("distance");
    await Page.Locator("select").Nth(1).SelectOptionAsync("ramp");
    await Page.Locator("select").Nth(2).SelectOptionAsync("ramp");
    await Expect(Page.GetByLabel("Kilometers")).ToBeVisibleAsync();
    await Expect(Page.GetByLabel("End speed (km/h)")).ToBeVisibleAsync();
    await Expect(Page.GetByLabel("End incline (%)")).ToBeVisibleAsync();
    await Expect(Page.GetByText("Start from an existing workout", new() { Exact = true })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Duplicate", Exact = true }).First.ClickAsync();
    ILocator rootBlocks = Page.Locator("section[aria-labelledby='blocks-title'] > .step-list > .step-card");
    await Expect(rootBlocks).ToHaveCountAsync(3);
    await rootBlocks.Nth(1).Locator("summary").ClickAsync();
    await rootBlocks.Nth(1).Locator("select").First.SelectOptionAsync("time");
    await Expect(rootBlocks.Nth(0).Locator("select").First).ToHaveValueAsync("distance");
    await rootBlocks.Nth(1).GetByRole(AriaRole.Button, new() { Name = "Remove", Exact = true }).ClickAsync();
    await Expect(rootBlocks).ToHaveCountAsync(2);
    await Page.GetByRole(AriaRole.Button, new() { Name = "Move down", Exact = true }).First.ClickAsync();
    await Expect(Page.Locator(".sticky-actions")).ToHaveCSSAsync("position", "static");
    LocatorBoundingBoxResult? editorActions = await Page.Locator(".sticky-actions").BoundingBoxAsync();
    LocatorBoundingBoxResult? finalStep = await rootBlocks.Last.BoundingBoxAsync();
    Assert.NotNull(editorActions);
    Assert.NotNull(finalStep);
    Assert.True(editorActions.Y >= finalStep.Y + finalStep.Height - 1, "Editor actions must not overlap workout fields.");
    await AssertNoOverflowAsync();
    await ScreenshotAsync($"{name}-workout-editor.png");

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/calendar").AbsoluteUri);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Calendar", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "New schedule" })).ToBeVisibleAsync();
    if (width <= 1200)
    {
      await Expect(Page.Locator("[role='grid'][aria-label^='Training calendar']")).ToBeHiddenAsync();
      if (await Page.Locator(".calendar-agenda-week").CountAsync() > 0)
      {
        await Expect(Page.Locator("[aria-label^='Training agenda']")).ToBeVisibleAsync();
      }
      else
      {
        await Expect(Page.GetByText("No workouts are planned this month.", new() { Exact = false })).ToBeVisibleAsync();
      }
    }
    else
    {
      await Expect(Page.Locator("[role='grid'][aria-label^='Training calendar']")).ToBeVisibleAsync();
      Assert.True(await Page.GetByRole(AriaRole.Gridcell).CountAsync() >= 35);
    }
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Previous month", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Next month", Exact = true })).ToBeVisibleAsync();
    await AssertTouchTargetsAsync(Page.GetByRole(AriaRole.Button), name);
    await AssertNoOverflowAsync();
    await ScreenshotAsync($"{name}-calendar.png");
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Workout_library_exposes_descriptions_stats_and_search_filters()
  {
    string unique = $"Searchable hills {Guid.NewGuid():N}";
    using HttpClient client = new() { BaseAddress = gateway.BaseAddress };
    using HttpResponseMessage created = await client.PostAsJsonAsync("/api/planning/workouts", new
    {
      operationId = Guid.NewGuid(),
      name = unique,
      description = "Short incline progression for recovery days",
      blocks = new[]
      {
        new
        {
          kind = "repeat", repetitions = 3,
          blocks = new[]
          {
            new
            {
              kind = "step", repetitions = 1, blocks = Array.Empty<object>(), goalKind = "time", goalValue = 2.0,
              speedKind = "fixed", speedStartKph = 5.0, speedEndKph = 0.0, heartRateMinimumBpm = 0,
              heartRateMaximumBpm = 0, heartRateZoneNumber = 0, heartRateInitialSpeedKph = 0.0,
              heartRateMinimumSpeedKph = 0.0, heartRateMaximumSpeedKph = 0.0, inclineKind = "fixed",
              inclineStartPercent = 1.0, inclineEndPercent = 0.0, cue = "Easy", notes = (string?)null,
            },
            new
            {
              kind = "step", repetitions = 1, blocks = Array.Empty<object>(), goalKind = "time", goalValue = 2.0,
              speedKind = "fixed", speedStartKph = 7.0, speedEndKph = 0.0, heartRateMinimumBpm = 0,
              heartRateMaximumBpm = 0, heartRateZoneNumber = 0, heartRateInitialSpeedKph = 0.0,
              heartRateMinimumSpeedKph = 0.0, heartRateMaximumSpeedKph = 0.0, inclineKind = "fixed",
              inclineStartPercent = 2.0, inclineEndPercent = 0.0, cue = "Strong", notes = (string?)null,
            },
          },
          goalKind = "time", goalValue = 1.0, speedKind = "open", speedStartKph = 0.0,
          speedEndKph = 0.0, heartRateMinimumBpm = 0, heartRateMaximumBpm = 0, heartRateZoneNumber = 0,
          heartRateInitialSpeedKph = 0.0, heartRateMinimumSpeedKph = 0.0, heartRateMaximumSpeedKph = 0.0,
          inclineKind = "fixed", inclineStartPercent = 0.0, inclineEndPercent = 0.0, cue = (string?)null, notes = (string?)null,
        },
      },
    });
    Assert.Equal(HttpStatusCode.Created, created.StatusCode);
    Guid workoutId = (await created.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("workoutId").GetGuid();

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    ILocator card = Page.Locator(".workout-card").Filter(new() { HasText = unique });
    await Expect(card).ToContainTextAsync("Short incline progression for recovery days");
    await Expect(card).ToContainTextAsync("12 min");
    await Expect(card).ToContainTextAsync("Intervals");
    await Expect(card).ToContainTextAsync("5–7 km/h");
    await Expect(card).ToContainTextAsync("1–2% incline");
    await card.GetByRole(AriaRole.Button, new() { Name = "View details", Exact = true }).ClickAsync();
    ILocator details = Page.GetByRole(AriaRole.Dialog);
    await Expect(details).ToBeVisibleAsync();
    await Expect(details).ToContainTextAsync("3 × this pattern");
    await Expect(details).ToContainTextAsync("6 expanded segment(s)");
    await Expect(details).ToContainTextAsync("Easy");
    await Expect(details).ToContainTextAsync("Strong");
    await Page.Keyboard.PressAsync("Escape");
    await Expect(details).ToBeHiddenAsync();
    await card.GetByRole(AriaRole.Button, new() { Name = "View details", Exact = true }).ClickAsync();
    await Expect(details).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Close workout details", Exact = true }).ClickAsync();
    await Expect(details).ToBeHiddenAsync();
    string revisionRoute = $"**/api/planning/workouts/{workoutId:D}/revisions";
    await Page.RouteAsync(revisionRoute, route => route.FulfillAsync(new RouteFulfillOptions { Status = 503, Body = "{}", ContentType = "application/json" }));
    await card.GetByRole(AriaRole.Button, new() { Name = "View details", Exact = true }).ClickAsync();
    await Expect(details.GetByRole(AriaRole.Alert)).ToContainTextAsync("could not be loaded");
    await Page.UnrouteAsync(revisionRoute);
    await details.GetByRole(AriaRole.Button, new() { Name = "Try again", Exact = true }).ClickAsync();
    await Expect(details).ToContainTextAsync("3 × this pattern");
    await details.GetByRole(AriaRole.Button, new() { Name = "Close", Exact = true }).ClickAsync();
    await Expect(details).ToBeHiddenAsync();
    await Page.GetByLabel("Workout structure filter", new() { Exact = true }).SelectOptionAsync("intervals");
    await Expect(card).ToBeVisibleAsync();
    await Page.GetByLabel("Search workouts", new() { Exact = true }).FillAsync("recovery days");
    await Expect(card).ToBeVisibleAsync();
    await Page.GetByLabel("Search workouts", new() { Exact = true }).FillAsync("5–7 km/h");
    await Expect(card).ToBeVisibleAsync();
    await Page.GetByLabel("Search workouts", new() { Exact = true }).FillAsync("does not exist");
    await Expect(card).ToHaveCountAsync(0);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Global_runner_and_plan_calendar_changes_use_one_previewed_action_sheet()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    DateOnly plannedDate = new(2026, 8, 10);
    Guid runId = Guid.NewGuid();
    Guid itemId = Guid.NewGuid();
    Guid revisionId = Guid.NewGuid();
    int applyRequests = 0;
    await Page.RouteAsync("**/api/planning/calendar/series?*", route => route.FulfillAsync(new()
    {
      Status = 200,
      ContentType = "application/json",
      Body = "[]",
    }));
    await Page.RouteAsync("**/api/planning/calendar/program-runs/*/schedule/preview", route => route.FulfillAsync(new()
    {
      Status = 200,
      ContentType = "application/json",
      Body = JsonSerializer.Serialize(new
      {
        runId,
        programItemId = itemId,
        action = "MoveOne",
        runVersion = 4,
        canApply = true,
        message = "Only this session will move; later sessions keep their dates. Warning: 1 date will contain more than one session.",
        impacts = new[] { new { programItemId = itemId, position = 2, currentDate = plannedDate, newDate = plannedDate.AddDays(1), isRepeat = false } },
        collisionDates = new[] { plannedDate.AddDays(1) },
      }, new JsonSerializerOptions(JsonSerializerDefaults.Web)),
    }));
    await Page.RouteAsync("**/api/planning/calendar/program-runs/*/schedule/apply", async route =>
    {
      Interlocked.Increment(ref applyRequests);
      await route.FulfillAsync(new()
      {
        Status = 200,
        ContentType = "application/json",
        Body = JsonSerializer.Serialize(new
        {
          runId,
          programItemId = itemId,
          action = "MoveOne",
          runVersion = 5,
          canApply = true,
          message = "Only this session moved; later sessions kept their dates.",
          impacts = Array.Empty<object>(),
          collisionDates = Array.Empty<DateOnly>(),
        }, new JsonSerializerOptions(JsonSerializerDefaults.Web)),
      });
    });
    await Page.RouteAsync("**/api/planning/calendar/*", async route =>
    {
      string lastSegment = new Uri(route.Request.Url).AbsolutePath.Split('/').Last();
      if (!Guid.TryParse(lastSegment, out _))
      {
        await route.FallbackAsync();
        return;
      }
      await route.FulfillAsync(new()
      {
        Status = 200,
        ContentType = "application/json",
        Body = JsonSerializer.Serialize(new
        {
          profileId = scenario.MarcProfileId,
          from = new DateOnly(2026, 7, 27),
          to = new DateOnly(2026, 9, 6),
          days = new[]
          {
            new
            {
              date = plannedDate,
              options = new[]
              {
                new
                {
                  seriesId = runId, scheduleGroupId = runId, scheduleName = "First 5K", workoutRevisionId = revisionId,
                  workoutName = "Easy foundation", revisionNumber = 1, displayOrder = 0, isSelected = true,
                  source = "Program", programRunId = runId, programItemId = itemId, programPosition = 2, programTotal = 18,
                  weekNumber = 1, phase = "Foundation", programRunVersion = 4, isRepeat = false, originalDate = plannedDate,
                  isCompleted = false,
                },
                new
                {
                  seriesId = runId, scheduleGroupId = runId, scheduleName = "First 5K", workoutRevisionId = revisionId,
                  workoutName = "Earlier foundation", revisionNumber = 1, displayOrder = 1, isSelected = false,
                  source = "Program", programRunId = runId, programItemId = Guid.NewGuid(), programPosition = 1, programTotal = 18,
                  weekNumber = 1, phase = "Foundation", programRunVersion = 4, isRepeat = false, originalDate = plannedDate,
                  isCompleted = true,
                },
              },
            },
          },
        }, new JsonSerializerOptions(JsonSerializerDefaults.Web)),
      });
    });

    await Page.SetViewportSizeAsync(440, 956);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/calendar").AbsoluteUri);
    await Expect(Page.Locator(".profile-context-picker")).ToHaveCountAsync(0);
    await Expect(Page.Locator(".active-runner-picker summary")).ToContainTextAsync("Marc");
    await Page.Locator(".calendar-agenda-week").Filter(new() { HasText = "10 Aug" }).Locator("summary").ClickAsync();
    ILocator manageButton = Page.Locator(".calendar-agenda .calendar-option-manage").First;
    await Expect(manageButton).ToBeVisibleAsync();
    await manageButton.ClickAsync();
    ILocator dialog = Page.GetByRole(AriaRole.Dialog);
    await Expect(dialog).ToContainTextAsync("step 2 of 18");
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Repeat · keep later dates", Exact = false })).ToHaveCountAsync(0);
    await dialog.GetByLabel("New or repeat date").FillAsync("2026-08-11");
    await dialog.GetByRole(AriaRole.Button, new() { Name = "Move only this session", Exact = false }).ClickAsync();
    await Expect(dialog).ToContainTextAsync("Impact preview");
    await Expect(dialog).ToContainTextAsync("Double-session warning");
    Assert.Equal(0, applyRequests);
    string showcaseDirectory = Path.Combine(gateway.ProjectRoot, "screenshots", "showcase");
    Directory.CreateDirectory(showcaseDirectory);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(showcaseDirectory, "tr-027-calendar-mobile.png"),
      FullPage = false,
    });
    await Page.SetViewportSizeAsync(1440, 900);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(showcaseDirectory, "tr-027-calendar-move.png"),
      FullPage = false,
    });
    await dialog.GetByRole(AriaRole.Button, new() { Name = "Confirm change", Exact = true }).ClickAsync();
    await Expect(dialog).ToBeHiddenAsync();
    Assert.Equal(1, applyRequests);
    await Page.GetByRole(AriaRole.Button, new() { Name = "Manage Earlier foundation on 10 August", Exact = true }).ClickAsync();
    await Expect(dialog).ToContainTextAsync("step 1 of 18");
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Repeat · keep later dates", Exact = false })).ToBeVisibleAsync();
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Repeat · shift the rest", Exact = false })).ToBeVisibleAsync();
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Move only this session", Exact = false })).ToHaveCountAsync(0);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Existing_workout_with_nested_repeat_is_rendered_and_preserved_when_saved()
  {
    object Step() => new
    {
      kind = "step",
      repetitions = 1,
      blocks = Array.Empty<object>(),
      goalKind = "time",
      goalValue = 1.0,
      speedKind = "fixed",
      speedStartKph = 7.0,
      speedEndKph = 0.0,
      heartRateMinimumBpm = 0,
      heartRateMaximumBpm = 0,
      heartRateZoneNumber = 0,
      heartRateInitialSpeedKph = 0.0,
      heartRateMinimumSpeedKph = 0.0,
      heartRateMaximumSpeedKph = 0.0,
      inclineKind = "fixed",
      inclineStartPercent = 0.0,
      inclineEndPercent = 0.0,
      cue = (string?)null,
      notes = (string?)null,
    };
    var nestedRepeat = new
    {
      kind = "repeat",
      repetitions = 2,
      blocks = new[] { Step() },
      goalKind = "time",
      goalValue = 0.0,
      speedKind = "open",
      speedStartKph = 0.0,
      speedEndKph = 0.0,
      heartRateMinimumBpm = 0,
      heartRateMaximumBpm = 0,
      heartRateZoneNumber = 0,
      heartRateInitialSpeedKph = 0.0,
      heartRateMinimumSpeedKph = 0.0,
      heartRateMaximumSpeedKph = 0.0,
      inclineKind = "fixed",
      inclineStartPercent = 0.0,
      inclineEndPercent = 0.0,
      cue = (string?)null,
      notes = (string?)null,
    };
    var outerRepeat = new
    {
      kind = "repeat",
      repetitions = 3,
      blocks = new object[] { Step(), nestedRepeat },
      goalKind = "time",
      goalValue = 0.0,
      speedKind = "open",
      speedStartKph = 0.0,
      speedEndKph = 0.0,
      heartRateMinimumBpm = 0,
      heartRateMaximumBpm = 0,
      heartRateZoneNumber = 0,
      heartRateInitialSpeedKph = 0.0,
      heartRateMinimumSpeedKph = 0.0,
      heartRateMaximumSpeedKph = 0.0,
      inclineKind = "fixed",
      inclineStartPercent = 0.0,
      inclineEndPercent = 0.0,
      cue = (string?)null,
      notes = (string?)null,
    };

    using HttpClient client = new() { BaseAddress = gateway.BaseAddress };
    using HttpResponseMessage created = await client.PostAsJsonAsync("/api/planning/workouts", new
    {
      operationId = Guid.NewGuid(),
      name = "Nested starter",
      description = "Nested repeat coverage",
      blocks = new[] { outerRepeat },
    });
    Assert.Equal(HttpStatusCode.Created, created.StatusCode);
    using JsonDocument document = JsonDocument.Parse(await created.Content.ReadAsStreamAsync());
    Guid workoutId = document.RootElement.GetProperty("workoutId").GetGuid();

    await Page.GotoAsync(new Uri(gateway.BaseAddress, $"/workouts/new?workoutId={workoutId:D}").AbsoluteUri);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "New immutable revision" })).ToBeVisibleAsync();
    await Expect(Page.Locator(".step-card > summary").Filter(new() { HasText = "Nested repeat block 2" })).ToBeVisibleAsync();

    await Page.GetByLabel("Workout name").FillAsync("Nested revision");
    await Page.GetByRole(AriaRole.Button, new() { Name = "Save new revision" }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Workouts", Exact = true })).ToBeVisibleAsync();

    JsonElement[] revisions = (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/workouts/{workoutId}/revisions"))!;
    Assert.Equal(2, revisions.Length);
    Assert.Equal("repeat", revisions[1].GetProperty("blocks")[0].GetProperty("blocks")[1].GetProperty("kind").GetString());

    await Page.GotoAsync(new Uri(gateway.BaseAddress, $"/workouts/new?workoutId={workoutId:D}").AbsoluteUri);
    await Page.GetByText("Start from an existing workout", new() { Exact = true }).ClickAsync();
    await Page.GetByRole(AriaRole.Link, new() { Name = "Blank workout Start a new plan", Exact = true }).ClickAsync();
    await Expect(Page).ToHaveURLAsync(new System.Text.RegularExpressions.Regex("/workouts/new$"));
    await Expect(Page.GetByLabel("Workout name", new() { Exact = true })).ToHaveValueAsync(string.Empty);
    await Expect(Page.Locator("section[aria-labelledby='blocks-title'] > .step-list > .step-card")).ToHaveCountAsync(1);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Heart_rate_guidance_can_be_applied_to_selected_steps()
  {
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts/new").AbsoluteUri);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "New workout" })).ToBeVisibleAsync();

    await Page.GetByLabel("Speed target").SelectOptionAsync("heartRate");
    await Expect(Page.GetByLabel("Minimum bpm")).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Note)).ToHaveCountAsync(1);
    await Expect(Page.GetByRole(AriaRole.Note)).ToContainTextAsync("This step follows your live heart rate");

    await Page.GetByRole(AriaRole.Button, new() { Name = "Add custom step" }).ClickAsync();
    await Expect(Page.GetByLabel("Speed target")).ToHaveCountAsync(2);
    await Expect(Page.GetByRole(AriaRole.Note)).ToHaveCountAsync(1);
    await Page.Locator(".step-card").Nth(1).Locator("summary").ClickAsync();
    await Page.GetByLabel("Speed target").Nth(1).SelectOptionAsync("heartRateZone");
    await Expect(Page.GetByRole(AriaRole.Spinbutton, new() { Name = "HR zone", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Note)).ToHaveCountAsync(2);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Garmin_activity_setup_remains_visible_when_optional_resources_are_empty_or_fail()
  {
    Guid profileId = await CreateProfileAsync($"Garmin readiness {Guid.NewGuid():N}");
    int jobsStatus = 200;
    int watchStatus = 204;
    await Page.RouteAsync("**/api/integrations/garmin/activity-upload/profiles/*/status", route =>
      route.FulfillAsync(new RouteFulfillOptions
      {
        Status = 200,
        ContentType = "application/json",
        Body = JsonSerializer.Serialize(new
        {
          profileId,
          connected = false,
          enabled = false,
          accountLabel = (string?)null,
          state = "Disconnected",
          pending = 0,
          confirmed = 0,
          failed = 0,
          unknown = 0,
          lastSuccessAtUtc = (DateTimeOffset?)null,
          lastError = (string?)null,
          version = 0,
          adapterState = "Ready",
          adapterMessage = "Garmin activity upload is ready to connect.",
          canConnect = true,
        }),
      }));
    await Page.RouteAsync("**/api/integrations/garmin/activity-upload/profiles/*/jobs", route =>
      route.FulfillAsync(new RouteFulfillOptions
      {
        Status = jobsStatus,
        ContentType = "application/json",
        Body = jobsStatus == 200 ? "[]" : "{\"error\":\"temporary\"}",
      }));
    await Page.RouteAsync("**/api/integrations/garmin/watch/profiles/*", route =>
      route.FulfillAsync(new RouteFulfillOptions
      {
        Status = watchStatus,
        ContentType = "application/json",
        Body = watchStatus == 204 ? string.Empty : "{\"error\":\"temporary\"}",
      }));

    await Page.GotoAsync(new Uri(gateway.BaseAddress, $"/profiles?garmin=connected&profileId={profileId:D}").AbsoluteUri,
      new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });

    ILocator panel = Page.GetByRole(AriaRole.Region, new() { Name = "Garmin activity upload", Exact = true });
    await Expect(panel.GetByLabel("Garmin email", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(panel.GetByLabel("Garmin password", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(panel).ToContainTextAsync("Experimental");
    await Expect(panel).ToContainTextAsync("Private-LAN HTTP setup is allowed but is not encrypted.");
    await Expect(panel.GetByRole(AriaRole.Button, new() { Name = "Connect Garmin", Exact = true })).ToBeVisibleAsync();
    await Expect(panel).Not.ToContainTextAsync("Activity-upload status is unavailable.");

    jobsStatus = 503;
    await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Expect(panel.GetByLabel("Garmin email", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Recent Garmin upload jobs are temporarily unavailable", new() { Exact = false })).ToBeVisibleAsync();

    jobsStatus = 200;
    watchStatus = 503;
    await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Expect(panel.GetByLabel("Garmin email", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Watch pairing status is temporarily unavailable", new() { Exact = false })).ToBeVisibleAsync();
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Browser_drafts_are_bounded_expiring_profile_scoped_and_no_service_worker_is_registered()
  {
    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    Assert.True(await Page.EvaluateAsync<bool>("() => treadmillRunnerDrafts.save('profile-a.workout.new', '{\"name\":\"Draft A\"}')"));
    Assert.Equal("{\"name\":\"Draft A\"}", await Page.EvaluateAsync<string?>("() => treadmillRunnerDrafts.load('profile-a.workout.new')"));
    Assert.Null(await Page.EvaluateAsync<string?>("() => treadmillRunnerDrafts.load('profile-b.workout.new')"));
    Assert.False(await Page.EvaluateAsync<bool>("() => treadmillRunnerDrafts.save('oversized', 'x'.repeat(262145))"));
    await Page.EvaluateAsync("() => localStorage.setItem('treadmillrunner.draft.v1.corrupt', '{broken')");
    Assert.Null(await Page.EvaluateAsync<string?>("() => treadmillRunnerDrafts.load('corrupt')"));
    await Page.EvaluateAsync("() => localStorage.setItem('treadmillrunner.draft.v1.expired', JSON.stringify({schemaVersion:1,savedAtUtc:new Date(Date.now()-31*86400000).toISOString(),payload:'{}'}))");
    Assert.Null(await Page.EvaluateAsync<string?>("() => treadmillRunnerDrafts.load('expired')"));
    int registrations = await Page.EvaluateAsync<int>("async () => navigator.serviceWorker ? (await navigator.serviceWorker.getRegistrations()).length : 0");
    Assert.Equal(0, registrations);
    string manifest = await Page.EvaluateAsync<string>("async () => await (await fetch('/manifest.webmanifest', {cache:'no-store'})).text()");
    Assert.Contains("standalone", manifest, StringComparison.Ordinal);
  }

  private async Task<Guid> CreateProfileAsync(string name)
  {
    using HttpClient client = new() { BaseAddress = gateway.BaseAddress };
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/profiles", new
    {
      operationId = Guid.NewGuid(),
      displayName = name,
      unitSystem = "Metric",
      weightKilograms = 70,
      maximumHeartRateBpm = 190,
      maximumSpeedKph = 10,
      heartRateZones = new[]
      {
        new { number = 1, name = "Warm up", minimumBpm = 95, maximumBpm = 113 },
        new { number = 2, name = "Easy", minimumBpm = 114, maximumBpm = 132 },
        new { number = 3, name = "Aerobic", minimumBpm = 133, maximumBpm = 151 },
        new { number = 4, name = "Threshold", minimumBpm = 152, maximumBpm = 170 },
        new { number = 5, name = "Maximum", minimumBpm = 171, maximumBpm = 190 },
      },
      expectedVersion = (int?)null,
      heartRateIncreaseStepKph = 0.2,
      heartRateIncreaseCooldownSeconds = 30,
      heartRateDecreaseStepKph = 0.5,
      heartRateDecreaseCooldownSeconds = 15,
    });
    response.EnsureSuccessStatusCode();
    JsonElement profile = await response.Content.ReadFromJsonAsync<JsonElement>();
    return profile.GetProperty("id").GetGuid();
  }

  private async Task AssertNoOverflowAsync()
  {
    string[] offenders = await Page.EvaluateAsync<string[]>("""
      () => {
        const width = document.documentElement.clientWidth;
        if (document.documentElement.scrollWidth <= width + 1) return [];
        return [...document.querySelectorAll('body *')]
          .filter(element => {
            const rect = element.getBoundingClientRect();
            return rect.right > width + 1 || rect.left < -1;
          })
          .slice(0, 8)
          .map(element => {
            const rect = element.getBoundingClientRect();
            return `${element.tagName.toLowerCase()}.${element.className || '-'} left=${rect.left.toFixed(1)} right=${rect.right.toFixed(1)} text=${(element.textContent || '').trim().slice(0, 60)}`;
          });
      }
      """);
    Assert.True(offenders.Length == 0, $"Horizontal overflow: {string.Join(" | ", offenders)}");
  }

  private static async Task AssertTouchTargetsAsync(ILocator controls, string viewport)
  {
    int count = await controls.CountAsync();
    for (int index = 0; index < count; index++)
    {
      ILocator control = controls.Nth(index);
      if (!await control.IsVisibleAsync())
      {
        continue;
      }

      LocatorBoundingBoxResult? box = await control.BoundingBoxAsync();
      Assert.NotNull(box);
      Assert.True(box.Width >= 44, $"Button {index} width was {box.Width}px at {viewport}.");
      Assert.True(box.Height >= 44, $"Button {index} height was {box.Height}px at {viewport}.");
    }
  }

  private async Task ScreenshotAsync(string fileName)
  {
    string directory = Path.Combine(gateway.ProjectRoot, "validation", "playwright", "accepted");
    Directory.CreateDirectory(directory);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(directory, fileName),
      FullPage = true,
    });
  }
}
