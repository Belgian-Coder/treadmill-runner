namespace TreadmillRunner.Core.Devices;

public enum HeartRateDeviceKind
{
  ChestStrap,
  Watch,
  Sensor,
}

public enum HeartRateDeviceFamily
{
  Polar,
  Garmin,
  Other,
}

public static class HeartRateDeviceClassifier
{
  public static readonly Guid HeartRateService =
    Guid.Parse("0000180d-0000-1000-8000-00805f9b34fb");

  public static readonly Guid PolarService =
    Guid.Parse("0000feee-0000-1000-8000-00805f9b34fb");

  private static readonly string[] ChestStrapTerms =
    ["polar h10", "polar h9", "chest", "strap", "belt"];

  private static readonly string[] WatchTerms =
  [
    "watch", "garmin watch", "forerunner", "fenix", "fēnix", "epix", "vivoactive", "vívoactive", "venu",
    "instinct", "enduro", "tactix", "quatix", "marq", "lily", "descent", "approach",
    "apple", "pixel", "galaxy", "coros", "suunto",
  ];

  public static HeartRateDeviceKind Classify(string? name, IReadOnlyCollection<Guid>? serviceUuids = null)
  {
    string normalized = name?.Trim().ToLowerInvariant() ?? string.Empty;
    if (serviceUuids?.Contains(PolarService) == true ||
        ChestStrapTerms.Any(normalized.Contains))
    {
      return HeartRateDeviceKind.ChestStrap;
    }

    return WatchTerms.Any(normalized.Contains)
      ? HeartRateDeviceKind.Watch
      : HeartRateDeviceKind.Sensor;
  }

  public static bool IsPreferredPolar(string? name, IReadOnlyCollection<Guid>? serviceUuids = null)
  {
    string normalized = name?.Trim().ToLowerInvariant() ?? string.Empty;
    return normalized.Contains("polar h10", StringComparison.Ordinal) ||
      serviceUuids?.Contains(PolarService) == true;
  }

  public static int Priority(string? name, IReadOnlyCollection<Guid>? serviceUuids = null)
  {
    if (IsPreferredPolar(name, serviceUuids)) return 0;
    return Classify(name, serviceUuids) switch
    {
      HeartRateDeviceKind.ChestStrap => 1,
      HeartRateDeviceKind.Watch => 2,
      _ => 3,
    };
  }

  public static HeartRateDeviceFamily Family(string? name, IReadOnlyCollection<Guid>? serviceUuids = null)
  {
    string normalized = name?.Trim().ToLowerInvariant() ?? string.Empty;
    if (IsPreferredPolar(name, serviceUuids) || normalized.Contains("polar", StringComparison.Ordinal))
    {
      return HeartRateDeviceFamily.Polar;
    }

    return normalized.Contains("garmin", StringComparison.Ordinal) ||
      normalized.Contains("forerunner", StringComparison.Ordinal) ||
      normalized.Contains("fenix", StringComparison.Ordinal) ||
      normalized.Contains("fēnix", StringComparison.Ordinal) ||
      normalized.Contains("epix", StringComparison.Ordinal) ||
      normalized.Contains("vivoactive", StringComparison.Ordinal) ||
      normalized.Contains("vívoactive", StringComparison.Ordinal) ||
      normalized.Contains("venu", StringComparison.Ordinal) ||
      normalized.Contains("instinct", StringComparison.Ordinal) ||
      normalized.Contains("enduro", StringComparison.Ordinal) ||
      normalized.Contains("tactix", StringComparison.Ordinal) ||
      normalized.Contains("quatix", StringComparison.Ordinal) ||
      normalized.Contains("marq", StringComparison.Ordinal) ||
      normalized.Contains("lily", StringComparison.Ordinal) ||
      normalized.Contains("descent", StringComparison.Ordinal) ||
      normalized.Contains("approach", StringComparison.Ordinal)
        ? HeartRateDeviceFamily.Garmin
        : HeartRateDeviceFamily.Other;
  }
}
