using TreadmillRunner.Core.Profiles;

namespace TreadmillRunner.Core.Tests;

public sealed class ProfileValidationTests
{
  [Fact]
  public void Profile_accepts_ordered_non_overlapping_zones()
  {
    var profile = new UserProfile(
        Guid.NewGuid(),
        "Runner",
        UnitSystem.Metric,
        72.5,
        190,
        18,
        [new HeartRateZone(1, "Easy", 100, 130), new HeartRateZone(2, "Tempo", 131, 160)]);

    Assert.Equal(2, profile.HeartRateZones.Count);
  }

  [Fact]
  public void Profile_rejects_overlapping_zones()
  {
    Assert.Throws<ArgumentException>(() => new UserProfile(
        Guid.NewGuid(),
        "Runner",
        UnitSystem.Metric,
        72.5,
        190,
        18,
        [new HeartRateZone(1, "Easy", 100, 140), new HeartRateZone(2, "Tempo", 140, 160)]));
  }

  [Theory]
  [InlineData(double.NaN)]
  [InlineData(double.PositiveInfinity)]
  [InlineData(0)]
  public void Profile_rejects_invalid_weight(double weight)
  {
    Assert.Throws<ArgumentOutOfRangeException>(() => new UserProfile(
        Guid.NewGuid(),
        "Runner",
        UnitSystem.Metric,
        weight,
        null,
        null,
        []));
  }

  [Fact]
  public void Suggested_zones_cover_fifty_to_one_hundred_percent_without_integer_overlap()
  {
    IReadOnlyList<HeartRateZone> zones = SuggestedHeartRateZones.FromMaximum(190);

    Assert.Collection(zones,
      zone => AssertZone(zone, 1, "Warm up", 95, 113),
      zone => AssertZone(zone, 2, "Easy", 114, 132),
      zone => AssertZone(zone, 3, "Aerobic", 133, 151),
      zone => AssertZone(zone, 4, "Threshold", 152, 170),
      zone => AssertZone(zone, 5, "Maximum", 171, 190));
  }

  private static void AssertZone(HeartRateZone zone, int number, string name, ushort minimum, ushort maximum)
  {
    Assert.Equal(number, zone.Number);
    Assert.Equal(name, zone.Name);
    Assert.Equal(minimum, zone.MinimumBpm);
    Assert.Equal(maximum, zone.MaximumBpm);
  }
}
