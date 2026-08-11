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
    { "planning-phone", 390, 844 },
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
    string profileName = $"{name}-runner";
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
    await Page.GetByRole(AriaRole.Button, new() { Name = "Add repeat group" }).ClickAsync();
    await Expect(Page.Locator(".builder-repeat-group").Filter(new() { HasText = "Repeat block 2" })).ToBeVisibleAsync();
    await Page.Locator(".builder-step-row").First.Locator(".builder-advanced > summary").ClickAsync();
    await Page.GetByLabel("Goal type").First.SelectOptionAsync("distance");
    await Page.GetByLabel("Speed target").First.SelectOptionAsync("ramp");
    await Page.GetByLabel("Incline target").First.SelectOptionAsync("ramp");
    await Expect(Page.GetByLabel("Distance in kilometers")).ToBeVisibleAsync();
    await Expect(Page.GetByLabel("End speed (km/h)")).ToBeVisibleAsync();
    await Expect(Page.GetByLabel("End incline (%)")).ToBeVisibleAsync();
    await Expect(Page.GetByText("Start from an existing workout", new() { Exact = true })).ToBeVisibleAsync();
    await Page.Locator(".workout-builder__list > .builder-step-row .builder-row-select").First.CheckAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Copy selected", Exact = true }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Insert 1 at end", Exact = true }).ClickAsync();
    ILocator rootBlocks = Page.Locator(".workout-builder__list > .builder-step-row, .workout-builder__list > .builder-repeat-group");
    await Expect(rootBlocks).ToHaveCountAsync(3);
    await Expect(Page.Locator(".workout-preview-chart")).ToHaveAttributeAsync("data-component", "live-progress-chart");
    await Expect(Page.Locator(".workout-preview-chart [data-series='planned-speed']")).ToHaveAttributeAsync("d", new System.Text.RegularExpressions.Regex("^M"));
    await Expect(Page.Locator(".workout-builder__save")).ToHaveCSSAsync("position", "static");
    await AssertNoOverflowAsync();
    await ScreenshotAsync($"{name}-workout-editor.png");

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/calendar").AbsoluteUri);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Calendar", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Plan training", Exact = true })).ToHaveCountAsync(0);
    ILocator calendarView = Page.GetByRole(AriaRole.Group, new() { Name = "Calendar view", Exact = true });
    ILocator agendaView = calendarView.GetByRole(AriaRole.Button, new() { Name = "Agenda", Exact = true });
    ILocator monthView = calendarView.GetByRole(AriaRole.Button, new() { Name = "Month", Exact = true });
    await Expect(agendaView).ToHaveAttributeAsync("aria-pressed", "true");
    await Expect(monthView).ToHaveAttributeAsync("aria-pressed", "false");
    await monthView.ClickAsync();
    await Expect(monthView).ToHaveAttributeAsync("aria-pressed", "true");
    await Expect(agendaView).ToHaveAttributeAsync("aria-pressed", "false");
    ILocator monthTable = Page.Locator("table.calendar-month");
    await Expect(monthTable).ToBeVisibleAsync();
    await Expect(monthTable.Locator("caption")).ToContainTextAsync("Training calendar");
    await Expect(monthTable.Locator("thead th")).ToHaveCountAsync(7);
    Assert.InRange(await monthTable.Locator("tbody tr").CountAsync(), 5, 6);
    Assert.True(await monthTable.Locator("tbody td").CountAsync() >= 35,
      "The native month table must expose all visible calendar days.");
    await AssertTouchTargetsAsync(Page.Locator(".calendar-date-button"), $"{name}-month");
    await agendaView.ClickAsync();
    await Expect(agendaView).ToHaveAttributeAsync("aria-pressed", "true");
    await Expect(monthView).ToHaveAttributeAsync("aria-pressed", "false");
    if (await Page.Locator(".calendar-agenda-week").CountAsync() > 0)
    {
      await Expect(Page.Locator("[aria-label^='Training agenda']")).ToBeVisibleAsync();
    }
    else
    {
      await Expect(Page.GetByText("No workouts are planned this month.", new() { Exact = false })).ToBeVisibleAsync();
    }
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Previous month", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Next month", Exact = true })).ToBeVisibleAsync();
    await AssertTouchTargetsAsync(Page.GetByRole(AriaRole.Button), name);
    await AssertNoOverflowAsync();
    await ScreenshotAsync($"{name}-calendar.png");
  }

  [Theory]
  [InlineData(1920, 1080)]
  [InlineData(390, 844)]
  [InlineData(844, 390)]
  [InlineData(360, 800)]
  [InlineData(320, 800)]
  [Trait("Category", "Browser")]
  public async Task Month_view_keeps_week_rows_touchable_and_bounded_at_supported_widths(int width, int height)
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    await Page.SetViewportSizeAsync(width, height);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/calendar").AbsoluteUri, new PageGotoOptions
    {
      WaitUntil = WaitUntilState.NetworkIdle,
    });

    await Page.GetByRole(AriaRole.Button, new() { Name = "Month", Exact = true }).ClickAsync();
    await Page.EvaluateAsync("() => { window.scrollTo(0, 0); const header = document.querySelector('#site-header'); if (header) header.dataset.scrollState = 'shown'; }");
    await Page.WaitForTimeoutAsync(50);
    ILocator monthTable = Page.Locator("table.calendar-month");
    await Expect(monthTable).ToBeVisibleAsync();
    Assert.InRange(await monthTable.Locator("tbody tr").CountAsync(), 5, 6);
    await AssertTouchTargetsAsync(Page.Locator(".calendar-date-button"), $"calendar-month-{width}x{height}");
    double[] monthViewport = await Page.Locator(".calendar-month-scroll").EvaluateAsync<double[]>(
      "element => [element.clientWidth, element.scrollWidth]");
    ILocator scrollHint = Page.Locator(".calendar-scroll-hint");
    if (width <= 360)
    {
      Assert.True(monthViewport[1] > monthViewport[0] + 1,
        $"The narrow month should preserve 44px day targets inside a deliberate scroll region; " +
        $"clientWidth={monthViewport[0]:0.0}, scrollWidth={monthViewport[1]:0.0}.");
      await Expect(scrollHint).ToBeVisibleAsync();
      await Expect(scrollHint).ToContainTextAsync("Swipe sideways");
    }
    else
    {
      Assert.True(monthViewport[1] <= monthViewport[0] + 1,
        $"The full seven-day month must be visible without a hidden horizontal scroll at {width}x{height}; " +
        $"clientWidth={monthViewport[0]:0.0}, scrollWidth={monthViewport[1]:0.0}.");
      await Expect(scrollHint).ToBeHiddenAsync();
    }
    LocatorBoundingBoxResult? scrollRegionBox = await Page.Locator(".calendar-month-scroll").BoundingBoxAsync();
    LocatorBoundingBoxResult? calendarPanelBox = await Page.Locator(".calendar-agenda-panel").BoundingBoxAsync();
    Assert.NotNull(scrollRegionBox);
    Assert.NotNull(calendarPanelBox);
    Assert.True(scrollRegionBox.X >= calendarPanelBox.X - 1 &&
                scrollRegionBox.X + scrollRegionBox.Width <= calendarPanelBox.X + calendarPanelBox.Width + 1,
      $"The month grid must remain visually contained by its panel at {width}x{height}: " +
      $"grid={scrollRegionBox}, panel={calendarPanelBox}.");
    await Expect(monthTable.Locator("thead th").Last).ToContainTextAsync("Sun");
    await Expect(Page.Locator(".calendar-selected-day")).ToBeVisibleAsync();
    await Expect(Page.Locator(".calendar-marker-key")).ToContainTextAsync("The number shows scheduled sessions.");
    ILocator populatedDates = Page.Locator(".calendar-date-button:has(.calendar-date-count)");
    Assert.True(await populatedDates.CountAsync() > 0, "The populated month fixture must expose visible session counts.");
    await Expect(populatedDates.First.Locator(".calendar-date-count")).ToBeVisibleAsync();
    if (width <= 580)
    {
      ILocator currentMore = Page.Locator(".primary-nav--mobile .nav-more > summary");
      await Expect(currentMore).ToHaveAttributeAsync("aria-current", "page");
      await Expect(currentMore).ToHaveClassAsync(new System.Text.RegularExpressions.Regex("active"));
      await Expect(Page.Locator(".primary-nav--mobile > a.active, .primary-nav--mobile > .nav-more > summary.active"))
        .ToHaveCountAsync(1);
    }
    await AssertNoOverflowAsync();

    string directory = Path.Combine(gateway.ProjectRoot, "output", "playwright", "bug-tr-037");
    Directory.CreateDirectory(directory);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(directory, $"calendar-month-{width}x{height}.png"),
      FullPage = false,
    });
    await Page.EvaluateAsync("() => { const header = document.querySelector('#site-header'); if (!header) return; header.dataset.scrollState = 'shown'; header.style.position = 'static'; header.style.transform = 'none'; window.scrollTo(0, 0); }");
    try
    {
      double[] headerGeometry = await Page.Locator("#site-header").EvaluateAsync<double[]>(
        "header => [window.scrollY, header.getBoundingClientRect().top]");
      Assert.InRange(headerGeometry[0], 0, 1);
      Assert.InRange(headerGeometry[1], -1, 1);
      await Page.ScreenshotAsync(new PageScreenshotOptions
      {
        Path = Path.Combine(directory, $"calendar-month-{width}x{height}-full.png"),
        FullPage = true,
      });
    }
    finally
    {
      await Page.EvaluateAsync("() => { const header = document.querySelector('#site-header'); if (!header) return; header.style.position = ''; header.style.transform = ''; }");
    }
  }

  [Theory]
  [InlineData("calendar-planner-mobile", 440, 956)]
  [InlineData("calendar-planner-desktop", 1440, 900)]
  [Trait("Category", "Browser")]
  public async Task Workouts_owns_searchable_workout_and_training_plan_scheduling_while_calendar_is_management_only(
    string name,
    int width,
    int height)
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    await Page.SetViewportSizeAsync(width, height);
    int startRequests = 0;
    int seriesRequests = 0;
    await Page.RouteAsync("**/api/planning/programs/*/start", route =>
    {
      Interlocked.Increment(ref startRequests);
      return route.FulfillAsync(new RouteFulfillOptions
      {
        Status = 200,
        ContentType = "application/json",
        Body = "{}",
      });
    });
    await Page.RouteAsync("**/api/planning/calendar/series", route =>
    {
      Interlocked.Increment(ref seriesRequests);
      return route.FulfillAsync(new RouteFulfillOptions { Status = 201, ContentType = "application/json", Body = "{}" });
    });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/calendar").AbsoluteUri, new PageGotoOptions
    {
      WaitUntil = WaitUntilState.NetworkIdle,
    });
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Plan training", Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "What do you want to plan?", Exact = true })).ToHaveCountAsync(0);

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.GetByRole(AriaRole.Button, new() { Name = "Standalone workouts", Exact = true }).ClickAsync();
    await Page.GetByLabel("Search workouts", new() { Exact = true }).FillAsync(GalleryScenario.FeaturedWorkoutName);
    await Page.GetByRole(AriaRole.Button, new() { Name = "Schedule", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = $"Schedule {GalleryScenario.FeaturedWorkoutName}", Exact = true })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Save schedule", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Status)).ToContainTextAsync($"{GalleryScenario.FeaturedWorkoutName} was scheduled for Marc.");
    Assert.Equal(1, seriesRequests);

    await Page.GetByRole(AriaRole.Button, new() { Name = "My training plans", Exact = true }).ClickAsync();
    await Page.GetByLabel("Search training plans", new() { Exact = true }).FillAsync("Stronger 10K");
    await Page.GetByRole(AriaRole.Button, new() { Name = "Start plan", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Schedule Stronger 10K", Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByText("will be abandoned", new() { Exact = false })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Abandon and start", Exact = true })).ToBeEnabledAsync();
    await AssertNoOverflowAsync();
    await ScreenshotAsync($"{name}.png");

    Assert.Equal(0, startRequests);
    await Page.GetByRole(AriaRole.Button, new() { Name = "Abandon and start", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Status)).ToContainTextAsync("Stronger 10K is active for Marc.");
    Assert.Equal(1, startRequests);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Workout_library_exposes_descriptions_stats_and_search_filters()
  {
    const string unique = "Searchable hills fixture";
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
    JsonElement createdWorkout = await created.Content.ReadFromJsonAsync<JsonElement>();
    Guid workoutId = createdWorkout.GetProperty("workoutId").GetGuid();
    Guid revisionId = createdWorkout.GetProperty("revisionId").GetGuid();

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.GetByRole(AriaRole.Button, new() { Name = "Standalone workouts", Exact = true }).ClickAsync();
    ILocator card = Page.Locator(".workout-card").Filter(new() { HasText = unique });
    await Expect(card).ToContainTextAsync("Short incline progression for recovery days");
    await Expect(card).ToContainTextAsync("12 min");
    await Expect(card).ToContainTextAsync("Intervals");
    await Expect(card).ToContainTextAsync("5–7 km/h");
    await Expect(card).ToContainTextAsync("1–2% incline");
    await card.GetByRole(AriaRole.Button, new() { Name = unique, Exact = true }).ClickAsync();
    ILocator details = Page.GetByRole(AriaRole.Dialog);
    await Expect(details).ToBeVisibleAsync();
    await Expect(details).ToContainTextAsync("3 × this pattern");
    await Expect(details).ToContainTextAsync("6 expanded segment(s)");
    await Expect(details).ToContainTextAsync("Easy");
    await Expect(details).ToContainTextAsync("Strong");
    await Expect(details.GetByRole(AriaRole.Heading, new() { Name = "Planned graph", Exact = true })).ToBeVisibleAsync();
    await Expect(details.GetByRole(AriaRole.Heading, new() { Name = "All planned changes", Exact = true })).ToBeVisibleAsync();
    await Expect(details.GetByRole(AriaRole.Region, new() { Name = "All planned workout changes", Exact = true }).Locator("tbody tr")).ToHaveCountAsync(6);
    await Expect(details.GetByRole(AriaRole.Button, new() { Name = "Start", Exact = true })).ToHaveCountAsync(0);
    await Page.Keyboard.PressAsync("Escape");
    await Expect(details).ToBeHiddenAsync();
    await card.GetByRole(AriaRole.Button, new() { Name = "View details", Exact = true }).ClickAsync();
    await Expect(details).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Close workout details", Exact = true }).ClickAsync();
    await Expect(details).ToBeHiddenAsync();
    string revisionRoute = $"**/api/planning/workouts/revisions/{revisionId:D}";
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

  [Theory]
  [InlineData("plan-details-desktop", 1920, 1080)]
  [InlineData("plan-details-mobile", 440, 956)]
  [Trait("Category", "Browser")]
  public async Task Training_plan_rows_open_bounded_details_without_expanding_the_library(
    string name,
    int width,
    int height)
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    await Page.SetViewportSizeAsync(width, height);
    int programDetailRequests = 0;
    Page.Request += (_, request) =>
    {
      Uri uri = new(request.Url);
      if (request.Method == "GET" &&
          uri.AbsolutePath.StartsWith("/api/planning/programs/", StringComparison.OrdinalIgnoreCase))
      {
        Interlocked.Increment(ref programDetailRequests);
      }
    };
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });

    await Page.GetByLabel("Search training plans", new() { Exact = true }).FillAsync("Stronger 10K");
    await Expect(Page.Locator(".program-card .template-program-groups")).ToHaveCountAsync(0);
    Assert.Equal(0, programDetailRequests);
    await Page.GetByRole(AriaRole.Button, new() { Name = "View Stronger 10K", Exact = true }).ClickAsync();
    Assert.Equal(1, programDetailRequests);
    ILocator dialog = Page.GetByRole(AriaRole.Dialog);
    await Expect(dialog.GetByRole(AriaRole.Heading, new() { Name = "Stronger 10K", Exact = true })).ToBeVisibleAsync();
    LocatorBoundingBoxResult? bounds = await dialog.BoundingBoxAsync();
    Assert.NotNull(bounds);
    Assert.True(bounds.Width <= width + 1 && bounds.Height <= height + 1, $"Plan details must remain viewport-bounded at {width}x{height}: {bounds}.");
    await ScreenshotAsync($"{name}.png");

    await dialog.Locator(".program-session-detail").First.ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Dialog).GetByText("Workout structure", new() { Exact = true })).ToBeVisibleAsync();
    await AssertNoOverflowAsync();
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Header_runner_switch_refreshes_workouts_profiles_and_history()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    int runnerTwoProgramRequests = 0;
    int runnerTwoHistoryRequests = 0;
    await Page.RouteAsync("**/api/planning/programs?*", async route =>
    {
      if (new Uri(route.Request.Url).Query.Contains(scenario.SecondProfileId.ToString("D"), StringComparison.OrdinalIgnoreCase))
        Interlocked.Increment(ref runnerTwoProgramRequests);
      await route.ContinueAsync();
    });
    await Page.RouteAsync("**/api/history?*", async route =>
    {
      if (new Uri(route.Request.Url).Query.Contains(scenario.SecondProfileId.ToString("D"), StringComparison.OrdinalIgnoreCase))
        Interlocked.Increment(ref runnerTwoHistoryRequests);
      await route.ContinueAsync();
    });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.SelectActiveRunnerAsync("Marc");
    await Page.GetByRole(AriaRole.Button, new() { Name = "My training plans", Exact = true }).ClickAsync();
    ILocator first5K = Page.Locator(".program-card").Filter(new() { HasText = "First 5K" });
    await Expect(first5K.GetByRole(AriaRole.Button, new() { Name = "Restart", Exact = true })).ToBeVisibleAsync();
    await Page.SelectActiveRunnerAsync(GalleryScenario.SecondProfileName);
    await Expect(first5K.GetByRole(AriaRole.Button, new() { Name = "Start plan", Exact = true })).ToBeVisibleAsync();
    Assert.True(runnerTwoProgramRequests > 0);

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/profiles").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    ILocator runnerTwoRow = Page.Locator(".profile-row").Filter(new() { HasText = GalleryScenario.SecondProfileName });
    await Expect(runnerTwoRow.Locator(".integration-badge")).ToContainTextAsync("Active in this browser");

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/history").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "History", Exact = true })).ToBeVisibleAsync();
    Assert.True(runnerTwoHistoryRequests > 0);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Profile_save_network_failure_reenables_save_for_retry()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    await Page.RouteAsync("**/api/planning/profiles/*", async route =>
    {
      if (string.Equals(route.Request.Method, "PUT", StringComparison.OrdinalIgnoreCase))
      {
        await route.AbortAsync();
        return;
      }

      await route.ContinueAsync();
    });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/profiles").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    ILocator marcRow = Page.Locator(".profile-row").Filter(new() { HasText = "Marc" }).First;
    await marcRow.GetByRole(AriaRole.Button, new() { Name = "Edit", Exact = true }).ClickAsync();
    await Page.GetByLabel("Display name", new() { Exact = true }).FillAsync($"Marc retry {Guid.NewGuid():N}");
    ILocator save = Page.GetByRole(AriaRole.Button, new() { Name = "Save profile", Exact = true });
    await save.ClickAsync();
    await Expect(Page.GetByText("The profile could not reach the local gateway.", new() { Exact = false })).ToBeVisibleAsync();
    await Expect(save).ToBeEnabledAsync();
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Calendar_load_failure_is_retryable_without_freezing_the_page()
  {
    Page.PageError += (_, exception) => output.WriteLine("Browser page error: {0}", exception);
    Page.Console += (_, message) => output.WriteLine("Browser console {0}: {1}", message.Type, message.Text);
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    int rangeRequests = 0;
    await Page.RouteAsync($"**/api/planning/calendar/{scenario.MarcProfileId:D}?*", async route =>
    {
      if (Interlocked.Increment(ref rangeRequests) == 1)
      {
        await route.FulfillAsync(new()
        {
          Status = 503,
          ContentType = "application/json",
          Body = "{\"message\":\"temporary calendar failure\"}",
        });
      }
      else
      {
        await route.ContinueAsync();
      }
    });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/calendar").AbsoluteUri);
    await Expect(Page.GetByText("The calendar could not be loaded from the local gateway.", new() { Exact = false })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Retry calendar", Exact = true }).ClickAsync();

    await Expect(Page.GetByText("The calendar could not be loaded from the local gateway.", new() { Exact = false })).ToHaveCountAsync(0);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Calendar", Exact = true })).ToBeVisibleAsync();
    Assert.True(rangeRequests >= 2);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Calendar_ignores_a_stale_profile_response_after_switching_runners()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    var releaseMarcRange = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    await Page.RouteAsync($"**/api/planning/calendar/{scenario.MarcProfileId:D}?*", async route =>
    {
      await releaseMarcRange.Task;
      try { await route.ContinueAsync(); }
      catch (PlaywrightException) { }
    });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/calendar").AbsoluteUri);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Calendar", Exact = true })).ToBeVisibleAsync();
    await Page.SelectActiveRunnerAsync(GalleryScenario.SecondProfileName);
    await Expect(Page.Locator(".active-runner-picker summary")).ToContainTextAsync(GalleryScenario.SecondProfileName);
    await Expect(Page.GetByRole(AriaRole.Status)).ToContainTextAsync($"Showing {GalleryScenario.SecondProfileName}’s calendar.");

    releaseMarcRange.TrySetResult();
    await Page.WaitForTimeoutAsync(250);
    await Expect(Page.Locator(".active-runner-picker summary")).ToContainTextAsync(GalleryScenario.SecondProfileName);
    await Expect(Page.GetByRole(AriaRole.Status)).ToContainTextAsync($"Showing {GalleryScenario.SecondProfileName}’s calendar.");
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
    int defaultDaysApplyRequests = 0;
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
        canApply = false,
        message = "That change would place two plan sessions on 11 Aug 2026. Choose an empty date instead.",
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
    await Page.RouteAsync("**/api/planning/calendar/program-runs/*/default-days/preview", route => route.FulfillAsync(new()
    {
      Status = 200,
      ContentType = "application/json",
      Body = JsonSerializer.Serialize(new
      {
        runId,
        runVersion = 4,
        currentWeekdayMask = 37,
        newWeekdayMask = 42,
        effectiveDate = plannedDate,
        canApply = false,
        message = "The new training days would place two sessions on 13 Aug 2026. Choose different days or move the existing session first.",
        revision = "preview-revision",
        impacts = new[]
        {
          new { programItemId = itemId, position = 2, currentDate = plannedDate, newDate = plannedDate.AddDays(1) },
          new { programItemId = Guid.NewGuid(), position = 3, currentDate = plannedDate.AddDays(2), newDate = plannedDate.AddDays(3) },
        },
        collisionDates = new[] { plannedDate.AddDays(3) },
        preservedExceptionCount = 2,
      }, new JsonSerializerOptions(JsonSerializerDefaults.Web)),
    }));
    await Page.RouteAsync("**/api/planning/calendar/program-runs/*/default-days/apply", async route =>
    {
      Interlocked.Increment(ref defaultDaysApplyRequests);
      await route.FulfillAsync(new()
      {
        Status = 200,
        ContentType = "application/json",
        Body = JsonSerializer.Serialize(new
        {
          runId,
          runVersion = 5,
          currentWeekdayMask = 37,
          newWeekdayMask = 42,
          effectiveDate = plannedDate,
          canApply = true,
          message = "Future generated sessions now use Tuesday, Thursday, and Saturday.",
          revision = "preview-revision",
          impacts = Array.Empty<object>(),
          collisionDates = Array.Empty<DateOnly>(),
          preservedExceptionCount = 2,
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
                  isCompleted = false, programWeekdayMask = 37,
                },
                new
                {
                  seriesId = runId, scheduleGroupId = runId, scheduleName = "First 5K", workoutRevisionId = revisionId,
                  workoutName = "Earlier foundation", revisionNumber = 1, displayOrder = 1, isSelected = false,
                  source = "Program", programRunId = runId, programItemId = Guid.NewGuid(), programPosition = 1, programTotal = 18,
                  weekNumber = 1, phase = "Foundation", programRunVersion = 4, isRepeat = false, originalDate = plannedDate,
                  isCompleted = true, programWeekdayMask = 37,
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
    ILocator agendaWeek = Page.Locator(".calendar-agenda-week").Filter(new() { HasText = "10 Aug" });
    if (!await agendaWeek.EvaluateAsync<bool>("element => element.open"))
      await agendaWeek.Locator("summary").ClickAsync();
    ILocator manageButton = Page.Locator(".calendar-agenda .calendar-option-manage").First;
    await Expect(manageButton).ToBeVisibleAsync();
    await manageButton.ClickAsync();
    ILocator dialog = Page.GetByRole(AriaRole.Dialog);
    await Expect(dialog).ToContainTextAsync("step 2 of 18");
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Repeat · keep later dates", Exact = false })).ToHaveCountAsync(0);
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Remove all upcoming sessions from this plan", Exact = true })).ToBeVisibleAsync();
    await dialog.GetByRole(AriaRole.Button, new() { Name = "Change training days", Exact = true }).ClickAsync();
    await Expect(dialog.GetByRole(AriaRole.Heading, new() { Name = "Change training days", Exact = true })).ToBeVisibleAsync();
    await dialog.GetByRole(AriaRole.Button, new() { Name = "Mon", Exact = true }).ClickAsync();
    await dialog.GetByRole(AriaRole.Button, new() { Name = "Tue", Exact = true }).ClickAsync();
    await dialog.GetByRole(AriaRole.Button, new() { Name = "Preview new schedule", Exact = true }).ClickAsync();
    await Expect(dialog).ToContainTextAsync("Sessions moving");
    await Expect(dialog).ToContainTextAsync("Date unavailable");
    Assert.Equal(0, defaultDaysApplyRequests);
    string showcaseDirectory = ScreenshotArtifactPaths.ShowcaseDirectory(gateway.ProjectRoot);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(showcaseDirectory, "tr-030-training-days-preview.png"),
      FullPage = false,
    });
    await Page.SetViewportSizeAsync(1440, 900);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(showcaseDirectory, "tr-030-training-days-preview-desktop.png"),
      FullPage = false,
    });
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Confirm all future changes", Exact = true })).ToBeDisabledAsync();
    await dialog.GetByRole(AriaRole.Button, new() { Name = "Back", Exact = true }).ClickAsync();
    await dialog.GetByRole(AriaRole.Button, new() { Name = "Back", Exact = true }).ClickAsync();
    Assert.Equal(0, defaultDaysApplyRequests);
    await dialog.GetByLabel("New or repeat date").FillAsync("2026-08-11");
    await dialog.GetByRole(AriaRole.Button, new() { Name = "Move only this session", Exact = false }).ClickAsync();
    await Expect(dialog).ToContainTextAsync("Impact preview");
    await Expect(dialog).ToContainTextAsync("Date unavailable");
    Assert.Equal(0, applyRequests);
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
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Confirm change", Exact = true })).ToBeDisabledAsync();
    Assert.Equal(0, applyRequests);
    await dialog.GetByRole(AriaRole.Button, new() { Name = "Close plan session manager", Exact = true }).ClickAsync();
    await Expect(dialog).ToBeHiddenAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Manage Earlier foundation on 10 August", Exact = true }).ClickAsync();
    await Expect(dialog).ToContainTextAsync("step 1 of 18");
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Repeat · keep later dates", Exact = false })).ToBeVisibleAsync();
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Repeat · shift the rest", Exact = false })).ToBeVisibleAsync();
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Move only this session", Exact = false })).ToHaveCountAsync(0);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Calendar_live_program_training_days_round_trip_preserves_mask_and_dates()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    string profileName = $"Calendar days {Guid.NewGuid():N}";
    Guid profileId = await CreateProfileAsync(profileName);
    Guid programId = Guid.Empty;
    Guid runId = Guid.Empty;

    try
    {
      using HttpClient client = new() { BaseAddress = gateway.BaseAddress };
      using HttpResponseMessage createResponse = await client.PostAsJsonAsync("/api/planning/programs", new
      {
        operationId = Guid.NewGuid(),
        name = $"Live calendar days {Guid.NewGuid():N}",
        description = "Browser regression for real program calendar rescheduling.",
        category = "5K",
        ownerProfileId = profileId,
        items = new[]
        {
          new { workoutRevisionId = scenario.FeaturedWorkoutRevisionId },
          new { workoutRevisionId = scenario.FeaturedWorkoutRevisionId },
          new { workoutRevisionId = scenario.FeaturedWorkoutRevisionId },
        },
      });
      createResponse.EnsureSuccessStatusCode();
      JsonElement created = await createResponse.Content.ReadFromJsonAsync<JsonElement>();
      programId = created.GetProperty("id").GetGuid();
      Guid revisionId = created.GetProperty("revisionId").GetGuid();
      int firstMask = 37; // Monday, Wednesday, Saturday.
      DateOnly startDate = NextSelectedDate(DateOnly.FromDateTime(DateTime.Today), firstMask);

      using HttpResponseMessage startResponse = await client.PostAsJsonAsync($"/api/planning/programs/{programId:D}/start", new
      {
        operationId = Guid.NewGuid(),
        profileId,
        expectedProgramRevisionId = revisionId,
        expectedActiveRunId = (Guid?)null,
        expectedActiveRunVersion = (int?)null,
        scheduledStartDate = startDate,
        scheduledWeekdayMask = firstMask,
        scheduleTimeZoneId = "Europe/Brussels",
      });
      startResponse.EnsureSuccessStatusCode();
      JsonElement started = await startResponse.Content.ReadFromJsonAsync<JsonElement>();
      runId = started.GetProperty("id").GetGuid();

      await Page.GotoAsync(new Uri(gateway.BaseAddress, "/calendar").AbsoluteUri,
        new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
      await Page.SelectActiveRunnerAsync(profileName);
      await Expect(Page.Locator(".active-runner-picker summary")).ToContainTextAsync(profileName);

      await ChangeLiveProgramDaysAsync(37, 69);
      await AssertProgramCalendarUsesMaskAsync(client, profileId, runId, expectedMask: 69);

      await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });
      await ChangeLiveProgramDaysAsync(69, 37);
      await AssertProgramCalendarUsesMaskAsync(client, profileId, runId, expectedMask: 37);

      await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });
      await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Calendar", Exact = true })).ToBeVisibleAsync();
      await AssertProgramCalendarUsesMaskAsync(client, profileId, runId, expectedMask: 37);
    }
    finally
    {
      using HttpClient cleanupClient = new() { BaseAddress = gateway.BaseAddress };
      if (runId != Guid.Empty)
      {
        using HttpResponseMessage previewResponse = await cleanupClient.GetAsync(
          $"/api/planning/programs/runs/{runId:D}/clear-upcoming/preview?profileId={profileId:D}");
        if (previewResponse.IsSuccessStatusCode)
        {
          JsonElement preview = await previewResponse.Content.ReadFromJsonAsync<JsonElement>();
          if (preview.GetProperty("canApply").GetBoolean())
          {
            using HttpResponseMessage clearResponse = await cleanupClient.PostAsJsonAsync(
              $"/api/planning/programs/runs/{runId:D}/clear-upcoming",
              new
              {
                operationId = Guid.NewGuid(),
                profileId,
                expectedRunVersion = preview.GetProperty("runVersion").GetInt32(),
              });
            clearResponse.EnsureSuccessStatusCode();
          }
        }
      }
      JsonElement[] profiles = (await cleanupClient.GetFromJsonAsync<JsonElement[]>("/api/planning/profiles")) ?? [];
      JsonElement profile = Assert.Single(profiles, candidate => candidate.GetProperty("id").GetGuid() == profileId);
      using HttpResponseMessage archiveResponse = await cleanupClient.PostAsJsonAsync(
        $"/api/planning/profiles/{profileId:D}/archive",
        new { operationId = Guid.NewGuid(), expectedVersion = profile.GetProperty("version").GetInt32() });
      archiveResponse.EnsureSuccessStatusCode();
    }

    Assert.NotEqual(Guid.Empty, programId);
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

    await Page.GotoAsync(new Uri(gateway.BaseAddress, $"/workouts/new?workoutId={workoutId:D}").AbsoluteUri, new PageGotoOptions
    {
      WaitUntil = WaitUntilState.NetworkIdle,
    });
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "New immutable revision" }))
      .ToBeVisibleAsync(new LocatorAssertionsToBeVisibleOptions { Timeout = 15_000 });
    await Expect(Page.Locator(".builder-repeat-children > .builder-repeat-group").Filter(new() { HasText = "Nested repeat block 2" })).ToBeVisibleAsync();

    await Page.GetByLabel("Workout name").FillAsync("Nested revision");
    await Page.GetByRole(AriaRole.Button, new() { Name = "Save new revision" }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Training plans", Exact = true })).ToBeVisibleAsync();

    JsonElement[] revisions = (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/workouts/{workoutId}/revisions"))!;
    Assert.Equal(2, revisions.Length);
    Assert.Equal("repeat", revisions[1].GetProperty("blocks")[0].GetProperty("blocks")[1].GetProperty("kind").GetString());

    await Page.GotoAsync(new Uri(gateway.BaseAddress, $"/workouts/new?workoutId={workoutId:D}").AbsoluteUri);
    await Page.GetByText("Start from an existing workout", new() { Exact = true }).ClickAsync();
    await Page.GetByRole(AriaRole.Link, new() { Name = "Blank workout Start a new plan", Exact = true }).ClickAsync();
    await Expect(Page).ToHaveURLAsync(new System.Text.RegularExpressions.Regex("/workouts/new$"));
    await Expect(Page.GetByLabel("Workout name", new() { Exact = true })).ToHaveValueAsync(string.Empty);
    await Expect(Page.Locator(".workout-builder__list > .builder-step-row, .workout-builder__list > .builder-repeat-group")).ToHaveCountAsync(1);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Heart_rate_guidance_can_be_applied_to_selected_steps()
  {
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts/new").AbsoluteUri);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "New workout" })).ToBeVisibleAsync();

    await Page.Locator(".builder-step-row").First.Locator(".builder-advanced > summary").ClickAsync();
    await Page.GetByLabel("Speed target").SelectOptionAsync("heartRate");
    await Expect(Page.GetByLabel("Minimum bpm")).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Note)).ToHaveCountAsync(1);
    await Expect(Page.GetByRole(AriaRole.Note)).ToContainTextAsync("This step follows your live heart rate");

    await Page.GetByRole(AriaRole.Button, new() { Name = "Add step", Exact = true }).ClickAsync();
    await Expect(Page.GetByLabel("Speed target")).ToHaveCountAsync(2);
    await Expect(Page.GetByRole(AriaRole.Note)).ToHaveCountAsync(1);
    await Page.Locator(".builder-step-row").Nth(1).Locator(".builder-advanced > summary").ClickAsync();
    await Page.GetByLabel("Speed target").Nth(1).SelectOptionAsync("heartRateZone");
    await Expect(Page.GetByRole(AriaRole.Spinbutton, new() { Name = "HR zone", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Note)).ToHaveCountAsync(2);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Garmin_activity_setup_remains_visible_when_optional_resources_are_empty_or_fail()
  {
    string profileName = $"Garmin readiness {Guid.NewGuid():N}";
    Guid profileId = await CreateProfileAsync(profileName);
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
    await Page.GetByRole(AriaRole.Button, new() { Name = $"Use {profileName} as active profile", Exact = true }).ClickAsync();
    ILocator profileRow = Page.Locator(".profile-row").Filter(new() { HasText = profileName });
    await profileRow.GetByRole(AriaRole.Button, new() { Name = "Edit", Exact = true }).ClickAsync();

    ILocator panel = Page.GetByRole(AriaRole.Region, new() { Name = "Garmin activity upload", Exact = true });
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Garmin Connect", Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Display and cues", Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByText("Personal local goal", new() { Exact = true })).ToHaveCountAsync(0);
    await Expect(panel.GetByLabel("Garmin email", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(panel.GetByLabel("Garmin password", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(panel).ToContainTextAsync("Experimental");
    await Expect(panel).ToContainTextAsync("Private-LAN HTTP setup is allowed but is not encrypted.");
    await Expect(panel.GetByRole(AriaRole.Button, new() { Name = "Connect Garmin", Exact = true })).ToBeVisibleAsync();
    await Expect(panel).Not.ToContainTextAsync("Activity-upload status is unavailable.");

    jobsStatus = 503;
    await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await profileRow.GetByRole(AriaRole.Button, new() { Name = "Edit", Exact = true }).ClickAsync();
    await Expect(panel.GetByLabel("Garmin email", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Recent Garmin upload jobs are temporarily unavailable", new() { Exact = false })).ToBeVisibleAsync();

    jobsStatus = 200;
    watchStatus = 503;
    await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await profileRow.GetByRole(AriaRole.Button, new() { Name = "Edit", Exact = true }).ClickAsync();
    await Expect(panel.GetByLabel("Garmin email", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Watch pairing status is temporarily unavailable", new() { Exact = false })).ToBeVisibleAsync();
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Header_runner_selection_closes_and_updates_the_run_page_without_reselecting()
  {
    string firstName = $"Runner A {Guid.NewGuid():N}";
    string secondName = $"Runner B {Guid.NewGuid():N}";
    Guid firstId = await CreateProfileAsync(firstName);
    await CreateProfileAsync(secondName);

    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.EvaluateAsync("([id]) => localStorage.setItem('treadmillrunner.active-profile', id)", new[] { firstId.ToString("D") });
    await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });

    ILocator picker = Page.Locator("details.active-runner-picker");
    await picker.Locator("summary").ClickAsync();
    await picker.GetByRole(AriaRole.Radio, new() { Name = secondName, Exact = true }).ClickAsync();

    await Expect(picker).Not.ToHaveAttributeAsync("open", string.Empty);
    await Expect(Page.Locator(".active-runner-picker summary")).ToContainTextAsync(secondName);
    await Expect(Page.GetByLabel("Selected runner", new() { Exact = true })).ToHaveTextAsync(secondName);
    await Expect(Page.GetByLabel("Recommended next run", new() { Exact = true })).ToContainTextAsync($"Next for {secondName}");
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Run_page_profile_switch_does_not_wait_for_device_readiness_retries()
  {
    string firstName = $"Fast runner A {Guid.NewGuid():N}";
    string secondName = $"Fast runner B {Guid.NewGuid():N}";
    string workoutName = $"Fast switch workout {Guid.NewGuid():N}";
    Guid firstId = await CreateProfileAsync(firstName);
    Guid secondId = await CreateProfileAsync(secondName);
    Guid workoutRevisionId = await CreateWorkoutAsync(workoutName);
    DateOnly today = DateOnly.FromDateTime(DateTime.Today);
    using (HttpClient client = new() { BaseAddress = gateway.BaseAddress })
    using (HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/calendar/series", new
    {
      operationId = Guid.NewGuid(),
      profileId = secondId,
      name = "Fast switch schedule",
      timeZoneId = "Europe/Brussels",
      startDate = today,
      endDate = today,
      intervalWeeks = 1,
      weekdayMask = WeekdayFlag(today.DayOfWeek),
      alternatives = new[] { new { workoutRevisionId, displayOrder = 0 } },
      exceptions = Array.Empty<object>(),
      expectedVersion = (int?)null,
    }))
    {
      response.EnsureSuccessStatusCode();
    }

    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.EvaluateAsync("([id]) => localStorage.setItem('treadmillrunner.active-profile', id)", new[] { firstId.ToString("D") });
    await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Expect(Page.GetByLabel("Selected runner", new() { Exact = true })).ToHaveTextAsync(firstName);

    TaskCompletionSource<bool> preflightRequested = new(TaskCreationOptions.RunContinuationsAsynchronously);
    TaskCompletionSource<bool> releasePreflight = new(TaskCreationOptions.RunContinuationsAsynchronously);
    await Page.RouteAsync("**/api/live/preflight?**", async route =>
    {
      if (!TryGetProfileId(new Uri(route.Request.Url), out Guid requestedProfileId) || requestedProfileId != secondId)
      {
        await route.ContinueAsync();
        return;
      }

      preflightRequested.TrySetResult(true);
      await releasePreflight.Task;
      try
      {
        await route.FulfillAsync(new RouteFulfillOptions
        {
          Status = 200,
          ContentType = "application/json",
          Body = JsonSerializer.Serialize(new
          {
            capturedAt = DateTimeOffset.UtcNow,
            userProfileId = secondId,
            userProfileName = secondName,
            workoutRevisionId,
            workoutTitle = workoutName,
            expectedDuration = "00:20:00",
            intensityLabel = "Planned pace",
            requiresHeartRate = false,
            selectedHeartRateSource = 0,
            checks = new[]
            {
              new { id = "treadmill", label = "Treadmill", status = 2, detail = "Waiting for a nearby treadmill." },
            },
            canStartRemotely = false,
            canStopRemotely = false,
            minimumStartSpeedKph = (double?)null,
            canSetSpeedRemotely = false,
            canSetInclineRemotely = false,
            canPauseRemotely = false,
            speedRange = (object?)null,
            inclineRange = (object?)null,
            heartRateAutomationMode = 0,
            heartRateAutomationReason = (string?)null,
            targetEvaluations = Array.Empty<object>(),
          }),
        });
      }
      catch (PlaywrightException)
      {
        // The browser can cancel an in-flight retry during teardown.
      }
    });

    try
    {
      ILocator picker = Page.Locator("details.active-runner-picker");
      await picker.Locator("summary").ClickAsync();
      await picker.GetByRole(AriaRole.Radio, new() { Name = secondName, Exact = true }).ClickAsync();
      await preflightRequested.Task.WaitAsync(TimeSpan.FromSeconds(5));

      // The readiness request remains blocked. Runner planning and the selected workout
      // must already be usable instead of looking stuck for the retry window.
      await Expect(Page.GetByLabel("Recommended next run", new() { Exact = true })).ToContainTextAsync($"Next for {secondName}");
      await Expect(Page.GetByLabel("Selected runner", new() { Exact = true })).ToHaveTextAsync(secondName);
      await Expect(Page.GetByLabel("Selected workout", new() { Exact = true })).ToHaveTextAsync(workoutName);

      releasePreflight.TrySetResult(true);
      await Expect(Page.GetByText("Connecting the devices needed for this run…", new() { Exact = true })).ToBeVisibleAsync();
    }
    finally
    {
      releasePreflight.TrySetResult(true);
    }
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Run_page_rapid_profile_switches_keep_final_workout_readiness_coherent()
  {
    string firstName = $"Rapid runner A {Guid.NewGuid():N}";
    string secondName = $"Rapid runner B {Guid.NewGuid():N}";
    string workoutName = $"Rapid switch workout {Guid.NewGuid():N}";
    Guid firstId = await CreateProfileAsync(firstName);
    Guid secondId = await CreateProfileAsync(secondName);
    Guid workoutRevisionId = await CreateWorkoutAsync(workoutName);

    await CreateTodayScheduleAsync(firstId, workoutRevisionId, "Rapid switch schedule A");
    await CreateTodayScheduleAsync(secondId, workoutRevisionId, "Rapid switch schedule B");
    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.EvaluateAsync("([id]) => localStorage.setItem('treadmillrunner.active-profile', id)", new[] { firstId.ToString("D") });
    await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Expect(Page.GetByLabel("Selected runner", new() { Exact = true })).ToHaveTextAsync(firstName);

    await Page.RouteAsync("**/api/live/preflight?**", async route =>
    {
      if (!TryGetProfileId(new Uri(route.Request.Url), out Guid requestedProfileId) ||
          requestedProfileId != firstId && requestedProfileId != secondId)
      {
        await route.ContinueAsync();
        return;
      }

      try
      {
        string requestedName = requestedProfileId == firstId ? firstName : secondName;
        string expectedDuration = requestedProfileId == firstId ? "00:10:00" : "00:20:00";
        string intensityLabel = requestedProfileId == firstId ? "Easy pace" : "Planned pace";
        await route.FulfillAsync(new RouteFulfillOptions
        {
          Status = 200,
          ContentType = "application/json",
          Body = WaitingPreflightJson(
            requestedProfileId,
            requestedName,
            workoutRevisionId,
            workoutName,
            expectedDuration: expectedDuration,
            intensityLabel: intensityLabel),
        });
      }
      catch (PlaywrightException)
      {
        // Superseded retry requests can be canceled by the next profile selection.
      }
    });

    ILocator picker = Page.Locator("details.active-runner-picker");
    foreach (string name in new[] { secondName, firstName, secondName, firstName, secondName })
    {
      await picker.Locator("summary").ClickAsync();
      await picker.GetByRole(AriaRole.Radio, new() { Name = name, Exact = true }).ClickAsync();
      await Expect(Page.Locator(".active-runner-picker summary")).ToContainTextAsync(name);
    }

    await Expect(Page.GetByLabel("Recommended next run", new() { Exact = true })).ToContainTextAsync($"Next for {secondName}");
    await Expect(Page.GetByLabel("Selected runner", new() { Exact = true })).ToHaveTextAsync(secondName);
    await Expect(Page.GetByLabel("Selected workout", new() { Exact = true })).ToHaveTextAsync(workoutName);
    await Expect(Page.Locator(".selection-summary").GetByText("00:20:00", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.Locator(".selection-summary").GetByText("Planned pace", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Pre-run checks are temporarily unavailable. Try selecting the workout again.", new() { Exact = true })).ToHaveCountAsync(0);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Run_page_preflight_recovers_after_transient_failures_and_extended_reconnect_without_reselection()
  {
    string profileName = $"Retry runner {Guid.NewGuid():N}";
    string workoutName = $"Retry workout {Guid.NewGuid():N}";
    Guid profileId = await CreateProfileAsync(profileName);
    Guid workoutRevisionId = await CreateWorkoutAsync(workoutName);
    await CreateTodayScheduleAsync(profileId, workoutRevisionId, "Transient retry schedule");

    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.EvaluateAsync("() => localStorage.removeItem('treadmillrunner.active-profile')");
    await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.Clock.InstallAsync(new ClockInstallOptions());

    int requestCount = 0;
    TaskCompletionSource<int> recovered = new(TaskCreationOptions.RunContinuationsAsynchronously);
    await Page.RouteAsync("**/api/live/preflight?**", async route =>
    {
      if (!TryGetProfileId(new Uri(route.Request.Url), out Guid requestedProfileId) || requestedProfileId != profileId)
      {
        await route.ContinueAsync();
        return;
      }

      try
      {
        int attempt = Interlocked.Increment(ref requestCount);
        if (attempt <= 3)
        {
          await route.FulfillAsync(new RouteFulfillOptions
          {
            Status = 503,
            ContentType = "application/json",
            Body = "{\"error\":\"Transient readiness failure\"}",
          });
          return;
        }

        bool isReady = attempt > 15;
        await route.FulfillAsync(new RouteFulfillOptions
        {
          Status = 200,
          ContentType = "application/json",
          Body = WaitingPreflightJson(profileId, profileName, workoutRevisionId, workoutName, isReady),
        });
        if (isReady) recovered.TrySetResult(attempt);
      }
      catch (PlaywrightException)
      {
        // Page teardown can cancel a later retry after the successful response.
      }
    });

    ILocator picker = Page.Locator("details.active-runner-picker");
    await picker.Locator("summary").ClickAsync();
    await picker.GetByRole(AriaRole.Radio, new() { Name = profileName, Exact = true }).ClickAsync();
    for (var step = 0; step < 30 && !recovered.Task.IsCompleted; step++)
    {
      await Page.Clock.RunForAsync(5_000);
      await Task.Delay(25);
    }

    Assert.True(await recovered.Task.WaitAsync(TimeSpan.FromSeconds(5)) >= 16);
    Assert.True(Volatile.Read(ref requestCount) >= 16);
    await Expect(Page.GetByLabel("Selected runner", new() { Exact = true })).ToHaveTextAsync(profileName);
    await Expect(Page.GetByLabel("Selected workout", new() { Exact = true })).ToHaveTextAsync(workoutName);
    await Expect(Page.Locator(".selection-summary").GetByText("00:20:00", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.Locator(".selection-summary").GetByText("Planned pace", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Gateway ready — take control when you are prepared.", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Pre-run checks are temporarily unavailable. Try selecting the workout again.", new() { Exact = true })).ToHaveCountAsync(0);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Run_page_profile_switches_supersede_out_of_order_planning_responses()
  {
    string firstName = $"Race runner A {Guid.NewGuid():N}";
    string secondName = $"Race runner B {Guid.NewGuid():N}";
    Guid firstId = await CreateProfileAsync(firstName);
    Guid secondId = await CreateProfileAsync(secondName);

    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.EvaluateAsync("([id]) => localStorage.setItem('treadmillrunner.active-profile', id)", new[] { firstId.ToString("D") });
    await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Expect(Page.GetByLabel("Selected runner", new() { Exact = true })).ToHaveTextAsync(firstName);

    TaskCompletionSource<bool> firstResponses = new(TaskCreationOptions.RunContinuationsAsynchronously);
    TaskCompletionSource<bool> secondResponses = new(TaskCreationOptions.RunContinuationsAsynchronously);
    await Page.RouteAsync("**/api/planning/**", async route =>
    {
      if (!TryGetProfileId(new Uri(route.Request.Url), out Guid requestedProfileId) ||
          requestedProfileId != firstId && requestedProfileId != secondId)
      {
        await route.ContinueAsync();
        return;
      }

      try
      {
        await (requestedProfileId == firstId ? firstResponses.Task : secondResponses.Task);
        using HttpClient client = new() { BaseAddress = gateway.BaseAddress };
        using HttpResponseMessage response = await client.GetAsync(new Uri(route.Request.Url).PathAndQuery);
        string body = await response.Content.ReadAsStringAsync();
        await route.FulfillAsync(new RouteFulfillOptions
        {
          Status = (int)response.StatusCode,
          ContentType = response.Content.Headers.ContentType?.MediaType ?? "application/json",
          Body = body,
        });
      }
      catch (Exception exception) when (exception is OperationCanceledException or PlaywrightException)
      {
        // A superseded browser request may be aborted while this route is held.
      }
    });

    ILocator picker = Page.Locator("details.active-runner-picker");
    await picker.Locator("summary").ClickAsync();
    await picker.GetByRole(AriaRole.Radio, new() { Name = secondName, Exact = true }).ClickAsync();
    await Expect(Page.GetByLabel("Loading runner plan", new() { Exact = true })).ToBeVisibleAsync();

    await picker.Locator("summary").ClickAsync();
    await picker.GetByRole(AriaRole.Radio, new() { Name = firstName, Exact = true }).ClickAsync();
    await Expect(Page.GetByLabel("Loading runner plan", new() { Exact = true })).ToBeVisibleAsync();

    // Let stale B responses win the network race; they must not overwrite final A.
    secondResponses.TrySetResult(true);
    await Page.WaitForTimeoutAsync(150);
    firstResponses.TrySetResult(true);

    await Expect(Page.GetByLabel("Selected runner", new() { Exact = true })).ToHaveTextAsync(firstName);
    await Expect(picker).Not.ToHaveAttributeAsync("open", string.Empty);
    await Expect(Page.Locator(".active-runner-picker summary")).ToContainTextAsync(firstName);
    await Expect(Page.GetByLabel("Recommended next run", new() { Exact = true })).ToContainTextAsync($"Next for {firstName}");
    await Expect(Page.GetByLabel("Recommended next run", new() { Exact = true })).Not.ToContainTextAsync($"Next for {secondName}");
    await Expect(Page.GetByLabel("Selected runner", new() { Exact = true })).Not.ToContainTextAsync(secondName);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Run_page_keeps_an_explicit_workout_when_profile_planning_finishes()
  {
    string firstName = $"Choice runner A {Guid.NewGuid():N}";
    string secondName = $"Choice runner B {Guid.NewGuid():N}";
    string workoutName = $"Keep my workout {Guid.NewGuid():N}";
    Guid firstId = await CreateProfileAsync(firstName);
    Guid secondId = await CreateProfileAsync(secondName);
    await CreateWorkoutAsync(workoutName);

    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.EvaluateAsync("([id]) => localStorage.setItem('treadmillrunner.active-profile', id)", new[] { firstId.ToString("D") });
    await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });

    TaskCompletionSource<bool> secondResponses = new(TaskCreationOptions.RunContinuationsAsynchronously);
    await Page.RouteAsync("**/api/planning/**", async route =>
    {
      if (!TryGetProfileId(new Uri(route.Request.Url), out Guid requestedProfileId) || requestedProfileId != secondId)
      {
        await route.ContinueAsync();
        return;
      }

      try
      {
        await secondResponses.Task;
        using HttpClient client = new() { BaseAddress = gateway.BaseAddress };
        using HttpResponseMessage response = await client.GetAsync(new Uri(route.Request.Url).PathAndQuery);
        await route.FulfillAsync(new RouteFulfillOptions
        {
          Status = (int)response.StatusCode,
          ContentType = response.Content.Headers.ContentType?.MediaType ?? "application/json",
          Body = await response.Content.ReadAsStringAsync(),
        });
      }
      catch (Exception exception) when (exception is OperationCanceledException or PlaywrightException)
      {
        // A superseded browser request may be aborted while this route is held.
      }
    });

    try
    {
      ILocator picker = Page.Locator("details.active-runner-picker");
      await picker.Locator("summary").ClickAsync();
      await picker.GetByRole(AriaRole.Radio, new() { Name = secondName, Exact = true }).ClickAsync();
      await Expect(Page.GetByLabel("Loading runner plan", new() { Exact = true })).ToBeVisibleAsync();

      ILocator otherWorkout = Page.Locator("details.choose-another-run");
      await otherWorkout.Locator("summary").ClickAsync();
      ILocator explicitWorkout = otherWorkout.Locator(".workout-choice-card").Filter(new() { HasText = workoutName });
      await Expect(explicitWorkout).ToBeVisibleAsync();
      await explicitWorkout.ClickAsync();

      secondResponses.TrySetResult(true);
      await Expect(Page.GetByLabel("Selected workout", new() { Exact = true })).ToHaveTextAsync(workoutName);
      await Expect(explicitWorkout).ToHaveAttributeAsync("aria-pressed", "true");
    }
    finally
    {
      secondResponses.TrySetResult(true);
    }
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

  private async Task<Guid> CreateWorkoutAsync(string name)
  {
    using HttpClient client = new() { BaseAddress = gateway.BaseAddress };
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/workouts", new
    {
      operationId = Guid.NewGuid(),
      name,
      description = "Explicit selection race regression fixture",
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
          heartRateMinimumBpm = 0,
          heartRateMaximumBpm = 0,
          heartRateZoneNumber = 0,
          heartRateInitialSpeedKph = 0.0,
          heartRateMinimumSpeedKph = 0.0,
          heartRateMaximumSpeedKph = 0.0,
          inclineKind = "fixed",
          inclineStartPercent = 1.0,
          inclineEndPercent = 0.0,
          cue = "Steady",
          notes = "Deterministic browser fixture",
        },
      },
    });
    response.EnsureSuccessStatusCode();
    JsonElement saved = await response.Content.ReadFromJsonAsync<JsonElement>();
    return saved.GetProperty("revisionId").GetGuid();
  }

  private async Task CreateTodayScheduleAsync(Guid profileId, Guid workoutRevisionId, string scheduleName)
  {
    DateOnly today = DateOnly.FromDateTime(DateTime.Today);
    using HttpClient client = new() { BaseAddress = gateway.BaseAddress };
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/calendar/series", new
    {
      operationId = Guid.NewGuid(),
      profileId,
      name = scheduleName,
      timeZoneId = "Europe/Brussels",
      startDate = today,
      endDate = today,
      intervalWeeks = 1,
      weekdayMask = WeekdayFlag(today.DayOfWeek),
      alternatives = new[] { new { workoutRevisionId, displayOrder = 0 } },
      exceptions = Array.Empty<object>(),
      expectedVersion = (int?)null,
    });
    response.EnsureSuccessStatusCode();
  }

  private static string WaitingPreflightJson(
    Guid profileId,
    string profileName,
    Guid workoutRevisionId,
    string workoutName,
    bool isReady = false,
    string expectedDuration = "00:20:00",
    string intensityLabel = "Planned pace")
  {
    object treadmillCheck = new
    {
      id = "treadmill",
      label = "Treadmill",
      status = isReady ? 0 : 2,
      detail = isReady ? "Connected and ready." : "Waiting for a nearby treadmill.",
    };
    return JsonSerializer.Serialize(new
    {
      capturedAt = DateTimeOffset.UtcNow,
      userProfileId = profileId,
      userProfileName = profileName,
      workoutRevisionId,
      workoutTitle = workoutName,
      expectedDuration,
      intensityLabel,
      requiresHeartRate = false,
      selectedHeartRateSource = 0,
      checks = new[] { treadmillCheck },
      canStartRemotely = false,
      canStopRemotely = false,
      minimumStartSpeedKph = (double?)null,
      canSetSpeedRemotely = false,
      canSetInclineRemotely = false,
      canPauseRemotely = false,
      speedRange = (object?)null,
      inclineRange = (object?)null,
      heartRateAutomationMode = 0,
      heartRateAutomationReason = (string?)null,
      targetEvaluations = Array.Empty<object>(),
      isReady,
      readinessBlockers = isReady ? Array.Empty<object>() : new[] { treadmillCheck },
    });
  }

  private static bool TryGetProfileId(Uri uri, out Guid profileId)
  {
    if (uri.AbsolutePath.Contains("/calendar/", StringComparison.OrdinalIgnoreCase) &&
        Guid.TryParse(uri.AbsolutePath[(uri.AbsolutePath.LastIndexOf('/') + 1)..], out profileId))
    {
      return true;
    }

    string query = uri.Query.TrimStart('?');
    foreach (string part in query.Split('&', StringSplitOptions.RemoveEmptyEntries))
    {
      string[] pair = part.Split('=', 2);
      if (pair.Length == 2 && string.Equals(pair[0], "profileId", StringComparison.OrdinalIgnoreCase) &&
          Guid.TryParse(Uri.UnescapeDataString(pair[1]), out profileId))
      {
        return true;
      }
    }

    profileId = Guid.Empty;
    return false;
  }

  private async Task ChangeLiveProgramDaysAsync(int currentMask, int targetMask)
  {
    ILocator manageButton = Page.GetByRole(AriaRole.Button, new()
    {
      NameRegex = new System.Text.RegularExpressions.Regex("^Manage "),
    }).First;
    await Expect(manageButton).ToBeVisibleAsync();
    await manageButton.ClickAsync();
    ILocator dialog = Page.GetByRole(AriaRole.Dialog);
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Change training days", Exact = true })).ToBeVisibleAsync();
    await dialog.GetByRole(AriaRole.Button, new() { Name = "Change training days", Exact = true }).ClickAsync();
    ILocator picker = dialog.Locator(".default-days-picker");
    foreach ((string label, int flag) in new[] { ("Mon", 1), ("Tue", 2), ("Wed", 4), ("Thu", 8), ("Fri", 16), ("Sat", 32), ("Sun", 64) })
    {
      ILocator dayButton = picker.GetByRole(AriaRole.Button, new() { Name = label, Exact = true });
      bool shouldBeSelected = (targetMask & flag) != 0;
      bool isSelected = string.Equals(await dayButton.GetAttributeAsync("aria-pressed"), "true", StringComparison.OrdinalIgnoreCase);
      Assert.Equal((currentMask & flag) != 0, isSelected);
      if (shouldBeSelected != isSelected)
      {
        await dayButton.ClickAsync();
      }
    }

    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Preview new schedule", Exact = true })).ToBeEnabledAsync();
    await dialog.GetByRole(AriaRole.Button, new() { Name = "Preview new schedule", Exact = true }).ClickAsync();
    await Expect(dialog.GetByText("New days", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Confirm all future changes", Exact = true })).ToBeEnabledAsync();
    await dialog.GetByRole(AriaRole.Button, new() { Name = "Confirm all future changes", Exact = true }).ClickAsync();
    await Expect(dialog).ToBeHiddenAsync();
  }

  private static async Task AssertProgramCalendarUsesMaskAsync(HttpClient client, Guid profileId, Guid runId, int expectedMask)
  {
    DateOnly from = DateOnly.FromDateTime(DateTime.Today);
    DateOnly to = from.AddDays(60);
    JsonElement range = (await client.GetFromJsonAsync<JsonElement>(
      $"/api/planning/calendar/{profileId:D}?from={from:yyyy-MM-dd}&to={to:yyyy-MM-dd}"))!;
    List<(DateOnly Date, int Mask)> sessions = [];
    foreach (JsonElement day in range.GetProperty("days").EnumerateArray())
    {
      DateOnly date = DateOnly.Parse(day.GetProperty("date").GetString()!);
      foreach (JsonElement option in day.GetProperty("options").EnumerateArray())
      {
        if (option.GetProperty("source").GetString() != "Program" ||
            option.GetProperty("programRunId").GetGuid() != runId)
        {
          continue;
        }

        sessions.Add((date, option.GetProperty("programWeekdayMask").GetInt32()));
      }
    }

    Assert.Equal(3, sessions.Count);
    Assert.All(sessions, session =>
    {
      Assert.Equal(expectedMask, session.Mask);
      Assert.True((expectedMask & WeekdayFlag(session.Date.DayOfWeek)) != 0,
        $"Program session {session.Date:yyyy-MM-dd} did not use mask {expectedMask}.");
    });
  }

  private static DateOnly NextSelectedDate(DateOnly date, int mask)
  {
    for (int offset = 0; offset < 7; offset++)
    {
      DateOnly candidate = date.AddDays(offset);
      if ((mask & WeekdayFlag(candidate.DayOfWeek)) != 0)
      {
        return candidate;
      }
    }

    throw new InvalidOperationException($"Mask {mask} did not select a weekday.");
  }

  private static int WeekdayFlag(DayOfWeek day) => day switch
  {
    DayOfWeek.Monday => 1,
    DayOfWeek.Tuesday => 2,
    DayOfWeek.Wednesday => 4,
    DayOfWeek.Thursday => 8,
    DayOfWeek.Friday => 16,
    DayOfWeek.Saturday => 32,
    DayOfWeek.Sunday => 64,
    _ => throw new ArgumentOutOfRangeException(nameof(day)),
  };

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
