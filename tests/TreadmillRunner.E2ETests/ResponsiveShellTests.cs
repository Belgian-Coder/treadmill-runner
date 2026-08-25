using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;
using System.Text.RegularExpressions;

namespace TreadmillRunner.E2ETests;

public sealed class ResponsiveShellTests(GatewayFixture gateway) : PageTest, IClassFixture<GatewayFixture>
{
  [Fact]
  [Trait("Category", "Browser")]
  public async Task Mobile_header_disclosures_close_outside_on_escape_and_after_navigation()
  {
    await Page.SetViewportSizeAsync(390, 844);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/history").AbsoluteUri, new PageGotoOptions
    {
      WaitUntil = WaitUntilState.NetworkIdle,
    });

    ILocator more = Page.Locator(".primary-nav--mobile .nav-more");
    ILocator moreSummary = more.Locator("summary");
    await moreSummary.ClickAsync();
    await Expect(more).ToHaveAttributeAsync("open", "");
    await Page.GetByRole(AriaRole.Heading, new() { Name = "History", Exact = true }).ClickAsync();
    await Expect(more).Not.ToHaveAttributeAsync("open", "");

    ILocator runnerPicker = Page.Locator("details.active-runner-picker");
    ILocator runnerSummary = runnerPicker.Locator("summary");
    await Expect(runnerSummary).ToHaveAttributeAsync("aria-label", "Choose runner");
    await runnerSummary.ClickAsync();
    await Expect(runnerPicker).ToHaveAttributeAsync("open", "");
    await runnerSummary.PressAsync("Escape");
    await Expect(runnerPicker).Not.ToHaveAttributeAsync("open", "");
    await Expect(runnerSummary).ToBeFocusedAsync();

    await runnerSummary.ClickAsync();
    await Expect(runnerPicker).ToHaveAttributeAsync("open", "");
    await moreSummary.ClickAsync();
    await Expect(more).ToHaveAttributeAsync("open", "");
    await Expect(runnerPicker).Not.ToHaveAttributeAsync("open", "");

    await more.GetByRole(AriaRole.Link, new() { Name = "Calendar", Exact = true }).ClickAsync();
    await Expect(Page).ToHaveURLAsync(new Regex("/calendar$"));
    await Expect(more).Not.ToHaveAttributeAsync("open", "");
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Iphone17_shell_auto_hides_and_remains_keyboard_recoverable()
  {
    await Page.SetViewportSizeAsync(440, 956);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/devices").AbsoluteUri, new PageGotoOptions
    {
      WaitUntil = WaitUntilState.NetworkIdle,
    });

    ILocator header = Page.Locator("#site-header");
    await Expect(header).ToHaveAttributeAsync("data-scroll-state", "shown");
    LocatorBoundingBoxResult? box = await header.BoundingBoxAsync();
    Assert.NotNull(box);
    Assert.InRange(box.Height, 44, 64);

    // Keep the synthetic pointer away from the header while testing scroll-only
    // behavior; pointerenter intentionally reveals the header for mouse users.
    await Page.Mouse.MoveAsync(220, 500);

    await Page.EvaluateAsync("() => window.scrollTo(0, 700)");
    await Expect(header).ToHaveAttributeAsync("data-scroll-state", "hidden");
    await Page.EvaluateAsync("() => window.scrollBy(0, -100)");
    await Expect(header).ToHaveAttributeAsync("data-scroll-state", "shown");

    await Page.EvaluateAsync("() => window.scrollTo(0, 700)");
    await Expect(header).ToHaveAttributeAsync("data-scroll-state", "hidden");
    await Page.Locator(".primary-nav--mobile a").First.FocusAsync();
    await Expect(header).ToHaveAttributeAsync("data-scroll-state", "shown");

    foreach (ILocator target in await Page.Locator(".primary-nav--mobile > a, .primary-nav--mobile > details > summary").AllAsync())
    {
      LocatorBoundingBoxResult? targetBox = await target.BoundingBoxAsync();
      Assert.NotNull(targetBox);
      Assert.True(targetBox.Width >= 44 && targetBox.Height >= 44, "A mobile navigation target is below 44x44 CSS pixels.");
    }

    string galleryDirectory = Path.Combine(gateway.ProjectRoot, "output", "playwright", "gallery");
    Directory.CreateDirectory(galleryDirectory);
    await Page.EvaluateAsync("() => { document.activeElement?.blur(); window.scrollTo(0, 0); }");
    await Expect(header).ToHaveAttributeAsync("data-scroll-state", "shown");
    await Page.EvaluateAsync("() => window.scrollTo(0, 700)");
    await Expect(header).ToHaveAttributeAsync("data-scroll-state", "hidden");
    await Page.WaitForTimeoutAsync(250);
    LocatorBoundingBoxResult? hiddenBox = await header.BoundingBoxAsync();
    Assert.NotNull(hiddenBox);
    Assert.True(hiddenBox.Y + hiddenBox.Height <= 1, "The mobile navigation did not finish moving outside the viewport.");
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(galleryDirectory, "navigation-hidden-iphone17-pro-max.png"),
      FullPage = false,
    });
  }
}
