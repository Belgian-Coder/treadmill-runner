namespace TreadmillRunner.Core.Profiles;

public static class SuggestedHeartRateZones
{
  private static readonly ZoneDefinition[] Definitions =
  [
    new(1, "Warm up", 0.50, 0.60),
    new(2, "Easy", 0.60, 0.70),
    new(3, "Aerobic", 0.70, 0.80),
    new(4, "Threshold", 0.80, 0.90),
    new(5, "Maximum", 0.90, 1.00),
  ];

  public static IReadOnlyList<HeartRateZone> FromMaximum(ushort maximumHeartRateBpm)
  {
    if (maximumHeartRateBpm is < 10 or > 250)
      throw new ArgumentOutOfRangeException(nameof(maximumHeartRateBpm), "Maximum heart rate must be between 10 and 250 bpm to calculate five zones.");

    return Definitions
      .Select((definition, index) => new HeartRateZone(
        definition.Number,
        definition.Name,
        (ushort)Math.Ceiling(maximumHeartRateBpm * definition.MinimumFraction),
        index == Definitions.Length - 1
          ? maximumHeartRateBpm
          : (ushort)(Math.Ceiling(maximumHeartRateBpm * definition.MaximumFraction) - 1)))
      .ToArray();
  }

  private sealed record ZoneDefinition(int Number, string Name, double MinimumFraction, double MaximumFraction);
}
