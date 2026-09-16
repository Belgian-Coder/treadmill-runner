using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;
using System.Text.RegularExpressions;

namespace TreadmillRunner.E2ETests;

public sealed class ResponsiveShellTests(GatewayFixture gateway) : PageTest, IClassFixture<GatewayFixture>
{
  [Theory]
  [InlineData(390, 844)]
  [InlineData(844, 390)]
  [InlineData(820, 1180)]
  [InlineData(1180, 820)]
  [Trait("Category", "Browser")]
  public async Task Touch_forms_are_readable_and_open_at_the_requested_editor(int width, int height)
  {
    await Page.SetViewportSizeAsync(width, height);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/profiles").AbsoluteUri);
    await Page.GetByRole(AriaRole.Button, new() { Name = "New profile", Exact = true }).ClickAsync();
    ILocator heading = Page.GetByRole(AriaRole.Heading, new() { Name = "Create profile", Exact = true });
    await Expect(heading).ToBeFocusedAsync();
    await Expect(heading).ToBeInViewportAsync();
    await AssertReadableFieldsAsync();

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts/new").AbsoluteUri);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Level = 1 })).ToBeVisibleAsync();
    await AssertReadableFieldsAsync();
  }

  [Theory]
  [InlineData(667, 280)]
  [InlineData(844, 320)]
  [Trait("Category", "Browser")]
  public async Task Navigation_menu_stays_reachable_in_short_phone_landscape(int width, int height)
  {
    await Page.SetViewportSizeAsync(width, height);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/history").AbsoluteUri);
    ILocator more = Page.Locator(".primary-nav--mobile .nav-more");
    await more.Locator("summary").ClickAsync();
    ILocator menu = more.Locator(".nav-more__menu");
    LocatorBoundingBoxResult? bounds = await menu.BoundingBoxAsync();
    Assert.NotNull(bounds);
    Assert.True(bounds.Y >= 0 && bounds.Y + bounds.Height <= height,
      $"The navigation menu extends below the {width}x{height} viewport: {bounds}.");
    await menu.GetByRole(AriaRole.Link, new() { Name = "Operations", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Operations", Exact = true })).ToBeVisibleAsync();
  }

  private async Task AssertReadableFieldsAsync()
  {
    string[] issues = await Page.EvaluateAsync<string[]>("""
      () => Array.from(document.querySelectorAll('input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=hidden]):not([type=file]), select, textarea'))
        .filter(element => element.checkVisibility())
        .flatMap(element => {
          const bounds = element.getBoundingClientRect();
          const fontSize = parseFloat(getComputedStyle(element).fontSize);
          return fontSize < 16 || bounds.height < 44
            ? [`${element.closest('label')?.textContent?.trim() || element.getAttribute('aria-label') || element.tagName}: ${fontSize}px text, ${bounds.height}px height`]
            : [];
        })
      """);
    Assert.Empty(issues);
    Assert.False(await Page.EvaluateAsync<bool>("() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"));
  }

  [Fact]
  [Trait("Category", "Browser")]
  [Trait("Category", "ReleaseSmoke")]
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

  [Theory]
  [InlineData(390, 844)]
  [InlineData(956, 440)]
  [Trait("Category", "Browser")]
  [Trait("Category", "ReleaseSmoke")]
  public async Task Runner_picker_supports_radio_keys_and_restores_summary_focus(int width, int height)
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    await Page.SetViewportSizeAsync(width, height);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/history").AbsoluteUri,
      new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });

    ILocator picker = Page.Locator("details.active-runner-picker");
    ILocator summary = picker.Locator("summary");
    await summary.FocusAsync();
    await summary.PressAsync("Enter");
    ILocator marc = picker.GetByRole(AriaRole.Radio, new() { Name = "Marc", Exact = true });
    ILocator runnerTwo = picker.GetByRole(AriaRole.Radio, new() { Name = GalleryScenario.SecondProfileName, Exact = true });
    await Expect(marc).ToHaveAttributeAsync("tabindex", "0");
    await Expect(runnerTwo).ToHaveAttributeAsync("tabindex", "-1");

    await marc.FocusAsync();
    double scrollBeforeNavigation = await Page.EvaluateAsync<double>("window.scrollY");
    await marc.PressAsync("End");
    Assert.Equal(scrollBeforeNavigation, await Page.EvaluateAsync<double>("window.scrollY"));
    await Expect(picker).Not.ToHaveAttributeAsync("open", "");
    await Expect(summary).ToBeFocusedAsync();
    await Expect(summary).ToContainTextAsync(GalleryScenario.SecondProfileName);
    await Expect(Page.Locator(".active-runner-picker summary")).ToHaveAttributeAsync(
      "aria-label", $"Active runner: {GalleryScenario.SecondProfileName}. Choose runner");
    Assert.Equal(scenario.SecondProfileId.ToString("D"),
      await Page.EvaluateAsync<string>("window.localStorage.getItem('treadmillrunner.active-profile')"));

    await summary.PressAsync("Enter");
    await Expect(runnerTwo).ToHaveAttributeAsync("tabindex", "0");
    await runnerTwo.FocusAsync();
    await runnerTwo.PressAsync("Home");
    await Expect(picker).Not.ToHaveAttributeAsync("open", "");
    await Expect(summary).ToBeFocusedAsync();
    await Expect(summary).ToContainTextAsync("Marc");
    Assert.Equal(scenario.MarcProfileId.ToString("D"),
      await Page.EvaluateAsync<string>("window.localStorage.getItem('treadmillrunner.active-profile')"));

    await summary.PressAsync("Enter");
    await marc.FocusAsync();
    await marc.PressAsync("ArrowDown");
    await Expect(picker).Not.ToHaveAttributeAsync("open", "");
    await Expect(summary).ToBeFocusedAsync();
    await Expect(summary).ToContainTextAsync(GalleryScenario.SecondProfileName);

    await summary.PressAsync("Enter");
    await runnerTwo.FocusAsync();
    bool modifiedShortcutWasCanceled = await runnerTwo.EvaluateAsync<bool>("element => !element.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', altKey: true, bubbles: true, cancelable: true }))");
    Assert.False(modifiedShortcutWasCanceled);
    await Expect(picker).ToHaveAttributeAsync("open", "");
    await Expect(runnerTwo).ToBeFocusedAsync();
    await runnerTwo.PressAsync("ArrowDown");
    await Expect(picker).Not.ToHaveAttributeAsync("open", "");
    await Expect(summary).ToBeFocusedAsync();
    await Expect(summary).ToContainTextAsync("Marc");

    await summary.PressAsync("Enter");
    await marc.FocusAsync();
    await marc.PressAsync("ArrowUp");
    await Expect(picker).Not.ToHaveAttributeAsync("open", "");
    await Expect(summary).ToBeFocusedAsync();
    await Expect(summary).ToContainTextAsync(GalleryScenario.SecondProfileName);
  }

  [Theory]
  [InlineData(390, 844)]
  [InlineData(956, 440)]
  [Trait("Category", "Browser")]
  [Trait("Category", "ReleaseSmoke")]
  public async Task Runner_picker_distinguishes_load_failure_from_an_empty_household_and_retries(int width, int height)
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    int requests = 0;
    await Page.RouteAsync("**/api/planning/profiles", route =>
    {
      if (Interlocked.Increment(ref requests) <= 2)
        return route.FulfillAsync(new RouteFulfillOptions { Status = 503, ContentType = "application/json", Body = "{}" });
      return route.FulfillAsync(new RouteFulfillOptions { Status = 200, ContentType = "application/json", Body = "[]" });
    });
    await Page.SetViewportSizeAsync(width, height);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/history").AbsoluteUri,
      new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });

    ILocator picker = Page.Locator("details.active-runner-picker");
    ILocator summary = picker.Locator("summary");
    await summary.ClickAsync();
    await Expect(picker.GetByRole(AriaRole.Alert)).ToContainTextAsync("Runners could not be loaded");
    await Expect(picker.GetByRole(AriaRole.Link, new() { Name = "Create a profile", Exact = true })).ToHaveCountAsync(0);
    ILocator retry = picker.GetByRole(AriaRole.Button, new() { Name = "Try again", Exact = true });
    await retry.ClickAsync();
    await Expect(picker.GetByRole(AriaRole.Alert)).ToContainTextAsync("Runners could not be loaded");
    await Expect(retry).ToBeFocusedAsync();
    await retry.ClickAsync();

    await Expect(picker).Not.ToHaveAttributeAsync("open", "");
    await Expect(summary).ToBeFocusedAsync();
    await Expect(summary).ToContainTextAsync("Choose runner");
    await summary.ClickAsync();
    await Expect(picker.GetByRole(AriaRole.Link, new() { Name = "Create a profile", Exact = true })).ToBeVisibleAsync();
    Assert.True(requests >= 3);
  }

  [Theory]
  [InlineData(390, 844)]
  [InlineData(844, 390)]
  [Trait("Category", "Browser")]
  public async Task History_cards_activate_once_without_space_scroll_and_restore_focus(int width, int height)
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    await scenario.InstallVisualDataRoutesAsync(Page);
    await Page.SetViewportSizeAsync(width, height);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/history").AbsoluteUri,
      new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });

    ILocator card = Page.GetByRole(AriaRole.Button, new() { Name = $"View details for {GalleryScenario.FeaturedWorkoutName}", Exact = true });
    await card.ScrollIntoViewIfNeededAsync();
    await card.FocusAsync();
    await card.EvaluateAsync("element => { window.__historyCardClicks = 0; element.addEventListener('click', () => window.__historyCardClicks++); }");
    await card.DispatchEventAsync("keydown", new Dictionary<string, object> { ["key"] = " ", ["repeat"] = true });
    Assert.Equal(0, await Page.EvaluateAsync<int>("window.__historyCardClicks"));
    double scrollBefore = await Page.EvaluateAsync<double>("window.scrollY");
    await card.PressAsync("Space");
    Assert.Equal(1, await Page.EvaluateAsync<int>("window.__historyCardClicks"));

    ILocator dialog = Page.GetByRole(AriaRole.Dialog);
    await Expect(dialog).ToBeVisibleAsync();
    await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Close session details", Exact = true })).ToBeFocusedAsync();
    Assert.Equal(scrollBefore, await Page.EvaluateAsync<double>("window.scrollY"));

    await Page.Keyboard.PressAsync("Escape");
    await Expect(dialog).ToBeHiddenAsync();
    await Expect(card).ToBeFocusedAsync();

    await card.PressAsync("Enter");
    await Expect(dialog).ToBeVisibleAsync();
  }
}
