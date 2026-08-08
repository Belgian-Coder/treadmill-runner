using System.Collections.Concurrent;
using System.Text.RegularExpressions;
using Microsoft.Playwright;
using Microsoft.Playwright.Xunit;

namespace TreadmillRunner.E2ETests;

public sealed class ScreenshotGalleryTests(GatewayFixture gateway) : PageTest, IClassFixture<GatewayFixture>
{
  public static TheoryData<string, string> Screens => new()
  {
    { "/", "run" },
    { "/control", "control" },
    { "/workouts", "workouts" },
    { "/workouts/new", "workout-editor" },
    { "/workouts/import", "workout-import" },
    { "/calendar", "calendar" },
    { "/history", "history" },
    { "/history/detail", "history-detail" },
    { "/devices", "devices" },
    { "/profiles", "profiles" },
    { "/operations", "operations" },
  };

  [Theory]
  [MemberData(nameof(Screens))]
  [Trait("Category", "Browser")]
  public async Task Every_screen_has_a_populated_current_root_gallery_image(string path, string fileName)
  {
    var browserErrors = new ConcurrentQueue<string>();
    Page.PageError += (_, error) => browserErrors.Enqueue(error);
    Page.Console += (_, message) =>
    {
      if (message.Type == "error")
      {
        browserErrors.Enqueue(message.Text);
      }
    };
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ResetSimulatorAsync(gateway.BaseAddress);
    await scenario.ConfigureBrowserAsync(Page);
    await scenario.InstallVisualDataRoutesAsync(Page);
    await Page.SetViewportSizeAsync(1180, 820);

    try
    {
      if (fileName == "control")
      {
        await PrepareActiveControlAsync(scenario);
      }
      else
      {
        string resolvedPath = ResolvePath(path, fileName, scenario);
        await Page.GotoAsync(new Uri(gateway.BaseAddress, resolvedPath).AbsoluteUri, new PageGotoOptions
        {
          WaitUntil = WaitUntilState.NetworkIdle,
        });
        try
        {
          await PreparePopulatedScreenAsync(fileName, scenario);
        }
        catch (Exception exception)
        {
          throw new InvalidOperationException(
            $"Could not prepare the populated {fileName} screen. Browser errors: {string.Join(" | ", browserErrors)}", exception);
        }
      }

      ILocator pageHeading = Page.Locator("h1").First;
      try
      {
        await pageHeading.WaitForAsync(new LocatorWaitForOptions { State = WaitForSelectorState.Visible, Timeout = 5_000 });
      }
      catch (TimeoutException)
      {
        string pageText = await Page.Locator("body").InnerTextAsync();
        Assert.Fail($"The {fileName} gallery page did not render a heading. Browser errors: {string.Join(" | ", browserErrors)}. Body: {pageText}");
      }
      await AssertPopulatedAsync(fileName, scenario);
      await AssertNoHorizontalOverflowAsync(fileName, "desktop");

      string galleryDirectory = Path.Combine(gateway.ProjectRoot, "screenshots");
      Directory.CreateDirectory(galleryDirectory);
      await Page.EvaluateAsync("""
        () => {
          window.scrollTo(0, 0);
          document.activeElement?.blur();
          const skip = document.querySelector('.skip-link');
          if (skip) skip.style.setProperty('display', 'none', 'important');
          const header = document.querySelector('.site-header');
          if (header) {
            header.setAttribute('data-scroll-state', 'shown');
            header.style.setProperty('position', 'static', 'important');
            header.style.setProperty('transform', 'none', 'important');
          }
        }
        """);
      await Page.AddStyleTagAsync(new PageAddStyleTagOptions
      {
        Content = ".site-header{position:static!important;transform:none!important}.skip-link{display:none!important}",
      });
      await Page.WaitForTimeoutAsync(250);
      await Page.EvaluateAsync("() => window.scrollTo({ top: 0, left: 0, behavior: 'instant' })");
      await Page.WaitForTimeoutAsync(50);
      Assert.InRange(await Page.EvaluateAsync<double>("() => window.scrollY"), 0, 1);
      LocatorBoundingBoxResult? galleryHeader = await Page.Locator(".site-header").BoundingBoxAsync();
      Assert.NotNull(galleryHeader);
      Assert.InRange(galleryHeader.Y, 0, 1);
      await Page.ScreenshotAsync(new PageScreenshotOptions
      {
        Path = Path.Combine(galleryDirectory, $"{fileName}.png"),
        FullPage = true,
      });
      if (fileName == "profiles")
      {
        await Page.ScreenshotAsync(new PageScreenshotOptions
        {
          Path = Path.Combine(galleryDirectory, "profiles-garmin-tablet.png"),
          FullPage = true,
        });
      }

      if (fileName == "calendar")
      {
        await Page.GotoAsync(new Uri(gateway.BaseAddress, "/calendar").AbsoluteUri, new PageGotoOptions
        {
          WaitUntil = WaitUntilState.NetworkIdle,
        });
        await PreparePopulatedScreenAsync(fileName, scenario);
        await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Plan training", Exact = true })).ToHaveCountAsync(0);
        await Expect(Page.GetByText("Review scheduled training", new() { Exact = false })).ToBeVisibleAsync();
        await Page.ScreenshotAsync(new PageScreenshotOptions
        {
          Path = Path.Combine(galleryDirectory, "calendar-schedules-tablet.png"),
          FullPage = true,
        });
        ILocator manage = await OpenCalendarManagerAsync();
        await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Close schedule manager", Exact = true })).ToBeFocusedAsync();
        Assert.Equal("hidden", await Page.EvaluateAsync<string>("() => document.documentElement.style.overflow"));
        LocatorBoundingBoxResult? backdrop = await Page.Locator("#schedule-dialog-backdrop").BoundingBoxAsync();
        Assert.NotNull(backdrop);
        Assert.InRange(backdrop.Width, 1179, 1181);
        Assert.InRange(backdrop.Height, 819, 821);
        await Page.Keyboard.PressAsync("Escape");
        await Expect(Page.GetByRole(AriaRole.Dialog)).ToBeHiddenAsync();
        await Expect(manage).ToBeFocusedAsync();
        Assert.NotEqual("hidden", await Page.EvaluateAsync<string>("() => document.documentElement.style.overflow"));
        await OpenCalendarManagerAsync();
        await Page.GetByRole(AriaRole.Button, new() { Name = "Delete from schedule", Exact = true }).ClickAsync();
        await Page.GetByRole(AriaRole.Button, new() { Name = "Complete workout group", Exact = true }).ClickAsync();
        await Expect(Page.GetByText("Completed runs stay in history.", new() { Exact = false })).ToBeVisibleAsync();
        await Page.ScreenshotAsync(new PageScreenshotOptions
        {
          Path = Path.Combine(galleryDirectory, "calendar-delete-group-tablet.png"),
          FullPage = false,
        });
        await Page.GetByRole(AriaRole.Button, new() { Name = "Close schedule manager", Exact = true }).ClickAsync();
      }

      await Page.SetViewportSizeAsync(440, 956);
      await Page.EvaluateAsync("() => { window.scrollTo(0, 0); document.activeElement?.blur(); }");
      await Page.WaitForTimeoutAsync(100);
      await AssertNoHorizontalOverflowAsync(fileName, "iPhone 17 Pro Max");
      await AssertPhonePresentationAsync(fileName);
      if (fileName is "control" or "history-detail")
      {
        await AssertTimeAxisLabelsDoNotOverlapAsync($"{fileName} iPhone 17 Pro Max");
      }
      await Page.ScreenshotAsync(new PageScreenshotOptions
      {
        Path = Path.Combine(galleryDirectory, $"{fileName}-iphone17-pro-max.png"),
        FullPage = true,
      });
      if (fileName == "profiles")
      {
        await Page.ScreenshotAsync(new PageScreenshotOptions
        {
          Path = Path.Combine(galleryDirectory, "profiles-garmin-iphone17-pro-max.png"),
          FullPage = true,
        });
      }
      if (fileName == "calendar")
      {
        await Page.GotoAsync(new Uri(gateway.BaseAddress, "/calendar").AbsoluteUri, new PageGotoOptions
        {
          WaitUntil = WaitUntilState.NetworkIdle,
        });
        await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Plan training", Exact = true })).ToHaveCountAsync(0);
        await AssertNoHorizontalOverflowAsync(fileName, "iPhone calendar management view");
        int moveMutationRequests = 0;
        Page.Request += (_, request) =>
        {
          if (request.Method == "POST" && request.Url.Contains("/occurrences/", StringComparison.Ordinal) &&
              request.Url.EndsWith("/move", StringComparison.Ordinal))
          {
            Interlocked.Increment(ref moveMutationRequests);
          }
        };
        await OpenCalendarManagerAsync();
        await AdvanceCalendarMoveToScopeAsync();
        Assert.Equal(0, Volatile.Read(ref moveMutationRequests));
        await AssertNoHorizontalOverflowAsync(fileName, "open iPhone schedule dialog");
        foreach (ILocator target in new[]
        {
          Page.GetByRole(AriaRole.Button, new() { Name = "Only this session", Exact = true }),
          Page.GetByRole(AriaRole.Button, new() { Name = "This and all later sessions", Exact = true }),
          Page.GetByRole(AriaRole.Button, new() { Name = "Change date", Exact = true }),
        })
        {
          LocatorBoundingBoxResult? box = await target.BoundingBoxAsync();
          Assert.NotNull(box);
          Assert.True(box.Width >= 44 && box.Height >= 44, $"Schedule dialog touch target is too small: {box}");
        }
        await Page.ScreenshotAsync(new PageScreenshotOptions
        {
          Path = Path.Combine(galleryDirectory, "calendar-move-scope-iphone17-pro-max.png"),
          FullPage = false,
        });
        Assert.Equal(0, Volatile.Read(ref moveMutationRequests));
        await Page.GetByRole(AriaRole.Button, new() { Name = "This and all later sessions", Exact = true }).ClickAsync();
        await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Move this and every later session?", Exact = true })).ToBeVisibleAsync();
        Assert.Equal(0, Volatile.Read(ref moveMutationRequests));
        await Page.GetByRole(AriaRole.Button, new() { Name = "Back", Exact = true }).ClickAsync();
        await Page.GetByRole(AriaRole.Button, new() { Name = "Only this session", Exact = true }).ClickAsync();
        await Expect(Page.GetByRole(AriaRole.Dialog)).ToBeHiddenAsync();
        Assert.Equal(1, Volatile.Read(ref moveMutationRequests));
      }
      else if (fileName == "workout-editor")
      {
        await Page.GetByRole(AriaRole.Button, new() { Name = "Intervals", Exact = true }).ClickAsync();
        await Expect(Page.GetByText("4× repeated sequence · 2 blocks", new() { Exact = true })).ToBeVisibleAsync();
        await Page.GetByRole(AriaRole.Button, new() { Name = "Duplicate", Exact = true }).First.ClickAsync();
        await Page.EvaluateAsync("() => { window.scrollTo(0, 0); document.activeElement?.blur(); }");
        await Page.WaitForTimeoutAsync(100);
        await AssertNoHorizontalOverflowAsync(fileName, "edited iPhone workout");
        await Page.ScreenshotAsync(new PageScreenshotOptions
        {
          Path = Path.Combine(galleryDirectory, "workout-editor-building-iphone17-pro-max.png"),
          FullPage = true,
        });
        await Page.GetByLabel("Workout name", new() { Exact = true }).FillAsync("Recovered progressive intervals");
        await Page.WaitForTimeoutAsync(650);
        await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });
        await Expect(Page.GetByText("Unfinished workout found.", new() { Exact = true })).ToBeVisibleAsync();
        await Page.GetByRole(AriaRole.Button, new() { Name = "Continue draft", Exact = true }).ClickAsync();
        await Expect(Page.GetByLabel("Workout name", new() { Exact = true })).ToHaveValueAsync("Recovered progressive intervals");
        await Page.ScreenshotAsync(new PageScreenshotOptions
        {
          Path = Path.Combine(galleryDirectory, "workout-editor-restored-draft-iphone17-pro-max.png"),
          FullPage = true,
        });
      }
      else if (fileName == "workout-import")
      {
        await Page.GetByRole(AriaRole.Button, new() { Name = "Generated set", Exact = true }).ClickAsync();
        await Page.GetByLabel("Generated treadmill-workout v4 bundle", new() { Exact = true })
          .SetInputFilesAsync(GalleryScenario.GeneratedSetFile());
        await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "First 5K · six-week builder", Exact = true }))
          .ToBeVisibleAsync();
        await Expect(Page.GetByText("v4.2.0", new() { Exact = true })).ToBeVisibleAsync();
        await Expect(Page.Locator("body")).Not.ToContainTextAsync("@setPreview");
        await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Import 18 workouts and create plan", Exact = true }))
          .ToBeEnabledAsync();
        await AssertNoHorizontalOverflowAsync(fileName, "generated set iPhone");
        await Page.ScreenshotAsync(new PageScreenshotOptions
        {
          Path = Path.Combine(galleryDirectory, "workout-set-import-iphone17-pro-max.png"),
          FullPage = true,
        });
      }
      else if (fileName == "history")
      {
        await Page.GetByLabel("Search history", new() { Exact = true }).FillAsync("Recovery");
        await Expect(Page.Locator(".history-card")).ToHaveCountAsync(1);
        await Expect(Page.Locator(".history-card")).ToContainTextAsync("4.9 km/h");
        await Page.ScreenshotAsync(new PageScreenshotOptions
        {
          Path = Path.Combine(galleryDirectory, "history-filtered-iphone17-pro-max.png"),
          FullPage = true,
        });
        await Page.GetByLabel("Search history", new() { Exact = true }).FillAsync(string.Empty);
        ILocator statusFilter = Page.Locator(".history-toolbar select").Nth(0);
        ILocator periodFilter = Page.Locator(".history-toolbar select").Nth(1);
        await periodFilter.SelectOptionAsync("all");
        await Expect(Page.Locator(".history-card")).ToHaveCountAsync(4);
        await statusFilter.SelectOptionAsync("Interrupted");
        await Expect(Page.Locator(".history-card")).ToHaveCountAsync(1);
        await periodFilter.SelectOptionAsync("30");
        await Expect(Page.Locator(".history-card")).ToHaveCountAsync(0);
        await Page.GetByLabel("Filter running history", new() { Exact = true })
          .GetByRole(AriaRole.Button, new() { Name = "Clear filters", Exact = true }).ClickAsync();
        await Expect(Page.Locator(".history-card")).ToHaveCountAsync(3);
        await Page.GetByRole(AriaRole.Button, new() { Name = "Tests", Exact = true }).ClickAsync();
        await Expect(Page.Locator(".history-card")).ToHaveCountAsync(1);
        await Expect(Page.Locator(".history-card")).ToContainTextAsync("System test");
        await Expect(Page.Locator(".weekly-summary-panel")).ToHaveCountAsync(0);
        await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Tests", Exact = true })).ToHaveAttributeAsync("aria-pressed", "true");
        await Page.ScreenshotAsync(new PageScreenshotOptions
        {
          Path = Path.Combine(galleryDirectory, "history-tests-iphone17-pro-max.png"),
          FullPage = true,
        });
      }
      else if (fileName == "history-detail")
      {
        await Page.GetByRole(AriaRole.Button, new() { Name = "Delete local session", Exact = true }).ClickAsync();
        await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Permanent deletion preview", Exact = true })).ToBeVisibleAsync();
        foreach (ILocator action in new[]
        {
          Page.GetByRole(AriaRole.Button, new() { Name = "Delete local session", Exact = true }),
          Page.GetByRole(AriaRole.Button, new() { Name = "Permanently delete", Exact = true }),
        })
        {
          LocatorBoundingBoxResult? box = await action.BoundingBoxAsync();
          Assert.NotNull(box);
          Assert.True(box.Width >= 44 && box.Height >= 44, $"Deletion touch target is too small: {box}");
        }
        await Page.ScreenshotAsync(new PageScreenshotOptions
        {
          Path = Path.Combine(galleryDirectory, "history-deletion-preview-iphone17-pro-max.png"),
          FullPage = true,
        });
      }
      else if (fileName == "devices")
      {
        await scenario.InstallMaintenanceSetupRouteAsync(Page);
        await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });
        await Expect(Page.GetByText("Record the last belt/deck service", new() { Exact = false })).ToBeVisibleAsync();
        await Page.ScreenshotAsync(new PageScreenshotOptions
        {
          Path = Path.Combine(galleryDirectory, "devices-maintenance-setup-iphone17-pro-max.png"),
          FullPage = true,
        });
      }
      else if (fileName == "workouts")
      {
        int programStartRequests = 0;
        Page.Request += (_, request) =>
        {
          if (request.Method == "POST" && request.Url.Contains("/api/planning/programs/", StringComparison.Ordinal) &&
              request.Url.EndsWith("/start", StringComparison.Ordinal))
          {
            Interlocked.Increment(ref programStartRequests);
          }
        };
        await Page.GetByRole(AriaRole.Button, new() { Name = "Start plan", Exact = true }).ClickAsync();
        await Expect(Page.GetByRole(AriaRole.Alertdialog)).ToBeVisibleAsync();
        await Expect(Page.GetByRole(AriaRole.Alertdialog)).ToContainTextAsync("will be abandoned");
        Assert.Equal(0, Volatile.Read(ref programStartRequests));
        await Page.GetByRole(AriaRole.Button, new() { Name = "Keep for later", Exact = true }).ClickAsync();
        await Expect(Page.GetByRole(AriaRole.Alertdialog)).ToBeHiddenAsync();
        Assert.Equal(0, Volatile.Read(ref programStartRequests));
        await Page.Locator(".program-card__select").First.ClickAsync();
        ILocator planDialog = Page.GetByRole(AriaRole.Dialog);
        await Expect(planDialog.GetByRole(AriaRole.Heading, new() { Level = 2 })).ToBeVisibleAsync();
        await Expect(planDialog.GetByRole(AriaRole.Heading, new() { Name = "Plan sessions", Exact = true })).ToBeVisibleAsync();
        await Expect(planDialog.Locator(".program-session-summary-list li")).ToHaveCountAsync(3);
        await AssertNoHorizontalOverflowAsync(fileName, "open iPhone training-plan details");
        await Page.ScreenshotAsync(new PageScreenshotOptions
        {
          Path = Path.Combine(galleryDirectory, "workouts-plan-details-iphone17-pro-max.png"),
          FullPage = true,
        });
      }
    }
    finally
    {
      await scenario.ResetSimulatorAsync(gateway.BaseAddress);
    }
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Phone_shell_captures_installed_update_and_landscape_states()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ResetSimulatorAsync(gateway.BaseAddress);
    await scenario.ConfigureBrowserAsync(Page);
    await scenario.InstallVisualDataRoutesAsync(Page);
    await Page.AddInitScriptAsync("Object.defineProperty(navigator, 'standalone', { value: true, configurable: true });");
    await Page.SetViewportSizeAsync(440, 956);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await SelectFeaturedRunAsync();
    await Expect(Page.Locator("html")).ToHaveClassAsync(new Regex("standalone-shell"));
    string galleryDirectory = Path.Combine(gateway.ProjectRoot, "screenshots");
    Directory.CreateDirectory(galleryDirectory);
    await Page.ScreenshotAsync(new PageScreenshotOptions { Path = Path.Combine(galleryDirectory, "run-installed-iphone17-pro-max.png"), FullPage = true });
    await Page.ScreenshotAsync(new PageScreenshotOptions { Path = Path.Combine(galleryDirectory, "run-installed-iphone17-pro-max-viewport.png"), FullPage = false });

    await Page.SetViewportSizeAsync(956, 440);
    await Page.ScreenshotAsync(new PageScreenshotOptions { Path = Path.Combine(galleryDirectory, "run-installed-iphone17-pro-max-landscape.png"), FullPage = true });
    await Page.ScreenshotAsync(new PageScreenshotOptions { Path = Path.Combine(galleryDirectory, "run-installed-iphone17-pro-max-landscape-viewport.png"), FullPage = false });

    await Page.RouteAsync("**/api/system/version*", route => route.FulfillAsync(new RouteFulfillOptions
    {
      Status = 200,
      ContentType = "application/json",
      Headers = new Dictionary<string, string> { ["Cache-Control"] = "no-store" },
      Body = "{\"releaseVersion\":\"9.9.9\",\"buildFingerprint\":\"new-build-ready\",\"serviceStartedAtUtc\":\"2026-08-06T10:00:00Z\"}",
    }));
    await Page.SetViewportSizeAsync(440, 956);
    await Page.ReloadAsync(new PageReloadOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Expect(Page.GetByText("Update ready", new() { Exact = true })).ToBeVisibleAsync();
    await Page.ScreenshotAsync(new PageScreenshotOptions { Path = Path.Combine(galleryDirectory, "update-ready-iphone17-pro-max.png"), FullPage = true });
  }

  private async Task<ILocator> OpenCalendarManagerAsync()
  {
    ILocator manage = Page.Locator(".calendar-agenda-week[open]")
      .GetByRole(AriaRole.Button, new() { NameRegex = new Regex("^Manage .+ on ") }).First;
    LocatorBoundingBoxResult? box = await manage.BoundingBoxAsync();
    Assert.NotNull(box);
    Assert.True(box.Width >= 44 && box.Height >= 44, $"Calendar Manage touch target is too small: {box}");
    await manage.ClickAsync();
    await Expect(Page.GetByRole(AriaRole.Dialog)).ToBeVisibleAsync();
    return manage;
  }

  private async Task AdvanceCalendarMoveToScopeAsync()
  {
    await Page.GetByRole(AriaRole.Button, new() { Name = "Move session", Exact = true }).ClickAsync();
    await Expect(Page.Locator("#schedule-dialog-stage")).ToBeFocusedAsync();
    ILocator date = Page.GetByLabel("Move to", new() { Exact = true });
    DateOnly current = DateOnly.ParseExact(await date.InputValueAsync(), "yyyy-MM-dd");
    await date.FillAsync(current.AddDays(2).ToString("yyyy-MM-dd"));
    await Page.GetByRole(AriaRole.Button, new() { Name = "Continue", Exact = true }).ClickAsync();
    await Expect(Page.Locator("#schedule-dialog-stage")).ToBeFocusedAsync();
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Should later sessions move too?", Exact = true })).ToBeVisibleAsync();
  }

  private async Task AssertPhonePresentationAsync(string fileName)
  {
    if (fileName == "control")
    {
      await Expect(Page.Locator(".global-hr-status")).ToHaveClassAsync(new Regex("global-hr-status--ready"));
      await Expect(Page.Locator(".global-hr-status .heart-sensor-slash")).ToHaveCountAsync(0);
      await Expect(Page.Locator(".global-hr-status .heart-device-icon")).ToHaveCountAsync(1);
      return;
    }

    if (fileName != "workouts") return;

    ILocator cards = Page.Locator(".program-card");
    Assert.True(await cards.CountAsync() >= 2, "Phone training-plan gallery must contain at least two cards.");
    LocatorBoundingBoxResult? first = await cards.Nth(0).BoundingBoxAsync();
    LocatorBoundingBoxResult? second = await cards.Nth(1).BoundingBoxAsync();
    Assert.NotNull(first);
    Assert.NotNull(second);
    Assert.True(second.Y >= first.Y + first.Height,
      $"Phone training-plan cards must stack vertically without clipping (first={first}, second={second}).");
  }

  private static string ResolvePath(string path, string fileName, GalleryScenario scenario) => fileName switch
  {
    "workout-editor" => $"/workouts/new?workoutId={scenario.FeaturedWorkoutId:D}",
    "history-detail" => $"/history/{scenario.HistorySessionId:D}",
    "profiles" => "/profiles",
    _ => path,
  };

  private async Task PreparePopulatedScreenAsync(string fileName, GalleryScenario scenario)
  {
    switch (fileName)
    {
      case "run":
        await SelectFeaturedRunAsync();
        break;
      case "workout-import":
        await Page.Locator("input[type=file]").SetInputFilesAsync(GalleryScenario.ImportFile());
        await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Imported 5K tempo preview", Exact = true }))
          .ToBeVisibleAsync();
        break;
      case "profiles":
        await Page.Locator(".profile-row").Filter(new() { HasText = "Marc" })
          .GetByRole(AriaRole.Button, new() { Name = "Edit", Exact = true }).ClickAsync();
        await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Garmin Connect", Exact = true })).ToHaveCountAsync(0);
        await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Display and cues", Exact = true })).ToHaveCountAsync(0);
        await Expect(Page.GetByText("Personal local goal", new() { Exact = true })).ToHaveCountAsync(0);
        await Expect(Page.GetByRole(AriaRole.Region, new() { Name = "Garmin activity upload", Exact = true })).ToContainTextAsync("Experimental");
        await Expect(Page.GetByRole(AriaRole.Region, new() { Name = "Connect IQ watch app", Exact = true })).ToBeVisibleAsync();
        break;
      case "workouts":
        await Page.GetByRole(AriaRole.Button, new() { Name = "My training plans", Exact = true }).ClickAsync();
        await Expect(Page.Locator(".program-card").Nth(1)).ToBeVisibleAsync();
        break;
      case "history-detail":
        ILocator heartRateZones = Page.GetByText("Time in heart-rate zones", new() { Exact = true });
        try
        {
          await heartRateZones.ClickAsync(new LocatorClickOptions { Timeout = 5_000 });
        }
        catch (TimeoutException exception)
        {
          throw new InvalidOperationException(
            $"History detail did not become interactive. Body: {await Page.Locator("body").InnerTextAsync()}", exception);
        }
        await Page.GetByText("Session events", new() { Exact = true }).ClickAsync();
        await Page.GetByText("Runner debrief", new() { Exact = true }).ClickAsync();
        break;
      case "devices":
        await Page.GetByText("Bluetooth reliability report", new() { Exact = true }).ClickAsync();
        await Expect(Page.GetByText("2 outages", new() { Exact = false })).ToBeVisibleAsync();
        break;
    }
  }

  private async Task PrepareActiveControlAsync(GalleryScenario scenario)
  {
    await Page.GotoAsync(gateway.BaseAddress.AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await SelectFeaturedRunAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = "Prepare run", Exact = true }).ClickAsync();
    await Expect(Page).ToHaveURLAsync(new Regex("/control$"));
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Ready at the treadmill", Exact = true }))
      .ToBeVisibleAsync();
    await scenario.SetPhysicalMotionAsync(gateway.BaseAddress, 4.5, 0.5);
    await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Live run", Exact = true })).ToBeVisibleAsync();
    foreach ((double speed, double incline) in new[] { (5.5, 0.5), (7.0, 1.0), (6.0, 0.5), (7.5, 1.5) })
    {
      await scenario.SetPhysicalMotionAsync(gateway.BaseAddress, speed, incline);
      await Page.WaitForTimeoutAsync(350);
    }
  }

  private async Task SelectFeaturedRunAsync()
  {
    await Page.SelectActiveRunnerAsync("Marc");
    await Page.OpenRunChoicesAsync();
    await Page.GetByRole(AriaRole.Button, new() { Name = GalleryScenario.FeaturedWorkoutName, Exact = false }).First.ClickAsync();
    ILocator selectedWorkout = Page.GetByLabel("Selected workout", new() { Exact = true });
    await Expect(Page.GetByLabel("Selected runner", new() { Exact = true })).ToHaveTextAsync("Marc");
    await Expect(selectedWorkout)
      .ToContainTextAsync(GalleryScenario.FeaturedWorkoutName);
  }

  private async Task AssertPopulatedAsync(string fileName, GalleryScenario scenario)
  {
    switch (fileName)
    {
      case "run":
        await Expect(Page.GetByLabel("Selected runner", new() { Exact = true })).ToHaveTextAsync("Marc");
        await Expect(Page.GetByLabel("Selected workout", new() { Exact = true }))
          .ToHaveTextAsync(GalleryScenario.FeaturedWorkoutName);
        await Expect(Page.Locator(".readiness-list li")).Not.ToHaveCountAsync(0);
        await Expect(Page.GetByText("Run a recent workout again", new() { Exact = true }).First).ToBeVisibleAsync();
        break;
      case "control":
        await Expect(Page.GetByLabel("Heart rate")).ToContainTextAsync("132");
        await Expect(Page.GetByLabel("Measured speed", new() { Exact = true })).ToContainTextAsync("4.5");
        await Expect(Page.GetByLabel("Live workout metrics", new() { Exact = true }).Locator("article")).ToHaveCountAsync(3);
        await Expect(Page.Locator(".control-rail--incline")).ToContainTextAsync("0.5");
        await Expect(Page.GetByLabel("Live speed in kilometers per hour and incline percentage over elapsed time", new() { Exact = true })).ToBeVisibleAsync();
        await Expect(Page.GetByLabel("Elapsed time axis", new() { Exact = true })).ToContainTextAsync("0:00");
        ILocator liveSpeedAxis = Page.GetByLabel("Speed axis in kilometers per hour", new() { Exact = true });
        await Expect(liveSpeedAxis.Locator("span")).ToHaveCountAsync(10);
        await Expect(liveSpeedAxis.Locator("span").First).ToHaveTextAsync("10");
        await Expect(liveSpeedAxis.Locator("span").Last).ToHaveTextAsync("1");
        ILocator liveInclineAxis = Page.GetByLabel("Incline axis in percent", new() { Exact = true });
        await Expect(liveInclineAxis.Locator("span")).ToHaveCountAsync(10);
        await Expect(liveInclineAxis.Locator("span").First).ToHaveTextAsync("10");
        await Expect(liveInclineAxis.Locator("span").Last).ToHaveTextAsync("1");
        string plannedPath = await Page.Locator("[data-series='planned-speed']").GetAttributeAsync("d") ?? string.Empty;
        Assert.True(plannedPath.Count(character => character == 'L') >= 5, "Control gallery plan must contain the full interval workout.");
        await Expect(Page.Locator("[data-series='measured-speed']")).ToHaveAttributeAsync("d", new Regex("^M"));
        await AssertTimeAxisLabelsDoNotOverlapAsync("control desktop");
        break;
      case "workouts":
        Assert.True(await Page.Locator(".program-card").CountAsync() >= 2, "Workout gallery must show populated training plans.");
        await Expect(Page.GetByText("First 5K", new() { Exact = true })).ToBeVisibleAsync();
        await Expect(Page.GetByText("Stronger 10K", new() { Exact = true })).ToBeVisibleAsync();
        await Expect(Page.Locator(".program-card").Filter(new() { HasText = "First 5K" }))
          .ToContainTextAsync("0 complete · 3 remaining");
        break;
      case "workout-editor":
        await Expect(Page.GetByLabel("Workout name", new() { Exact = true })).ToHaveValueAsync(GalleryScenario.FeaturedWorkoutName);
        Assert.True(await Page.Locator(".step-card").CountAsync() >= 5, "Workout editor gallery must contain the complete plan.");
        await Expect(Page.GetByText("Start from an existing workout", new() { Exact = true })).ToBeVisibleAsync();
        break;
      case "workout-import":
        await Expect(Page.Locator(".preview-card")).ToContainTextAsync("3");
        await Expect(Page.Locator(".preview-card")).ToContainTextAsync("21 min");
        await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Confirm and save workout", Exact = true })).ToBeEnabledAsync();
        break;
      case "calendar":
        await Expect(Page.Locator(".calendar-agenda-week[open] .calendar-option").First).ToBeVisibleAsync(new LocatorAssertionsToBeVisibleOptions { Timeout = 10_000 });
        Assert.True(await Page.Locator(".calendar-option").CountAsync() >= 6, "Calendar gallery must contain planned workouts.");
        Assert.True(await Page.Locator(".schedule-group-card").CountAsync() >= 2, "Calendar gallery must contain logical workout groups.");
        await Expect(Page.Locator(".calendar-agenda")).ToBeVisibleAsync();
        await Expect(Page.Locator(".calendar-agenda-week[open]").GetByText(GalleryScenario.FeaturedWorkoutName, new() { Exact = true }).First).ToBeVisibleAsync();
        break;
      case "history":
        await Expect(Page.GetByLabel("This week's completed running", new() { Exact = true })).ToContainTextAsync("9.64 km");
        Assert.Equal(3, await Page.Locator(".history-card").CountAsync());
        await Expect(Page.GetByText("5.02 km", new() { Exact = true })).ToBeVisibleAsync();
        break;
      case "history-detail":
        await Expect(Page.GetByText("5.02 km", new() { Exact = true })).ToBeVisibleAsync();
        await Expect(Page.GetByLabel("Elapsed time axis", new() { Exact = true })).ToContainTextAsync("34:12");
        ILocator historySpeedAxis = Page.GetByLabel("Speed axis in kilometers per hour", new() { Exact = true });
        Assert.True(await historySpeedAxis.Locator("span").CountAsync() >= 10);
        await Expect(historySpeedAxis.Locator("span").First).ToHaveTextAsync(new Regex("^\\d+$"));
        await Expect(Page.GetByText("Zone 3 · Aerobic", new() { Exact = true })).ToBeVisibleAsync();
        await Expect(Page.GetByText("Manual speed override:", new() { Exact = false })).ToBeVisibleAsync();
        await Expect(Page.GetByText("Comfortable progression; held form", new() { Exact = false })).ToBeVisibleAsync();
        Assert.True((await Page.Locator(".chart-current").GetAttributeAsync("d"))?.Count(character => character == 'L') >= 8);
        await AssertTimeAxisLabelsDoNotOverlapAsync("history detail desktop");
        break;
      case "devices":
        await Expect(Page.Locator(".device-card h2").Filter(new() { HasText = "Horizon Omega Z" })).ToBeVisibleAsync();
        await Expect(Page.Locator(".device-card h2").Filter(new() { HasText = "Polar H10 A1B2C3D4" })).ToBeVisibleAsync();
        await Expect(Page.Locator(".device-card h2").Filter(new() { HasText = "Garmin Fenix 8 HR Broadcast" })).ToBeVisibleAsync();
        await Expect(Page.GetByText("143 bpm", new() { Exact = true })).ToBeVisibleAsync();
        await Expect(Page.GetByText("Preferred", new() { Exact = true })).ToBeVisibleAsync();
        await Expect(Page.GetByText("86%", new() { Exact = false }).First).ToBeVisibleAsync();
        ILocator connectActions = Page.GetByRole(AriaRole.Button, new() { Name = "Connect / retry", Exact = true });
        ILocator disconnectActions = Page.GetByRole(AriaRole.Button, new() { Name = "Disconnect", Exact = true });
        await Expect(connectActions).ToHaveCountAsync(3);
        await Expect(disconnectActions).ToHaveCountAsync(3);
        for (int index = 0; index < 3; index++)
        {
          await Expect(connectActions.Nth(index)).ToBeVisibleAsync();
          await Expect(disconnectActions.Nth(index)).ToBeVisibleAsync();
          LocatorBoundingBoxResult? connectBox = await connectActions.Nth(index).BoundingBoxAsync();
          LocatorBoundingBoxResult? disconnectBox = await disconnectActions.Nth(index).BoundingBoxAsync();
          Assert.NotNull(connectBox);
          Assert.NotNull(disconnectBox);
          Assert.True(connectBox.Height >= 44);
          Assert.True(disconnectBox.Height >= 44);
        }
        await Expect(Page.GetByText("Bluetooth reliability report", new() { Exact = true })).ToBeVisibleAsync();
        break;
      case "profiles":
        Assert.Equal(2, await Page.Locator(".profile-row").CountAsync());
        await Expect(Page.Locator(".profile-row--active")).ToContainTextAsync("Marc");
        await Expect(Page.GetByLabel("Maximum heart rate", new() { Exact = true })).ToHaveValueAsync("206");
        ILocator advancedSections = Page.Locator(".advanced-settings");
        await Expect(advancedSections).ToHaveCountAsync(1);
        await Expect(advancedSections.Nth(0)).Not.ToHaveAttributeAsync("open", "");
        await Expect(Page.GetByRole(AriaRole.Region, new() { Name = "Garmin activity upload", Exact = true })).ToContainTextAsync("disabled by default");
        await Expect(Page.GetByRole(AriaRole.Region, new() { Name = "Connect IQ watch app", Exact = true })).ToContainTextAsync("never starts the treadmill");
        break;
      case "operations":
        await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Open on another device", Exact = true })).ToBeVisibleAsync();
        await Expect(Page.Locator(".app-access-qr")).ToBeVisibleAsync();
        await Expect(Page.GetByLabel("Update status", new() { Exact = true })).ToContainTextAsync("Available");
        await Expect(Page.GetByLabel("Update status", new() { Exact = true })).ToContainTextAsync("1.5.5");
        await Expect(Page.GetByRole(AriaRole.Button, new() { Name = "Verify and stage", Exact = true })).ToBeVisibleAsync();
        await Expect(Page.GetByRole(AriaRole.Heading, new() { Name = "Database health", Exact = true })).ToBeVisibleAsync();
        ILocator databasePanel = Page.GetByRole(AriaRole.Region, new() { Name = "Database health", Exact = true });
        await Expect(databasePanel.GetByRole(AriaRole.Status)).ToContainTextAsync("verified last-known-good backup");
        break;
      default:
        throw new ArgumentOutOfRangeException(nameof(fileName), fileName, "Unknown gallery screen.");
    }
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Workout_library_and_details_have_populated_showcase_images()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ResetSimulatorAsync(gateway.BaseAddress);
    await scenario.ConfigureBrowserAsync(Page);
    await scenario.InstallVisualDataRoutesAsync(Page);
    string directory = Path.Combine(gateway.ProjectRoot, "screenshots", "showcase");
    Directory.CreateDirectory(directory);

    await Page.SetViewportSizeAsync(1440, 1000);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/workouts").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    await Page.GetByRole(AriaRole.Button, new() { Name = "Standalone workouts", Exact = true }).ClickAsync();
    ILocator featured = Page.Locator(".workout-card").Filter(new() { HasText = GalleryScenario.FeaturedWorkoutName });
    await Expect(featured).ToBeVisibleAsync();
    await Expect(featured.GetByText("Intervals", new() { Exact = true })).ToBeVisibleAsync();
    await Page.ScreenshotAsync(new PageScreenshotOptions { Path = Path.Combine(directory, "tr-026-workout-library.png"), FullPage = true });

    await featured.GetByRole(AriaRole.Button, new() { Name = "View details", Exact = true }).ClickAsync();
    ILocator dialog = Page.GetByRole(AriaRole.Dialog);
    await Expect(dialog).ToBeVisibleAsync();
    await Expect(dialog.GetByRole(AriaRole.Heading, new() { Name = "Workout structure", Exact = true })).ToBeVisibleAsync();
    await Page.ScreenshotAsync(new PageScreenshotOptions { Path = Path.Combine(directory, "tr-026-workout-details.png"), FullPage = false });

    await Page.SetViewportSizeAsync(440, 956);
    await AssertNoHorizontalOverflowAsync("workout details", "iPhone 17 Pro Max portrait");
    await Page.ScreenshotAsync(new PageScreenshotOptions { Path = Path.Combine(directory, "tr-026-workout-details-mobile.png"), FullPage = false });
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task Calendar_and_history_items_open_complete_read_only_detail_sheets()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ResetSimulatorAsync(gateway.BaseAddress);
    await scenario.ConfigureBrowserAsync(Page);
    await scenario.InstallVisualDataRoutesAsync(Page);
    string directory = Path.Combine(gateway.ProjectRoot, "screenshots", "showcase");
    Directory.CreateDirectory(directory);

    await Page.SetViewportSizeAsync(1440, 1000);
    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/calendar").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    ILocator calendarItem = Page.Locator(".calendar-option:visible").First;
    await Expect(calendarItem).ToBeVisibleAsync();
    await calendarItem.ClickAsync();
    ILocator calendarDialog = Page.GetByRole(AriaRole.Dialog);
    await Expect(calendarDialog.GetByRole(AriaRole.Heading, new() { Name = "Planned graph", Exact = true })).ToBeVisibleAsync();
    await Expect(calendarDialog.GetByRole(AriaRole.Heading, new() { Name = "All planned changes", Exact = true })).ToBeVisibleAsync();
    Assert.True(await calendarDialog.GetByRole(AriaRole.Region, new() { Name = "All planned workout changes", Exact = true }).Locator("tbody tr").CountAsync() > 0);
    await Expect(calendarDialog.GetByRole(AriaRole.Button, new() { Name = "Start", Exact = true })).ToHaveCountAsync(0);
    await Expect(calendarDialog.GetByRole(AriaRole.Button, new() { Name = "Send this session to Garmin", Exact = true })).ToHaveCountAsync(0);
    await Page.ScreenshotAsync(new PageScreenshotOptions { Path = Path.Combine(directory, "tr-031-calendar-workout-details.png"), FullPage = false });
    await calendarDialog.GetByRole(AriaRole.Button, new() { Name = "Close calendar workout details", Exact = true }).ClickAsync();
    await Expect(calendarDialog).ToBeHiddenAsync();

    await Page.GotoAsync(new Uri(gateway.BaseAddress, "/history").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
    ILocator historyItem = Page.Locator(".history-card").First;
    await Expect(historyItem).ToBeVisibleAsync();
    await historyItem.ClickAsync();
    ILocator historyDialog = Page.GetByRole(AriaRole.Dialog);
    await Expect(historyDialog.GetByRole(AriaRole.Heading, new() { Name = "Live graph", Exact = true })).ToBeVisibleAsync();
    await Expect(historyDialog.GetByRole(AriaRole.Heading, new() { Name = "All recorded changes", Exact = true })).ToBeVisibleAsync();
    await Expect(historyDialog.GetByRole(AriaRole.Region, new() { Name = "All recorded session changes", Exact = true }).Locator("tbody tr")).Not.ToHaveCountAsync(0);
    await Expect(historyDialog.Locator("[data-series='measured-speed']")).ToHaveAttributeAsync("d", new Regex("^M"));
    await Expect(historyDialog.GetByRole(AriaRole.Button, new() { Name = "Start", Exact = true })).ToHaveCountAsync(0);
    await Expect(historyDialog.GetByRole(AriaRole.Button, new() { Name = "Stop", Exact = true })).ToHaveCountAsync(0);
    await Page.ScreenshotAsync(new PageScreenshotOptions { Path = Path.Combine(directory, "tr-031-history-session-details.png"), FullPage = false });

    await Page.SetViewportSizeAsync(440, 956);
    await AssertNoHorizontalOverflowAsync("history session details", "iPhone 17 Pro Max portrait");
    await Page.ScreenshotAsync(new PageScreenshotOptions { Path = Path.Combine(directory, "tr-031-history-session-details-mobile.png"), FullPage = false });
  }

  [Fact]
  [Trait("Category", "Browser")]
  public async Task History_detail_sheet_replaces_a_stalled_local_request_with_a_retryable_error()
  {
    GalleryScenario scenario = await gateway.GetOrCreateGalleryScenarioAsync();
    await scenario.ResetSimulatorAsync(gateway.BaseAddress);
    await scenario.ConfigureBrowserAsync(Page);
    await scenario.InstallVisualDataRoutesAsync(Page);
    var releaseRequest = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    await Page.RouteAsync($"**/api/history/{scenario.HistorySessionId:D}", async route =>
    {
      await releaseRequest.Task;
      await route.AbortAsync();
    });

    try
    {
      await Page.GotoAsync(new Uri(gateway.BaseAddress, "/history").AbsoluteUri, new PageGotoOptions { WaitUntil = WaitUntilState.NetworkIdle });
      await Page.Locator(".history-card").First.ClickAsync();
      ILocator dialog = Page.GetByRole(AriaRole.Dialog);
      await Expect(dialog.GetByText("Loading the stored session, graph, and changes…", new() { Exact = true })).ToBeVisibleAsync();
      await Expect(dialog.GetByText("The session details could not be loaded from the local gateway. No stored data was changed.", new() { Exact = true }))
        .ToBeVisibleAsync(new() { Timeout = 6_000 });
      await Expect(dialog.GetByRole(AriaRole.Button, new() { Name = "Try again", Exact = true })).ToBeVisibleAsync();
    }
    finally
    {
      releaseRequest.TrySetResult();
    }
  }

  private async Task AssertNoHorizontalOverflowAsync(string fileName, string viewport)
  {
    bool overflow = await Page.EvaluateAsync<bool>(
      "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1");
    Assert.False(overflow, $"{fileName} has horizontal overflow at the {viewport} viewport.");
  }

  private async Task AssertTimeAxisLabelsDoNotOverlapAsync(string viewport)
  {
    ILocator labels = Page.GetByLabel("Elapsed time axis", new() { Exact = true }).Locator("span");
    LocatorBoundingBoxResult? previous = null;
    int count = await labels.CountAsync();
    for (int index = 0; index < count; index++)
    {
      LocatorBoundingBoxResult? current = await labels.Nth(index).BoundingBoxAsync();
      if (current is null)
      {
        continue;
      }
      if (previous is not null)
      {
        Assert.True(current.X >= previous.X + previous.Width - 1,
          $"Elapsed-time labels overlap at {viewport}: previous x={previous.X:0.##}, width={previous.Width:0.##}; current x={current.X:0.##}, width={current.Width:0.##}.");
      }

      previous = current;
    }
  }
}
