using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Microsoft.Playwright;

namespace TreadmillRunner.E2ETests;

public sealed record GalleryScenario(
  Guid MarcProfileId,
  Guid SecondProfileId,
  Guid FeaturedWorkoutId,
  Guid FeaturedWorkoutRevisionId,
  Guid HistorySessionId)
{
  public const string FeaturedWorkoutName = "5K builder · Easy intervals";
  public const string FeaturedSeriesName = "5K Builder";
  public const string SecondProfileName = "Runner 2";
  private const string ControllerHolderId = "gallery-controller";
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

  public static async Task<GalleryScenario> CreateAsync(Uri baseAddress)
  {
    using HttpClient client = new() { BaseAddress = baseAddress };
    JsonElement marc = await CreateProfileAsync(client, "Marc", 114, 206, 12);
    JsonElement second = await CreateProfileAsync(client, SecondProfileName, 68, 188, 11);
    Guid marcId = marc.GetProperty("id").GetGuid();
    Guid secondId = second.GetProperty("id").GetGuid();

    JsonElement featured = await CreateWorkoutAsync(client, FeaturedWorkoutName,
      "A progressive five-step run with warm-up, aerobic intervals, recoveries, and cool-down.",
      [
        Step(6, 4.5, 0.5, "Warm up"),
        Step(5, 7.0, 1.0, "Steady"),
        Step(3, 5.5, 0.5, "Recover"),
        Step(5, 7.5, 1.5, "Build"),
        Step(6, 4.5, 0.5, "Cool down"),
      ]);
    JsonElement steady = await CreateWorkoutAsync(client, "5K builder · Steady progression",
      "A controlled aerobic progression for the next step in the 5K plan.",
      [Step(8, 5.0, 0.5, "Easy"), Step(12, 6.5, 1.0, "Aerobic"), Step(8, 7.0, 1.0, "Finish")]);
    JsonElement longRun = await CreateWorkoutAsync(client, "10K builder · Long easy run",
      "Comfortable endurance running with a gentle incline change in the middle.",
      [Step(10, 5.0, 0.5, "Settle"), Step(30, 6.0, 1.0, "Endurance"), Step(10, 5.0, 0.5, "Cool down")]);
    JsonElement recovery = await CreateWorkoutAsync(client, "Recovery walk",
      "A short, low-intensity recovery session.",
      [Step(20, 4.5, 0.5, "Relax")]);

    Guid featuredRevision = featured.GetProperty("revisionId").GetGuid();
    Guid steadyRevision = steady.GetProperty("revisionId").GetGuid();
    Guid longRunRevision = longRun.GetProperty("revisionId").GetGuid();
    Guid recoveryRevision = recovery.GetProperty("revisionId").GetGuid();
    DateOnly monthStart = new(DateTime.Today.Year, DateTime.Today.Month, 1);
    await CreateSeriesAsync(client, marcId, FeaturedSeriesName, monthStart, 1 | 4 | 32,
      [featuredRevision, steadyRevision]);
    await CreateSeriesAsync(client, marcId, "10K Builder", monthStart, 2 | 16,
      [longRunRevision, recoveryRevision]);
    JsonElement first5K = await CreateProgramAsync(
      client,
      "First 5K",
      "A progressive three-run plan that builds confidence toward a comfortable 5K.",
      "5K",
      [featuredRevision, steadyRevision, recoveryRevision]);
    await CreateProgramAsync(
      client,
      "Stronger 10K",
      "A reusable endurance plan combining steady work, a long run, and recovery.",
      "10K",
      [steadyRevision, longRunRevision, recoveryRevision]);
    await StartProgramAsync(client, first5K.GetProperty("id").GetGuid(), first5K.GetProperty("revisionId").GetGuid(), marcId);

    return new GalleryScenario(
      marcId,
      secondId,
      featured.GetProperty("workoutId").GetGuid(),
      featuredRevision,
      Guid.Parse("d07a6dd5-cae7-4ca4-b099-06be38ed2694"));
  }

  public async Task ResetSimulatorAsync(Uri baseAddress)
  {
    using HttpClient client = new() { BaseAddress = baseAddress };
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/live/simulator/reset", new { });
    if (response.StatusCode != HttpStatusCode.NoContent)
      throw new InvalidOperationException($"Gallery simulator reset failed with {(int)response.StatusCode}.");
  }

  public async Task SetPhysicalMotionAsync(Uri baseAddress, double speedKph, double inclinePercent)
  {
    using HttpClient client = new() { BaseAddress = baseAddress };
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/live/simulator/physical-motion", new
    {
      isMoving = true,
      measuredSpeedKph = speedKph,
      measuredInclinePercent = inclinePercent,
    });
    if (response.StatusCode != HttpStatusCode.NoContent)
      throw new InvalidOperationException($"Gallery simulator motion failed with {(int)response.StatusCode}.");
  }

  public Task ConfigureBrowserAsync(IPage page)
  {
    string initialization = $$"""
      window.localStorage.setItem('treadmillrunner.active-profile', '{{MarcProfileId:D}}');
      window.localStorage.setItem('treadmillrunner.controller-holder', '{{ControllerHolderId}}');
      """;
    return page.AddInitScriptAsync(initialization);
  }

  public async Task InstallVisualDataRoutesAsync(IPage page)
  {
    await page.RouteAsync("**/api/operations/dashboard", route => route.FulfillAsync(new() { Status = 404 }));
    await page.RouteAsync("**/api/devices/enrollments", route => FulfillJsonAsync(route, DeviceEnrollments()));
    await page.RouteAsync("**/api/devices/status*", route => FulfillJsonAsync(route, DeviceStatus()));
    await page.RouteAsync("**/api/devices/reliability*", route => FulfillJsonAsync(route, DeviceReliability()));
    await page.RouteAsync("**/api/devices/treadmill/maintenance", route => FulfillJsonAsync(route, MaintenanceDue()));
    await page.RouteAsync("**/api/planning/workouts/reuse*", route => FulfillJsonAsync(route, WorkoutReuse()));
    await page.RouteAsync("**/api/planning/workout-sets/import/preview", route => FulfillJsonAsync(route, WorkoutSetPreview()));
    await page.RouteAsync("**/api/updates/status", route => FulfillJsonAsync(route, UpdateStatus()));
    await page.RouteAsync("**/api/operations/database/status", route => FulfillJsonAsync(route, DatabaseIntegrityStatus()));
    await page.RouteAsync("**/api/operations/access**", async route =>
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

      await FulfillJsonAsync(route, new
      {
        available = true,
        preferredCandidateId = "gallery-lan",
        candidates = new[]
        {
          new
          {
            id = "gallery-lan",
            label = "Private Wi-Fi · 192.168.1.20",
            url = "http://192.168.1.20:5180/",
            isSecure = false,
          },
        },
        message = "Scan from a device on the same private Wi-Fi network.",
      });
    });
    await page.RouteAsync("**/api/integrations/garmin/profiles/*/status", route =>
      FulfillJsonAsync(route, GarminStatus(new Uri(route.Request.Url).AbsolutePath)));
    await page.RouteAsync("**/api/integrations/garmin/activity-upload/profiles/*/status", route =>
      FulfillJsonAsync(route, new
      {
        profileId = MarcProfileId,
        connected = true,
        enabled = false,
        accountLabel = "Marc's private Garmin session",
        state = "Connected",
        pending = 0,
        confirmed = 4,
        failed = 0,
        unknown = 1,
        lastSuccessAtUtc = DateTimeOffset.Parse("2026-08-04T07:02:00Z"),
        lastError = "One upload outcome needs review before dismissal.",
        version = 3,
        adapterState = "Ready",
        adapterMessage = "Garmin activity upload is ready to connect.",
        canConnect = true,
      }));
    await page.RouteAsync("**/api/integrations/garmin/activity-upload/profiles/*/jobs", route =>
      FulfillJsonAsync(route, new[]
      {
        new
        {
          id = Guid.Parse("90f72113-58ec-4e15-b39c-f7cf21bd02cd"),
          workoutSessionId = HistorySessionId,
          status = "Unknown",
          attemptCount = 1,
          remoteId = (string?)null,
          failureKind = "transport",
          canRetry = false,
          workoutTitle = "Progressive 5K intervals",
          startedAtUtc = DateTimeOffset.Parse("2026-08-04T06:28:00Z"),
          durationSeconds = 2052.0,
          lastError = "Confirmation was lost; Garmin Connect must be checked before dismissal.",
          updatedAtUtc = DateTimeOffset.Parse("2026-08-04T07:03:00Z"),
        },
      }));
    await page.RouteAsync("**/api/integrations/garmin/watch/profiles/*", route =>
      FulfillJsonAsync(route, new
      {
        id = Guid.Parse("4209617b-9b2e-4c7f-b728-0adfb6ec3e7a"),
        userProfileId = MarcProfileId,
        runnerName = "Marc",
        deviceLabel = "Marc's Fenix 8",
        createdAtUtc = DateTimeOffset.Parse("2026-08-03T19:15:00Z"),
        lastSeenAtUtc = DateTimeOffset.Parse("2026-08-04T07:04:00Z"),
        version = 2,
      }));
    await page.RouteAsync("**/api/history**", async route =>
    {
      string path = new Uri(route.Request.Url).AbsolutePath;
      if (path.Equals("/api/history/weekly", StringComparison.OrdinalIgnoreCase))
      {
        await FulfillJsonAsync(route, WeeklyHistory());
      }
      else if (path.Equals($"/api/history/{HistorySessionId:D}/deletion-preview", StringComparison.OrdinalIgnoreCase))
      {
        await FulfillJsonAsync(route, DeletionPreview());
      }
      else if (path.Equals($"/api/history/{HistorySessionId:D}", StringComparison.OrdinalIgnoreCase))
      {
        await FulfillJsonAsync(route, HistoryDetail());
      }
      else if (path.Equals("/api/history", StringComparison.OrdinalIgnoreCase))
      {
        bool tests = new Uri(route.Request.Url).Query.Contains("includeTests=true", StringComparison.OrdinalIgnoreCase);
        await FulfillJsonAsync(route, tests ? SystemTestSummaries() : HistorySummaries());
      }
      else
      {
        await route.ContinueAsync();
      }
    });
  }

  public Task InstallMaintenanceSetupRouteAsync(IPage page) =>
    page.RouteAsync("**/api/devices/treadmill/maintenance", route => FulfillJsonAsync(route, MaintenanceSetup()));

  private object GarminStatus(string path)
  {
    bool marc = path.Contains(MarcProfileId.ToString("D"), StringComparison.OrdinalIgnoreCase);
    return new
    {
      profileId = marc ? MarcProfileId : SecondProfileId,
      providerConfigured = true,
      setupMessage = "Garmin Connect Developer Program configuration is ready.",
      connected = marc,
      accountLabel = marc ? "Marc's Garmin account" : null,
      connectedAtUtc = marc ? DateTimeOffset.Parse("2026-08-03T19:10:00Z") : (DateTimeOffset?)null,
      lastSyncAttemptAtUtc = marc ? DateTimeOffset.Parse("2026-08-04T06:45:00Z") : (DateTimeOffset?)null,
      lastSyncSuccessAtUtc = marc ? DateTimeOffset.Parse("2026-08-04T06:45:03Z") : (DateTimeOffset?)null,
      lastError = (string?)null,
      pendingItems = marc ? 2 : 0,
      failedItems = 0,
      syncedItems = marc ? 7 : 0,
    };
  }

  public static FilePayload ImportFile() => new()
  {
    Name = "5k-gallery-workout.json",
    MimeType = "application/json",
    Buffer = Encoding.UTF8.GetBytes(
      """
      {"schemaVersion":1,"title":"Imported 5K tempo preview","description":"A deterministic gallery import preview.","blocks":[{"kind":"step","goal":{"kind":"time","durationTicks":3600000000},"speed":{"kind":"fixed","kilometersPerHour":5.0},"incline":{"kind":"fixed","percent":0.5},"cue":"Warm up","notes":null},{"kind":"step","goal":{"kind":"time","durationTicks":6000000000},"speed":{"kind":"fixed","kilometersPerHour":7.5},"incline":{"kind":"fixed","percent":1.0},"cue":"Tempo","notes":null},{"kind":"step","goal":{"kind":"time","durationTicks":3000000000},"speed":{"kind":"fixed","kilometersPerHour":4.5},"incline":{"kind":"fixed","percent":0.5},"cue":"Cool down","notes":null}]}
      """),
  };

  public static FilePayload GeneratedSetFile() => new()
  {
    Name = "first-5k-treadmill-workout-v4.zip",
    MimeType = "application/zip",
    Buffer = "deterministic intercepted gallery bundle"u8.ToArray(),
  };

  private static async Task<JsonElement> CreateProfileAsync(
    HttpClient client,
    string name,
    double weight,
    ushort maximumHeartRate,
    double maximumSpeed)
  {
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/profiles", new
    {
      operationId = Guid.NewGuid(),
      displayName = name,
      unitSystem = "Metric",
      weightKilograms = weight,
      maximumHeartRateBpm = maximumHeartRate,
      maximumSpeedKph = maximumSpeed,
      heartRateZones = SuggestedZones(maximumHeartRate),
      expectedVersion = (int?)null,
      heartRateIncreaseStepKph = 0.2,
      heartRateIncreaseCooldownSeconds = 30,
      heartRateDecreaseStepKph = 0.5,
      heartRateDecreaseCooldownSeconds = 15,
    });
    return await ReadCreatedAsync(response, $"profile {name}");
  }

  private static async Task<JsonElement> CreateWorkoutAsync(
    HttpClient client,
    string name,
    string description,
    object[] blocks)
  {
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/workouts", new
    {
      operationId = Guid.NewGuid(),
      name,
      description,
      blocks,
    });
    return await ReadCreatedAsync(response, $"workout {name}");
  }

  private static async Task CreateSeriesAsync(
    HttpClient client,
    Guid profileId,
    string name,
    DateOnly startDate,
    int weekdayMask,
    Guid[] revisionIds)
  {
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/calendar/series", new
    {
      operationId = Guid.NewGuid(),
      profileId,
      name,
      timeZoneId = "Europe/Brussels",
      startDate,
      endDate = (DateOnly?)null,
      intervalWeeks = 1,
      weekdayMask,
      alternatives = revisionIds.Select((revisionId, index) => new { workoutRevisionId = revisionId, displayOrder = index }).ToArray(),
      exceptions = Array.Empty<object>(),
      expectedVersion = (int?)null,
    });
    if (response.StatusCode != HttpStatusCode.Created)
      throw new InvalidOperationException($"Could not create gallery series {name}: {await response.Content.ReadAsStringAsync()}");
  }

  private static async Task<JsonElement> CreateProgramAsync(
    HttpClient client,
    string name,
    string description,
    string category,
    Guid[] revisionIds)
  {
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/programs", new
    {
      operationId = Guid.NewGuid(),
      name,
      description,
      category,
      items = revisionIds.Select(revisionId => new { workoutRevisionId = revisionId }).ToArray(),
    });
    return await ReadCreatedAsync(response, $"training plan {name}");
  }

  private static async Task StartProgramAsync(HttpClient client, Guid programId, Guid revisionId, Guid profileId)
  {
    using HttpResponseMessage response = await client.PostAsJsonAsync(
      $"/api/planning/programs/{programId:D}/start",
      new { operationId = Guid.NewGuid(), profileId, expectedProgramRevisionId = revisionId, expectedActiveRunId = (Guid?)null, expectedActiveRunVersion = (int?)null });
    if (response.StatusCode != HttpStatusCode.OK)
      throw new InvalidOperationException($"Could not start gallery training plan: {await response.Content.ReadAsStringAsync()}");
  }

  private static object Step(double minutes, double speed, double incline, string cue) => new
  {
    kind = "step",
    repetitions = 1,
    blocks = Array.Empty<object>(),
    goalKind = "time",
    goalValue = minutes,
    speedKind = "fixed",
    speedStartKph = speed,
    speedEndKph = 0.0,
    heartRateMinimumBpm = 0,
    heartRateMaximumBpm = 0,
    heartRateZoneNumber = 0,
    heartRateInitialSpeedKph = 0.0,
    heartRateMinimumSpeedKph = 0.0,
    heartRateMaximumSpeedKph = 0.0,
    inclineKind = "fixed",
    inclineStartPercent = incline,
    inclineEndPercent = 0.0,
    cue,
    notes = "Deterministic screenshot fixture",
  };

  private static object[] SuggestedZones(ushort maximum) =>
  [
    Zone(1, "Warm up", maximum, 0.50, 0.60, false),
    Zone(2, "Easy", maximum, 0.60, 0.70, false),
    Zone(3, "Aerobic", maximum, 0.70, 0.80, false),
    Zone(4, "Threshold", maximum, 0.80, 0.90, false),
    Zone(5, "Maximum", maximum, 0.90, 1.00, true),
  ];

  private static object Zone(int number, string name, ushort maximum, double minimum, double upper, bool last) => new
  {
    number,
    name,
    minimumBpm = (ushort)Math.Ceiling(maximum * minimum),
    maximumBpm = last ? maximum : (ushort)(Math.Ceiling(maximum * upper) - 1),
  };

  private static async Task<JsonElement> ReadCreatedAsync(HttpResponseMessage response, string label)
  {
    if (response.StatusCode != HttpStatusCode.Created)
      throw new InvalidOperationException($"Could not create gallery {label}: {await response.Content.ReadAsStringAsync()}");
    return await response.Content.ReadFromJsonAsync<JsonElement>();
  }

  private object DeviceEnrollments()
  {
    Guid treadmillId = Guid.Parse("9d8764d1-0113-4728-8e5a-b18a8064f836");
    Guid polarId = Guid.Parse("a0db1f20-1d27-435a-901b-4610f60481f4");
    Guid garminId = Guid.Parse("b71e5747-0767-4833-a85c-6af49a117fe1");
    return new object[]
    {
      new
      {
        id = treadmillId, role = "Treadmill", deviceId = "gallery-omega-z-7D3A", protocolId = "horizon-omega-z-ftms",
        identityFingerprint = new string('A', 64), displayName = "Horizon Omega Z", modelNumber = "Omega Z",
        firmwareRevision = "S3.02", telemetryMode = "Ftms", capabilities = (object?)null, evidence = "HardwareVerified",
        lastVerifiedAtUtc = DateTimeOffset.UtcNow.AddMinutes(-2), version = 1,
        heartRateDeviceKind = (string?)null, heartRateDeviceFamily = (string?)null, assignments = Array.Empty<object>(),
      },
      new
      {
        id = polarId, role = "HeartRate", deviceId = "gallery-polar-5A36", protocolId = "bluetooth-heart-rate",
        identityFingerprint = new string('B', 64), displayName = "Polar H10 A1B2C3D4", modelNumber = "H10",
        firmwareRevision = "Current", telemetryMode = (string?)null, capabilities = (object?)null, evidence = "PassivelyObserved",
        lastVerifiedAtUtc = DateTimeOffset.UtcNow.AddSeconds(-5), version = 1,
        heartRateDeviceKind = "ChestStrap", heartRateDeviceFamily = "Polar",
        assignments = new[] { Assignment(Guid.Parse("11a3f623-dd90-4680-a191-9e50dc106e34"), MarcProfileId, true, 0) },
      },
      new
      {
        id = garminId, role = "HeartRate", deviceId = "gallery-garmin-FE08", protocolId = "bluetooth-heart-rate",
        identityFingerprint = new string('C', 64), displayName = "Garmin Fenix 8 HR Broadcast", modelNumber = "Fenix 8",
        firmwareRevision = "Current", telemetryMode = (string?)null, capabilities = (object?)null, evidence = "PassivelyObserved",
        lastVerifiedAtUtc = DateTimeOffset.UtcNow.AddSeconds(-8), version = 1,
        heartRateDeviceKind = "Watch", heartRateDeviceFamily = "Garmin",
        assignments = new[] { Assignment(Guid.Parse("f7146ec7-243a-4a4d-9299-0e29e8c31930"), MarcProfileId, false, 10) },
      },
    };
  }

  private static object Assignment(Guid id, Guid profileId, bool preferred, int priority) => new
  {
    id,
    userProfileId = profileId,
    priority,
    autoConnect = true,
    isPreferred = preferred,
    version = 1,
  };

  private object DeviceStatus()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    Guid polarId = Guid.Parse("a0db1f20-1d27-435a-901b-4610f60481f4");
    Guid garminId = Guid.Parse("b71e5747-0767-4833-a85c-6af49a117fe1");
    object treadmill = new
    {
      role = 0,
      state = 6,
      connectionGeneration = 4,
      displayName = "Horizon Omega Z",
      protocolId = "horizon-omega-z-ftms",
      telemetryMode = "Ftms",
      lastObservedAt = now.AddMilliseconds(-180),
      fault = (string?)null,
    };
    object heartRate = new
    {
      role = 1,
      state = 6,
      connectionGeneration = 7,
      displayName = "Polar H10 A1B2C3D4",
      protocolId = "bluetooth-heart-rate",
      telemetryMode = "ChestStrap",
      lastObservedAt = now.AddMilliseconds(-120),
      fault = (string?)null,
    };
    return new
    {
      capturedAt = now,
      treadmill,
      heartRate,
      treadmillTelemetry = new { observedAt = now.AddMilliseconds(-180), speedKph = 0.0, inclinePercent = 0.0 },
      heartRateBpm = 143,
      heartRateObservedAt = now.AddMilliseconds(-120),
      reportedCapabilities = (object?)null,
      heartRateSources = new object[]
      {
        new { enrollmentId = polarId, displayName = "Polar H10 A1B2C3D4", kind = 0, family = 0, state = 6, connectionGeneration = 7, beatsPerMinute = 143, observedAt = now.AddMilliseconds(-120), fault = (string?)null, batteryPercent = 86, batteryObservedAt = now.AddMinutes(-2) },
        new { enrollmentId = garminId, displayName = "Garmin Fenix 8 HR Broadcast", kind = 1, family = 1, state = 6, connectionGeneration = 5, beatsPerMinute = 141, observedAt = now.AddMilliseconds(-350), fault = (string?)null },
      },
      selectedHeartRateEnrollmentId = polarId,
      selectedHeartRateDeviceKind = 0,
      selectedHeartRateDeviceFamily = 0,
      heartRateSelectionGeneration = 3,
      heartRateSelectionReason = "Polar H10 is the preferred fresh sensor for Marc.",
      selectedHeartRateBatteryPercent = 86,
      selectedHeartRateBatteryObservedAt = now.AddMinutes(-2),
    };
  }

  private object DeviceReliability()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    return new
    {
      capturedAtUtc = now,
      windowStartedAtUtc = now.AddDays(-7),
      windowDays = 7,
      devices = new object[]
      {
        new { enrollmentId = Guid.Parse("9d8764d1-0113-4728-8e5a-b18a8064f836"), displayName = "Horizon Omega Z", role = "Treadmill", currentState = "Ready", connectionGeneration = 4, lastTelemetryAtUtc = now.AddMilliseconds(-180), incidentCount = 1, recoveredIncidentCount = 1, currentOutageStartedAtUtc = (DateTimeOffset?)null, currentFailedAttemptCount = 0, lastRecoverySeconds = 4.2, longestRecoverySeconds = 4.2, lastFailureKind = "NativeDisconnected", lastSanitizedFault = "Bluetooth connection ended.", batteryPercent = (byte?)null, batteryObservedAtUtc = (DateTimeOffset?)null },
        new { enrollmentId = Guid.Parse("a0db1f20-1d27-435a-901b-4610f60481f4"), displayName = "Polar H10 A1B2C3D4", role = "HeartRate", currentState = "Ready", connectionGeneration = 7, lastTelemetryAtUtc = now.AddMilliseconds(-120), incidentCount = 2, recoveredIncidentCount = 2, currentOutageStartedAtUtc = (DateTimeOffset?)null, currentFailedAttemptCount = 0, lastRecoverySeconds = 2.1, longestRecoverySeconds = 5.8, lastFailureKind = "TelemetrySilent", lastSanitizedFault = "Heart-rate telemetry became silent.", batteryPercent = (byte?)86, batteryObservedAtUtc = now.AddMinutes(-2) },
      },
    };
  }

  private object[] WorkoutReuse() =>
  [
    new { workoutId = FeaturedWorkoutId, workoutRevisionId = FeaturedWorkoutRevisionId, name = FeaturedWorkoutName, description = "A progressive five-step run.", expandedStepCount = 5, plannedDurationMinutes = 25.0, lastCompletedAtUtc = DateTimeOffset.UtcNow.AddDays(-1), lastActualDuration = "00:34:12", completionCount = 3 },
    new { workoutId = Guid.Parse("5d4d90d9-a6e9-4372-93a5-a40bf0ba5d81"), workoutRevisionId = Guid.Parse("ed8ecbb4-4dab-4ccd-8275-596a8f9a9f99"), name = "Recovery walk", description = "An easy local recovery.", expandedStepCount = 1, plannedDurationMinutes = 20.0, lastCompletedAtUtc = DateTimeOffset.UtcNow.AddDays(-5), lastActualDuration = "00:18:05", completionCount = 2 },
  ];

  private static object WorkoutSetPreview() => new
  {
    previewId = Guid.Parse("67062a9a-380a-431a-a795-3e92fae31fcf"),
    sourceSha256 = new string('d', 64),
    fileName = "first-5k-treadmill-workout-v4.zip",
    planName = "First 5K · six-week builder",
    category = "5K",
    toolVersion = "4.2.0",
    slotCount = 18,
    variantCount = 54,
    expiresAtUtc = DateTimeOffset.UtcNow.AddMinutes(15),
    warnings = Array.Empty<string>(),
    strategies = new[] { new { name = "Default", substitutions = 0 }, new { name = "PreferHeartRate", substitutions = 12 }, new { name = "PreferFixed", substitutions = 18 } },
    slots = new[]
    {
      new { canonicalSlot = "W01-S01", week = 1, session = 1, variants = new[] { new { sessionId = "w01s01-primary", variant = "primary", title = "Easy foundation", controlMode = "adaptive", selectionRule = "default" }, new { sessionId = "w01s01-hr", variant = "hr-alternative", title = "Easy foundation · HR", controlMode = "heart-rate", selectionRule = "prefer-heart-rate" } } },
      new { canonicalSlot = "W01-S02", week = 1, session = 2, variants = new[] { new { sessionId = "w01s02-primary", variant = "primary", title = "Short intervals", controlMode = "adaptive", selectionRule = "default" }, new { sessionId = "w01s02-fixed", variant = "fixed-fallback", title = "Short intervals · fixed", controlMode = "fixed", selectionRule = "prefer-fixed" } } },
      new { canonicalSlot = "W01-S03", week = 1, session = 3, variants = new[] { new { sessionId = "w01s03-primary", variant = "primary", title = "Long easy run", controlMode = "adaptive", selectionRule = "default" } } },
    },
  };

  private static object DatabaseIntegrityStatus() => new
  {
    state = "Healthy",
    message = "The database passed quick and full validation, and a verified last-known-good backup was retained.",
    updatedAtUtc = DateTimeOffset.UtcNow.AddMinutes(-2),
    lastQuickCheckAtUtc = DateTimeOffset.UtcNow.AddMinutes(-2),
    lastFullCheckAtUtc = DateTimeOffset.UtcNow.AddMinutes(-2),
    lastHealthyAtUtc = DateTimeOffset.UtcNow.AddMinutes(-2),
    lastMaintenanceAtUtc = DateTimeOffset.UtcNow.AddMinutes(-2),
    lastBackupAtUtc = DateTimeOffset.UtcNow.AddMinutes(-1),
    lastBackupFileName = "integrity-verified.trb",
    lastBackupSha256 = new string('a', 64),
    nextCheckAtUtc = DateTimeOffset.UtcNow.AddHours(23),
    recoveryRequired = false,
    issues = Array.Empty<string>(),
  };

  private static object MaintenanceDue()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    return new
    {
      policy = new { id = Guid.Parse("80b048cf-eef7-41cf-9bea-e64ab85c1899"), deviceEnrollmentId = Guid.Parse("9d8764d1-0113-4728-8e5a-b18a8064f836"), deviceDisplayName = "Horizon Omega Z", intervalMonths = 3, distanceIntervalKilometers = 241.0, version = 3, updatedAtUtc = now.AddDays(-100) },
      state = 2,
      isDue = true,
      appTrackedHardwareDistanceKilometers = 534.7,
      nextDueAtUtc = now.AddDays(-8),
      nextDueDistanceKilometers = 610.2,
      remainingKilometers = 75.5,
      lastEvent = new { id = Guid.Parse("e90dfeb9-21e9-468c-9949-36f0eb1a793f"), policyId = Guid.Parse("80b048cf-eef7-41cf-9bea-e64ab85c1899"), operationId = Guid.Parse("56e21b02-5555-49a4-a79f-bfed4b2fdcdd"), performedAtUtc = now.AddDays(-100), appDistanceBaselineKilometers = 369.2, note = "Deck inspected; silicone surface serviced.", createdAtUtc = now.AddDays(-100) },
      events = new[] { new { id = Guid.Parse("e90dfeb9-21e9-468c-9949-36f0eb1a793f"), policyId = Guid.Parse("80b048cf-eef7-41cf-9bea-e64ab85c1899"), operationId = Guid.Parse("56e21b02-5555-49a4-a79f-bfed4b2fdcdd"), performedAtUtc = now.AddDays(-100), appDistanceBaselineKilometers = 369.2, note = "Deck inspected; silicone surface serviced.", createdAtUtc = now.AddDays(-100) } },
      usageNotice = "Only hardware sessions recorded by TreadmillRunner count toward this distance. Console-only use is not visible to the app.",
    };
  }

  private static object MaintenanceSetup()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    return new
    {
      policy = new { id = Guid.Parse("80b048cf-eef7-41cf-9bea-e64ab85c1899"), deviceEnrollmentId = Guid.Parse("9d8764d1-0113-4728-8e5a-b18a8064f836"), deviceDisplayName = "Horizon Omega Z", intervalMonths = 3, distanceIntervalKilometers = 241.0, version = 1, updatedAtUtc = now },
      state = 0,
      isDue = false,
      appTrackedHardwareDistanceKilometers = 534.7,
      nextDueAtUtc = (DateTimeOffset?)null,
      nextDueDistanceKilometers = (double?)null,
      remainingKilometers = (double?)null,
      lastEvent = (object?)null,
      events = Array.Empty<object>(),
      usageNotice = "Only hardware sessions recorded by TreadmillRunner count toward this distance. Console-only use is not visible to the app.",
    };
  }

  private static object UpdateStatus() => new
  {
    state = "Available",
    currentVersion = "1.5.4",
    availableVersion = "1.5.5",
    stagedVersion = (string?)null,
    releaseNotes = "Touch dashboard polish, populated visual fixtures, and update reliability improvements.",
    lastCheckedAtUtc = DateTimeOffset.UtcNow.AddMinutes(-1),
    message = "A newer signed release is available and ready to verify.",
  };

  private object[] HistorySummaries()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    return
    [
      HistorySummary(HistorySessionId, FeaturedWorkoutRevisionId, FeaturedWorkoutName, 4, now.AddDays(-1), "00:34:12", 5.02, 148, 171, 8.8, 1.0),
      HistorySummary(Guid.Parse("98cdba4a-39d3-4c35-a05f-e2d7f8e8a48f"), FeaturedWorkoutRevisionId, "5K builder · Steady progression", 4, now.AddDays(-3), "00:31:46", 4.62, 142, 164, 8.7, 0.8),
      HistorySummary(Guid.Parse("fd15908e-80ba-43bc-967e-974136582fd4"), FeaturedWorkoutRevisionId, "Recovery walk", 5, now.AddDays(-5), "00:18:05", 1.47, 116, 129, 4.9, 0.5),
      HistorySummary(Guid.Parse("f6aa0559-5882-4ab6-bce9-c65cda8e793e"), FeaturedWorkoutRevisionId, "Winter base interruption", 6, now.AddDays(-40), "00:12:10", 0.91, 121, 137, 4.5, 0.4),
    ];
  }

  private object[] SystemTestSummaries()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    return [HistorySummary(Guid.Parse("60d0485d-fc7a-4e55-b8a8-2574ab34f359"), FeaturedWorkoutRevisionId, "Garmin upload verification", 4, now.AddHours(-4), "00:01:00", .08, 120, 120, 4.5, 0, 3)];
  }

  private object HistorySummary(
    Guid sessionId,
    Guid revisionId,
    string title,
    int status,
    DateTimeOffset startedAt,
    string duration,
    double distance,
    double averageHeartRate,
    ushort maximumHeartRate,
    double averageSpeed,
    double averageIncline,
    int origin = 1) => new
    {
      sessionId,
      userProfileId = MarcProfileId,
      userProfileName = "Marc",
      workoutRevisionId = revisionId,
      workoutTitle = title,
      status,
      startedAt,
      endedAt = startedAt.Add(TimeSpan.Parse(duration)),
      duration,
      distanceKilometers = distance,
      estimatedKilocalories = distance * 72,
      averageHeartRateBpm = averageHeartRate,
      maximumHeartRateBpm = maximumHeartRate,
      averageSpeedKph = averageSpeed,
      averageInclinePercent = averageIncline,
      origin,
    };

  private object DeletionPreview() => new
  {
    sessionId = HistorySessionId,
    userProfileId = MarcProfileId,
    workoutTitle = FeaturedWorkoutName,
    state = 4,
    origin = 1,
    sampleCount = 2052,
    eventCount = 4,
    distanceKilometers = 5.02,
    maintenanceDistanceImpactKilometers = 5.02,
    isProgramLinked = false,
    garminStatus = (string?)null,
    canDelete = true,
    reason = "This session can be permanently deleted.",
    revision = new string('a', 64),
    garminRemoteActivityMayRemain = false,
  };

  private static object WeeklyHistory()
  {
    DateTimeOffset from = DateTimeOffset.UtcNow.Date.AddDays(-((int)DateTimeOffset.UtcNow.DayOfWeek + 6) % 7);
    return new
    {
      from,
      throughExclusive = from.AddDays(7),
      completedSessionCount = 2,
      duration = "01:05:58",
      distanceKilometers = 9.64,
    };
  }

  internal object HistoryDetail()
  {
    DateTimeOffset started = DateTimeOffset.UtcNow.AddDays(-1).AddMinutes(-35);
    double[] speeds = [4.5, 5.0, 6.2, 7.0, 7.1, 6.0, 7.5, 7.4, 5.5, 4.5];
    ushort[] heartRates = [112, 120, 132, 146, 151, 143, 158, 162, 139, 124];
    object[] samples = speeds.Select((speed, index) => new
    {
      sessionId = HistorySessionId,
      sequence = index + 1,
      capturedAt = started.AddSeconds(index * 210),
      elapsed = TimeSpan.FromSeconds(index * 210),
      plannedSpeedKph = speed,
      requestedSpeedKph = speed,
      measuredSpeedKph = speed + (index % 3 - 1) * 0.1,
      plannedInclinePercent = index is 3 or 4 or 6 or 7 ? 1.0 : 0.5,
      requestedInclinePercent = index is 3 or 4 or 6 or 7 ? 1.0 : 0.5,
      measuredInclinePercent = index is 3 or 4 or 6 or 7 ? 1.0 : 0.5,
      heartRateBpm = heartRates[index],
      distanceKilometers = 5.02 * index / (speeds.Length - 1),
      estimatedKilocalories = 362.0 * index / (speeds.Length - 1),
      telemetryAge = "00:00:00.2",
      metricAlgorithmVersion = "estimated-calories/v1",
    }).ToArray();
    return new
    {
      definition = new
      {
        sessionId = HistorySessionId,
        userProfileId = MarcProfileId,
        userProfileName = "Marc",
        workoutRevisionId = FeaturedWorkoutRevisionId,
        workoutTitle = FeaturedWorkoutName,
        armedAt = started.AddMinutes(-1),
        controllerConfigurationJson = "{}",
        metricAlgorithmVersion = "estimated-calories/v1",
      },
      state = 4,
      startedAt = started,
      endedAt = started.AddMinutes(34).AddSeconds(12),
      duration = "00:34:12",
      distanceKilometers = 5.02,
      estimatedKilocalories = 362.0,
      averageHeartRateBpm = 148.0,
      maximumHeartRateBpm = 171,
      averageSpeedKph = 8.8,
      averageInclinePercent = 1.0,
      samples,
      events = new object[]
      {
        new { eventType = "physical-movement-detected", occurredAt = started, message = "Physical treadmill movement confirmed." },
        new { eventType = "manual-speed-override", occurredAt = started.AddMinutes(18), previousSpeedKph = 7.0, requestedSpeedKph = 7.5 },
        new { eventType = "workout-step-transition", occurredAt = started.AddMinutes(22) },
        new { eventType = "session-completed", occurredAt = started.AddMinutes(34).AddSeconds(12) },
      },
      analytics = new
      {
        sessionId = HistorySessionId,
        heartRateZones = new object[]
        {
          new { zoneNumber = 1, name = "Warm up", duration = "00:05:10" },
          new { zoneNumber = 2, name = "Easy", duration = "00:08:20" },
          new { zoneNumber = 3, name = "Aerobic", duration = "00:13:42" },
          new { zoneNumber = 4, name = "Threshold", duration = "00:07:00" },
          new { zoneNumber = 5, name = "Maximum", duration = "00:00:00" },
        },
        adherencePercentage = 94.6,
        adherenceAlgorithmVersion = "adherence/v1",
        eventCounts = new { manualSpeedOverrides = 1, manualInclineOverrides = 0, pauses = 0, disconnects = 0, warnings = 0 },
      },
    };
  }

  private static Task FulfillJsonAsync(IRoute route, object payload) => route.FulfillAsync(new RouteFulfillOptions
  {
    Status = 200,
    ContentType = "application/json",
    Body = JsonSerializer.Serialize(payload, JsonOptions),
  });
}
