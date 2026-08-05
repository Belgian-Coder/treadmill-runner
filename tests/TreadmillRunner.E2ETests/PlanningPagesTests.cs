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
          kind = "step", repetitions = 1, blocks = Array.Empty<object>(), goalKind = "time", goalValue = 12.0,
          speedKind = "fixed", speedStartKph = 5.0, speedEndKph = 0.0, heartRateMinimumBpm = 0,
          heartRateMaximumBpm = 0, heartRateZoneNumber = 0, heartRateInitialSpeedKph = 0.0,
          heartRateMinimumSpeedKph = 0.0, heartRateMaximumSpeedKph = 0.0, inclineKind = "fixed",
          inclineStartPercent = 2.0, inclineEndPercent = 0.0, cue = (string?)null, notes = (string?)null,
        },
      },
    });
    Assert.Equal(HttpStatusCode.Created, created.StatusCode);

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    ILocator card = Page.Locator(".workout-card").Filter(new() { HasText = unique });
    await Expect(card).ToContainTextAsync("Short incline progression for recovery days");
    await Expect(card).ToContainTextAsync("12 min");
    await Page.GetByLabel("Search workouts", new() { Exact = true }).FillAsync("recovery days");
    await Expect(card).ToBeVisibleAsync();
    await Page.GetByLabel("Search workouts", new() { Exact = true }).FillAsync("does not exist");
    await Expect(card).ToHaveCountAsync(0);
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

  private async Task AssertNoOverflowAsync()
  {
    bool overflow = await Page.EvaluateAsync<bool>(
      "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1");
    Assert.False(overflow);
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
