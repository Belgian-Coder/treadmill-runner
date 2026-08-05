using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;

namespace TreadmillRunner.E2ETests;

public sealed class OperationsPageTests(GatewayFixture gateway) : PageTest, IClassFixture<GatewayFixture>
{
  [Fact]
  [Trait("Category", "Browser")]
  public async Task Operations_page_progresses_available_stage_and_two_step_activation()
  {
    string state = "Available";
    await Page.RouteAsync("**/api/updates/**", async route =>
    {
      string path = new Uri(route.Request.Url).AbsolutePath;
      if (route.Request.Method == "POST" && path.EndsWith("/stage", StringComparison.Ordinal)) state = "Staged";
      if (route.Request.Method == "POST" && path.EndsWith("/activate", StringComparison.Ordinal)) state = "Activating";
      string? staged = state == "Staged" ? "2.0.0" : null;
      await route.FulfillAsync(new RouteFulfillOptions
      {
        Status = state == "Activating" && path.EndsWith("/activate", StringComparison.Ordinal) ? 202 : 200,
        ContentType = "application/json",
        Body = System.Text.Json.JsonSerializer.Serialize(new
        {
          state,
          currentVersion = "1.5.6",
          availableVersion = "2.0.0",
          stagedVersion = staged,
          releaseNotes = "Signed household release",
          feedSource = "GitHub Releases (belgian-coder/treadmill-runner)",
          lastCheckedAtUtc = DateTimeOffset.UtcNow,
          message = state switch
          {
            "Available" => "A signed update is available.",
            "Staged" => "The signed update is verified and staged for activation.",
            _ => "The signed update is activating. The service will reconnect after promotion or rollback.",
          },
        }),
      });
    });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/operations").AbsoluteUri);
    await Expect(Page.GetByLabel("Update status", new() { Exact = true }))
      .ToContainTextAsync("GitHub Releases (belgian-coder/treadmill-runner)");
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Verify and stage", Exact = true })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Verify and stage", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Activate staged update", Exact = true })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Activate staged update", Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Region, new() { Name = "Confirm software update activation" })).ToBeVisibleAsync();
    await Expect(Page.GetByText("Install 2.0.0 now?", new() { Exact = false })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Confirm activation", Exact = true }).ClickAsync();
    await Expect(Page.GetByText("The signed update is activating. The service will reconnect after promotion or rollback.", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Activate staged update", Exact = true })).ToHaveCountAsync(0);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Operations_page_exposes_bounded_recovery_and_fail_closed_update_controls()
  {
    await Page.SetViewportSizeAsync(440, 956);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/operations").AbsoluteUri, new PageGotoOptions
    {
      WaitUntil = WaitUntilState.NetworkIdle,
    });

    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Operations", Exact = true }))
      .ToBeVisibleAsync();
    await Page.GetByText("Backup and diagnostics", new() { Exact = true }).ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Link, new() { Name = "Download full backup" }))
      .ToHaveAttributeAsync("href", "/api/operations/backup");
    await Expect(Page.GetByRole(AriaRole.Link, new() { Name = "Download diagnostics" }))
      .ToHaveAttributeAsync("href", "/api/operations/diagnostics");
    await Expect(Page.GetByLabel("Backup file"))
      .ToHaveAttributeAsync("accept", ".trb,.db,application/vnd.treadmillrunner.backup,application/vnd.sqlite3");
    await Page.GetByText("Install from a signed file", new() { Exact = true }).ClickAsync();
    await Expect(Page.GetByLabel("Signed update bundle (.zip)"))
      .ToHaveAttributeAsync("accept", ".zip,application/vnd.treadmillrunner.update+zip");

    ILocator check = Page.GetByRole(AriaRole.Button, new() { Name = "Check now", Exact = true });
    await check.ClickAsync();
    await Expect(Page.GetByText("The update feed is unavailable or its configuration could not be validated.", new() { Exact = true }))
      .ToBeVisibleAsync();
    foreach (string label in new[] { "Check now" })
    {
      LocatorBoundingBoxResult? box = await Page.GetByRole(AriaRole.Button, new() { Name = label, Exact = true })
        .BoundingBoxAsync();
      Assert.NotNull(box);
      Assert.True(box.Width >= 44 && box.Height >= 44, $"{label} is smaller than the 44px touch target.");
    }
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Verify and stage", Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Activate staged update", Exact = true })).ToHaveCountAsync(0);
    await Page.GetByText("Restore from backup", new() { Exact = true }).ClickAsync();
    await Expect(Page.GetByLabel("Backup file")).ToBeVisibleAsync();

    bool hasHorizontalOverflow = await Page.EvaluateAsync<bool>(
      "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1");
    Assert.False(hasHorizontalOverflow);

    string screenshotDirectory = Path.Combine(gateway.ProjectRoot, "validation", "playwright", "accepted");
    Directory.CreateDirectory(screenshotDirectory);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(screenshotDirectory, "operations-iphone17-pro-max.png"),
      FullPage = true,
    });
  }
}
