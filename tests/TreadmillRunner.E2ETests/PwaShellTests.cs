using System.Text.Json;
using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;

namespace TreadmillRunner.E2ETests;

public sealed class PwaShellTests(GatewayFixture gateway) : PageTest, IClassFixture<GatewayFixture>
{
  [Theory]
  [InlineData(390, 844, "unsupported")]
  [InlineData(820, 1180, "installable")]
  [InlineData(1440, 900, "installed")]
  [Trait("Category", "Browser")]
  public async Task Operations_reports_secure_install_states_across_supported_viewports(
    int width,
    int height,
    string state)
  {
    await Page.SetViewportSizeAsync(width, height);
    if (state == "installed")
    {
      await Page.AddInitScriptAsync(
        "Object.defineProperty(navigator, 'standalone', { value: true, configurable: true });");
    }

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/operations").AbsoluteUri);
    Assert.True(await Page.EvaluateAsync<bool>("window.isSecureContext"));

    if (state == "installable")
    {
      await Page.EvaluateAsync("""
        () => {
          const event = new Event('beforeinstallprompt');
          Object.defineProperty(event, 'prompt', { value: async () => {} });
          Object.defineProperty(event, 'userChoice', { value: Promise.resolve({ outcome: 'accepted' }) });
          window.dispatchEvent(event);
        }
        """);
      ILocator install = Page.GetByRole(AriaRole.Button, new() { Name = "Install app", Exact = true });
      await Expect(install).ToBeVisibleAsync();
      await install.ClickAsync();
      await Expect(Page.GetByText("Installation was accepted.", new() { Exact = false })).ToBeVisibleAsync();
    }
    else if (state == "installed")
    {
      await Expect(Page.GetByText("Installed", new() { Exact = true })).ToBeVisibleAsync();
      await Expect(Page.GetByText("standalone app window", new() { Exact = false })).ToBeVisibleAsync();
    }
    else
    {
      await Expect(Page.GetByText("Add to Home Screen", new() { Exact = false })).ToBeVisibleAsync();
      await Expect(Page.GetByText("browser menu’s install command", new() { Exact = false })).ToBeVisibleAsync();
    }

    await Expect(Page.GetByText("installed from HTTP is a separate browser app", new() { Exact = false }))
      .ToBeVisibleAsync();
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Share_bridge_preserves_server_filenames_and_handles_success_fallback_cancel_and_failure()
  {
    await Page.AddInitScriptAsync("""
      window.__shareMode = 'success';
      window.__sharedFiles = [];
      Object.defineProperty(Navigator.prototype, 'canShare', {
        configurable: true,
        value: data => window.__shareMode !== 'unsupported' && Array.isArray(data.files) && data.files.length === 1
      });
      Object.defineProperty(Navigator.prototype, 'share', {
        configurable: true,
        value: async data => {
          if (window.__shareMode === 'cancel') throw new DOMException('Canceled', 'AbortError');
          if (window.__shareMode === 'fail') throw new DOMException('Unavailable', 'NotAllowedError');
          const file = data.files[0];
          window.__sharedFiles.push({ name: file.name, type: file.type, size: file.size, title: data.title });
        }
      });
      """);
    await Page.RouteAsync("**/api/pwa-share-test/**", async route =>
    {
      string extension = new Uri(route.Request.Url).AbsolutePath.Split('/').Last();
      (string fileName, string contentType) = extension switch
      {
        "csv" => ("Morning Run.csv", "text/csv"),
        "fit" => ("Morning Run.fit", "application/vnd.ant.fit"),
        _ => ("full-backup.trb", "application/vnd.treadmillrunner.backup"),
      };
      await route.FulfillAsync(new RouteFulfillOptions
      {
        Status = 200,
        ContentType = contentType,
        Body = $"test-{extension}",
        Headers = new Dictionary<string, string>
        {
          ["Content-Disposition"] = $"attachment; filename=\"{fileName}\"",
        },
      });
    });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/operations").AbsoluteUri);

    foreach (string extension in new[] { "csv", "fit", "trb" })
    {
      JsonElement result = await ShareAsync(extension);
      Assert.Equal("Shared", result.GetProperty("state").GetString());
    }
    JsonElement shared = await Page.EvaluateAsync<JsonElement>("window.__sharedFiles");
    Assert.Equal("Morning Run.csv", shared[0].GetProperty("name").GetString());
    Assert.Equal("text/csv", shared[0].GetProperty("type").GetString());
    Assert.Equal("Morning Run.fit", shared[1].GetProperty("name").GetString());
    Assert.Equal("application/vnd.ant.fit", shared[1].GetProperty("type").GetString());
    Assert.Equal("full-backup.trb", shared[2].GetProperty("name").GetString());

    await Page.EvaluateAsync("window.__shareMode = 'unsupported'");
    JsonElement fallback = default;
    IDownload download = await Page.RunAndWaitForDownloadAsync(async () => fallback = await ShareAsync("trb"));
    Assert.Equal("Downloaded", fallback.GetProperty("state").GetString());
    Assert.Equal("full-backup.trb", download.SuggestedFilename);

    await Page.EvaluateAsync("window.__shareMode = 'cancel'");
    JsonElement canceled = await ShareAsync("csv");
    Assert.Equal("Canceled", canceled.GetProperty("state").GetString());

    await Page.EvaluateAsync("window.__shareMode = 'fail'");
    JsonElement failed = await ShareAsync("fit");
    Assert.Equal("Failed", failed.GetProperty("state").GetString());
    Assert.Contains("Use Download instead", failed.GetProperty("message").GetString(), StringComparison.Ordinal);
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task History_keeps_direct_downloads_and_shares_csv_and_fit_only_after_click()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    await Page.AddInitScriptAsync("""
      window.__historyShares = [];
      Object.defineProperty(Navigator.prototype, 'canShare', {
        configurable: true,
        value: data => Array.isArray(data.files) && data.files.length === 1
      });
      Object.defineProperty(Navigator.prototype, 'share', {
        configurable: true,
        value: async data => window.__historyShares.push({ name: data.files[0].name, type: data.files[0].type })
      });
      """);
    await scenario.InstallVisualDataRoutesAsync(Page);
    var requests = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase)
    {
      ["csv"] = 0,
      ["fit"] = 0,
    };
    await Page.RouteAsync($"**/api/history/{scenario.HistorySessionId:D}/export.*", async route =>
    {
      string extension = Path.GetExtension(new Uri(route.Request.Url).AbsolutePath).TrimStart('.');
      requests[extension]++;
      await route.FulfillAsync(new RouteFulfillOptions
      {
        Status = 200,
        ContentType = extension == "csv" ? "text/csv" : "application/vnd.ant.fit",
        Body = $"history-{extension}",
        Headers = new Dictionary<string, string>
        {
          ["Content-Disposition"] = $"attachment; filename=\"completed-session.{extension}\"",
        },
      });
    });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, $"/history/{scenario.HistorySessionId:D}").AbsoluteUri);
    await Expect(Page.GetByRole(AriaRole.Link, new() { Name = "Download CSV", Exact = true }))
      .ToHaveAttributeAsync("href", $"/api/history/{scenario.HistorySessionId:D}/export.csv");
    await Expect(Page.GetByRole(AriaRole.Link, new() { Name = "Download FIT Activity", Exact = true }))
      .ToHaveAttributeAsync("href", $"/api/history/{scenario.HistorySessionId:D}/export.fit");
    Assert.Equal(0, requests["csv"]);
    Assert.Equal(0, requests["fit"]);

    await Page.GetByRole(AriaRole.Button, new() { Name = "Share CSV", Exact = true }).ClickAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Share FIT Activity", Exact = true }).ClickAsync();
    Assert.Equal(1, requests["csv"]);
    Assert.Equal(1, requests["fit"]);
    JsonElement shares = await Page.EvaluateAsync<JsonElement>("window.__historyShares");
    Assert.Equal("completed-session.csv", shares[0].GetProperty("name").GetString());
    Assert.Equal("completed-session.fit", shares[1].GetProperty("name").GetString());
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Offline_worker_shows_only_safety_guidance_and_recovers_to_the_live_application()
  {
    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.EvaluateAsync("async () => await navigator.serviceWorker.ready");
    await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });
    Assert.True(await Page.EvaluateAsync<bool>("navigator.serviceWorker.controller !== null"));

    JsonElement cacheState = await Page.EvaluateAsync<JsonElement>("""
      async () => {
        const names = (await caches.keys()).filter(name => name.startsWith('treadmillrunner-offline-safety-'));
        const entries = names.length === 1 ? await (await caches.open(names[0])).keys() : [];
        return { names, entries: entries.map(request => new URL(request.url).pathname) };
      }
      """);
    Assert.Single(cacheState.GetProperty("names").EnumerateArray());
    Assert.Equal(["/offline.html"], cacheState.GetProperty("entries").EnumerateArray().Select(static item => item.GetString()!).ToArray());

    await Page.Context.SetOfflineAsync(true);
    try
    {
      await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.DOMContentLoaded });
      await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "TreadmillRunner gateway unavailable", Exact = true }))
        .ToBeVisibleAsync();
      await Expect(Page.GetByText("Wi-Fi or Bluetooth loss does not stop the treadmill belt", new() { Exact = false }))
        .ToBeVisibleAsync();
      await Expect(Page.GetByText("physical Stop control", new() { Exact = false })).ToBeVisibleAsync();
      await Expect(Page.GetByText("No previous session information is shown", new() { Exact = false })).ToBeVisibleAsync();

      bool allNetworkOnly = await Page.EvaluateAsync<bool>("""
        async () => {
          const paths = ['/api/system/version', '/hubs/live/negotiate?negotiateVersion=1', '/_framework/blazor.boot.json', '/api/history/00000000-0000-0000-0000-000000000000/export.csv'];
          const outcomes = await Promise.all(paths.map(path => fetch(path, { cache: 'no-store' }).then(() => false, () => true)));
          return outcomes.every(Boolean);
        }
        """);
      Assert.True(allNetworkOnly);
    }
    finally
    {
      await Page.Context.SetOfflineAsync(false);
    }

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/operations").AbsoluteUri);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Operations", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "TreadmillRunner gateway unavailable", Exact = true }))
      .ToHaveCountAsync(0);
  }

  private Task<JsonElement> ShareAsync(string extension) => Page.EvaluateAsync<JsonElement>(
    "async extension => await window.treadmillRunnerPwa.shareOrDownload(`/api/pwa-share-test/${extension}`, `fallback.${extension}`, 'Test share')",
    extension);
}
