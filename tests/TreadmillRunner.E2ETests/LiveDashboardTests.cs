using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;

namespace TreadmillRunner.E2ETests;

public sealed class LiveDashboardTests(GatewayFixture gateway) : PageTest, IClassFixture<GatewayFixture>
{
  public static TheoryData<string, int, int> Viewports => new()
    {
        { "iphone17-pro-max", 440, 956 },
        { "ipad-landscape", 1180, 820 },
        { "desktop-full-hd", 1920, 1080 },
    };

  [Theory]
  [MemberData(nameof(Viewports))]
  [Trait("Category", "Browser")]
  public async Task Live_dashboard_is_safe_responsive_and_readable(string name, int width, int height)
  {
    await Page.SetViewportSizeAsync(width, height);
    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions
    {
      WaitUntil = WaitUntilState.NetworkIdle,
    });

    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready to run", Exact = true }))
        .ToBeVisibleAsync(new() { Timeout = 30_000 });
    await Expect(Page.Locator(".connection-state"))
        .ToHaveTextAsync("Gateway ready", new() { Timeout = 15_000 });
    await Expect(Page.Locator(".site-header .global-hr-status")).ToHaveCountAsync(0);
    await Expect(Page.GetByLabel("Safety notice"))
        .ToContainTextAsync("Remote Start appears only for an exact model and firmware");

    ILocator visibleNavigation = Page.Locator(".primary-nav:visible");
    await Expect(visibleNavigation.Locator("a[aria-current='page']")).ToHaveCountAsync(1);
    await Expect(visibleNavigation.GetByRole(AriaRole.Link, new() { Name = "Run", Exact = true })).ToBeVisibleAsync();
    if (width <= 860)
    {
      await Expect(visibleNavigation.GetByText("More", new() { Exact = true })).ToBeVisibleAsync();
      bool navigationOverflows = await visibleNavigation.EvaluateAsync<bool>("element => element.scrollWidth > element.clientWidth + 1");
      Assert.False(navigationOverflows);
    }

    ILocator preflightControls = Page.Locator(".preflight-actions button");
    await Expect(preflightControls).ToHaveCountAsync(1);
    for (int index = 0; index < await preflightControls.CountAsync(); index++)
    {
      ILocator control = preflightControls.Nth(index);
      await Expect(control).ToBeDisabledAsync();
      LocatorBoundingBoxResult? box = await control.BoundingBoxAsync();
      Assert.NotNull(box);
      Assert.True(box.Width >= 44, $"Preflight control width was {box.Width}px at {name}.");
      Assert.True(box.Height >= 44, $"Preflight control height was {box.Height}px at {name}.");
    }

    bool hasHorizontalOverflow = await Page.EvaluateAsync<bool>(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1");
    Assert.False(hasHorizontalOverflow);

    string screenshotDirectory = Path.Combine(gateway.ProjectRoot, "validation", "playwright", "accepted");
    Directory.CreateDirectory(screenshotDirectory);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(screenshotDirectory, $"{name}.png"),
      FullPage = true,
    });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/devices").AbsoluteUri, new PageGotoOptions
    {
      WaitUntil = WaitUntilState.NetworkIdle,
    });
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Devices", Exact = true }))
      .ToBeVisibleAsync();
    await Expect(Page.Locator(".site-header .global-hr-status")).ToHaveCountAsync(0);
    await Expect(Page.GetByText("Not enrolled", new() { Exact = true })).ToHaveCountAsync(1);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "None added", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("New heart-rate sensors are available to", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Watch and health-app setup", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByLabel("Device safety notice"))
      .ToContainTextAsync("Disconnect never stops a moving treadmill");
    ILocator scanButton = Page.GetByRole(AriaRole.Button, new() { Name = "Scan for 5 seconds" });
    LocatorBoundingBoxResult? scanBox = await scanButton.BoundingBoxAsync();
    Assert.NotNull(scanBox);
    Assert.True(scanBox.Width >= 44 && scanBox.Height >= 44);
    bool devicesOverflow = await Page.EvaluateAsync<bool>(
      "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1");
    Assert.False(devicesOverflow);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(screenshotDirectory, $"{name}-devices.png"),
      FullPage = true,
    });
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Device_management_buttons_complete_their_local_actions()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ResetSimulatorAsync(gateway.BaseAddress);
    await scenario.ConfigureBrowserAsync(Page);
    await scenario.InstallVisualDataRoutesAsync(Page);
    var actions = new System.Collections.Concurrent.ConcurrentQueue<(string Method, string Path, string? Body)>();
    await Page.RouteAsync("**/api/devices/enrollments/**", async route =>
    {
      Uri requestUri = new(route.Request.Url);
      actions.Enqueue((route.Request.Method, requestUri.AbsolutePath, route.Request.PostData));
      await route.FulfillAsync(new RouteFulfillOptions { Status = 204, Body = string.Empty });
    });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/devices").AbsoluteUri, new PageGotoOptions
    {
      WaitUntil = WaitUntilState.NetworkIdle,
    });
    ILocator treadmillCard = Page.Locator(".device-card").First;
    await Expect(treadmillCard.GetByRole(AriaRole.Heading, new() { Name = "Horizon Omega Z", Exact = true }))
      .ToBeVisibleAsync();

    await treadmillCard.Locator("details > summary").ClickAsync();
    await treadmillCard.GetByLabel("Local device name", new() { Exact = true }).FillAsync("Office treadmill");
    await treadmillCard.GetByRole(AriaRole.Button, new() { Name = "Save name", Exact = true }).ClickAsync();
    await Expect(Page.GetByText("Device renamed to Office treadmill.", new() { Exact = true })).ToBeVisibleAsync();

    await treadmillCard.GetByRole(AriaRole.Button, new() { Name = "Enable verified controls", Exact = true }).ClickAsync();
    await Expect(Page.GetByText("Verified Start, Stop, speed, and incline controls are enabled. Prepare a new session to use them.", new() { Exact = true }))
      .ToBeVisibleAsync();

    await treadmillCard.GetByRole(AriaRole.Button, new() { Name = "Connect / retry", Exact = true }).ClickAsync();
    await Expect(Page.GetByText("Horizon Omega Z connected with fresh telemetry. End the run or use Disconnect when you are finished.", new() { Exact = true }))
      .ToBeVisibleAsync(new() { Timeout = 5_000 });

    await treadmillCard.GetByRole(AriaRole.Button, new() { Name = "Disconnect", Exact = true }).ClickAsync();
    await Expect(Page.GetByText("Horizon Omega Z disconnected. It will reconnect when you prepare a run or press Connect / retry.", new() { Exact = true }))
      .ToBeVisibleAsync(new() { Timeout = 5_000 });

    Assert.Contains(actions, action => action.Method == "PUT" &&
      action.Path.EndsWith("/name", StringComparison.Ordinal) &&
      action.Body?.Contains("Office treadmill", StringComparison.Ordinal) == true);
    Assert.Contains(actions, action => action.Method == "PUT" &&
      action.Path.EndsWith("/treadmill-controls", StringComparison.Ordinal));
    Assert.Contains(actions, action => action.Method == "POST" &&
      action.Path.EndsWith("/retry", StringComparison.Ordinal));
    Assert.Contains(actions, action => action.Method == "POST" &&
      action.Path.EndsWith("/disconnect", StringComparison.Ordinal));
  }
}
