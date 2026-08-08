using Microsoft.Playwright;

namespace TreadmillRunner.E2ETests;

internal static class RunnerSelection
{
  public static async Task SelectActiveRunnerAsync(this IPage page, string displayName)
  {
    ILocator summary = page.Locator(".active-runner-picker summary");
    await Assertions.Expect(summary).ToBeVisibleAsync();
    if ((await summary.InnerTextAsync()).Contains(displayName, StringComparison.Ordinal)) return;

    await summary.ClickAsync();
    ILocator choice = page.GetByRole(AriaRole.Radio, new() { Name = displayName, Exact = true });
    string profileId = await choice.GetAttributeAsync("data-profile-id")
      ?? throw new InvalidOperationException("The runner choice does not expose its profile identifier.");
    double previousDocumentTimeOrigin = await page.EvaluateAsync<double>("performance.timeOrigin");
    await choice.ClickAsync();
    await page.WaitForFunctionAsync(
      "origin => performance.timeOrigin !== origin",
      previousDocumentTimeOrigin,
      new PageWaitForFunctionOptions { Timeout = 10_000 });
    await page.WaitForLoadStateAsync(LoadState.DOMContentLoaded);
    await page.WaitForFunctionAsync(
      "id => window.localStorage.getItem('treadmillrunner.active-profile') === id",
      profileId);
    await Assertions.Expect(summary).ToContainTextAsync(
      displayName,
      new LocatorAssertionsToContainTextOptions { Timeout = 10_000 });
  }

  public static async Task OpenRunChoicesAsync(this IPage page)
  {
    ILocator choices = page.Locator("details.choose-another-run");
    await Assertions.Expect(choices).ToBeVisibleAsync();
    if (await choices.GetAttributeAsync("open") is null)
      await choices.Locator("summary").ClickAsync();
    await Assertions.Expect(choices).ToHaveAttributeAsync("open", "");
  }
}
