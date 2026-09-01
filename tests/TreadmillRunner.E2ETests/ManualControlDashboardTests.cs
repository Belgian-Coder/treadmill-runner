using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;

namespace TreadmillRunner.E2ETests;

public sealed class ManualControlDashboardTests(GatewayFixture gateway) : PageTest, IClassFixture<GatewayFixture>
{
  public static TheoryData<string, int, int> Viewports => new()
  {
    { "phone-narrow", 320, 800 },
    { "phone-portrait", 390, 844 },
    { "iphone17-pro-max", 440, 956 },
    { "phone-landscape", 844, 390 },
    { "iphone17-pro-max-landscape", 956, 440 },
    { "ipad-portrait", 820, 1180 },
    { "tablet", 1180, 820 },
    { "desktop", 1920, 1080 },
  };

  public static TheoryData<string, int, int> ActiveResponsiveViewports => new()
  {
    { "desktop-full-hd", 1920, 1080 },
    { "phone-portrait", 390, 844 },
    { "phone-landscape", 844, 390 },
    { "phone-compact", 360, 800 },
    { "phone-narrow", 320, 800 },
  };

  public static TheoryData<string, string, int, int> ReadabilityViewports => new()
  {
    { "LargeText", "phone-portrait", 390, 844 },
    { "LargeText", "phone-landscape", 844, 390 },
    { "LargeText", "tablet", 1180, 820 },
    { "LargeText", "desktop", 1920, 1080 },
    { "HighContrast", "phone-portrait", 390, 844 },
    { "HighContrast", "phone-landscape", 844, 390 },
    { "HighContrast", "tablet", 1180, 820 },
    { "HighContrast", "desktop", 1920, 1080 },
  };

  [Theory]
  [MemberData(nameof(ReadabilityViewports))]
  [Trait("Category", "Browser")]
  public async Task Runner_readability_preferences_render_without_overflow(
    string displayStyle,
    string viewport,
    int width,
    int height)
  {
    await Page.SetViewportSizeAsync(width, height);
    await ResetSimulatorAsync();
    SeededPlan plan = await SeedPlanAsync($"{displayStyle}-{viewport}");
    await SavePreferencesAsync(plan.ProfileId, displayStyle);

    try
    {
      await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
      await Page.SelectActiveRunnerAsync(plan.ProfileName);
      await Page.OpenRunChoicesAsync();
      await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
      await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();
      await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
        .ToBeVisibleAsync();
      await SetPhysicalMotionAsync(1.2, 0.5);

      await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();
      await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true })).ToBeEnabledAsync();
      await Expect(Page.Locator(".control-command-status")).Not.ToHaveTextAsync("Waiting for an active session.");
      await SetSimulatedHeartRateAsync(132);
      await Expect(Page.Locator(".control-header-actions .global-hr-status")).ToContainTextAsync("132");
      string expectedClass = displayStyle == "LargeText" ? "control-page--largetext" : "control-page--highcontrast";
      await Expect(Page.Locator("#control-dashboard")).ToHaveClassAsync(
        new System.Text.RegularExpressions.Regex(expectedClass));
      await Expect(Page.Locator(".control-primary-metrics article")).ToHaveCountAsync(3);

      bool hasHorizontalOverflow = await Page.EvaluateAsync<bool>(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1");
      Assert.False(hasHorizontalOverflow, $"{displayStyle} overflowed horizontally at {width}x{height}.");

      if (displayStyle == "LargeText")
      {
        await Expect(Page.Locator(".control-live-chart")).ToHaveCSSAsync("display", "none");
        double metricFontSize = await Page.Locator(".control-primary-metrics strong").First.EvaluateAsync<double>(
          "element => Number.parseFloat(getComputedStyle(element).fontSize)");
        Assert.True(metricFontSize >= 29,
          $"Large-text metrics must remain conspicuously readable at {width}x{height}; actual {metricFontSize}px.");
      }
      else
      {
        await Expect(Page.Locator("#control-dashboard")).ToHaveCSSAsync("background-color", "rgb(0, 0, 0)");
        await Expect(Page.Locator(".control-primary-metrics article").First).ToHaveCSSAsync("border-top-width", "2px");
        await Expect(Page.Locator(".control-primary-metrics article").First).ToHaveCSSAsync("color", "rgb(255, 255, 255)");
      }

      ILocator stop = Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true });
      LocatorBoundingBoxResult? stopBox = await stop.BoundingBoxAsync();
      Assert.NotNull(stopBox);
      Assert.True(stopBox.Width >= 44 && stopBox.Height >= 44,
        $"Stop must retain a 44px target in {displayStyle} at {width}x{height}: {stopBox}.");
      if (width <= 844)
      {
        Assert.True(stopBox.Y >= 0 && stopBox.Y + stopBox.Height <= height + 1,
          $"Stop must remain visible in {displayStyle} at {width}x{height}: {stopBox}.");
      }

      string showcaseDirectory = ScreenshotArtifactPaths.ShowcaseDirectory(gateway.ProjectRoot);
      await Page.ScreenshotAsync(new PageScreenshotOptions
      {
        Path = Path.Combine(showcaseDirectory, $"tr-031-control-{displayStyle.ToLowerInvariant()}-{viewport}.png"),
        FullPage = false,
      });
    }
    finally
    {
      await ResetSimulatorAsync();
    }
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Local_first_surfaces_remain_usable_when_nonlocal_network_is_blocked()
  {
    var externalRequests = new ConcurrentQueue<string>();
    await Page.RouteAsync("**/*", route =>
    {
      if (Uri.TryCreate(route.Request.Url, UriKind.Absolute, out Uri? uri) &&
          uri.Scheme is "http" or "https" && !uri.IsLoopback)
      {
        externalRequests.Enqueue(route.Request.Url);
        return route.AbortAsync("internetdisconnected");
      }

      return route.ContinueAsync();
    });
    Page.WebSocket += (_, socket) =>
    {
      if (Uri.TryCreate(socket.Url, UriKind.Absolute, out Uri? uri) && !uri.IsLoopback)
        externalRequests.Enqueue(socket.Url);
    };

    await ResetSimulatorAsync();
    SeededPlan plan = await SeedPlanAsync("offline-local-first");
    await SavePreferencesAsync(plan.ProfileId, "LargeText");

    try
    {
      foreach ((string path, string heading) in new[]
      {
        ("/profiles", "Profiles"),
        ("/history", "History"),
        ("/operations", "Operations"),
      })
      {
        await Page.GotoAsync(new Uri(gateway.BaseAddress, path).AbsoluteUri,
          new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
        await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = heading, Exact = true })).ToBeVisibleAsync();
      }

      await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
      await Page.SelectActiveRunnerAsync(plan.ProfileName);
      await Page.OpenRunChoicesAsync();
      await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
      await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();
      await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true })).ToBeVisibleAsync();
      await Expect(Page.Locator("#control-dashboard")).ToHaveClassAsync(
        new System.Text.RegularExpressions.Regex("control-page--largetext"));

      Assert.Empty(externalRequests);
    }
    finally
    {
      await ResetSimulatorAsync();
    }
  }

  [Theory]
  [MemberData(nameof(ActiveResponsiveViewports))]
  [Trait("Category", "Browser")]
  public async Task Active_control_remains_compact_and_touchable_at_narrow_viewports(
    string viewport,
    int width,
    int height)
  {
    await Page.SetViewportSizeAsync(width, height);
    await ResetSimulatorAsync();
    SeededPlan plan = await SeedPlanAsync($"tr036-{viewport}");

    try
    {
      await PrepareActiveControlAsync(plan);

      await Expect(Page.Locator(".active-runner-picker summary")).ToContainTextAsync(plan.ProfileName);
      ILocator compactRunnerContext = Page.Locator(".control-runner-context");
      if (height <= 500)
      {
        await Expect(compactRunnerContext).ToBeVisibleAsync();
        await Expect(compactRunnerContext).ToContainTextAsync(plan.ProfileName);
        await Expect(compactRunnerContext).ToHaveAttributeAsync("aria-label", $"Active runner: {plan.ProfileName}");
      }
      else
      {
        await Expect(compactRunnerContext).ToBeHiddenAsync();
      }
      await Expect(Page.Locator(".control-command-status")).ToContainTextAsync("Session running");
      await Expect(Page.Locator(".control-command-status")).Not.ToContainTextAsync("Hold Start");

      bool hasHorizontalOverflow = await Page.EvaluateAsync<bool>(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 || document.body.scrollWidth > document.body.clientWidth + 1");
      Assert.False(hasHorizontalOverflow, $"Active Control overflowed horizontally at {width}x{height}.");

      foreach (string actionName in new[] { "Pause", "Stop" })
      {
        ILocator action = Page.GetByRole(AriaRole.Button, new() { Name = actionName, Exact = true });
        await Expect(action).ToBeVisibleAsync();
        await Expect(action).ToBeEnabledAsync();
        LocatorBoundingBoxResult? actionBox = await action.BoundingBoxAsync();
        Assert.NotNull(actionBox);
        Assert.True(actionBox.Width >= 44 && actionBox.Height >= 44,
          $"{actionName} must retain a 44px touch target at {width}x{height}: {actionBox}.");
        Assert.True(actionBox.X >= -1 && actionBox.X + actionBox.Width <= width + 1,
          $"{actionName} must remain inside the viewport horizontally at {width}x{height}: {actionBox}.");
        Assert.True(actionBox.Y >= -1 && actionBox.Y + actionBox.Height <= height + 1,
          $"{actionName} must remain inside the viewport vertically at {width}x{height}: {actionBox}.");
      }

      ILocator fullscreenButton = Page.GetByRole(AriaRole.Button, new()
      {
        Name = "Toggle full-screen dashboard",
        Exact = true,
      });
      await Expect(fullscreenButton).ToBeVisibleAsync();
      await Expect(fullscreenButton).ToHaveAttributeAsync("aria-pressed", "false");
      await Expect(fullscreenButton).ToHaveCSSAsync("display", "grid");
      await Expect(fullscreenButton.Locator("svg")).ToHaveCountAsync(1);
      string fullscreenText = await fullscreenButton.EvaluateAsync<string>(
        "element => (element.textContent || '').trim()");
      Assert.Empty(fullscreenText);
      LocatorBoundingBoxResult? fullscreenBox = await fullscreenButton.BoundingBoxAsync();
      Assert.NotNull(fullscreenBox);
      Assert.True(fullscreenBox.Width >= 44 && fullscreenBox.Height >= 44,
        $"Full-screen control must retain a 44px target at {width}x{height}: {fullscreenBox}.");
      Assert.True(fullscreenBox.Width <= 72 && fullscreenBox.Height <= 72,
        $"Full-screen control must remain a compact icon button at {width}x{height}: {fullscreenBox}.");
      Assert.True(fullscreenBox.Width <= fullscreenBox.Height * 1.6,
        $"Full-screen control must not stretch into a text row at {width}x{height}: {fullscreenBox}.");

      ILocator chartFocus = Page.GetByRole(AriaRole.Button, new() { Name = "Chart", Exact = true });
      await chartFocus.ClickAsync();
      await Expect(chartFocus).ToHaveAttributeAsync("aria-pressed", "true");
      await Expect(Page.Locator("#control-dashboard")).ToHaveClassAsync(
        new System.Text.RegularExpressions.Regex("control-page--chart"));
      if (width <= 650)
      {
        await Expect(Page.Locator(".control-live-chart--focused .control-chart-actions > span"))
          .ToHaveTextAsync(plan.WorkoutName);
        ILocator collapseGraph = Page.GetByRole(AriaRole.Button, new() { Name = "Collapse live graph", Exact = true });
        await Expect(collapseGraph).ToBeVisibleAsync();
        await Expect(collapseGraph).ToContainTextAsync("Back");
      }

      ILocator legendGroups = Page.Locator(".control-chart-legend__group");
      await Expect(legendGroups).ToHaveCountAsync(2);
      foreach (ILocator group in await legendGroups.AllAsync())
      {
        await Expect(group).ToContainTextAsync("Plan");
        await Expect(group).ToContainTextAsync("Target");
        await Expect(group).ToContainTextAsync("Measured");
        bool headingSharesFirstRow = await group.EvaluateAsync<bool>(
          "element => { const heading = element.querySelector('strong'); const first = element.querySelector('span'); if (!heading || !first) return false; const a = heading.getBoundingClientRect(); const b = first.getBoundingClientRect(); return Math.abs(a.top - b.top) <= 2; }");
        Assert.True(headingSharesFirstRow, $"Each legend heading must stay with its first item at {width}x{height}.");
      }

      JsonElement chartGeometry = await Page.Locator(".control-live-chart").EvaluateAsync<JsonElement>(
        "chart => { const heading = chart.querySelector('.control-chart-heading'); const speedUnit = chart.querySelector('.chart-y-scale:not(.chart-y-scale--secondary) .chart-axis-unit'); const axis = chart.querySelector('.chart-time-scale'); const legend = chart.querySelector('.control-chart-legend'); if (!heading || !speedUnit || !axis || !legend) return { missing: true }; const headingBox = heading.getBoundingClientRect(); const unitBox = speedUnit.getBoundingClientRect(); const axisBox = axis.getBoundingClientRect(); const legendBox = legend.getBoundingClientRect(); const labels = [...axis.querySelectorAll('span')].filter(label => getComputedStyle(label).display !== 'none').map(label => { const box = label.getBoundingClientRect(); return { bottom: box.bottom, left: box.left, right: box.right }; }); return { missing: false, headingBottom: headingBox.bottom, speedUnitTop: unitBox.top, axisBottom: axisBox.bottom, legendTop: legendBox.top, legendBottom: legendBox.bottom, labels }; }");
      Assert.False(chartGeometry.GetProperty("missing").GetBoolean(),
        $"Chart time scale and legend must be present in Chart mode at {width}x{height}.");
      double headingBottom = chartGeometry.GetProperty("headingBottom").GetDouble();
      double speedUnitTop = chartGeometry.GetProperty("speedUnitTop").GetDouble();
      double requiredHeadingClearance = width <= 650 ? 2 : 0;
      Assert.True(speedUnitTop >= headingBottom + requiredHeadingClearance,
        $"Chart speed unit must clear the heading at {width}x{height}; heading bottom {headingBottom}, unit top {speedUnitTop}.");
      double axisBottom = chartGeometry.GetProperty("axisBottom").GetDouble();
      double legendTop = chartGeometry.GetProperty("legendTop").GetDouble();
      Assert.True(legendTop >= axisBottom - 1,
        $"Chart time labels must have a dedicated row before the legend at {width}x{height}: {chartGeometry}.");
      double legendBottom = chartGeometry.GetProperty("legendBottom").GetDouble();
      Assert.True(legendBottom <= height + 1,
        $"The complete chart legend must remain in the viewport at {width}x{height}: {chartGeometry}.");
      foreach (JsonElement label in chartGeometry.GetProperty("labels").EnumerateArray())
      {
        Assert.True(label.GetProperty("bottom").GetDouble() <= legendTop + 1,
          $"Chart time label overlapped the legend at {width}x{height}: {chartGeometry}");
      }
      if (height > 500)
      {
        LocatorBoundingBoxResult? chartDockBox = await Page.Locator(".control-action-dock").BoundingBoxAsync();
        Assert.NotNull(chartDockBox);
        Assert.True(legendBottom <= chartDockBox.Y - 4,
          $"The chart legend must clear the motion dock at {width}x{height}: legend bottom {legendBottom}, dock={chartDockBox}.");
      }

      string screenshotDirectory = Path.Combine(gateway.ProjectRoot, "output", "playwright", "bug-tr-037");
      Directory.CreateDirectory(screenshotDirectory);
      await Page.ScreenshotAsync(new PageScreenshotOptions
      {
        Path = Path.Combine(screenshotDirectory, $"control-chart-{width}x{height}.png"),
        FullPage = false,
      });
      ILocator exitChartFocus = width <= 650
        ? Page.GetByRole(AriaRole.Button, new() { Name = "Collapse live graph", Exact = true })
        : Page.GetByRole(AriaRole.Button, new() { Name = "Balanced", Exact = true });
      await exitChartFocus.ClickAsync();
      await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Balanced", Exact = true }))
        .ToHaveAttributeAsync("aria-pressed", "true");
      await Page.EvaluateAsync("window.scrollTo(0, 0)");

      if (width <= 360)
      {
        ILocator overviewAxis = Page.Locator(".control-console-grid--balanced .chart-time-scale");
        await Expect(overviewAxis).ToHaveAttributeAsync("data-endpoint", "3:00");
        LocatorBoundingBoxResult? overviewAxisBox = await overviewAxis.BoundingBoxAsync();
        LocatorBoundingBoxResult? overviewDockBox = await Page.Locator(".control-action-dock").BoundingBoxAsync();
        Assert.NotNull(overviewAxisBox);
        Assert.NotNull(overviewDockBox);
        Assert.True(overviewAxisBox.Y + overviewAxisBox.Height <= overviewDockBox.Y - 4,
          $"The compact overview time axis must clear the motion dock at {width}x{height}: axis={overviewAxisBox}, dock={overviewDockBox}.");
      }

      if (width <= 650 && height > 500)
      {
        LocatorBoundingBoxResult? initialDetailsBox = await Page.Locator(".control-details").BoundingBoxAsync();
        LocatorBoundingBoxResult? initialDockBox = await Page.Locator(".control-action-dock").BoundingBoxAsync();
        Assert.NotNull(initialDetailsBox);
        Assert.NotNull(initialDockBox);
        Assert.True(initialDetailsBox.Y >= initialDockBox.Y + initialDockBox.Height - 1,
          $"Technical details must not begin behind the fixed motion dock at {width}x{height}: details={initialDetailsBox}, dock={initialDockBox}.");
      }

      await Page.ScreenshotAsync(new PageScreenshotOptions
      {
        Path = Path.Combine(screenshotDirectory, $"control-active-{width}x{height}.png"),
        FullPage = false,
      });

      if (width <= 650 && height > 500)
      {
        ILocator details = Page.Locator(".control-details");
        await details.EvaluateAsync("element => element.scrollIntoView({ block: 'end' })");
        await Page.WaitForTimeoutAsync(50);
        LocatorBoundingBoxResult? detailsBox = await details.BoundingBoxAsync();
        LocatorBoundingBoxResult? dockBox = await Page.Locator(".control-action-dock").BoundingBoxAsync();
        Assert.NotNull(detailsBox);
        Assert.NotNull(dockBox);
        Assert.True(detailsBox.Y + detailsBox.Height <= dockBox.Y - 4,
          $"The last Control section must clear the fixed action dock at {width}x{height}: details={detailsBox}, dock={dockBox}.");
      }
    }
    finally
    {
      await ResetSimulatorAsync();
    }
  }

  [Theory]
  [MemberData(nameof(Viewports))]
  [Trait("Category", "Browser")]
  public async Task Control_page_is_realtime_touchable_with_vertical_preset_rails(
    string viewport,
    int width,
    int height)
  {
    var browserErrors = new ConcurrentQueue<string>();
    object liveSocketSync = new();
    int activeLiveSockets = 0;
    int maximumLiveSockets = 0;
    Page.WebSocket += (_, socket) =>
    {
      if (socket.Url.Contains("/hubs/live", StringComparison.OrdinalIgnoreCase))
      {
        lock (liveSocketSync)
        {
          activeLiveSockets++;
          maximumLiveSockets = Math.Max(maximumLiveSockets, activeLiveSockets);
        }
        socket.Close += (_, _) =>
        {
          lock (liveSocketSync) activeLiveSockets--;
        };
      }
    };
    Page.PageError += (_, error) => browserErrors.Enqueue(error);
    Page.Console += (_, message) =>
    {
      if (message.Type == "error") browserErrors.Enqueue(message.Text);
    };
    await Page.SetViewportSizeAsync(width, height);
    await Page.AddInitScriptAsync("""
        window.__wakeLockRequests = 0;
        window.__wakeLockReleases = 0;
        window.__wakeLockReleaseCallback = null;
        Object.defineProperty(Navigator.prototype, 'wakeLock', { configurable: true, get: () => ({
          request: async () => {
            window.__wakeLockRequests++;
            const listeners = {};
            return {
              addEventListener: (name, callback) => {
                listeners[name] = callback;
                if (name === 'release') window.__wakeLockReleaseCallback = callback;
              },
              release: async () => { window.__wakeLockReleases++; if (listeners.release) listeners.release(); }
            };
          }
        })});
        """);
    await ResetSimulatorAsync();
    SeededPlan plan = await SeedPlanAsync(viewport);

    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.SelectActiveRunnerAsync(plan.ProfileName);
    await Page.OpenRunChoicesAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();
    await Expect(Page).ToHaveURLAsync(new System.Text.RegularExpressions.Regex("/control$"));
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
      .ToBeVisibleAsync();
    await Page.WaitForFunctionAsync("window.__wakeLockRequests >= 1", null, new PageWaitForFunctionOptions { Timeout = 5_000 });
    Assert.True(await Page.EvaluateAsync<int>("window.__wakeLockRequests") >= 1,
      "Control must request a screen wake lock while a run is armed.");
    await Expect(Page.GetByLabel("Screen stay-awake active", new() { Exact = true })).ToBeVisibleAsync();
    await Page.EvaluateAsync("window.__wakeLockReleaseCallback && window.__wakeLockReleaseCallback()");
    await Page.WaitForFunctionAsync("window.__wakeLockRequests >= 2", null, new PageWaitForFunctionOptions { Timeout = 5_000 });
    await Expect(Page.GetByLabel("Screen stay-awake active", new() { Exact = true })).ToBeVisibleAsync();
    await SetPhysicalMotionAsync(1.2, 0.5);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.Locator(".control-command-status")).ToContainTextAsync("Session running");
    await Expect(Page.Locator(".control-command-status")).Not.ToContainTextAsync("Hold Start");
    await Expect(Page.GetByLabel("Live workout metrics", new() { Exact = true })).ToContainTextAsync("4.5");
    await Expect(Page.Locator(".control-rail--incline h2")).ToContainTextAsync("0.5");
    await Expect(Page.GetByRole(AriaRole.Group, new() { Name = "Speed presets", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Group, new() { Name = "Incline presets", Exact = true })).ToBeVisibleAsync();
    foreach (string speed in new[] { "5.0", "7.0", "7.5", "8.0", "8.5", "9.0", "9.5", "10.0" })
      await Expect(Page.GetByRole(AriaRole.Button, new() { Name = $"Set speed to {speed} km/h", Exact = true })).ToBeVisibleAsync();
    foreach (string incline in new[] { "0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "4.0", "5.0" })
      await Expect(Page.GetByRole(AriaRole.Button, new() { Name = $"Set incline to {incline}%", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Set speed to 4.5 km/h", Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Set speed to 5.5 km/h", Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Set incline to 3.5%", Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Set incline to 4.5%", Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByLabel("Live speed in kilometers per hour and incline percentage over elapsed time", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.Locator("[data-component='live-progress-chart']")).ToHaveCountAsync(1);
    ILocator speedAxis = Page.GetByLabel("Speed axis in kilometers per hour", new() { Exact = true });
    await Expect(speedAxis.Locator("span")).ToHaveCountAsync(10);
    await Expect(speedAxis.Locator("span").First).ToHaveTextAsync("10");
    ILocator inclineAxis = Page.GetByLabel("Incline axis in percent", new() { Exact = true });
    await Expect(inclineAxis.Locator("span")).ToHaveCountAsync(10);
    await Expect(inclineAxis.Locator("span").First).ToHaveTextAsync("10");
    await Expect(inclineAxis.Locator("span").Last).ToHaveTextAsync("1");
    string plannedSpeedPath = await Page.Locator("[data-series='planned-speed']").GetAttributeAsync("d") ?? string.Empty;
    string plannedInclinePath = await Page.Locator("[data-series='planned-incline']").GetAttributeAsync("d") ?? string.Empty;
    Assert.True(plannedSpeedPath.Count(static character => character == 'L') >= 5,
      "The selected interval workout must be visible as a multi-segment plan overlay.");
    Assert.True(HasVerticalSvgSegment(plannedSpeedPath),
      "The planned speed overlay must retain vertical jumps at fixed-step boundaries.");
    Assert.True(HasVerticalSvgSegment(plannedInclinePath),
      "The planned incline overlay must retain vertical jumps at fixed-step boundaries.");
    await Expect(Page.GetByLabel("Technical session details", new() { Exact = true })).Not.ToHaveAttributeAsync("open", "");
    await Expect(Page.Locator(".site-header .global-hr-status")).ToHaveCountAsync(0);
    await Expect(Page.Locator(".control-header-actions .global-hr-status")).ToBeVisibleAsync();
    lock (liveSocketSync) Assert.Equal(1, maximumLiveSockets);
    if (width == 440)
    {
      await Expect(Page.Locator(".control-header-actions .global-hr-status__copy")).ToBeVisibleAsync();
      await Expect(Page.Locator(".control-header-actions .global-hr-status__copy")).ToContainTextAsync("Sensor · Simulated");
    }
    ILocator fullscreenButton = Page.GetByRole(AriaRole.Button, new() { Name = "Toggle full-screen dashboard", Exact = true });
    await Expect(fullscreenButton).ToBeVisibleAsync();
    await Expect(fullscreenButton).ToHaveAttributeAsync("aria-label", "Toggle full-screen dashboard");
    await Expect(fullscreenButton).ToHaveAttributeAsync("aria-pressed", "false");
    ILocator fullscreenIcon = fullscreenButton.Locator("svg");
    await Expect(fullscreenIcon).ToHaveCountAsync(1);
    await Expect(fullscreenIcon).ToHaveAttributeAsync("aria-hidden", "true");
    await Expect(Page.GetByLabel("Control safety notice", new() { Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByText("Physical movement detected", new() { Exact = true })).ToHaveCountAsync(0);

    ILocator focusGroup = Page.GetByRole(AriaRole.Group, new() { Name = "Dashboard focus", Exact = true });
    ILocator balancedFocus = focusGroup.GetByRole(AriaRole.Button, new() { Name = "Balanced", Exact = true });
    ILocator chartFocus = focusGroup.GetByRole(AriaRole.Button, new() { Name = "Chart", Exact = true });
    ILocator controlsFocus = focusGroup.GetByRole(AriaRole.Button, new() { Name = "Controls", Exact = true });
    await Expect(focusGroup.GetByRole(AriaRole.Button)).ToHaveCountAsync(3);
    await Expect(balancedFocus).ToHaveAttributeAsync("aria-pressed", "true");
    await Expect(Page.Locator("#control-dashboard")).ToHaveClassAsync(
      new System.Text.RegularExpressions.Regex("control-page--balanced"));
    foreach (ILocator focusButton in new[] { balancedFocus, chartFocus, controlsFocus })
    {
      LocatorBoundingBoxResult? box = await focusButton.BoundingBoxAsync();
      Assert.NotNull(box);
      Assert.True(box.Width >= 44 && box.Height >= 44,
        $"Dashboard focus target was smaller than 44px at the {viewport} viewport.");
    }
    await controlsFocus.ClickAsync();
    await Expect(controlsFocus).ToHaveAttributeAsync("aria-pressed", "true");
    await Expect(Page.Locator("#control-dashboard")).ToHaveClassAsync(
      new System.Text.RegularExpressions.Regex("control-page--controls"));
    bool isPhoneViewport = width <= 650 || (height <= 500 && width <= 1000);
    if (isPhoneViewport)
    {
      await SaveTr039EvidenceAsync(gateway.ProjectRoot, $"controls-{viewport}");
      await AssertNoScrollMobileControlsAsync(viewport, width, height);
    }
    await chartFocus.ClickAsync();
    await Expect(chartFocus).ToHaveAttributeAsync("aria-pressed", "true");
    await Expect(Page.Locator("#control-dashboard")).ToHaveClassAsync(
      new System.Text.RegularExpressions.Regex("control-page--chart"));
    if (isPhoneViewport)
    {
      await AssertFocusedMobileChartAsync(viewport, width, height);
      await SaveTr039EvidenceAsync(gateway.ProjectRoot, $"chart-{viewport}");
      await Page.GetByRole(AriaRole.Button, new() { Name = "Collapse live graph", Exact = true }).ClickAsync();
    }
    else
      await balancedFocus.ClickAsync();
    await Expect(balancedFocus).ToHaveAttributeAsync("aria-pressed", "true");

    LocatorBoundingBoxResult? speedRail = await Page.Locator(".control-rail--speed").BoundingBoxAsync();
    LocatorBoundingBoxResult? center = await Page.GetByLabel("Live control center", new() { Exact = true }).BoundingBoxAsync();
    LocatorBoundingBoxResult? inclineRail = await Page.Locator(".control-rail--incline").BoundingBoxAsync();
    Assert.NotNull(speedRail);
    Assert.NotNull(center);
    Assert.NotNull(inclineRail);
    Assert.True(speedRail.X < center.X && center.X < inclineRail.X,
      "Speed and incline presets must remain vertical rails around the center controls.");

    if (viewport.EndsWith("landscape", StringComparison.Ordinal))
    {
      await Expect(Page.Locator(".control-action-dock .media-control").First).ToHaveCSSAsync("flex-direction", "column");
      await Expect(Page.Locator(".control-action-dock .media-control > span:last-child").First).ToHaveCSSAsync("white-space", "nowrap");
      foreach (string groupName in new[] { "Speed presets", "Incline presets" })
      {
        ILocator presetButtons = Page.GetByRole(AriaRole.Group, new() { Name = groupName, Exact = true })
          .GetByRole(AriaRole.Button);
        await Expect(presetButtons).ToHaveCountAsync(8);
        LocatorBoundingBoxResult? first = await presetButtons.Nth(0).BoundingBoxAsync();
        LocatorBoundingBoxResult? second = await presetButtons.Nth(1).BoundingBoxAsync();
        LocatorBoundingBoxResult? third = await presetButtons.Nth(2).BoundingBoxAsync();
        Assert.NotNull(first);
        Assert.NotNull(second);
        Assert.NotNull(third);
        Assert.InRange(Math.Abs(first.Y - second.Y), 0, 1);
        Assert.True(second.X > first.X, $"{groupName} must place two presets across in landscape.");
        Assert.True(third.Y > first.Y, $"{groupName} third preset must begin the second row in landscape.");
        for (int index = 0; index < await presetButtons.CountAsync(); index++)
        {
          LocatorBoundingBoxResult? box = await presetButtons.Nth(index).BoundingBoxAsync();
          Assert.NotNull(box);
          Assert.True(box.Width >= 44 && box.Height >= 44,
            $"{groupName} preset {index + 1} was smaller than 44px in landscape: {box}.");
        }
      }
    }

    ILocator speedTarget = Page.GetByRole(AriaRole.Button, new() { Name = "Set speed to 7.5 km/h", Exact = true });
    await speedTarget.ClickAsync();
    await Expect(speedTarget).ToHaveAttributeAsync("aria-pressed", "true");
    await Expect(Page.Locator(".control-rail--speed h2")).ToContainTextAsync("7.5");

    ILocator inclineTarget = Page.GetByRole(AriaRole.Button, new() { Name = "Set incline to 1.0%", Exact = true });
    await inclineTarget.ClickAsync();
    await Expect(inclineTarget).ToHaveAttributeAsync("aria-pressed", "true");
    await Expect(Page.Locator(".control-rail--incline h2")).ToContainTextAsync("1.0");

    foreach (ILocator control in new[]
    {
      speedTarget,
      inclineTarget,
      Page.GetByRole(AriaRole.Button, new() { Name = "Pause", Exact = true }),
      Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true }),
    })
    {
      LocatorBoundingBoxResult? box = await control.BoundingBoxAsync();
      Assert.NotNull(box);
      Assert.True(box.Width >= 44 && box.Height >= 44,
        $"Control target was smaller than 44px at the {viewport} viewport.");
    }

    bool hasHorizontalOverflow = await Page.EvaluateAsync<bool>(
      "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1");
    Assert.False(hasHorizontalOverflow);
    string expectedDockPosition = viewport.EndsWith("landscape", StringComparison.Ordinal)
      ? "static"
      : width <= 650 ? "fixed" : "sticky";
    await Expect(Page.Locator(".control-action-dock")).ToHaveCSSAsync("position", expectedDockPosition);
    if (viewport.StartsWith("iphone17-pro-max", StringComparison.Ordinal))
    {
      LocatorBoundingBoxResult? stopBox = await Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true }).BoundingBoxAsync();
      Assert.NotNull(stopBox);
      Assert.True(stopBox.Y >= 0 && stopBox.Y + stopBox.Height <= height + 1,
        "Stop must remain entirely inside the phone viewport.");
    }

    string screenshotDirectory = Path.Combine(gateway.ProjectRoot, "validation", "playwright", "accepted");
    Directory.CreateDirectory(screenshotDirectory);
    await Page.EvaluateAsync("window.scrollTo(0, 0)");
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(screenshotDirectory, $"tr006e-control-{viewport}.png"),
      FullPage = false,
    });
    string galleryDirectory = Path.Combine(gateway.ProjectRoot, "output", "playwright", "gallery");
    Directory.CreateDirectory(galleryDirectory);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(galleryDirectory, $"control-active-{viewport}.png"),
      FullPage = false,
    });

    string showcaseDirectory = ScreenshotArtifactPaths.ShowcaseDirectory(gateway.ProjectRoot);
    if (viewport is "desktop" or "iphone17-pro-max" or "iphone17-pro-max-landscape")
    {
      await Page.ScreenshotAsync(new PageScreenshotOptions
      {
        Path = Path.Combine(showcaseDirectory, $"tr-029-control-balanced-{viewport}.png"),
        FullPage = false,
      });
    }
    if (viewport == "desktop")
    {
      await controlsFocus.ClickAsync();
      await Page.ScreenshotAsync(new PageScreenshotOptions
      {
        Path = Path.Combine(showcaseDirectory, "tr-029-control-controls-focus-desktop.png"),
        FullPage = false,
      });
      await chartFocus.ClickAsync();
      await Page.ScreenshotAsync(new PageScreenshotOptions
      {
        Path = Path.Combine(showcaseDirectory, "tr-029-control-chart-focus-desktop.png"),
        FullPage = false,
      });
      await balancedFocus.ClickAsync();
    }

    if (viewport == "iphone17-pro-max")
    {
      ILocator expandGraph = Page.GetByRole(AriaRole.Button, new() { Name = "Expand live graph", Exact = true });
      await expandGraph.ClickAsync();
      ILocator focusedGraph = Page.Locator(".control-live-chart--focused");
      await Expect(focusedGraph).ToBeVisibleAsync();
      LocatorBoundingBoxResult? focusedGraphBox = await focusedGraph.BoundingBoxAsync();
      Assert.NotNull(focusedGraphBox);
      Assert.True(focusedGraphBox.Width >= 420 && focusedGraphBox.Height >= 650,
        $"Focused portrait graph must use the available screen; actual bounds: {focusedGraphBox}.");
      LocatorBoundingBoxResult? focusedStopBox = await Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true }).BoundingBoxAsync();
      Assert.NotNull(focusedStopBox);
      Assert.True(focusedStopBox.Y >= 0 && focusedStopBox.Y + focusedStopBox.Height <= height + 1,
        "Stop must remain visible while the portrait graph is focused.");
      await Page.ScreenshotAsync(new PageScreenshotOptions
      {
        Path = Path.Combine(galleryDirectory, "control-chart-focused-iphone17-pro-max.png"),
        FullPage = false,
      });
      await Page.ScreenshotAsync(new PageScreenshotOptions
      {
        Path = Path.Combine(showcaseDirectory, "tr-029-control-chart-focus-iphone17-pro-max.png"),
        FullPage = false,
      });
      await Page.GetByRole(AriaRole.Button, new() { Name = "Collapse live graph", Exact = true }).ClickAsync();
      await Expect(focusedGraph).ToHaveCountAsync(0);
    }

    if (viewport.StartsWith("iphone17-pro-max", StringComparison.Ordinal))
    {
      await fullscreenButton.ClickAsync();
      await Expect(Page.Locator("#control-dashboard:fullscreen")).ToBeVisibleAsync();
      await Expect(fullscreenButton).ToHaveAttributeAsync("aria-pressed", "true");
      Assert.Equal("control-dashboard", await Page.EvaluateAsync<string?>("document.fullscreenElement?.id"));
      LocatorBoundingBoxResult? fullscreenStopBox = await Page
        .GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true })
        .BoundingBoxAsync();
      Assert.NotNull(fullscreenStopBox);
      double fullscreenViewportHeight = await Page.EvaluateAsync<double>("window.innerHeight");
      Assert.True(
        fullscreenStopBox.Y >= 0 && fullscreenStopBox.Y + fullscreenStopBox.Height <= fullscreenViewportHeight + 1,
        $"Stop must remain entirely visible after native full screen; viewport height {fullscreenViewportHeight}, bounds {fullscreenStopBox}.");
      Assert.True(fullscreenStopBox.Width >= 44 && fullscreenStopBox.Height >= 44,
        $"Stop must retain a 44px touch target after native full screen: {fullscreenStopBox}.");
      await Page.ScreenshotAsync(new PageScreenshotOptions
      {
        Path = Path.Combine(galleryDirectory, $"control-active-fullscreen-{viewport}.png"),
        FullPage = false,
      });
      await Page.EvaluateAsync("() => document.exitFullscreen()");
      await Expect(Page.Locator("#control-dashboard:fullscreen")).ToHaveCountAsync(0);
      await Expect(fullscreenButton).ToHaveAttributeAsync("aria-pressed", "false");

      await Page.EvaluateAsync(
        "() => document.getElementById('control-dashboard').requestFullscreen = () => Promise.reject(new Error('unsupported'))");
      await fullscreenButton.ClickAsync();
      await Expect(Page.Locator("#control-dashboard.control-page--immersive")).ToBeVisibleAsync();
      await Expect(fullscreenButton).ToHaveAttributeAsync("aria-pressed", "true");
      await Expect(Page.Locator(".control-command-status")).ToContainTextAsync("Immersive view enabled");
      await Page.ScreenshotAsync(new PageScreenshotOptions
      {
        Path = Path.Combine(galleryDirectory, $"control-active-immersive-fallback-{viewport}.png"),
        FullPage = false,
      });
      await fullscreenButton.ClickAsync();
      await Expect(Page.Locator("#control-dashboard.control-page--immersive")).ToHaveCountAsync(0);
      await Expect(fullscreenButton).ToHaveAttributeAsync("aria-pressed", "false");
      Assert.True(await Page.Locator("#blazor-error-ui").IsHiddenAsync(),
        $"Full-screen entry/exit must not fault the Blazor control page: {string.Join(" | ", browserErrors)}");
    }

    await Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "What should happen to this session?", Exact = true })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "End and save", Exact = false }).ClickAsync();
    await Expect(Page).ToHaveURLAsync(new System.Text.RegularExpressions.Regex("/history/[0-9a-f-]+$"));
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = plan.WorkoutName, Exact = true }))
      .ToBeVisibleAsync();
    Assert.True(await Page.EvaluateAsync<int>("window.__wakeLockReleases") >= 1,
      "Control must release the wake lock when the run ends or navigation leaves the dashboard.");
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Delayed_wake_lock_is_released_on_disposal_and_can_be_reacquired()
  {
    await ResetSimulatorAsync();
    await Page.AddInitScriptAsync("""
      window.__wakeLockRequests = 0;
      window.__wakeLockReleases = 0;
      window.__pendingWakeLocks = [];
      Object.defineProperty(Navigator.prototype, 'wakeLock', { configurable: true, get: () => ({
        request: () => {
          window.__wakeLockRequests++;
          const listeners = {};
          let resolveRequest;
          const sentinel = {
            released: false,
            addEventListener: (name, callback) => listeners[name] = callback,
            release: async () => {
              if (sentinel.released) return;
              sentinel.released = true;
              window.__wakeLockReleases++;
              if (listeners.release) await listeners.release();
            }
          };
          const promise = new Promise(resolve => resolveRequest = () => resolve(sentinel));
          window.__pendingWakeLocks.push({ sentinel, resolve: resolveRequest });
          return promise;
        }
      })});
      """);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/operations").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });

    await Page.EvaluateAsync("() => { window.__firstWakeResult = window.treadmillRunnerView.setRunWakeLock(true); }");
    await Page.WaitForFunctionAsync("window.__wakeLockRequests === 1");
    await Page.EvaluateAsync("window.treadmillRunnerView.disposeRunWakeLock()");
    await Page.EvaluateAsync("window.__pendingWakeLocks[0].resolve()");
    await Page.EvaluateAsync("window.__firstWakeResult");

    Assert.Equal(1, await Page.EvaluateAsync<int>("window.__wakeLockReleases"));
    Assert.False(await Page.EvaluateAsync<bool>("window.treadmillRunnerView.wakeLockWanted"));
    Assert.True(await Page.EvaluateAsync<bool>("window.treadmillRunnerView.wakeLock === null"));
    Assert.True(await Page.EvaluateAsync<bool>("window.treadmillRunnerView.wakeLockRequest === null"));

    await Page.EvaluateAsync("() => { window.__secondWakeResult = window.treadmillRunnerView.setRunWakeLock(true); }");
    await Page.WaitForFunctionAsync("window.__wakeLockRequests === 2");
    await Page.EvaluateAsync("window.__pendingWakeLocks[1].resolve()");
    string secondState = await Page.EvaluateAsync<string>("window.__secondWakeResult.then(result => result.state)");

    Assert.Equal("Active", secondState);
    Assert.True(await Page.EvaluateAsync<bool>("window.treadmillRunnerView.wakeLockWanted"));
    Assert.True(await Page.EvaluateAsync<bool>("window.treadmillRunnerView.wakeLock === window.__pendingWakeLocks[1].sentinel"));

    await Page.EvaluateAsync("window.treadmillRunnerView.disposeRunWakeLock()");
    Assert.Equal(2, await Page.EvaluateAsync<int>("window.__wakeLockReleases"));
    Assert.False(await Page.EvaluateAsync<bool>("window.treadmillRunnerView.wakeLockWanted"));
    Assert.True(await Page.EvaluateAsync<bool>("window.treadmillRunnerView.wakeLock === null"));
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Control_route_without_a_session_returns_to_the_run_picker()
  {
    await Page.SetViewportSizeAsync(440, 956);
    await ResetSimulatorAsync();
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/control").AbsoluteUri, new PageGotoOptions
    {
      WaitUntil = WaitUntilState.NetworkIdle,
    });

    await Expect(Page).ToHaveURLAsync(gateway.BaseAddress.AbsoluteUri);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready to run", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Link, new() { Name = "Control", Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Pause", Exact = true })).ToHaveCountAsync(0);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Pause_is_resumable_and_stop_offers_keep_discard_end_or_restart()
  {
    var profileDisconnectRequests = 0;
    Page.Request += (_, request) =>
    {
      if (request.Method == "POST" && request.Url.Contains("/api/devices/profiles/", StringComparison.Ordinal) &&
          request.Url.EndsWith("/disconnect", StringComparison.Ordinal))
        Interlocked.Increment(ref profileDisconnectRequests);
    };
    await Page.SetViewportSizeAsync(440, 956);
    await ResetSimulatorAsync();
    SeededPlan plan = await SeedPlanAsync("pause-end-reset");
    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.SelectActiveRunnerAsync(plan.ProfileName);
    await Page.OpenRunChoicesAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();
    await Expect(Page).ToHaveURLAsync(new System.Text.RegularExpressions.Regex("/control$"));
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
      .ToBeVisibleAsync();
    await SetPhysicalMotionAsync(6.0, 1.0);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();

    await Page.GetByRole(AriaRole.Button, new() { Name = "Pause", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Run paused", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "What should happen to this session?", Exact = true })).ToHaveCountAsync(0);
    ILocator resume = Page.GetByRole(AriaRole.Button, new() { Name = "Resume", Exact = true });
    await Expect(resume).ToBeVisibleAsync();
    await resume.ClickAsync();
    await SetPhysicalMotionAsync(6.0, 1.0);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true }).ClickAsync();
    ILocator dialog = Page.GetByRole(AriaRole.Dialog);
    await Expect(dialog.GetByRole(AriaRole.Heading, new() { Name = "What should happen to this session?", Exact = true })).ToBeVisibleAsync();
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Keep paused", Exact = false })).ToBeVisibleAsync();
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Start from beginning", Exact = false })).ToBeVisibleAsync();
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Discard session", Exact = false })).ToBeVisibleAsync();
    ILocator endAndSave = dialog.GetByRole(AriaRole.Button, new() { Name = "End and save", Exact = false });
    await Expect(endAndSave).ToBeVisibleAsync();
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "End, save and disconnect devices", Exact = false })).ToBeVisibleAsync();
    Assert.Equal("center", await endAndSave.EvaluateAsync<string>("element => getComputedStyle(element).textAlign"));
    string screenshotDirectory = Path.Combine(gateway.ProjectRoot, "validation", "playwright", "accepted");
    Directory.CreateDirectory(screenshotDirectory);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(screenshotDirectory, "tr-033-stop-decision-phone.png"),
      FullPage = false,
    });

    await dialog.GetByRole(AriaRole.Button, new() { Name = "Start from beginning", Exact = false }).ClickAsync();
    await Expect(dialog).ToBeHiddenAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Run paused", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByLabel("Workout progress time", new() { Exact = true })).ToContainTextAsync("0:00");
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Resume", Exact = true })).ToBeVisibleAsync();

    await Page.GetByRole(AriaRole.Button, new() { Name = "Resume", Exact = true }).ClickAsync();
    await SetPhysicalMotionAsync(6.0, 1.0);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true }).ClickAsync();
    await dialog.GetByRole(AriaRole.Button, new() { Name = "End, save and disconnect devices", Exact = false }).ClickAsync();
    await Expect(Page).ToHaveURLAsync(new System.Text.RegularExpressions.Regex("/history/[0-9a-f-]+$"));
    Assert.Equal(1, Volatile.Read(ref profileDisconnectRequests));
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Start_uses_one_normal_click_without_a_hold_gesture()
  {
    await Page.SetViewportSizeAsync(440, 956);
    await ResetSimulatorAsync();
    SeededPlan plan = await SeedPlanAsync("start-stop-race");
    int startRequests = 0;
    Page.Request += (_, request) =>
    {
      if (request.Method != "POST") return;
      if (request.Url.EndsWith("/api/live/sessions/start", StringComparison.Ordinal))
        Interlocked.Increment(ref startRequests);
    };

    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.SelectActiveRunnerAsync(plan.ProfileName);
    await Page.OpenRunChoicesAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();

    ILocator start = Page.GetByRole(AriaRole.Button, new()
    {
      Name = "Start",
      Exact = true,
    });
    await Expect(start).ToBeEnabledAsync();
    Assert.Equal("none", await start.EvaluateAsync<string>("button => getComputedStyle(button).userSelect"));
    await start.ClickAsync();
    Assert.Equal(1, Volatile.Read(ref startRequests));
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Fullscreen_chart_uses_the_short_iPhone_landscape_viewport()
  {
    await Page.SetViewportSizeAsync(844, 390);
    await ResetSimulatorAsync();
    SeededPlan plan = await SeedPlanAsync("fullscreen-iphone-landscape");
    await PrepareActiveControlAsync(plan);

    await Page.GetByRole(AriaRole.Button, new() { Name = "Chart", Exact = true }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Toggle full-screen dashboard", Exact = true }).ClickAsync();
    await Expect(Page.Locator("#control-dashboard")).ToHaveClassAsync(
      new System.Text.RegularExpressions.Regex("control-page--chart"));
    Assert.True(await Page.Locator("#control-dashboard").EvaluateAsync<bool>(
      "element => element.matches(':fullscreen') || element.classList.contains('control-page--immersive')"));

    ILocator graph = Page.Locator(".control-live-chart--focused");
    LocatorBoundingBoxResult? graphBox = await graph.BoundingBoxAsync();
    Assert.NotNull(graphBox);
    string screenshotDirectory = Path.Combine(gateway.ProjectRoot, "validation", "playwright", "accepted");
    Directory.CreateDirectory(screenshotDirectory);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(screenshotDirectory, "fullscreen-chart-844x390.png"),
      FullPage = false,
    });
    Assert.True(graphBox.Width >= 720 && graphBox.Height >= 350,
      $"Fullscreen iPhone-landscape graph still wastes the viewport: {graphBox.Width:F1}x{graphBox.Height:F1}.");
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Toggle full-screen dashboard", Exact = true })).ToBeVisibleAsync();
    ILocator pause = Page.GetByRole(AriaRole.Button, new() { Name = "Pause", Exact = true });
    ILocator stop = Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true });
    await Expect(pause).ToBeVisibleAsync();
    await Expect(stop).ToBeVisibleAsync();
    LocatorBoundingBoxResult? dockBox = await Page.Locator(".control-action-dock").BoundingBoxAsync();
    Assert.NotNull(dockBox);
    string layout = await graph.EvaluateAsync<string>(
      "element => { const chart = getComputedStyle(element); const center = getComputedStyle(element.parentElement); const dock = getComputedStyle(element.parentElement.querySelector('.control-action-dock')); return `chart[position=${chart.position},column=${chart.gridColumn},width=${chart.width}] center[display=${center.display},columns=${center.gridTemplateColumns}] dock[position=${dock.position},column=${dock.gridColumn},width=${dock.width}]`; }");
    Assert.True(graphBox.X + graphBox.Width <= dockBox.X + 1,
      $"Pause/Stop overlapped the graph: graph right={graphBox.X + graphBox.Width:F1}, dock left={dockBox.X:F1}; {layout}.");

  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Stop_waits_for_an_inflight_start_then_sends_once_with_current_session_state()
  {
    await Page.SetViewportSizeAsync(440, 956);
    await ResetSimulatorAsync();
    SeededPlan plan = await SeedPlanAsync("inflight-stop");
    int startRequests = 0;
    int stopRequests = 0;
    var startArrived = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var releaseStart = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    await Page.RouteAsync("**/api/live/sessions/start", async route =>
    {
      Interlocked.Increment(ref startRequests);
      startArrived.TrySetResult();
      await releaseStart.Task.WaitAsync(TimeSpan.FromSeconds(10));
      await route.ContinueAsync();
    });
    Page.Request += (_, request) =>
    {
      if (request.Method == "POST" && request.Url.EndsWith("/api/live/sessions/stop", StringComparison.Ordinal))
        Interlocked.Increment(ref stopRequests);
    };

    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.SelectActiveRunnerAsync(plan.ProfileName);
    await Page.OpenRunChoicesAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();
    ILocator start = Page.GetByRole(AriaRole.Button, new()
    {
      Name = "Start",
      Exact = true,
    });

    await start.ClickAsync();
    await startArrived.Task.WaitAsync(TimeSpan.FromSeconds(6));
    await Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true }).ClickAsync();
    await Expect(Page.GetByText("Stop requested — finishing the in-flight command, then sending one current-state Stop.", new() { Exact = true })).ToBeVisibleAsync();
    Assert.Equal(0, Volatile.Read(ref stopRequests));
    releaseStart.TrySetResult();

    await Expect(Page).ToHaveURLAsync(new System.Text.RegularExpressions.Regex("/control$"), new PageAssertionsToHaveURLOptions { Timeout = 10_000 });
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "What should happen to this session?", Exact = true })).ToBeVisibleAsync();
    Assert.Equal(1, Volatile.Read(ref startRequests));
    Assert.Equal(1, Volatile.Read(ref stopRequests));
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Stop_waits_for_an_inflight_speed_change_then_sends_once_with_current_session_state()
  {
    await Page.SetViewportSizeAsync(440, 956);
    await ResetSimulatorAsync();
    SeededPlan plan = await SeedPlanAsync("speed-stop-race");
    int speedRequests = 0;
    int stopRequests = 0;
    var speedArrived = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var releaseSpeed = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    await Page.RouteAsync("**/api/live/sessions/speed-override", async route =>
    {
      Interlocked.Increment(ref speedRequests);
      speedArrived.TrySetResult();
      await releaseSpeed.Task.WaitAsync(TimeSpan.FromSeconds(10));
      await route.ContinueAsync();
    });
    Page.Request += (_, request) =>
    {
      if (request.Method == "POST" && request.Url.EndsWith("/api/live/sessions/stop", StringComparison.Ordinal))
        Interlocked.Increment(ref stopRequests);
    };

    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.SelectActiveRunnerAsync(plan.ProfileName);
    await Page.OpenRunChoicesAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();
    await Expect(Page).ToHaveURLAsync(new System.Text.RegularExpressions.Regex("/control$"));
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
      .ToBeVisibleAsync();
    await SetPhysicalMotionAsync(1.2, 0.5);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();

    await Page.GetByRole(AriaRole.Button, new() { Name = "Set speed to 5.0 km/h", Exact = true }).ClickAsync();
    await speedArrived.Task.WaitAsync(TimeSpan.FromSeconds(6));
    await Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true }).ClickAsync();
    await Expect(Page.GetByText("Stop requested — finishing the in-flight command, then sending one current-state Stop.", new() { Exact = true })).ToBeVisibleAsync();
    Assert.Equal(0, Volatile.Read(ref stopRequests));
    releaseSpeed.TrySetResult();

    await Expect(Page).ToHaveURLAsync(new System.Text.RegularExpressions.Regex("/control$"), new PageAssertionsToHaveURLOptions { Timeout = 10_000 });
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "What should happen to this session?", Exact = true })).ToBeVisibleAsync();
    Assert.Equal(1, Volatile.Read(ref speedRequests));
    Assert.Equal(1, Volatile.Read(ref stopRequests));
  }

  private async Task AssertNoScrollMobileControlsAsync(string viewport, int width, int height)
  {
    ILocator summary = Page.GetByLabel("Control mode summary", new() { Exact = true });
    await Expect(summary).ToBeVisibleAsync();
    await Expect(summary).ToContainTextAsync("Measured speed");
    await Expect(summary).ToContainTextAsync("Heart rate");
    await Expect(summary).ToContainTextAsync("Workout");

    ILocator mobileControls = Page.Locator(
      ".control-console-grid--controls .control-rail button, .control-console-grid--controls .control-action-dock button");
    await Expect(mobileControls).ToHaveCountAsync(22);
    for (int index = 0; index < await mobileControls.CountAsync(); index++)
    {
      ILocator control = mobileControls.Nth(index);
      await Expect(control).ToBeVisibleAsync();
      LocatorBoundingBoxResult? box = await control.BoundingBoxAsync();
      Assert.NotNull(box);
      string targetName = await control.GetAttributeAsync("aria-label") ?? $"target {index + 1}";
      Assert.True(box.Width >= 44 && box.Height >= 44,
        $"Mobile Controls {targetName} was smaller than 44px at {viewport}: " +
        $"x={box.X:F1}, y={box.Y:F1}, width={box.Width:F1}, height={box.Height:F1}.");
      Assert.True(box.X >= -1 && box.X + box.Width <= width + 1 && box.Y >= -1 && box.Y + box.Height <= height + 1,
        $"Mobile Controls {targetName} was outside the {viewport} viewport: " +
        $"x={box.X:F1}, y={box.Y:F1}, width={box.Width:F1}, height={box.Height:F1}, viewport={width}x{height}.");
    }

    foreach (ILocator rail in await Page.Locator(".control-console-grid--controls .control-rail").AllAsync())
    {
      bool needsInternalScroll = await rail.EvaluateAsync<bool>("""
        element => {
          const style = getComputedStyle(element);
          return element.scrollHeight > element.clientHeight + 1 || style.overflowY === 'auto' || style.overflowY === 'scroll';
        }
        """);
      Assert.False(needsInternalScroll, $"Controls mode retained an internally scrolling rail at {viewport}.");
    }

    Assert.Equal(0, await Page.EvaluateAsync<double>("window.scrollY"));
    Assert.False(await Page.EvaluateAsync<bool>(
      "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"));
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Live_chart_inspector_supports_hover_touch_keyboard_and_missing_values()
  {
    await Page.SetViewportSizeAsync(390, 844);
    await ResetSimulatorAsync();
    SeededPlan plan = await SeedPlanAsync("tr040-live-chart-inspector");

    try
    {
      await PrepareActiveControlAsync(plan);
      await Page.GetByRole(AriaRole.Button, new() { Name = "Chart", Exact = true }).ClickAsync();

      ILocator inspector = Page.Locator(".control-live-chart--focused .chart-inspector--enabled");
      ILocator surface = inspector.Locator(".chart-inspector__surface");
      ILocator tooltip = inspector.Locator("[data-chart-tooltip]");
      ILocator crosshair = inspector.Locator("[data-chart-crosshair]");
      await Expect(surface).ToBeVisibleAsync();
      LocatorBoundingBoxResult? surfaceBox = await surface.BoundingBoxAsync();
      Assert.NotNull(surfaceBox);

      await Page.Mouse.MoveAsync(surfaceBox.X + 12, surfaceBox.Y + (surfaceBox.Height / 2));
      await Expect(tooltip).ToBeVisibleAsync();
      await Expect(crosshair).ToBeVisibleAsync();
      await Expect(tooltip.Locator("[data-chart-value]")).ToHaveCountAsync(6);
      await Expect(tooltip).ToContainTextAsync("Speed");
      await Expect(tooltip).ToContainTextAsync("Incline");
      await Expect(tooltip).ToContainTextAsync("km/h");
      await Expect(tooltip).ToContainTextAsync("%");
      await Expect(inspector.Locator(".chart-cursor")).ToHaveCountAsync(1);

      LocatorBoundingBoxResult? tooltipBox = await tooltip.BoundingBoxAsync();
      Assert.NotNull(tooltipBox);
      Assert.True(tooltipBox.X >= surfaceBox.X - 1 && tooltipBox.X + tooltipBox.Width <= surfaceBox.X + surfaceBox.Width + 1,
        $"Live chart tooltip escaped the plot horizontally: tooltip={tooltipBox}, surface={surfaceBox}.");

      await surface.FocusAsync();
      await surface.PressAsync("Home");
      await Expect(tooltip.Locator("[data-chart-time]")).ToHaveTextAsync("0:00");
      string firstAnnouncement = await inspector.Locator("[data-chart-announcement]").TextContentAsync() ?? string.Empty;
      Assert.Contains("Speed", firstAnnouncement, StringComparison.Ordinal);
      await surface.PressAsync("ArrowRight");
      await Expect(tooltip).ToBeVisibleAsync();
      await Page.Locator(".primary-nav--mobile .nav-more summary").FocusAsync();
      await Expect(tooltip).ToBeHiddenAsync();
      await surface.FocusAsync();
      await surface.PressAsync("End");
      await Expect(tooltip).ToBeVisibleAsync();
      await surface.PressAsync("Escape");
      await Expect(tooltip).ToBeHiddenAsync();

      await surface.DispatchEventAsync("pointerdown", new
      {
        pointerType = "touch",
        pointerId = 41,
        clientX = surfaceBox.X + (surfaceBox.Width * .88),
        clientY = surfaceBox.Y + (surfaceBox.Height * .55),
        bubbles = true,
      });
      await Expect(tooltip).ToBeVisibleAsync();
      await Page.Locator(".control-page__header h1").ClickAsync();
      await Expect(tooltip).ToBeHiddenAsync();

      await surface.FocusAsync();
      await surface.PressAsync("End");
      await Expect(tooltip).ToContainTextAsync("—");
      await surface.PressAsync("Escape");

      foreach (string name in new[] { "Pause", "Stop" })
      {
        await Expect(Page.GetByRole(AriaRole.Button, new() { Name = name, Exact = true })).ToBeVisibleAsync();
      }
      Assert.False(await Page.EvaluateAsync<bool>(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"));
    }
    finally
    {
      await ResetSimulatorAsync();
    }
  }

  private async Task AssertFocusedMobileChartAsync(string viewport, int width, int height)
  {
    ILocator collapse = Page.GetByRole(AriaRole.Button, new() { Name = "Collapse live graph", Exact = true });
    await Expect(collapse).ToBeVisibleAsync();
    LocatorBoundingBoxResult? collapseBox = await collapse.BoundingBoxAsync();
    Assert.NotNull(collapseBox);
    Assert.True(collapseBox.Width >= 44 && collapseBox.Height >= 44,
      $"Focused Chart Back target was smaller than 44px at {viewport}: {collapseBox}.");

    ILocator focusedGraph = Page.Locator(".control-live-chart--focused");
    await Expect(focusedGraph).ToBeVisibleAsync();
    LocatorBoundingBoxResult? graphBox = await focusedGraph.BoundingBoxAsync();
    Assert.NotNull(graphBox);
    if (height <= 500)
    {
      LocatorBoundingBoxResult? consoleBox = await Page.Locator(".control-console-grid--chart").BoundingBoxAsync();
      LocatorBoundingBoxResult? dockBox = await Page.Locator(".control-console-grid--chart .control-action-dock").BoundingBoxAsync();
      Assert.NotNull(consoleBox);
      Assert.NotNull(dockBox);
      double availableGraphWidth = consoleBox.Width - dockBox.Width - 16;
      Assert.True(graphBox.Width >= availableGraphWidth * .98 && graphBox.Height >= height - 100,
        $"Focused landscape graph did not fill the space beside Pause/Stop at {viewport}: " +
        $"graph={graphBox.Width:F1}x{graphBox.Height:F1}, available={availableGraphWidth:F1}x{height - 100}.");
    }
    else
    {
      Assert.True(graphBox.Width >= width - 24 && graphBox.Height >= height * .62,
        $"Focused portrait graph did not fill the available screen at {viewport}: {graphBox}.");
    }

    await Expect(focusedGraph.GetByLabel("Speed axis in kilometers per hour", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(focusedGraph.GetByLabel("Incline axis in percent", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(focusedGraph.GetByLabel("Elapsed time axis", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(focusedGraph.GetByLabel("Chart legend", new() { Exact = true })).ToBeVisibleAsync();

    foreach (string name in new[] { "Pause", "Stop" })
    {
      ILocator motionControl = Page.GetByRole(AriaRole.Button, new() { Name = name, Exact = true });
      LocatorBoundingBoxResult? box = await motionControl.BoundingBoxAsync();
      Assert.NotNull(box);
      Assert.True(box.Width >= 44 && box.Height >= 44 && box.X >= -1 && box.X + box.Width <= width + 1 && box.Y >= -1 && box.Y + box.Height <= height + 1,
        $"{name} was not safely visible with the focused graph at {viewport}: " +
        $"x={box.X:F1}, y={box.Y:F1}, width={box.Width:F1}, height={box.Height:F1}, viewport={width}x{height}.");
    }
  }

  private async Task SaveTr039EvidenceAsync(string projectRoot, string name)
  {
    string evidenceDirectory = Path.Combine(projectRoot, "output", "playwright", "tr-039");
    Directory.CreateDirectory(evidenceDirectory);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(evidenceDirectory, $"{name}.png"),
      FullPage = false
    });
  }

  private async Task PrepareActiveControlAsync(SeededPlan plan)
  {
    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.SelectActiveRunnerAsync(plan.ProfileName);
    await Page.OpenRunChoicesAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();
    await Expect(Page).ToHaveURLAsync(new System.Text.RegularExpressions.Regex("/control$"));
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
      .ToBeVisibleAsync();
    await SetPhysicalMotionAsync(1.2, 0.5);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByLabel("Live speed in kilometers per hour and incline percentage over elapsed time", new() { Exact = true }))
      .ToBeVisibleAsync();
  }

  private async Task ResetSimulatorAsync()
  {
    using HttpClient client = CreateClient();
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/live/simulator/reset", new { });
    Assert.Equal(HttpStatusCode.NoContent, response.StatusCode);
  }

  private async Task SetPhysicalMotionAsync(double speedKph, double inclinePercent)
  {
    using HttpClient client = CreateClient();
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/live/simulator/physical-motion", new
    {
      isMoving = true,
      measuredSpeedKph = speedKph,
      measuredInclinePercent = inclinePercent,
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

  private async Task<SeededPlan> SeedPlanAsync(string scenario)
  {
    string stableSuffix = Convert.ToHexString(
      SHA256.HashData(Encoding.UTF8.GetBytes(scenario)))[..8].ToLowerInvariant();
    string suffix = $"{scenario}-{stableSuffix}";
    string profileName = $"Alex {stableSuffix[..5]}";
    string workoutName = $"Control run {suffix}";
    using HttpClient client = CreateClient();
    using HttpResponseMessage profileResponse = await client.PostAsJsonAsync("/api/planning/profiles", new
    {
      operationId = Guid.NewGuid(),
      displayName = profileName,
      unitSystem = "Metric",
      weightKilograms = 72.5,
      maximumHeartRateBpm = 190,
      maximumSpeedKph = 15.0,
      heartRateZones = new[] { new { number = 2, name = "Aerobic", minimumBpm = 125, maximumBpm = 145 } },
      expectedVersion = (int?)null,
    });
    Assert.Equal(HttpStatusCode.Created, profileResponse.StatusCode);
    JsonElement createdProfile = await profileResponse.Content.ReadFromJsonAsync<JsonElement>();

    using HttpResponseMessage workoutResponse = await client.PostAsJsonAsync("/api/planning/workouts", new
    {
      operationId = Guid.NewGuid(),
      name = workoutName,
      description = "Manual control dashboard fixture",
      blocks = new[]
      {
        new
        {
          kind = "step",
          repetitions = 1,
          blocks = Array.Empty<object>(),
          goalKind = "time",
          goalValue = 45.0,
          speedKind = "fixed",
          speedStartKph = 4.5,
          speedEndKph = 0.0,
          heartRateMinimumBpm = 0,
          heartRateMaximumBpm = 0,
          heartRateZoneNumber = 0,
          heartRateInitialSpeedKph = 0.0,
          heartRateMinimumSpeedKph = 0.0,
          heartRateMaximumSpeedKph = 0.0,
          inclineKind = "fixed",
          inclineStartPercent = 0.5,
          inclineEndPercent = 0.0,
          cue = "Warm up",
          notes = (string?)null,
        },
        new
        {
          kind = "step", repetitions = 1, blocks = Array.Empty<object>(), goalKind = "time", goalValue = 30.0,
          speedKind = "fixed", speedStartKph = 7.5, speedEndKph = 0.0,
          heartRateMinimumBpm = 0, heartRateMaximumBpm = 0, heartRateZoneNumber = 0,
          heartRateInitialSpeedKph = 0.0, heartRateMinimumSpeedKph = 0.0, heartRateMaximumSpeedKph = 0.0,
          inclineKind = "fixed", inclineStartPercent = 1.5, inclineEndPercent = 0.0,
          cue = "Interval 1", notes = (string?)null,
        },
        new
        {
          kind = "step", repetitions = 1, blocks = Array.Empty<object>(), goalKind = "time", goalValue = 30.0,
          speedKind = "fixed", speedStartKph = 5.0, speedEndKph = 0.0,
          heartRateMinimumBpm = 0, heartRateMaximumBpm = 0, heartRateZoneNumber = 0,
          heartRateInitialSpeedKph = 0.0, heartRateMinimumSpeedKph = 0.0, heartRateMaximumSpeedKph = 0.0,
          inclineKind = "fixed", inclineStartPercent = 0.5, inclineEndPercent = 0.0,
          cue = "Recovery", notes = (string?)null,
        },
        new
        {
          kind = "step", repetitions = 1, blocks = Array.Empty<object>(), goalKind = "time", goalValue = 30.0,
          speedKind = "fixed", speedStartKph = 8.5, speedEndKph = 0.0,
          heartRateMinimumBpm = 0, heartRateMaximumBpm = 0, heartRateZoneNumber = 0,
          heartRateInitialSpeedKph = 0.0, heartRateMinimumSpeedKph = 0.0, heartRateMaximumSpeedKph = 0.0,
          inclineKind = "fixed", inclineStartPercent = 2.5, inclineEndPercent = 0.0,
          cue = "Interval 2", notes = (string?)null,
        },
        new
        {
          kind = "step", repetitions = 1, blocks = Array.Empty<object>(), goalKind = "time", goalValue = 45.0,
          speedKind = "fixed", speedStartKph = 4.5, speedEndKph = 0.0,
          heartRateMinimumBpm = 0, heartRateMaximumBpm = 0, heartRateZoneNumber = 0,
          heartRateInitialSpeedKph = 0.0, heartRateMinimumSpeedKph = 0.0, heartRateMaximumSpeedKph = 0.0,
          inclineKind = "fixed", inclineStartPercent = 0.5, inclineEndPercent = 0.0,
          cue = "Cool down",
          notes = (string?)null,
        },
      },
    });
    Assert.Equal(HttpStatusCode.Created, workoutResponse.StatusCode);
    return new SeededPlan(createdProfile.GetProperty("id").GetGuid(), profileName, workoutName);
  }

  private static bool HasVerticalSvgSegment(string path)
  {
    System.Text.RegularExpressions.MatchCollection matches = System.Text.RegularExpressions.Regex.Matches(
      path,
      @"(?<command>[ML])\s*(?<x>-?(?:\d+(?:\.\d*)?|\.\d+))\s+(?<y>-?(?:\d+(?:\.\d*)?|\.\d+))",
      System.Text.RegularExpressions.RegexOptions.CultureInvariant);
    var points = matches
      .Select(match => (
        X: double.Parse(match.Groups["x"].Value, System.Globalization.CultureInfo.InvariantCulture),
        Y: double.Parse(match.Groups["y"].Value, System.Globalization.CultureInfo.InvariantCulture)))
      .ToArray();
    return points.Zip(points.Skip(1)).Any(pair =>
      Math.Abs(pair.First.X - pair.Second.X) < 0.01 &&
      Math.Abs(pair.First.Y - pair.Second.Y) >= 0.5);
  }

  private async Task SavePreferencesAsync(Guid profileId, string displayStyle)
  {
    using HttpClient client = CreateClient();
    using HttpResponseMessage response = await client.PutAsJsonAsync($"/api/local-first/profiles/{profileId}/preferences", new
    {
      displayStyle,
      primaryMetrics = new[] { "Speed", "HeartRate", "ElapsedTime" },
      cues = new
      {
        stepChanges = true,
        heartRateDeparture = true,
        halfway = true,
        connectionProblems = true,
        completion = true,
        volumePercent = 60,
      },
      expectedVersion = 0,
    });
    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
  }

  private HttpClient CreateClient() => new() { BaseAddress = gateway.BaseAddress };

  private sealed record SeededPlan(Guid ProfileId, string ProfileName, string WorkoutName);
}
