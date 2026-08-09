using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;

namespace TreadmillRunner.E2ETests;

/// <summary>
/// Browser-facing TR-004 acceptance contract. The simulator endpoints used here model physical
/// treadmill movement and completion only; they must never become a remote belt-start path.
/// </summary>
public sealed class DailyRunningExperienceTests(GatewayFixture gateway) : PageTest, IClassFixture<GatewayFixture>
{
  public static TheoryData<string, int, int> ResponsiveViewports => new()
  {
    { "iphone17-pro-max", 440, 956 },
    { "ipad-landscape", 1180, 820 },
    { "desktop-full-hd", 1920, 1080 },
  };

  [Theory]
  [MemberData(nameof(ResponsiveViewports))]
  [Trait("Category", "Browser")]
  public async Task Preflight_selects_runner_and_workout_and_is_touch_responsive(
    string viewportName,
    int width,
    int height)
  {
    var browserErrors = new List<string>();
    Page.PageError += (_, error) => browserErrors.Add(error);
    Page.Console += (_, message) =>
    {
      if (message.Type is "error" or "warning") browserErrors.Add($"{message.Type}: {message.Text}");
    };
    SeededPlan plan = await SeedPlanAsync(
      "preflight",
      heartRateTarget: true,
      profileDisplayName: viewportName == "iphone17-pro-max" ? "Alex Demo" : null,
      workoutDisplayName: viewportName == "iphone17-pro-max" ? "Aerobic base run" : null);
    await Page.SetViewportSizeAsync(width, height);
    await ResetSimulatorAsync();

    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });

    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready to run", Exact = true }))
      .ToBeVisibleAsync();
    try
    {
      await Page.SelectActiveRunnerAsync(plan.ProfileName);
    }
    catch (TimeoutException exception)
    {
      string body = await Page.Locator("body").InnerTextAsync();
      throw new InvalidOperationException(
        $"Run page did not hydrate the seeded runner. Browser errors: {string.Join(" | ", browserErrors)}. Body: {body}",
        exception);
    }
    await Expect(Page.Locator(".active-runner-picker summary")).ToContainTextAsync(plan.ProfileName);
    await Page.OpenRunChoicesAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();

    await Expect(Page.GetByLabel("Selected runner")).ToHaveTextAsync(plan.ProfileName);
    await Expect(Page.GetByLabel("Selected workout")).ToHaveTextAsync(plan.WorkoutName);
    await Expect(Page.Locator(".connection-state")).ToHaveTextAsync("Gateway ready");
    ILocator readiness = Page.Locator(".readiness-card");
    await Expect(readiness).ToHaveAttributeAsync("open", "");
    await Expect(readiness.GetByText("Ready", new() { Exact = true }).First).ToBeVisibleAsync();
    await Expect(Page.Locator(".readiness-list").GetByText("Treadmill connected", new() { Exact = false })).ToBeVisibleAsync();
    await Expect(Page.Locator(".readiness-list").GetByText("Heart rate connected", new() { Exact = false })).ToBeVisibleAsync();
    await Expect(Page.GetByLabel("Safety notice"))
      .ToContainTextAsync("Remote Start appears only for an exact model and firmware");
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run" })).ToBeEnabledAsync();

    await AssertNoHorizontalOverflowAsync();
    await AssertTouchTargetAsync(Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run" }), viewportName);
    if (viewportName == "iphone17-pro-max")
    {
      await readiness.ScrollIntoViewIfNeededAsync();
      await ShowcaseScreenshotAsync("tr-023-run-preflight-iphone.png");
      await Page.EvaluateAsync("() => window.scrollTo({ top: 0, left: 0, behavior: 'instant' })");
      await Page.WaitForTimeoutAsync(50);
      await ShowcaseScreenshotAsync("tr-029-simplified-run-iphone.png");
    }
    await ScreenshotAsync($"tr004-preflight-{viewportName}.png");
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Manual_run_is_created_once_and_selected_from_the_run_picker()
  {
    SeededPlan plan = await SeedPlanAsync("manual-picker", heartRateTarget: false);
    await Page.SetViewportSizeAsync(440, 956);
    await ResetSimulatorAsync();
    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.SelectActiveRunnerAsync(plan.ProfileName);
    await Page.OpenRunChoicesAsync();

    ILocator manualRun = Page.GetByRole(AriaRole.Button, new() { Name = "Manual run", Exact = false });
    await manualRun.ClickAsync();

    await Expect(manualRun).ToHaveAttributeAsync("aria-pressed", "true");
    await Expect(Page.GetByLabel("Selected workout")).ToHaveTextAsync("Manual run");
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true })).ToBeEnabledAsync();

    using HttpClient client = CreateClient();
    JsonElement[] workouts = await client.GetFromJsonAsync<JsonElement[]>("/api/planning/workouts") ?? [];
    Assert.Single(workouts, static workout => workout.GetProperty("name").GetString() == "Manual run");
  }

  [Theory]
  [MemberData(nameof(ResponsiveViewports))]
  [Trait("Category", "Browser")]
  public async Task Armed_session_advances_only_after_simulated_physical_motion_and_renders_live_metrics(
    string viewportName,
    int width,
    int height)
  {
    SeededPlan plan = await SeedPlanAsync("live", heartRateTarget: false);
    await Page.SetViewportSizeAsync(width, height);

    await NavigateAndSelectPlanAsync(plan);
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run" }).ClickAsync();

    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
      .ToBeVisibleAsync();
    await SetPhysicalMotionAsync(isMoving: true, measuredSpeedKph: 6.4, measuredInclinePercent: 1.5);

    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByLabel("Live treadmill controls", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByLabel("Heart rate")).ToContainTextAsync("bpm");
    await Expect(Page.GetByLabel("Measured speed", new() { Exact = true })).ToContainTextAsync("6.4");
    await Expect(Page.Locator(".control-rail--incline h2")).ToContainTextAsync("1.0");
    await Expect(Page.GetByText("Physical movement detected", new() { Exact = true })).ToHaveCountAsync(0);
    ILocator speedDown = Page.GetByRole(AriaRole.Button, new() { Name = "Speed -0.1 km/h" });
    ILocator speedUp = Page.GetByRole(AriaRole.Button, new() { Name = "Speed +0.1 km/h" });
    ILocator pause = Page.GetByRole(AriaRole.Button, new() { Name = "Pause", Exact = true });
    ILocator stop = Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true });
    foreach (ILocator control in new[] { speedDown, speedUp, pause, stop })
    {
      await AssertTouchTargetAsync(control, viewportName);
      await AssertInsideViewportAsync(control, viewportName);
    }
    await ScreenshotViewportAsync($"tr006d-live-{viewportName}.png");

    await Page.GetByRole(AriaRole.Button, new() { Name = "Speed +0.1 km/h" }).ClickAsync();
    await Expect(Page.Locator(".control-rail--speed h2")).ToContainTextAsync("6.6");
    await Page.GetByRole(AriaRole.Button, new() { Name = "Set incline to 1.5%", Exact = true }).ClickAsync();
    await Expect(Page.Locator(".control-rail--incline h2")).ToContainTextAsync("1.5");
    await Task.Delay(TimeSpan.FromMilliseconds(900));
    await Expect(Page.Locator("[data-series='measured-speed']")).ToHaveAttributeAsync("d", new System.Text.RegularExpressions.Regex("^M"));
    await Expect(Page.GetByLabel("Live speed in kilometers per hour and incline percentage over elapsed time", new() { Exact = true })).ToBeVisibleAsync();

    await AssertNoHorizontalOverflowAsync();
    await AssertTouchTargetAsync(Page.GetByRole(AriaRole.Button, new() { Name = "Speed +0.1 km/h" }), viewportName);
    await ScreenshotAsync($"tr004-live-{viewportName}.png");
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Initial_live_channel_failure_keeps_controls_disabled_and_recovers_without_reload()
  {
    SeededPlan plan = await SeedPlanAsync("initial-signalr-recovery", heartRateTarget: false);
    await Page.SetViewportSizeAsync(440, 956);
    await ResetSimulatorAsync();
    await Page.RouteAsync("**/hubs/live**", route => route.AbortAsync());

    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.SelectActiveRunnerAsync(plan.ProfileName);
    await Page.OpenRunChoicesAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
    await Expect(Page.Locator(".connection-state")).ToContainTextAsync("retrying", new() { Timeout = 15_000 });
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run" })).ToBeDisabledAsync();

    await Page.UnrouteAsync("**/hubs/live**");

    await Expect(Page.Locator(".connection-state")).ToContainTextAsync("Gateway ready", new() { Timeout = 20_000 });
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run" })).ToBeEnabledAsync();
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Browser_reload_keeps_the_gateway_owned_session_and_restores_its_controller_view()
  {
    SeededPlan plan = await SeedPlanAsync(
      "reconnect", heartRateTarget: false, goalMinutes: 0.2,
      profileDisplayName: "Morgan Demo", workoutDisplayName: "Steady reconnect run");
    await Page.SetViewportSizeAsync(1920, 1080);

    await NavigateAndSelectPlanAsync(plan);
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run" }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
      .ToBeVisibleAsync();
    await SetPhysicalMotionAsync(isMoving: true, measuredSpeedKph: 7.0, measuredInclinePercent: 0.0);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();
    await Task.Delay(TimeSpan.FromMilliseconds(2_200));
    await Expect(Page.Locator("[data-series='measured-speed']")).ToHaveAttributeAsync("d", new System.Text.RegularExpressions.Regex("^M.+L"));
    ILocator speedAxis = Page.GetByLabel("Speed axis in kilometers per hour", new() { Exact = true });
    Assert.True(await speedAxis.Locator("span").CountAsync() >= 10);

    await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });

    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();
    ILocator motionControls = Page.GetByRole(
      AriaRole.Region,
      new() { Name = "Treadmill motion controls", Exact = true });
    await Expect(motionControls.GetByRole(AriaRole.Status)).ToContainTextAsync("Controller access restored");
    await Expect(Page.GetByLabel("Live workout metrics", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.Locator("[data-series='measured-speed']")).ToHaveAttributeAsync("d", new System.Text.RegularExpressions.Regex("^M.+L"));
    await ShowcaseScreenshotAsync("tr-023-control-recovered-desktop.png");
    await ScreenshotAsync("tr004-live-browser-reconnect-desktop-full-hd.png");
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Active_session_survives_browser_network_loss_and_rehydrates_elapsed_time()
  {
    SeededPlan plan = await SeedPlanAsync(
      "offline-recovery", heartRateTarget: false,
      profileDisplayName: "Taylor Demo", workoutDisplayName: "Reliable easy run");
    await Page.SetViewportSizeAsync(440, 956);
    await NavigateAndSelectPlanAsync(plan);
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run" }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
      .ToBeVisibleAsync();
    await SetPhysicalMotionAsync(isMoving: true, measuredSpeedKph: 6.0, measuredInclinePercent: 0.5);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();
    string elapsedBefore = await Page.GetByLabel("Workout progress time", new() { Exact = true }).InnerTextAsync();

    await Page.Context.SetOfflineAsync(true);
    await Expect(Page.GetByRole(AriaRole.Alert).Filter(new() { HasText = "Live updates unavailable" }))
      .ToBeVisibleAsync(new() { Timeout = 15_000 });
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Speed +0.1 km/h" })).ToBeDisabledAsync();
    await ShowcaseScreenshotAsync("tr-023-control-reconnecting-iphone.png");
    await Task.Delay(TimeSpan.FromSeconds(12));
    await Page.Context.SetOfflineAsync(false);

    await Expect(Page.Locator(".control-header-actions .connection-state"))
      .ToContainTextAsync("Gateway ready", new() { Timeout = 40_000 });
    await Expect(Page.GetByRole(AriaRole.Alert).Filter(new() { HasText = "Live updates unavailable" }))
      .ToHaveCountAsync(0);
    string elapsedAfter = await Page.GetByLabel("Workout progress time", new() { Exact = true }).InnerTextAsync();
    Assert.NotEqual(elapsedBefore, elapsedAfter);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Active_control_is_touch_usable_in_iphone_landscape()
  {
    SeededPlan plan = await SeedPlanAsync("iphone-landscape", heartRateTarget: false);
    await Page.SetViewportSizeAsync(956, 440);
    await NavigateAndSelectPlanAsync(plan);
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run" }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true })).ToBeVisibleAsync();
    await SetPhysicalMotionAsync(isMoving: true, measuredSpeedKph: 6.0, measuredInclinePercent: 0.5);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();
    await AssertNoHorizontalOverflowAsync();
    await AssertTouchTargetAsync(Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true }), "iphone-landscape");
    await ShowcaseScreenshotAsync("tr-023-control-iphone-landscape.png");
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Completed_physical_session_returns_to_ready_and_exposes_history_detail_and_analytics()
  {
    SeededPlan plan = await SeedPlanAsync("history", heartRateTarget: true);
    await Page.SetViewportSizeAsync(1180, 820);

    await NavigateAndSelectPlanAsync(plan);
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run" }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
      .ToBeVisibleAsync();
    await SetPhysicalMotionAsync(isMoving: true, measuredSpeedKph: 7.2, measuredInclinePercent: 1.0);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Speed +0.1 km/h" }).ClickAsync();
    await Page.Locator(".control-automation > summary").ClickAsync();
    await Expect(Page.GetByText("HR automation: SuspendedManualOverride", new() { Exact = false }))
      .ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "HR shadow", Exact = true }).ClickAsync();
    await Expect(Page.GetByText("HR automation: Shadow", new() { Exact = false }))
      .ToBeVisibleAsync();
    await Task.Delay(TimeSpan.FromMilliseconds(2_200));

    await CompletePhysicalSessionAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready to run", Exact = true })).ToBeVisibleAsync();

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/history").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "History", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByLabel("This week's completed running")).ToContainTextAsync("1");
    await Page.GetByRole(AriaRole.Button, new() { Name = $"View details for {plan.WorkoutName}", Exact = true }).ClickAsync();
    ILocator sessionDialog = Page.GetByRole(AriaRole.Dialog);
    await Expect(sessionDialog.GetByRole(AriaRole.Heading, new() { Name = plan.WorkoutName, Exact = true })).ToBeVisibleAsync();
    await sessionDialog.GetByRole(AriaRole.Link, new() { Name = "Open full session page", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = plan.WorkoutName, Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Planned versus actual", new() { Exact = true })).ToBeVisibleAsync();
    await Page.GetByText("Time in heart-rate zones", new() { Exact = true }).ClickAsync();
    await Expect(Page.GetByText("Time in heart-rate zones", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Zone 2 · Aerobic", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Plan adherence:", new() { Exact = false })).ToBeVisibleAsync();
    await Page.GetByText("Session events", new() { Exact = true }).ClickAsync();
    await Expect(Page.GetByText("Manual speed override:", new() { Exact = false })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Runner debrief", new() { Exact = true })).ToHaveCountAsync(0);
    await ScreenshotAsync("tr004-history-session-detail-ipad-landscape.png");
  }

  [Fact]
  [Trait("Category", "Browser")]
  [Trait("Category", "Performance")]
  public async Task Loopback_telemetry_visibility_p95_is_below_500_milliseconds()
  {
    SeededPlan plan = await SeedPlanAsync("latency", heartRateTarget: false, openSpeed: true);
    await Page.SetViewportSizeAsync(1180, 820);
    await NavigateAndSelectPlanAsync(plan);
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run" }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
      .ToBeVisibleAsync();
    await SetPhysicalMotionAsync(isMoving: true, measuredSpeedKph: 6.0, measuredInclinePercent: 1.0);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();

    var durations = new List<double>();
    for (var iteration = 1; iteration <= 12; iteration++)
    {
      double speed = 6 + (iteration / 10d);
      long started = Stopwatch.GetTimestamp();
      await SetPhysicalMotionAsync(isMoving: true, measuredSpeedKph: speed, measuredInclinePercent: 1.0);
      await Expect(Page.GetByLabel("Measured speed", new() { Exact = true }))
        .ToContainTextAsync(speed.ToString("0.0", System.Globalization.CultureInfo.InvariantCulture));
      durations.Add(Stopwatch.GetElapsedTime(started).TotalMilliseconds);
    }

    double[] ordered = durations.Order().ToArray();
    double p95 = ordered[(int)Math.Ceiling(ordered.Length * 0.95) - 1];
    Assert.True(p95 < 500, $"Loopback telemetry visibility p95 was {p95:0.0} ms.");
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Portrait_live_run_reflects_manual_controls_and_mocked_heart_rate_automation_end_to_end()
  {
    SeededPlan plan = await SeedPlanAsync("portrait-hr", heartRateTarget: true, goalMinutes: 2.0);
    await Page.SetViewportSizeAsync(440, 956);
    await NavigateAndSelectPlanAsync(plan);
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
      .ToBeVisibleAsync();
    await SetPhysicalMotionAsync(isMoving: true, measuredSpeedKph: 6.5, measuredInclinePercent: 1.0);

    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();
    ILocator stop = Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true });
    await AssertInsideViewportAsync(stop, "iphone17-pro-max live start");
    await Expect(Page.GetByText("Target speed", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Measured speed", new() { Exact = true })).ToBeVisibleAsync();
    ILocator speedAxis = Page.GetByLabel("Speed axis in kilometers per hour", new() { Exact = true });
    await Expect(speedAxis).ToContainTextAsync("km/h");
    await Expect(speedAxis.Locator("span")).ToHaveCountAsync(10);
    await Expect(speedAxis.Locator("span").First).ToHaveTextAsync("10");
    ILocator inclineAxis = Page.GetByLabel("Incline axis in percent", new() { Exact = true });
    await Expect(inclineAxis.Locator("span")).ToHaveCountAsync(10);
    await Expect(inclineAxis.Locator("span").First).ToHaveTextAsync("10");

    await Page.GetByRole(AriaRole.Button, new() { Name = "Set speed to 7.0 km/h", Exact = true }).ClickAsync();
    await Expect(Page.Locator(".control-rail--speed h2")).ToContainTextAsync("7.0");
    await Page.GetByRole(AriaRole.Button, new() { Name = "Set incline to 1.5%", Exact = true }).ClickAsync();
    await Expect(Page.Locator(".control-rail--incline h2")).ToContainTextAsync("1.5");

    await Page.Locator(".control-automation > summary").ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "HR two-way", Exact = true }).ClickAsync();
    await Expect(Page.GetByText("HR automation: Full", new() { Exact = false })).ToBeVisibleAsync();

    await SetSimulatedHeartRateAsync(165);
    await Expect(Page.GetByLabel("Heart rate", new() { Exact = true })).ToContainTextAsync("165");
    await WaitForSpeedWithFreshHeartRateAsync("6.5", 165, TimeSpan.FromSeconds(16));

    await SetSimulatedHeartRateAsync(110);
    await Expect(Page.GetByLabel("Heart rate", new() { Exact = true })).ToContainTextAsync("110");
    await WaitForSpeedWithFreshHeartRateAsync("6.7", 110, TimeSpan.FromSeconds(26));

    await SetSimulatedHeartRateAsync(135);
    await Expect(Page.GetByLabel("Heart rate", new() { Exact = true })).ToContainTextAsync("135");
    await Page.WaitForTimeoutAsync(1_200);
    await Expect(Page.Locator(".control-rail--speed h2")).ToContainTextAsync("6.7");
    await Expect(Page.Locator("[data-series='measured-speed']")).ToHaveAttributeAsync(
      "d",
      new System.Text.RegularExpressions.Regex("^M.+L"));
    ChartTimelineReading firstTimeline = await ReadAndValidateTimelineAsync();
    await Page.WaitForFunctionAsync(
      "start => Number(document.querySelector('.chart-cursor')?.dataset.elapsedSeconds) >= start + 2",
      firstTimeline.ElapsedSeconds,
      new PageWaitForFunctionOptions { Timeout = 5_000 });
    ChartTimelineReading secondTimeline = await ReadAndValidateTimelineAsync();
    Assert.True(secondTimeline.ElapsedSeconds >= firstTimeline.ElapsedSeconds + 2,
      $"Elapsed time must advance by at least two seconds; first={firstTimeline.ElapsedSeconds:0.###}, second={secondTimeline.ElapsedSeconds:0.###}.");
    Assert.True(secondTimeline.CursorX > firstTimeline.CursorX,
      $"Live progress cursor must advance with elapsed time; first x={firstTimeline.CursorX:0.###}, second x={secondTimeline.CursorX:0.###}.");
    Assert.InRange(Math.Abs(secondTimeline.DurationSeconds - 120), 0, 0.01);
    Assert.True(secondTimeline.CursorX < 700,
      $"A two-minute workout cursor must not be at the end before completion; elapsed={secondTimeline.ElapsedSeconds:0.###}, x={secondTimeline.CursorX:0.###}.");
    double markerX = double.Parse(
      await Page.Locator(".chart-marker").GetAttributeAsync("cx") ?? "0",
      CultureInfo.InvariantCulture);
    Assert.True(markerX > 100 && markerX < 700, $"Latest measured marker must be inside the graph after elapsed time advances; x={markerX:0.##}.");
    await AssertInsideViewportAsync(stop, "iphone17-pro-max live recovery");
    await AssertNoHorizontalOverflowAsync();

    string validationDirectory = Path.Combine(gateway.ProjectRoot, "validation", "playwright", "accepted");
    string galleryDirectory = Path.Combine(gateway.ProjectRoot, "output", "playwright", "gallery");
    Directory.CreateDirectory(validationDirectory);
    Directory.CreateDirectory(galleryDirectory);
    foreach (string path in new[]
    {
      Path.Combine(validationDirectory, "tr014-live-heart-rate-iphone17-pro-max.png"),
      Path.Combine(galleryDirectory, "control-live-heart-rate-iphone17-pro-max.png"),
    })
    {
      await Page.ScreenshotAsync(new PageScreenshotOptions { Path = path, FullPage = false });
    }

    await SetPhysicalMotionAsync(isMoving: true, measuredSpeedKph: 12.0, measuredInclinePercent: 12.0);
    await Expect(speedAxis.Locator("span")).ToHaveCountAsync(12);
    await Expect(speedAxis.Locator("span").First).ToHaveTextAsync("12");
    await Expect(inclineAxis.Locator("span")).ToHaveCountAsync(12);
    await Expect(inclineAxis.Locator("span").First).ToHaveTextAsync("12");

    await stop.ClickAsync();
    ILocator stopDialog = Page.GetByRole(AriaRole.Dialog);
    await Expect(stopDialog.GetByRole(AriaRole.Heading, new() { Name = "What should happen to this session?", Exact = true })).ToBeVisibleAsync();
    await stopDialog.GetByRole(AriaRole.Button, new() { Name = "End and save", Exact = false }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready to run", Exact = true })).ToBeVisibleAsync();
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Distance_run_uses_an_expanding_timeline_and_cursor_advances_instead_of_staying_at_the_end()
  {
    SeededPlan plan = await SeedPlanAsync("distance-timeline", heartRateTarget: false, distanceGoal: true);
    await Page.SetViewportSizeAsync(440, 956);
    await NavigateAndSelectPlanAsync(plan);
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
      .ToBeVisibleAsync();
    await SetPhysicalMotionAsync(isMoving: true, measuredSpeedKph: 6.5, measuredInclinePercent: 1.0);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();

    ChartTimelineReading firstTimeline = await ReadAndValidateTimelineAsync();
    Assert.InRange(Math.Abs(firstTimeline.DurationSeconds - 300), 0, 0.01);
    Assert.True(firstTimeline.CursorX < 100,
      $"A new distance run must start within the first part of its five-minute timeline; x={firstTimeline.CursorX:0.###}.");
    await Page.WaitForFunctionAsync(
      "start => Number(document.querySelector('.chart-cursor')?.dataset.elapsedSeconds) >= start + 2",
      firstTimeline.ElapsedSeconds,
      new PageWaitForFunctionOptions { Timeout = 5_000 });
    ChartTimelineReading secondTimeline = await ReadAndValidateTimelineAsync();
    Assert.True(secondTimeline.CursorX > firstTimeline.CursorX,
      $"Distance-run cursor must advance; first x={firstTimeline.CursorX:0.###}, second x={secondTimeline.CursorX:0.###}.");
    Assert.True(secondTimeline.CursorX < 100,
      $"Distance-run cursor must not be pinned to the end; elapsed={secondTimeline.ElapsedSeconds:0.###}, x={secondTimeline.CursorX:0.###}.");

    await Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true }).ClickAsync();
  }

  private async Task NavigateAndSelectPlanAsync(SeededPlan plan)
  {
    await ResetSimulatorAsync();
    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready to run", Exact = true })).ToBeVisibleAsync();
    await Page.SelectActiveRunnerAsync(plan.ProfileName);
    await Page.OpenRunChoicesAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
  }

  private async Task ResetSimulatorAsync()
  {
    using HttpClient client = CreateClient();
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/live/simulator/reset", new { });
    Assert.Equal(HttpStatusCode.NoContent, response.StatusCode);
  }

  private async Task SetPhysicalMotionAsync(bool isMoving, double measuredSpeedKph, double measuredInclinePercent)
  {
    using HttpClient client = CreateClient();
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/live/simulator/physical-motion", new
    {
      isMoving,
      measuredSpeedKph,
      measuredInclinePercent,
    });
    Assert.Equal(HttpStatusCode.NoContent, response.StatusCode);
  }

  private async Task SetSimulatedHeartRateAsync(ushort beatsPerMinute)
  {
    using HttpClient client = CreateClient();
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/live/simulator/heart-rate", new
    {
      beatsPerMinute,
    });
    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
  }

  private async Task WaitForSpeedWithFreshHeartRateAsync(
    string expectedSpeed,
    ushort beatsPerMinute,
    TimeSpan timeout)
  {
    var stopwatch = Stopwatch.StartNew();
    ILocator target = Page.Locator(".control-rail--speed h2");
    while (stopwatch.Elapsed < timeout)
    {
      await SetSimulatedHeartRateAsync(beatsPerMinute);
      if ((await target.InnerTextAsync()).Contains(expectedSpeed, StringComparison.Ordinal)) return;
      await Page.WaitForTimeoutAsync(1_000);
    }

    string automation = await Page.Locator(".control-automation p").InnerTextAsync();
    Assert.Fail($"Target speed did not reach {expectedSpeed} km/h within {timeout.TotalSeconds:0} seconds while heart rate stayed fresh at {beatsPerMinute} bpm. Last value: {await target.InnerTextAsync()}. Automation: {automation}");
  }

  private async Task CompletePhysicalSessionAsync()
  {
    using HttpClient client = CreateClient();
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/live/simulator/complete-physical-session", new { });
    Assert.Equal(HttpStatusCode.NoContent, response.StatusCode);
  }

  private async Task<SeededPlan> SeedPlanAsync(
    string scenario,
    bool heartRateTarget,
    bool openSpeed = false,
    double? goalMinutes = null,
    bool distanceGoal = false,
    string? profileDisplayName = null,
    string? workoutDisplayName = null)
  {
    string suffix = $"{scenario}-{Guid.NewGuid():N}"[..(scenario.Length + 9)];
    string profileName = profileDisplayName ?? $"Runner {suffix}";
    string workoutName = workoutDisplayName ?? $"Workout {suffix}";

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
      heartRateIncreaseStepKph = 0.2,
      heartRateIncreaseCooldownSeconds = 15,
      heartRateDecreaseStepKph = 0.5,
      heartRateDecreaseCooldownSeconds = 5,
      expectedVersion = (int?)null,
    });
    Assert.Equal(HttpStatusCode.Created, profileResponse.StatusCode);

    using HttpResponseMessage workoutResponse = await client.PostAsJsonAsync("/api/planning/workouts", new
    {
      operationId = Guid.NewGuid(),
      name = workoutName,
      description = "TR-004 browser acceptance fixture",
      blocks = new[]
      {
        new
        {
          kind = "step",
          repetitions = 1,
          blocks = Array.Empty<object>(),
          goalKind = distanceGoal ? "distance" : "time",
          goalValue = distanceGoal ? 0.1 : goalMinutes ?? (heartRateTarget ? 120.0 : 20.0),
          speedKind = heartRateTarget ? "heartrate" : openSpeed ? "open" : "fixed",
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
          cue = "Settle into an even rhythm.",
          notes = "Acceptance-test fixture",
        },
      },
    });
    Assert.Equal(HttpStatusCode.Created, workoutResponse.StatusCode);
    return new SeededPlan(profileName, workoutName);
  }

  private HttpClient CreateClient() => new() { BaseAddress = gateway.BaseAddress };

  private async Task<ChartTimelineReading> ReadAndValidateTimelineAsync()
  {
    ILocator cursor = Page.Locator(".chart-cursor");
    JsonElement timeline = await cursor.EvaluateAsync<JsonElement>(
      "element => ({ elapsedSeconds: Number(element.dataset.elapsedSeconds), durationSeconds: Number(element.dataset.durationSeconds), cursorX: Number(element.getAttribute('x1')) })");
    double elapsedSeconds = timeline.GetProperty("elapsedSeconds").GetDouble();
    double durationSeconds = timeline.GetProperty("durationSeconds").GetDouble();
    double cursorX = timeline.GetProperty("cursorX").GetDouble();
    double expectedX = 10 + (Math.Clamp(elapsedSeconds / Math.Max(1, durationSeconds), 0, 1) * 700);
    Assert.InRange(Math.Abs(cursorX - expectedX), 0, 0.01);

    string displayedElapsedText = await Page.GetByLabel("Workout progress time", new() { Exact = true }).Locator("strong").InnerTextAsync();
    TimeSpan displayedElapsed = TimeSpan.ParseExact(
      displayedElapsedText,
      displayedElapsedText.Count(static character => character == ':') == 2 ? @"h\:mm\:ss" : @"m\:ss",
      CultureInfo.InvariantCulture);
    Assert.InRange(Math.Abs(displayedElapsed.TotalSeconds - elapsedSeconds), 0, 1);

    string displayedDurationText = await Page.GetByLabel("Elapsed time axis", new() { Exact = true }).Locator("span").Last.InnerTextAsync();
    TimeSpan displayedDuration = TimeSpan.ParseExact(
      displayedDurationText,
      displayedDurationText.Count(static character => character == ':') == 2 ? @"h\:mm\:ss" : @"m\:ss",
      CultureInfo.InvariantCulture);
    Assert.InRange(Math.Abs(displayedDuration.TotalSeconds - durationSeconds), 0, 1);
    return new ChartTimelineReading(elapsedSeconds, durationSeconds, cursorX);
  }

  private async Task AssertNoHorizontalOverflowAsync()
  {
    string overflowReport = await Page.EvaluateAsync<string>(
      """
      () => {
        const viewportWidth = document.documentElement.clientWidth;
        if (document.documentElement.scrollWidth <= viewportWidth + 1) return '';
        return Array.from(document.querySelectorAll('body *'))
          .map(element => ({ element, rect: element.getBoundingClientRect() }))
          .filter(item => item.rect.right > viewportWidth + 1 || item.rect.left < -1)
          .slice(0, 8)
          .map(item => `${item.element.tagName.toLowerCase()}.${item.element.className || ''} [${item.rect.left.toFixed(1)}, ${item.rect.right.toFixed(1)}]`)
          .join('; ');
      }
      """);
    Assert.True(string.IsNullOrEmpty(overflowReport), $"Horizontal overflow: {overflowReport}");
  }

  private static async Task AssertTouchTargetAsync(ILocator control, string viewportName)
  {
    LocatorBoundingBoxResult? box = await control.BoundingBoxAsync();
    Assert.NotNull(box);
    Assert.True(box.Width >= 44, $"Control width was {box.Width}px at {viewportName}.");
    Assert.True(box.Height >= 44, $"Control height was {box.Height}px at {viewportName}.");
  }

  private async Task AssertInsideViewportAsync(ILocator control, string viewportName)
  {
    LocatorBoundingBoxResult? box = await control.BoundingBoxAsync();
    Assert.NotNull(box);
    PageViewportSizeResult? viewport = Page.ViewportSize;
    Assert.NotNull(viewport);
    Assert.True(box.X >= 0 && box.X + box.Width <= viewport.Width + 1,
      $"Control was horizontally outside the initial viewport at {viewportName}: x={box.X:0.0}, width={box.Width:0.0}.");
    Assert.True(box.Y >= 0 && box.Y + box.Height <= viewport.Height + 1,
      $"Control was vertically outside the initial viewport at {viewportName}: y={box.Y:0.0}, height={box.Height:0.0}, viewport={viewport.Height}.");
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

  private async Task ScreenshotViewportAsync(string fileName)
  {
    string directory = Path.Combine(gateway.ProjectRoot, "validation", "playwright", "accepted");
    Directory.CreateDirectory(directory);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(directory, fileName),
      FullPage = false,
    });
  }

  private async Task ShowcaseScreenshotAsync(string fileName)
  {
    string directory = Path.Combine(gateway.ProjectRoot, "screenshots", "showcase");
    Directory.CreateDirectory(directory);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(directory, fileName),
      FullPage = false,
    });
  }

  private sealed record SeededPlan(string ProfileName, string WorkoutName);
  private sealed record ChartTimelineReading(double ElapsedSeconds, double DurationSeconds, double CursorX);
}
