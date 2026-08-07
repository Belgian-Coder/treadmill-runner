using System.Collections.Concurrent;
using System.Net;
using System.Net.Http.Json;
using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;

namespace TreadmillRunner.E2ETests;

public sealed class ManualControlDashboardTests(GatewayFixture gateway) : PageTest, IClassFixture<GatewayFixture>
{
  public static TheoryData<string, int, int> Viewports => new()
  {
    { "iphone17-pro-max", 440, 956 },
    { "iphone17-pro-max-landscape", 956, 440 },
    { "tablet", 1180, 820 },
    { "desktop", 1920, 1080 },
  };

  [Theory]
  [MemberData(nameof(Viewports))]
  [Trait("Category", "Browser")]
  public async Task Control_page_is_realtime_touchable_with_vertical_preset_rails(
    string viewport,
    int width,
    int height)
  {
    var browserErrors = new ConcurrentQueue<string>();
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
    await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Enable controls", Exact = true }).ClickAsync();
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
    await Expect(Page.GetByLabel("Live workout metrics", new() { Exact = true })).ToContainTextAsync("4.5");
    await Expect(Page.GetByLabel("Live workout metrics", new() { Exact = true })).ToContainTextAsync("0.5");
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
    ILocator speedAxis = Page.GetByLabel("Speed axis in kilometers per hour", new() { Exact = true });
    await Expect(speedAxis.Locator("span")).ToHaveCountAsync(10);
    await Expect(speedAxis.Locator("span").First).ToHaveTextAsync("10");
    ILocator inclineAxis = Page.GetByLabel("Incline axis in percent", new() { Exact = true });
    await Expect(inclineAxis.Locator("span")).ToHaveCountAsync(10);
    await Expect(inclineAxis.Locator("span").First).ToHaveTextAsync("10");
    await Expect(inclineAxis.Locator("span").Last).ToHaveTextAsync("1");
    string plannedSpeedPath = await Page.Locator("[data-series='planned-speed']").GetAttributeAsync("d") ?? string.Empty;
    Assert.True(plannedSpeedPath.Count(static character => character == 'L') >= 5,
      "The selected interval workout must be visible as a multi-segment plan overlay.");
    await Expect(Page.GetByLabel("Technical session details", new() { Exact = true })).Not.ToHaveAttributeAsync("open", "");
    await Expect(Page.Locator(".site-header .global-hr-status")).ToHaveCountAsync(0);
    await Expect(Page.Locator(".control-header-actions .global-hr-status")).ToBeVisibleAsync();
    if (width == 440)
    {
      await Expect(Page.Locator(".control-header-actions .global-hr-status__copy")).ToBeVisibleAsync();
      await Expect(Page.Locator(".control-header-actions .global-hr-status__copy")).ToContainTextAsync("Sensor · Simulated");
    }
    ILocator fullscreenButton = Page.GetByRole(AriaRole.Button, new() { Name = "Toggle full-screen dashboard", Exact = true });
    await Expect(fullscreenButton).ToBeVisibleAsync();
    await Expect(fullscreenButton).ToHaveTextAsync("⛶");
    await Expect(Page.GetByLabel("Control safety notice", new() { Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByText("Physical movement detected", new() { Exact = true })).ToHaveCountAsync(0);

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
    string galleryDirectory = Path.Combine(gateway.ProjectRoot, "screenshots");
    Directory.CreateDirectory(galleryDirectory);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(galleryDirectory, $"control-active-{viewport}.png"),
      FullPage = false,
    });

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
      await Page.GetByRole(AriaRole.Button, new() { Name = "Collapse live graph", Exact = true }).ClickAsync();
      await Expect(focusedGraph).ToHaveCountAsync(0);
    }

    if (viewport.StartsWith("iphone17-pro-max", StringComparison.Ordinal))
    {
      await fullscreenButton.ClickAsync();
      await Expect(Page.Locator("#control-dashboard:fullscreen")).ToBeVisibleAsync();
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

      await Page.EvaluateAsync(
        "() => document.getElementById('control-dashboard').requestFullscreen = () => Promise.reject(new Error('unsupported'))");
      await fullscreenButton.ClickAsync();
      await Expect(Page.Locator("#control-dashboard.control-page--immersive")).ToBeVisibleAsync();
      await Expect(Page.Locator(".control-command-status")).ToContainTextAsync("Immersive view enabled");
      await Page.ScreenshotAsync(new PageScreenshotOptions
      {
        Path = Path.Combine(galleryDirectory, $"control-active-immersive-fallback-{viewport}.png"),
        FullPage = false,
      });
      await fullscreenButton.ClickAsync();
      await Expect(Page.Locator("#control-dashboard.control-page--immersive")).ToHaveCountAsync(0);
      Assert.True(await Page.Locator("#blazor-error-ui").IsHiddenAsync(),
        $"Full-screen entry/exit must not fault the Blazor control page: {string.Join(" | ", browserErrors)}");
    }

    await Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true }).ClickAsync();
    await Expect(Page).ToHaveURLAsync(gateway.BaseAddress.AbsoluteUri);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "How did that run feel?", Exact = true }))
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
  public async Task Stop_cancels_a_pending_start_hold_before_any_start_request_is_sent()
  {
    await Page.SetViewportSizeAsync(440, 956);
    await ResetSimulatorAsync();
    SeededPlan plan = await SeedPlanAsync("start-stop-race");
    int startRequests = 0;
    int stopRequests = 0;
    Page.Request += (_, request) =>
    {
      if (request.Method != "POST") return;
      if (request.Url.EndsWith("/api/live/sessions/start", StringComparison.Ordinal))
        Interlocked.Increment(ref startRequests);
      if (request.Url.EndsWith("/api/live/sessions/stop", StringComparison.Ordinal))
        Interlocked.Increment(ref stopRequests);
    };

    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.SelectActiveRunnerAsync(plan.ProfileName);
    await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Enable controls", Exact = true }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();

    ILocator start = Page.GetByRole(AriaRole.Button, new()
    {
      NameRegex = new System.Text.RegularExpressions.Regex("^Hold to start"),
    });
    await Expect(start).ToBeEnabledAsync();
    await start.EvaluateAsync("button => button.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'touch' }))");
    await Expect(Page.GetByText("Keep holding — Start in 3.", new() { Exact = true })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true }).ClickAsync();
    await Expect(Page).ToHaveURLAsync(gateway.BaseAddress.AbsoluteUri);
    await Page.WaitForTimeoutAsync(3_200);

    Assert.Equal(0, Volatile.Read(ref startRequests));
    Assert.Equal(1, Volatile.Read(ref stopRequests));
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
    await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Enable controls", Exact = true }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();
    ILocator start = Page.GetByRole(AriaRole.Button, new()
    {
      NameRegex = new System.Text.RegularExpressions.Regex("^Hold to start"),
    });

    await start.EvaluateAsync("button => button.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'touch' }))");
    await startArrived.Task.WaitAsync(TimeSpan.FromSeconds(6));
    await Page.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true }).ClickAsync();
    await Expect(Page.GetByText("Stop requested — finishing the in-flight command, then sending one current-state Stop.", new() { Exact = true })).ToBeVisibleAsync();
    Assert.Equal(0, Volatile.Read(ref stopRequests));
    releaseStart.TrySetResult();

    await Expect(Page).ToHaveURLAsync(gateway.BaseAddress.AbsoluteUri, new PageAssertionsToHaveURLOptions { Timeout = 10_000 });
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
    await Page.GetByRole(AriaRole.Button, new() { Name = plan.WorkoutName, Exact = false }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Enable controls", Exact = true }).ClickAsync();
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

    await Expect(Page).ToHaveURLAsync(gateway.BaseAddress.AbsoluteUri, new PageAssertionsToHaveURLOptions { Timeout = 10_000 });
    Assert.Equal(1, Volatile.Read(ref speedRequests));
    Assert.Equal(1, Volatile.Read(ref stopRequests));
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

  private async Task<SeededPlan> SeedPlanAsync(string scenario)
  {
    string suffix = $"{scenario}-{Guid.NewGuid():N}"[..(scenario.Length + 9)];
    string profileName = $"Control runner {suffix}";
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
    return new SeededPlan(profileName, workoutName);
  }

  private HttpClient CreateClient() => new() { BaseAddress = gateway.BaseAddress };

  private sealed record SeededPlan(string ProfileName, string WorkoutName);
}
