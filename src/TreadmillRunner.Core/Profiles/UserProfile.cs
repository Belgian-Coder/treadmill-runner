namespace TreadmillRunner.Core.Profiles;

public enum UnitSystem
{
  Metric,
}

public sealed record HeartRateControllerSettings
{
  public static HeartRateControllerSettings Default { get; } = new(0.2, 30, 0.5, 15);

  public HeartRateControllerSettings(
    double increaseStepKph,
    int increaseCooldownSeconds,
    double decreaseStepKph,
    int decreaseCooldownSeconds)
  {
    if (!double.IsFinite(increaseStepKph) || increaseStepKph is < 0.1 or > 0.5)
      throw new ArgumentOutOfRangeException(nameof(increaseStepKph));
    if (increaseCooldownSeconds is < 15 or > 180)
      throw new ArgumentOutOfRangeException(nameof(increaseCooldownSeconds));
    if (!double.IsFinite(decreaseStepKph) || decreaseStepKph is < 0.1 or > 1.0)
      throw new ArgumentOutOfRangeException(nameof(decreaseStepKph));
    if (decreaseCooldownSeconds is < 5 or > 120)
      throw new ArgumentOutOfRangeException(nameof(decreaseCooldownSeconds));

    IncreaseStepKph = increaseStepKph;
    IncreaseCooldownSeconds = increaseCooldownSeconds;
    DecreaseStepKph = decreaseStepKph;
    DecreaseCooldownSeconds = decreaseCooldownSeconds;
  }

  public double IncreaseStepKph { get; }
  public int IncreaseCooldownSeconds { get; }
  public double DecreaseStepKph { get; }
  public int DecreaseCooldownSeconds { get; }
}

public sealed class HeartRateZone
{
  public HeartRateZone(int number, string name, ushort minimumBpm, ushort maximumBpm)
  {
    if (number is < 1 or > 10)
    {
      throw new ArgumentOutOfRangeException(nameof(number), "Zone number must be between 1 and 10.");
    }

    ArgumentException.ThrowIfNullOrWhiteSpace(name);
    if (minimumBpm == 0 || maximumBpm > 250 || minimumBpm > maximumBpm)
    {
      throw new ArgumentOutOfRangeException(nameof(minimumBpm), "Heart-rate bounds must be ordered and between 1 and 250 bpm.");
    }

    Number = number;
    Name = name.Trim();
    MinimumBpm = minimumBpm;
    MaximumBpm = maximumBpm;
  }

  public int Number { get; }

  public string Name { get; }

  public ushort MinimumBpm { get; }

  public ushort MaximumBpm { get; }
}

public sealed class UserProfile
{
  public UserProfile(
      Guid id,
      string displayName,
      UnitSystem unitSystem,
      double weightKilograms,
      ushort? maximumHeartRateBpm,
      double? maximumSpeedKph,
      IReadOnlyList<HeartRateZone> heartRateZones,
      HeartRateControllerSettings? heartRateController = null)
  {
    if (id == Guid.Empty)
    {
      throw new ArgumentException("Profile ID cannot be empty.", nameof(id));
    }

    ArgumentException.ThrowIfNullOrWhiteSpace(displayName);
    ArgumentNullException.ThrowIfNull(heartRateZones);
    if (unitSystem != UnitSystem.Metric)
    {
      throw new ArgumentOutOfRangeException(nameof(unitSystem), "Profiles use Metric units.");
    }
    ValidatePositiveFinite(weightKilograms, nameof(weightKilograms));

    if (maximumHeartRateBpm is 0 or > 250)
    {
      throw new ArgumentOutOfRangeException(nameof(maximumHeartRateBpm), "Maximum heart rate must be between 1 and 250 bpm.");
    }

    if (maximumSpeedKph is { } speed)
    {
      ValidatePositiveFinite(speed, nameof(maximumSpeedKph));
    }

    var zones = heartRateZones.OrderBy(static zone => zone.Number).ToArray();
    for (var index = 1; index < zones.Length; index++)
    {
      if (zones[index - 1].Number == zones[index].Number || zones[index].MinimumBpm <= zones[index - 1].MaximumBpm)
      {
        throw new ArgumentException("Heart-rate zones must have unique numbers and must not overlap.", nameof(heartRateZones));
      }
    }

    Id = id;
    DisplayName = displayName.Trim();
    UnitSystem = unitSystem;
    WeightKilograms = weightKilograms;
    MaximumHeartRateBpm = maximumHeartRateBpm;
    MaximumSpeedKph = maximumSpeedKph;
    HeartRateZones = Array.AsReadOnly(zones);
    HeartRateController = heartRateController ?? HeartRateControllerSettings.Default;
  }

  public Guid Id { get; }

  public string DisplayName { get; }

  public UnitSystem UnitSystem { get; }

  public double WeightKilograms { get; }

  public ushort? MaximumHeartRateBpm { get; }

  public double? MaximumSpeedKph { get; }

  public IReadOnlyList<HeartRateZone> HeartRateZones { get; }
  public HeartRateControllerSettings HeartRateController { get; }

  private static void ValidatePositiveFinite(double value, string parameterName)
  {
    if (!double.IsFinite(value) || value <= 0)
    {
      throw new ArgumentOutOfRangeException(parameterName, "Value must be finite and greater than zero.");
    }
  }
}
