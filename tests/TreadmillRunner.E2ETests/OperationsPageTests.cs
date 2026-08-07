using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;

namespace TreadmillRunner.E2ETests;

public sealed class OperationsPageTests(GatewayFixture gateway) : PageTest, IClassFixture<GatewayFixture>
{
  [Fact]
  [Trait("Category", "Browser")]
  public async Task Operations_page_progresses_available_stage_and_two_step_activation()
  {
    await InstallAccessRoutesAsync();
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
  public async Task Operations_page_automatically_reloads_after_activated_gateway_returns_with_new_build()
  {
    await InstallAccessRoutesAsync();
    using var versionClient = new HttpClient();
    using System.Text.Json.JsonDocument initialVersion = System.Text.Json.JsonDocument.Parse(
      await versionClient.GetStringAsync(new Uri(gateway.BaseAddress, "/api/system/version")));
    string clientFingerprint = initialVersion.RootElement.GetProperty("buildFingerprint").GetString()
      ?? throw new InvalidOperationException("The fixture build fingerprint is missing.");
    await Page.AddInitScriptAsync("""
      window.name = String(Number(window.name || "0") + 1);
      Object.defineProperty(window, "sessionStorage", {
        configurable: true,
        get: () => { throw new DOMException("Storage disabled", "SecurityError"); }
      });
      """);
    string state = "Staged";
    bool activationAccepted = false;
    bool promoted = false;
    bool activationResponseInterrupted = false;
    int reconnectChecks = 0;

    await Page.RouteAsync("**/api/system/version**", async route =>
    {
      if (!activationAccepted)
      {
        await route.ContinueAsync();
        return;
      }

      reconnectChecks++;
      if (reconnectChecks == 1)
      {
        await route.AbortAsync("connectionrefused");
        return;
      }

      promoted = true;
      state = "Current";
      await route.FulfillAsync(new RouteFulfillOptions
      {
        Status = 200,
        ContentType = "application/json",
        Body = System.Text.Json.JsonSerializer.Serialize(new
        {
          releaseVersion = "2.0.0",
          buildFingerprint = reconnectChecks == 2 ? "promoted-build" : clientFingerprint,
          serviceStartedAtUtc = DateTimeOffset.UtcNow,
        }),
      });
    });
    await Page.RouteAsync("**/api/updates/**", async route =>
    {
      string path = new Uri(route.Request.Url).AbsolutePath;
      if (route.Request.Method == "POST" && path.EndsWith("/activate", StringComparison.Ordinal))
      {
        activationAccepted = true;
        state = "Activating";
        await route.AbortAsync("connectionrefused");
        activationResponseInterrupted = true;
        return;
      }
      await route.FulfillAsync(new RouteFulfillOptions
      {
        Status = state == "Activating" && path.EndsWith("/activate", StringComparison.Ordinal) ? 202 : 200,
        ContentType = "application/json",
        Body = System.Text.Json.JsonSerializer.Serialize(new
        {
          state,
          currentVersion = promoted ? "2.0.0" : "1.5.14",
          availableVersion = "2.0.0",
          stagedVersion = state == "Staged" ? "2.0.0" : null,
          releaseNotes = "Automatic activation recovery regression",
          feedSource = "GitHub Releases (belgian-coder/treadmill-runner)",
          lastCheckedAtUtc = DateTimeOffset.UtcNow,
          message = state switch
          {
            "Staged" => "The signed update is verified and staged for activation.",
            "Activating" => "The signed update is activating. The service will reconnect after promotion or rollback.",
            _ => "The release is not newer.",
          },
        }),
      });
    });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/operations").AbsoluteUri);
    await Page.GetByRole(AriaRole.Button, new() { Name = "Activate staged update", Exact = true }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Confirm activation", Exact = true }).ClickAsync();

    await Expect(Page).ToHaveURLAsync(
      new System.Text.RegularExpressions.Regex(@"/operations\?build=promoted-build#signed-updates$"),
      new PageAssertionsToHaveURLOptions { Timeout = 15_000 });
    Assert.True(reconnectChecks >= 2, "The page did not observe disconnect followed by the promoted gateway build.");
    Assert.True(activationResponseInterrupted, "The test did not exercise the accepted-but-interrupted activation response.");
    Assert.Equal(2, await Page.EvaluateAsync<int>("Number(window.name)"));
    await Expect(Page.GetByLabel("Update status", new() { Exact = true })).ToContainTextAsync("Current");
    await Expect(Page.Locator("#signed-updates")).ToBeInViewportAsync();
    await Expect(Page.GetByText("Update ready", new() { Exact = false })).ToHaveCountAsync(0);

    string screenshotDirectory = Path.Combine(gateway.ProjectRoot, "screenshots", "tr-022");
    Directory.CreateDirectory(screenshotDirectory);
    await Page.ScreenshotAsync(new PageScreenshotOptions
    {
      Path = Path.Combine(screenshotDirectory, "operations-activation-reconnect.png"),
      FullPage = true,
    });
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Update_banner_manual_reload_bypasses_a_spent_automatic_guard()
  {
    await InstallAccessRoutesAsync();
    await Page.SetViewportSizeAsync(440, 956);
    await Page.AddInitScriptAsync("sessionStorage.setItem('treadmillrunner.reload.manual-build', 'attempted');");
    await Page.RouteAsync("**/api/system/version**", async route =>
    {
      await route.FulfillAsync(new RouteFulfillOptions
      {
        Status = 200,
        ContentType = "application/json",
        Body = System.Text.Json.JsonSerializer.Serialize(new
        {
          releaseVersion = "2.0.0",
          buildFingerprint = "manual-build",
          serviceStartedAtUtc = DateTimeOffset.UtcNow,
        }),
      });
    });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/operations").AbsoluteUri);
    await Expect(Page.GetByText("Update ready", new() { Exact = true })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Reload", Exact = true }).ClickAsync();

    await Expect(Page).ToHaveURLAsync(
      new System.Text.RegularExpressions.Regex(@"/operations\?build=manual-build&reload=\d+#signed-updates$"),
      new PageAssertionsToHaveURLOptions { Timeout = 10_000 });
    await Expect(Page.Locator("#signed-updates")).ToBeInViewportAsync();
    Assert.True(await Page.EvaluateAsync<bool>("document.documentElement.scrollWidth <= window.innerWidth"));
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Operations_page_renders_rollback_when_activation_returns_on_same_build()
  {
    await InstallAccessRoutesAsync();
    await Page.AddInitScriptAsync("""
      sessionStorage.setItem("tr022.rollbackLoads", String(Number(sessionStorage.getItem("tr022.rollbackLoads") ?? "0") + 1));
      """);
    string state = "Staged";
    bool activationAccepted = false;
    int activationStatusReads = 0;

    await Page.RouteAsync("**/api/updates/**", async route =>
    {
      string path = new Uri(route.Request.Url).AbsolutePath;
      if (route.Request.Method == "POST" && path.EndsWith("/activate", StringComparison.Ordinal))
      {
        activationAccepted = true;
        state = "Activating";
      }
      else if (activationAccepted && route.Request.Method == "GET" && path.EndsWith("/status", StringComparison.Ordinal))
      {
        activationStatusReads++;
        if (activationStatusReads >= 2) state = "RolledBack";
      }

      await route.FulfillAsync(new RouteFulfillOptions
      {
        Status = state == "Activating" && path.EndsWith("/activate", StringComparison.Ordinal) ? 202 : 200,
        ContentType = "application/json",
        Body = System.Text.Json.JsonSerializer.Serialize(new
        {
          state,
          currentVersion = "1.5.14",
          availableVersion = "2.0.0",
          stagedVersion = state == "Staged" ? "2.0.0" : null,
          releaseNotes = "Rollback recovery regression",
          feedSource = "GitHub Releases (belgian-coder/treadmill-runner)",
          lastCheckedAtUtc = DateTimeOffset.UtcNow,
          message = state == "RolledBack"
            ? "The previous release was restored after activation health failed."
            : state == "Staged"
              ? "The signed update is verified and staged for activation."
              : "The signed update is activating. The service will reconnect after promotion or rollback.",
        }),
      });
    });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/operations").AbsoluteUri);
    await Page.GetByRole(AriaRole.Button, new() { Name = "Activate staged update", Exact = true }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Confirm activation", Exact = true }).ClickAsync();

    await Expect(Page.GetByLabel("Update status", new() { Exact = true }))
      .ToContainTextAsync("RolledBack", new LocatorAssertionsToContainTextOptions { Timeout = 10_000 });
    await Expect(Page.GetByText("The previous release was restored after activation health failed.", new() { Exact = true }))
      .ToBeVisibleAsync();
    Assert.Equal(1, await Page.EvaluateAsync<int>("Number(sessionStorage.getItem('tr022.rollbackLoads'))"));
    await Expect(Page).ToHaveURLAsync(new Uri(gateway.BaseAddress, "/operations").AbsoluteUri);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Operations_page_exposes_bounded_recovery_and_fail_closed_update_controls()
  {
    await Page.SetViewportSizeAsync(440, 956);
    await Page.AddInitScriptAsync("""
      window.__copiedAppAddress = null;
      Object.defineProperty(Navigator.prototype, 'clipboard', {
        configurable: true,
        get: () => ({ writeText: async value => window.__copiedAppAddress = value })
      });
      """);
    await InstallAccessRoutesAsync();
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/operations").AbsoluteUri, new PageGotoOptions
    {
      WaitUntil = WaitUntilState.NetworkIdle,
    });

    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Operations", Exact = true }))
      .ToBeVisibleAsync();
    ILocator operationsSummary = Page.GetByRole(AriaRole.Region, new() { Name = "Operations summary", Exact = true });
    await Expect(operationsSummary).ToContainTextAsync("Service · Healthy");
    await Expect(operationsSummary.GetByRole(AriaRole.Button, new() { Name = "Try again", Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Open on another device", Exact = true }))
      .ToBeVisibleAsync();
    await Expect(Page.Locator(".app-access-qr")).ToHaveAttributeAsync("src", new System.Text.RegularExpressions.Regex("phone-secure$"));
    await Expect(Page.Locator(".app-access-url")).ToHaveTextAsync("https://treadmillrunner.home/");
    await Page.GetByLabel("Shared app address", new() { Exact = true }).SelectOptionAsync("phone-http");
    await Expect(Page.Locator(".app-access-qr")).ToHaveAttributeAsync("src", new System.Text.RegularExpressions.Regex("phone-http$"));
    await Expect(Page.Locator(".app-access-url")).ToHaveTextAsync("http://192.168.1.20:5180/");
    await Page.GetByRole(AriaRole.Button, new() { Name = "Copy address", Exact = true }).ClickAsync();
    await Expect(Page.GetByText("Address copied.", new() { Exact = true })).ToBeVisibleAsync();
    Assert.Equal("http://192.168.1.20:5180/", await Page.EvaluateAsync<string>("window.__copiedAppAddress"));
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

    ILocator check = Page.GetByRole(AriaRole.Heading, new() { Name = "Signed updates", Exact = true })
      .Locator("xpath=..").GetByRole(AriaRole.Button, new() { Name = "Check now", Exact = true });
    await check.ClickAsync();
    await Expect(Page.Locator("#signed-updates").GetByText("The update feed is unavailable or its configuration could not be validated.", new() { Exact = true }))
      .ToBeVisibleAsync();
    LocatorBoundingBoxResult? checkBox = await check.BoundingBoxAsync();
    Assert.NotNull(checkBox);
    Assert.True(checkBox.Width >= 44 && checkBox.Height >= 44, "Update check is smaller than the 44px touch target.");
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

  private async Task InstallAccessRoutesAsync()
  {
    await Page.RouteAsync("**/api/operations/access**", async route =>
    {
      string path = new Uri(route.Request.Url).AbsolutePath;
      if (path.Contains("/qr/", StringComparison.Ordinal))
      {
        await route.FulfillAsync(new RouteFulfillOptions
        {
          Status = 200,
          ContentType = "image/svg+xml",
          Body = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 21 21'><rect width='21' height='21' fill='white'/><g fill='#071f27'><path d='M1 1h7v7H1zm12 0h7v7h-7zM1 13h7v7H1z'/><path d='M10 2h2v2h-2zm0 4h2v3h-2zm3 4h2v2h-2zm3-1h3v2h-3zm-6 4h3v2h-3zm5 1h2v3h-2zm3-2h2v2h-2zm-9 5h2v3H9zm3 0h2v2h-2zm5 1h3v2h-3z'/></g><g fill='white'><path d='M3 3h3v3H3zm12 0h3v3h-3zM3 15h3v3H3z'/></g></svg>",
        });
        return;
      }

      await route.FulfillAsync(new RouteFulfillOptions
      {
        Status = 200,
        ContentType = "application/json",
        Body = System.Text.Json.JsonSerializer.Serialize(new
        {
          available = true,
          preferredCandidateId = "phone-secure",
          candidates = new[]
          {
            new { id = "phone-secure", label = "Server name · treadmillrunner.home", url = "https://treadmillrunner.home/", isSecure = true },
            new { id = "phone-http", label = "Private Wi-Fi · 192.168.1.20", url = "http://192.168.1.20:5180/", isSecure = false },
          },
          message = "Scan from a device on the same private Wi-Fi network.",
        }),
      });
    });
  }
}
