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
    await Expect(preflightControls).ToHaveCountAsync(2);
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
      .ToContainTextAsync("hardware-verified Start control appears only on the Run screen");
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
}
