using System.Text.Json;
using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;

namespace TreadmillRunner.E2ETests;

public sealed class PolarH10MemoryTests(GatewayFixture gateway) : PageTest, IClassFixture<GatewayFixture>
{
  [Fact]
  [Trait("Category", "Browser")]
  public async Task Per_run_h10_backup_is_available_but_unchecked_by_default()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    await Page.RouteAsync("**/api/polar-h10/status", route => route.FulfillAsync(new()
    {
      Status = 200,
      ContentType = "application/json",
      Body = "{\"memoryCapability\":true,\"isRecording\":false,\"connection\":\"Connected\"}",
    }));
    await Page.SetViewportSizeAsync(390, 844);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/").ToString());

    ILocator optIn = Page.Locator("#run-memory-checkbox");
    await Expect(optIn).ToBeVisibleAsync();
    await Expect(optIn).Not.ToBeCheckedAsync();
  }

  [Theory]
  [InlineData(390, 844)]
  [InlineData(844, 390)]
  [InlineData(1024, 768)]
  [InlineData(1920, 1080)]
  [Trait("Category", "Browser")]
  public async Task H10_memory_page_exposes_capability_gated_recording_and_verified_copy_delete(int width, int height)
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ConfigureBrowserAsync(Page);
    await Page.SetViewportSizeAsync(width, height);
    int startCalls = 0;
    int deleteCalls = 0;

    await Page.RouteAsync("**/api/polar-h10/status", route => route.FulfillAsync(new() { Status = 200, ContentType = "application/json", Body = "{\"memoryCapability\":true,\"isRecording\":false,\"connection\":\"Connected\",\"displayName\":\"Polar H10 A1B2C3D4\"}" }));
    await Page.RouteAsync("**/api/polar-h10/recordings?source=remote", route => route.FulfillAsync(new() { Status = 200, ContentType = "application/json", Body = "{\"items\":[{\"id\":\"remote-1\",\"title\":\"Morning run\",\"status\":\"Available\",\"startedAtUtc\":\"2026-09-10T06:00:00Z\",\"endedAtUtc\":\"2026-09-10T06:30:00Z\",\"canDeleteRemote\":false,\"samples\":[{\"capturedAtUtc\":\"2026-09-10T06:00:00Z\",\"beatsPerMinute\":132}]}]}" }));
    await Page.RouteAsync("**/api/polar-h10/recordings?source=local", route => route.FulfillAsync(new() { Status = 200, ContentType = "application/json", Body = "{\"items\":[{\"id\":\"11111111-1111-1111-1111-111111111111\",\"title\":\"Verified morning run\",\"status\":\"Retained\",\"canDeleteRemote\":true,\"samples\":[{\"capturedAtUtc\":\"2026-09-10T06:00:00Z\",\"beatsPerMinute\":132}]}]}" }));
    await Page.RouteAsync("**/api/polar-h10/recordings/start", async route => { startCalls++; await route.FulfillAsync(new() { Status = 202, ContentType = "application/json", Body = "{}" }); });
    await Page.RouteAsync("**/api/polar-h10/recordings/*/download", route => route.FulfillAsync(new() { Status = 202, ContentType = "application/json", Body = "{}" }));
    await Page.RouteAsync("**/api/polar-h10/recordings/*/delete-remote", async route => { deleteCalls++; await route.FulfillAsync(new() { Status = 204 }); });

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/devices/polar-h10-memory").ToString());
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "H10 memory", Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByRole(AriaRole.Checkbox, new() { Name = "Record Polar H10 memory for this run", Exact = true })).ToHaveCountAsync(0);
    await Expect(Page.GetByText("Remote H10", new() { Exact = true })).ToBeVisibleAsync();
    await Expect(Page.GetByLabel("Remote and local memory").GetByRole(AriaRole.Heading, new() { Name = "Morning run", Exact = true })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Start manual HR", Exact = true }).ClickAsync();
    Assert.Equal(1, startCalls);
    await Expect(Page.GetByText("Heart-rate recording start queued at 1s.", new() { Exact = true })).ToBeVisibleAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Verified local copies", Exact = true }).ClickAsync();
    ILocator local = Page.Locator(".polar-recording-card", new() { HasText = "Verified morning run" });
    await Expect(local).ToBeVisibleAsync();
    await local.GetByRole(AriaRole.Button, new() { Name = "Delete from H10", Exact = true }).ClickAsync();
    await Expect(Page.Locator("[role='alertdialog'][aria-label='Confirm remote recording deletion']")).ToContainTextAsync("verified local copy stays available");
    await Page.GetByRole(AriaRole.Button, new() { Name = "Confirm delete from H10", Exact = true }).ClickAsync();
    Assert.Equal(1, deleteCalls);
    Assert.False(await Page.EvaluateAsync<bool>("document.documentElement.scrollWidth > document.documentElement.clientWidth"));
  }
}
